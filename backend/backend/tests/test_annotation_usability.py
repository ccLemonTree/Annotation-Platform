from uuid import uuid4
import pytest
from sqlalchemy import delete
from models import Asset, Annotation, ProjectMember, LibraryImage, Project
from tests.test_current_image_analysis import analysis_env


@pytest.mark.asyncio
async def test_attribute_options_are_project_scoped_latest_known_values(analysis_env):
    s=analysis_env
    for index,value in enumerate(['常见车牌','常见车牌','唯一车牌',None,'无法判断']):
        asset=Asset(project_id=s.project.id,original_name=f'{index}.png',blob_path='unused',content_hash=str(index),file_size=1,source_type='model_crop')
        s.db.add(asset);await s.db.flush()
        s.db.add(Annotation(asset_id=asset.id,version=1,is_latest=True,review_status='pending',annotation_source='auto',
            annotation_data={'attribute_schema':s.schema,'attributes':{'plate':{'value':value,'reason':''}}}))
    await s.db.commit()
    path=f'/api/projects/{s.project.id}/attribute-options'
    response=s.client.get(path,headers=s.member)
    assert response.status_code==200,response.text
    assert response.json()=={'plate':['常见车牌']}
    await s.db.execute(delete(ProjectMember));await s.db.commit()
    assert s.client.get(path,headers=s.member).status_code==404


@pytest.mark.asyncio
async def test_original_view_uses_root_coordinates_and_private_content(analysis_env):
    s=analysis_env
    first={'parent_asset_id':str(s.asset.id),'parent_content_hash':s.asset.content_hash,'region':[10,5,60,45],'depth':1}
    crop=Asset(project_id=s.project.id,original_name='face.png',blob_path='unused',content_hash='crop',file_size=1,width=10,height=8,source_type='model_crop',
        device_info={'crop_chain':[first,{'region':[3,4,13,12],'depth':2}],
            'crop':{'region':[3,4,13,12],'root_region':[13,9,23,17],'depth':2}})
    s.db.add(crop);await s.db.commit()
    path=f'/api/assets/{crop.id}/source'
    response=s.client.get(path,headers=s.member)
    assert response.status_code==200,response.text
    assert response.json()['region']==[13,9,23,17]
    assert response.json()['width']==64 and 'blob_path' not in response.json()
    assert s.client.get(path+'/content',headers=s.member).content==s.raw
    assert s.client.get(path+'/content').status_code==401
    crop.device_info={**crop.device_info,'crop':{'region':[3,4,13,12],'depth':2}}
    await s.db.commit()
    assert s.client.get(path,headers=s.member).json()['region']==[13,9,23,17]
    await s.db.execute(delete(ProjectMember));await s.db.commit()
    assert s.client.get(path,headers=s.member).status_code==404


@pytest.mark.asyncio
async def test_original_view_rejects_missing_or_invalid_provenance(analysis_env):
    s=analysis_env;s.asset.source_type='model_crop'
    for info in [None,{'crop':{'parent_asset_id':str(uuid4()),'parent_blob_path':s.asset.blob_path,'region':[1,2,3,4]}}]:
        s.asset.device_info=info;await s.db.commit()
        response=s.client.get(f'/api/assets/{s.asset.id}/source',headers=s.member)
        assert response.status_code==404,response.text


@pytest.mark.asyncio
async def test_original_asset_can_be_viewed_without_crop_rectangle(analysis_env):
    s=analysis_env;response=s.client.get(f'/api/assets/{s.asset.id}/source',headers=s.member)
    assert response.status_code==200,response.text
    assert response.json()['region'] is None


@pytest.mark.asyncio
async def test_deleted_original_can_use_verified_library_image(analysis_env):
    s=analysis_env
    original=LibraryImage(tenant_id=1,blob_path=s.asset.blob_path,content_hash=s.asset.content_hash,original_name='original.png',file_size=len(s.raw),width=64,height=48,format='PNG',source_type='upload')
    s.db.add(original);await s.db.flush()
    s.asset.source_type='model_crop';s.asset.device_info={'crop':{'parent_asset_id':str(uuid4()),'parent_image_id':str(original.id),'parent_content_hash':original.content_hash,'region':[1,2,10,20]}}
    await s.db.commit();path=f'/api/assets/{s.asset.id}/source'
    assert s.client.get(path,headers=s.member).status_code==200
    assert s.client.get(path+'/content',headers=s.member).content==s.raw
    original.tenant_id=2;await s.db.commit()
    assert s.client.get(path,headers=s.member).status_code==404


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid',['foreign_project','wrong_hash','out_of_bounds'])
async def test_source_provenance_validation(analysis_env,invalid):
    s=analysis_env
    other=Project(tenant_id=1,project_name='other',task_type='caption');s.db.add(other);await s.db.flush()
    parent=Asset(project_id=other.id if invalid=='foreign_project' else s.project.id,original_name='root.png',blob_path=s.asset.blob_path,content_hash='root',file_size=1,width=64,height=48,source_type='upload')
    s.db.add(parent);await s.db.flush()
    s.asset.source_type='model_crop';s.asset.device_info={'crop':{'parent_asset_id':str(parent.id),'parent_content_hash':'wrong' if invalid=='wrong_hash' else 'root','region':[0,0,100 if invalid=='out_of_bounds' else 10,20]}}
    await s.db.commit()
    assert s.client.get(f'/api/assets/{s.asset.id}/source',headers=s.member).status_code==(404 if invalid=='foreign_project' else 409)


@pytest.mark.asyncio
async def test_options_exclude_old_rejected_skipped_and_other_projects(analysis_env):
    s=analysis_env
    for index in range(2):
        for kind in ['old','rejected','skipped','foreign']:
            project=s.project
            if kind=='foreign':
                project=Project(tenant_id=1,project_name='unassigned',task_type='caption');s.db.add(project);await s.db.flush()
            asset=Asset(project_id=project.id,original_name=kind,blob_path='unused',content_hash=f'{kind}{index}',file_size=1,source_type='model_crop');s.db.add(asset);await s.db.flush()
            s.db.add(Annotation(asset_id=asset.id,version=1,is_latest=kind!='old',review_status='rejected' if kind=='rejected' else 'pending',
                annotation_data={'skipped':kind=='skipped','attributes':{'plate':{'value':kind,'reason':''}}}))
    await s.db.commit()
    assert s.client.get(f'/api/projects/{s.project.id}/attribute-options',headers=s.member).json()=={}
