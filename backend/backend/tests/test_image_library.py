from uuid import uuid4
from tests.test_p2_annotations import BOX
import pytest_asyncio
import pytest

@pytest_asyncio.fixture
async def foreign_image(test_db):
    from models import Tenant,LibraryImage
    test_db.add(Tenant(id=2,tenant_name='other image tenant'))
    await test_db.flush()
    image=LibraryImage(tenant_id=2,blob_path='foreign.jpg',content_hash='foreign',original_name='foreign.jpg',
        file_size=10,width=10,height=10,format='JPEG',source_type='upload')
    test_db.add(image);await test_db.commit()
    return image

def test_library_cannot_read_or_attach_foreign_tenant_image(client,auth_headers,test_project,foreign_image):
    assert client.get('/api/images',headers=auth_headers).json()['total']==0
    assert client.get('/api/images/'+str(foreign_image.id)+'/content',headers=auth_headers).status_code==404
    assert client.delete('/api/images/'+str(foreign_image.id),headers=auth_headers).status_code==404
    assert client.post('/api/images/associate',headers=auth_headers,json={'project_id':str(test_project.id),'image_ids':[str(foreign_image.id)]}).status_code==404

def upload(client, headers, image):
    return client.post('/api/images/batch-upload', headers=headers,
        files=[('files', ('library.jpg', image.getvalue(), 'image/jpeg'))])

def test_upload_without_project_filter_and_reuse(client, auth_headers, mock_image_data, test_project):
    response = upload(client, auth_headers, mock_image_data)
    assert response.status_code == 201, response.text
    image = response.json()[0]
    assert image['created_at'].endswith('Z') or image['created_at'].endswith('+00:00')
    overview=client.get('/api/stats/dashboard',headers=auth_headers).json()
    assert overview['assets_overview']['total']==1
    assert upload(client, auth_headers, mock_image_data).json()[0]['id'] == image['id']
    assert client.get('/api/images', headers=auth_headers, params={'q':'library','source_type':'upload','batch_id':image['batch_id']}).json()['total'] == 1
    assert client.get('/api/images', headers=auth_headers, params={'q':'missing'}).json()['total'] == 0
    assert client.get('/api/images', headers=auth_headers, params={'q':'%'}).json()['total'] == 0
    assert client.get('/api/images', headers=auth_headers, params={'created_from':'2999-01-01T00:00:00Z'}).json()['total'] == 0
    assert client.get('/api/images', headers=auth_headers, params={'created_to':'2000-01-01T00:00:00Z'}).json()['total'] == 0
    assert client.get('/api/images', headers=auth_headers, params={'created_from':'2026-10-14T00:00:00Z','created_to':'2026-10-13T00:00:00'}).status_code == 400
    assert client.get('/api/images/'+image['id']+'/content', headers=auth_headers).content == mock_image_data.getvalue()
    second = client.post('/api/projects', headers=auth_headers, json={'project_name':'second image use','task_type':'detection','label_schema':{'labels':['人']}}).json()
    ids=[]
    for project_id in (str(test_project.id),second['id']):
        response=client.post('/api/images/associate',headers=auth_headers,json={'project_id':project_id,'image_ids':[image['id'],image['id']]})
        assert response.status_code == 200, response.text
        assert response.json()['added_count']==1
        assert client.post('/api/images/associate',headers=auth_headers,json={'project_id':project_id,'image_ids':[image['id']]}).json()['added_count']==0
        asset=client.get('/api/assets',headers=auth_headers,params={'project_id':project_id}).json()['items'][0]
        assert asset['image_id']==image['id'] and asset['blob_path']==image['blob_path']
        assert asset['created_at'].endswith('Z') or asset['created_at'].endswith('+00:00')
        ids.append(asset['id'])
        if project_id==str(test_project.id):
            saved=client.post('/api/annotations',headers=auth_headers,json={'asset_id':asset['id'],'annotation_data':{'boxes':[BOX]},'base_version':0}).json()
            assert client.post('/api/annotations/'+saved['id']+'/review',headers=auth_headers,json={'approved':True}).status_code==200
    assert ids[0]!=ids[1]
    overview=client.get('/api/stats/dashboard',headers=auth_headers).json()
    assert overview['assets_overview']['total']==1 and overview['assets_overview']['project_references']==2
    assert client.get('/api/images',headers=auth_headers).json()['total']==1
    assert client.get('/api/annotations',headers=auth_headers,params={'asset_id':ids[1]}).json()['total']==0
    assert client.delete('/api/images/'+image['id'],headers=auth_headers).status_code==409
    assert client.delete('/api/projects/'+str(test_project.id),headers=auth_headers).status_code==204
    assert client.get('/api/images/'+image['id']+'/content',headers=auth_headers).status_code==200
    assert client.get('/api/assets/'+ids[1],headers=auth_headers).status_code==200

def test_association_invalid_selection_is_atomic_and_scoped(client,auth_headers,mock_image_data,test_project):
    image=upload(client,auth_headers,mock_image_data)
    assert image.status_code==201, image.text
    image_id=image.json()[0]['id']
    for payload in ({'project_id':str(test_project.id),'image_ids':[image_id,str(uuid4())]},
                    {'project_id':str(uuid4()),'image_ids':[image_id]}):
        assert client.post('/api/images/associate',headers=auth_headers,json=payload).status_code==404
    assert client.get('/api/assets',headers=auth_headers,params={'project_id':str(test_project.id)}).json()['total']==0
    assert client.get('/api/images').status_code==401
    assert client.post('/api/images/batch-upload',headers=auth_headers,files={'files':('bad.jpg',b'bad','image/jpeg')}).status_code==400

def test_legacy_project_upload_also_registers_library(client,auth_headers,mock_image_data,test_project):
    result=client.post('/api/assets/upload',headers=auth_headers,params={'project_id':str(test_project.id)},files={'file':('legacy.jpg',mock_image_data.getvalue(),'image/jpeg')})
    assert result.status_code==201
    library=client.get('/api/images',headers=auth_headers)
    assert library.status_code==200,library.text
    assert library.json()['total']==1
    asset=client.get('/api/assets/'+result.json()['asset_id'],headers=auth_headers).json()
    image={'id':asset['image_id']}
    assert image['id']
    assert client.delete('/api/assets/'+asset['id'],headers=auth_headers).status_code==204
    assert client.get('/api/images/'+image['id']+'/content',headers=auth_headers).status_code==200


@pytest.mark.asyncio
async def test_collection_assignment_does_not_queue_visual_inference_for_device_name_scenes(test_db,test_tenant):
    from models import LibraryImage,ImageCollection
    from schemas.image_ingest import ImageIngestConfig
    from services.image_library import assign_collection
    image=LibraryImage(tenant_id=test_tenant.id,blob_path='device.jpg',content_hash='device-scene',
        original_name='device.jpg',file_size=10,source_type='upload',scene_status='unclassified',
        scene_metadata={'basis':'device_name'})
    group=ImageCollection(tenant_id=test_tenant.id,name='device-source',
        ingest_config=ImageIngestConfig(classification_enabled=True).model_dump())
    test_db.add_all([image,group]);await test_db.flush()
    for _ in range(2):await assign_collection(test_db,test_tenant.id,[image.id],group.name)
    assert image.scene_status=='unclassified' and image.scene_metadata['basis']=='device_name'
    assert image.scene_config is None


@pytest.mark.asyncio
async def test_same_bytes_reuse_one_blob_across_names_projects_resumable_and_smarteye(
        client,auth_headers,mock_image_data,test_project,test_engine,monkeypatch):
    import asyncio
    from pathlib import Path
    from sqlalchemy import create_engine
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from config.settings import settings
    from integrations.smarteye import runtime as smarteye
    from services.image_uploads import process_one,staging
    from tests.test_resumable_upload import start
    other=client.post('/api/projects',headers=auth_headers,json={
        'project_name':'same bytes second project','task_type':'detection','label_schema':{'labels':['人']}}).json()
    assets=[]
    for project_id,name in ((str(test_project.id),'first.jpg'),(other['id'],'different-name.jpeg')):
        response=client.post('/api/assets/upload',headers=auth_headers,params={'project_id':project_id},
            files={'file':(name,mock_image_data.getvalue(),'image/jpeg')})
        assert response.status_code==201,response.text
        assets.append(client.get('/api/assets/'+response.json()['asset_id'],headers=auth_headers).json())
        if project_id==str(test_project.id):
            saved=client.post('/api/annotations',headers=auth_headers,json={'asset_id':assets[-1]['id'],'annotation_data':{'boxes':[BOX]},'base_version':0}).json()
            assert client.post('/api/annotations/'+saved['id']+'/review',headers=auth_headers,json={'approved':True}).status_code==200
    assert assets[0]['id']!=assets[1]['id'] and assets[0]['image_id']==assets[1]['image_id']
    assert assets[0]['blob_path']==assets[1]['blob_path']
    job,parts=start(client,auth_headers,mock_image_data.getvalue(),'third-name.jpg','same bytes new collection')
    for index,part in enumerate(parts):assert client.put(f"/api/image-uploads/{job['id']}/parts/{index}",headers=auth_headers,content=part).status_code==200
    assert client.post(f"/api/image-uploads/{job['id']}/complete",headers=auth_headers).status_code==200
    assert await process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    assert not staging(job['id']).exists()
    engine=create_engine(f'sqlite:///{test_engine.url.database}')
    smarteye.metadata.create_all(engine)
    r=smarteye.Runtime(engine,settings.STORAGE_LOCAL_PATH);monkeypatch.setattr(smarteye,'_runtime',r)
    try:
        r.create('same-download','detection',{'status':'running'},{'tenant_id':1,'user_id':1,'min_long_edge':0})
        link=r.root/'smarteye'/'same-download'/'fourth-name.jpg'
        key=await asyncio.to_thread(r.save_image,'same-download',link,mock_image_data.getvalue(),1)
        assert key==assets[0]['blob_path'] and link.samefile(r.root/key)
        assert len(list((Path(settings.STORAGE_LOCAL_PATH)/'images').glob('*/*')))==1
        assert client.get('/api/images',headers=auth_headers).json()['total']==1
    finally:engine.dispose()
