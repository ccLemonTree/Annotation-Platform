from uuid import uuid4
import pytest
from sqlalchemy import select
from models import Asset,Annotation,LibraryImage


@pytest.mark.asyncio
async def test_crop_only_queue_neighbors_and_original_write_guard(client,auth_headers,test_db,test_project,test_asset):
    test_project.label_schema={'labels':['人'],'annotation_scope':'crops'}
    crop=Asset(project_id=test_project.id,original_name='crop.png',blob_path='owned/crop.png',content_hash='crop',
        source_type='model_crop',file_size=10,width=20,height=30,format='PNG',annotation_status='pending')
    test_db.add(crop);test_project.total_assets=2;await test_db.commit()
    path='/api/assets?project_id='+str(test_project.id)
    assert client.get(path,headers=auth_headers).json()['total']==2
    scoped=client.get(path+'&scope=annotation',headers=auth_headers).json()
    assert [row['id'] for row in scoped['items']]==[str(crop.id)]
    originals=client.get(path+'&image_kind=originals',headers=auth_headers).json()
    assert [row['id'] for row in originals['items']]==[str(test_asset.id)]
    neighbors=client.get('/api/assets/'+str(crop.id)+'/neighbors',headers=auth_headers).json()
    assert neighbors['total']==1 and neighbors['next_id'] is None and neighbors['previous_id'] is None
    assert client.get('/api/assets/'+str(test_asset.id)+'/neighbors',headers=auth_headers).status_code==409
    result=client.post('/api/annotations',headers=auth_headers,json={'asset_id':str(test_asset.id),'annotation_data':{'boxes':[]}})
    assert result.status_code==409
    test_db.add_all([Annotation(asset_id=asset.id,annotation_data={'boxes':[]},version=1,is_latest=True,
        review_status='pending') for asset in [test_asset,crop]])
    await test_db.commit()
    annotations=client.get('/api/annotations',headers=auth_headers,params={'project_id':str(test_project.id),'scope':'annotation'}).json()
    assert [row['asset_id'] for row in annotations['items']]==[str(crop.id)]
    exported=client.get('/api/export/projects/'+str(test_project.id)+'/export',headers=auth_headers,params={'format':'json'}).json()
    assert [row['id'] for row in exported['assets']]==[str(crop.id)]
    summary=client.get('/api/export/projects/'+str(test_project.id)+'/summary',headers=auth_headers).json()
    assert summary['all']['images']==1 and summary['approved']['images']==0
    stats=client.get('/api/projects/'+str(test_project.id)+'/stats?scope=annotation',headers=auth_headers).json()
    assert stats['total_assets']==1


@pytest.mark.asyncio
async def test_legacy_crops_cannot_be_listed_selected_or_attached(client,auth_headers,test_db,test_project):
    image=LibraryImage(tenant_id=test_project.tenant_id,original_name='private.png',blob_path='crops/private.png',
        content_hash='private',source_type='model_crop',file_size=10,width=20,height=30)
    test_db.add(image);await test_db.commit()
    assert client.get('/api/images',headers=auth_headers).json()['total']==0
    assert client.get('/api/images/selection',headers=auth_headers).json()['image_ids']==[]
    assert client.post('/api/images/associate',headers=auth_headers,json={'project_id':str(test_project.id),'image_ids':[str(image.id)]}).status_code==404
    assert client.get('/api/images/'+str(image.id)+'/content',headers=auth_headers).status_code==404


def test_annotation_scope_config_rejects_unknown_values():
    from schemas.project import ProjectCreate
    with pytest.raises(ValueError):ProjectCreate(project_name='scope',task_type='vector',label_schema={'annotation_scope':'invalid'})
