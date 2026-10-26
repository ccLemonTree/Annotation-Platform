from copy import deepcopy
import pytest
from models import Annotation,Asset,Project,LibraryImage,Tenant,User,ExportSnapshot
from services.auth import create_access_token

BOX=dict(label='person',x=1,y=2,width=3,height=4)


@pytest.fixture
def path(test_project):return f'/api/projects/{test_project.id}/annotations/remove-label'


@pytest.fixture
def preview(client,auth_headers,path):
    def load(label='person'):
        response=client.post(path+'/preview',headers=auth_headers,json=dict(label=label))
        assert response.status_code==200,response.text
        return response.json()
    return load


@pytest.mark.asyncio
async def test_remove_only_exact_label_project_wide_with_history_review_and_shared_data_preserved(client,auth_headers,test_db,test_project,test_asset,path,preview):
    test_project.label_schema=dict(labels=['person','Person','bike']);test_project.total_assets=3
    image=LibraryImage(tenant_id=1,original_name='shared.png',blob_path='shared.png',content_hash='shared',file_size=10,width=64,height=48,source_type='upload',
        imported_annotation=dict(names=['person'],boxes=[BOX],trusted=True))
    test_db.add(image);await test_db.flush();test_asset.image_id=image.id
    second=Asset(project_id=test_project.id,original_name='two.png',blob_path='two.png',content_hash='two',source_type='upload',file_size=10,width=64,height=48)
    third=Asset(project_id=test_project.id,original_name='three.png',blob_path='three.png',content_hash='three',source_type='upload',file_size=10,width=64,height=48)
    other=Project(tenant_id=1,project_name='other',task_type='detection')
    test_db.add_all([second,third,other]);await test_db.flush()
    other_asset=Asset(project_id=other.id,image_id=image.id,original_name='shared.png',blob_path='shared.png',content_hash='shared',file_size=10,width=64,height=48,source_type='upload')
    test_db.add(other_asset);await test_db.commit()
    rows=[]
    for asset,boxes in [(test_asset,[BOX,{**BOX,'label':'bike'},{**BOX,'label':'Person'}]),(second,[BOX,BOX]),(third,[{**BOX,'label':'bike'}]),(other_asset,[BOX])]:
        row=client.post('/api/annotations',headers=auth_headers,json=dict(asset_id=str(asset.id),annotation_data=dict(boxes=boxes,import_source={'fixture':True}))).json()
        assert client.post('/api/annotations/'+row['id']+'/review',headers=auth_headers,json=dict(approved=True)).status_code==200
        rows.append(row)
    before=deepcopy(rows)
    snapshot=ExportSnapshot(project_id=test_project.id,user_id=1,export_format='yolo',blob_path='snapshot.zip',content_hash='snapshot',manifest={'annotations':before})
    test_db.add(snapshot);await test_db.commit()
    planned=preview()
    assert (planned['affected_images'],planned['removed_boxes'],planned['empty_images'],planned['reviewed_images'])==(2,3,1,2)
    assert client.get('/api/annotations',headers=auth_headers).json()['total']==4
    response=client.post(path,headers=auth_headers,json=dict(label='person',preview_token=planned['preview_token']))
    assert response.status_code==200,response.text
    for index,asset in enumerate([test_asset,second]):
        versions=client.get('/api/annotations',headers=auth_headers,params=dict(asset_id=str(asset.id),latest_only=False)).json()['items']
        latest=next(row for row in versions if row['is_latest']);old=next(row for row in versions if not row['is_latest'])
        assert latest['version']==2 and latest['review_status']=='pending' and latest['reviewer_id'] is None
        assert latest['annotation_source']=='bulk_label_delete'
        assert latest['annotation_data']['boxes']==([before[0]['annotation_data']['boxes'][1],before[0]['annotation_data']['boxes'][2]] if index==0 else [])
        assert latest['annotation_data']['import_source']=={'fixture':True}
        assert old['annotation_data']==before[index]['annotation_data'] and old['review_status']=='approved'
    stats=client.get(f'/api/projects/{test_project.id}/stats',headers=auth_headers).json()
    assert stats['annotated_count']==3 and stats['reviewed_count']==1
    assert client.get('/api/annotations/'+rows[2]['id'],headers=auth_headers).json()['is_latest'] is True
    assert client.get('/api/annotations/'+rows[3]['id'],headers=auth_headers).json()['annotation_data']['boxes']==[BOX]
    await test_db.refresh(image);await test_db.refresh(snapshot);await test_db.refresh(test_project)
    assert image.imported_annotation['boxes']==[BOX] and snapshot.manifest=={'annotations':before}
    assert test_project.label_schema['labels']==['person','Person','bike']
    assert client.post(path,headers=auth_headers,json=dict(label='person',preview_token=planned['preview_token'])).status_code==409
    assert preview()['affected_images']==0


@pytest.mark.asyncio
async def test_stale_preview_rejects_new_changes_and_does_not_write(client,auth_headers,test_db,test_project,test_asset,path,preview):
    row=client.post('/api/annotations',headers=auth_headers,json=dict(asset_id=str(test_asset.id),annotation_data=dict(boxes=[BOX]))).json()
    plan=preview()
    changed=client.put('/api/annotations/'+row['id'],headers=auth_headers,json=dict(base_version=1,annotation_data=dict(boxes=[BOX,BOX]))).json()
    assert client.post(path,headers=auth_headers,json=dict(label='person',preview_token=plan['preview_token'])).status_code==409
    current=client.get('/api/annotations/'+changed['id'],headers=auth_headers).json()
    assert current['is_latest'] and len(current['annotation_data']['boxes'])==2
    assert client.get('/api/annotations',headers=auth_headers,params=dict(latest_only=False)).json()['total']==2


@pytest.mark.asyncio
async def test_crop_scope_and_zero_matches(client,auth_headers,test_db,test_project,test_asset,path,preview):
    test_project.label_schema=dict(labels=['person'],annotation_scope='crops')
    crop=Asset(project_id=test_project.id,original_name='crop.png',blob_path='crop.png',content_hash='crop',file_size=1,width=64,height=48,source_type='model_crop')
    test_db.add(crop);await test_db.flush()
    test_db.add_all([Annotation(asset_id=asset.id,version=1,is_latest=True,annotation_data=dict(boxes=[BOX]),review_status='pending') for asset in (test_asset,crop)])
    await test_db.commit()
    plan=preview();assert plan['affected_images']==1 and plan['scope']=='crops'
    assert client.post(path,headers=auth_headers,json=dict(label='person',preview_token=plan['preview_token'])).status_code==200
    original=client.get('/api/annotations',headers=auth_headers,params=dict(asset_id=str(test_asset.id))).json()['items'][0]
    assert original['annotation_data']['boxes']==[BOX] and original['version']==1
    empty=preview('absent');assert empty['affected_images']==empty['removed_boxes']==0


@pytest.mark.asyncio
async def test_label_removal_authorization_tenant_type_and_validation(client,auth_headers,test_db,test_project,path):
    user=User(id=2,tenant_id=1,username='annotator',email='annotator@test',password_hash='fixture',role='annotator',is_active=True)
    tenant=Tenant(id=2,tenant_name='other tenant');test_db.add_all([user,tenant]);await test_db.flush()
    other=Project(tenant_id=2,project_name='private',task_type='detection');test_db.add(other);await test_db.commit()
    token=create_access_token(dict(user_id=2,tenant_id=1,username='annotator'));headers={'Authorization':'Bearer '+token}
    for suffix,body in [('/preview',dict(label='person')),('',dict(label='person',preview_token='a'*64))]:
        assert client.post(path+suffix,headers=headers,json=body).status_code==403
        assert client.post(path+suffix,json=body).status_code==401
        other_path=f'/api/projects/{other.id}/annotations/remove-label'+suffix
        assert client.post(other_path,headers=auth_headers,json=body).status_code==404
    assert client.post(path+'/preview',headers=auth_headers,json=dict(label='')).status_code==422
    assert client.post(path,headers=auth_headers,json=dict(label='person')).status_code==422
    assert client.post(path+'/preview',headers=auth_headers,json=dict(label='person',asset_ids=[])).status_code==422
    test_project.task_type='classification';await test_db.commit()
    assert client.post(path+'/preview',headers=auth_headers,json=dict(label='person')).status_code==400


@pytest.mark.asyncio
async def test_failure_rolls_back_all_versions_and_counters(client,auth_headers,test_db,test_project,test_asset,path,preview,monkeypatch):
    from services import project_labels
    second=Asset(project_id=test_project.id,original_name='second.png',blob_path='second.png',content_hash='second',file_size=1,width=64,height=48,source_type='upload')
    test_db.add(second);await test_db.commit()
    for asset in (test_asset,second):
        assert client.post('/api/annotations',headers=auth_headers,json=dict(asset_id=str(asset.id),annotation_data=dict(boxes=[BOX]))).status_code==201
    plan=preview();original=project_labels.save_annotation;calls=[]
    async def fail_second(*args,**kwargs):
        from fastapi import HTTPException
        calls.append(True)
        if len(calls)==2:raise HTTPException(409,'fixture conflict')
        return await original(*args,**kwargs)
    monkeypatch.setattr(project_labels,'save_annotation',fail_second)
    result=client.post(path,headers=auth_headers,json=dict(label='person',preview_token=plan['preview_token']))
    assert result.status_code==409 and len(calls)==2
    versions=client.get('/api/annotations',headers=auth_headers,params=dict(latest_only=False)).json()['items']
    assert len(versions)==2 and all(row['version']==1 and row['is_latest'] and row['annotation_data']['boxes']==[BOX] for row in versions)


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['review','new_image','schema'])
async def test_preview_rejects_review_schema_and_project_image_changes(client,auth_headers,test_db,test_project,test_asset,path,preview,change):
    row=client.post('/api/annotations',headers=auth_headers,json=dict(asset_id=str(test_asset.id),annotation_data=dict(boxes=[BOX]))).json()
    plan=preview()
    if change=='review':
        assert client.post('/api/annotations/'+row['id']+'/review',headers=auth_headers,json=dict(approved=True)).status_code==200
    elif change=='new_image':
        test_db.add(Asset(project_id=test_project.id,original_name='new.png',blob_path='new.png',content_hash='new',file_size=1,width=64,height=48,source_type='upload'))
        await test_db.commit()
    else:
        test_project.label_schema={'labels':['person','bike']};await test_db.commit()
    assert client.post(path,headers=auth_headers,json=dict(label='person',preview_token=plan['preview_token'])).status_code==409
    assert client.get('/api/annotations/'+row['id'],headers=auth_headers).json()['annotation_data']['boxes']==[BOX]
