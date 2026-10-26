from uuid import UUID,uuid4
import pytest
from sqlalchemy import select,func
from models import ImageCollection,ImageUpload,ImageUploadItem,LibraryImage
from tests.test_image_collections import upload
from tests.test_image_library import foreign_image


def test_delete_collection_keeps_images_other_groups_and_project_labels(client,auth_headers,mock_image_data,test_project):
    from tests.test_p2_annotations import BOX
    image=upload(client,auth_headers,mock_image_data,'remove-group').json()[0]
    upload(client,auth_headers,mock_image_data,'keep-group')
    groups={row['name']:row for row in client.get('/api/images/collections',headers=auth_headers).json()}
    asset=client.post('/api/images/associate',headers=auth_headers,json={'project_id':str(test_project.id),'image_ids':[image['id']]}).json()['asset_ids'][0]
    annotation=client.post('/api/annotations',headers=auth_headers,json={'asset_id':asset,'base_version':0,'annotation_data':{'boxes':[BOX]}}).json()
    response=client.delete('/api/images/collections/'+groups['remove-group']['id'],headers=auth_headers)
    assert response.status_code==200,response.text
    assert response.json()=={'total_images':1,'deleted_images':0,'kept_images':1}
    assert [group['name'] for group in client.get('/api/images/collections',headers=auth_headers).json()]==['keep-group']
    rows=client.get('/api/images',headers=auth_headers).json()
    assert rows['total']==1 and rows['items'][0]['collection_names']==['keep-group']
    assert client.get('/api/images/'+image['id']+'/content',headers=auth_headers).content==mock_image_data.getvalue()
    assert client.get('/api/annotations/'+annotation['id'],headers=auth_headers).json()['annotation_data']==annotation['annotation_data']
    assert client.get('/api/assets/'+asset,headers=auth_headers).status_code==200


@pytest.mark.asyncio
async def test_delete_empty_collection_scope_auth_and_missing(client,auth_headers,test_db):
    from models import Tenant
    foreign=Tenant(id=2,tenant_name='foreign');test_db.add(foreign);await test_db.flush()
    group=ImageCollection(tenant_id=2,name='foreign-group');test_db.add(group);await test_db.commit()
    endpoint='/api/images/collections/'
    assert client.delete(endpoint+str(group.id)).status_code==401
    assert client.delete(endpoint+str(group.id),headers=auth_headers).status_code==404
    assert client.delete(endpoint+str(uuid4()),headers=auth_headers).status_code==404
    assert client.delete(endpoint+'invalid',headers=auth_headers).status_code==422
    own=client.post('/api/images/collections',headers=auth_headers,json={'name':'empty'}).json()
    assert client.delete(endpoint+own['id'],headers=auth_headers).status_code==200
    assert client.delete(endpoint+own['id'],headers=auth_headers).status_code==404
    assert await test_db.get(ImageCollection,group.id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize('status',['uploading','pending','processing','failed','completed'])
async def test_upload_records_block_until_completed(client,auth_headers,mock_image_data,test_db,test_user,status):
    group=client.post('/api/images/collections',headers=auth_headers,json={'name':'upload-group'}).json()
    image=upload(client,auth_headers,mock_image_data,group['name']).json()[0]
    job=ImageUpload(tenant_id=test_user.tenant_id,user_id=test_user.id,collection_id=UUID(group['id']),filename='sample.zip',size=1,
        chunk_hashes=['a'*64],resume_key=uuid4().hex,min_long_edge=0,status=status)
    test_db.add(job);await test_db.flush();job_id=job.id
    test_db.add(ImageUploadItem(upload_id=job.id,image_id=UUID(image['id'])));await test_db.commit()
    response=client.delete('/api/images/collections/'+group['id'],headers=auth_headers)
    assert response.status_code==(200 if status=='completed' else 409),response.text
    test_db.expire_all()
    assert (await test_db.get(ImageCollection,UUID(group['id'])) is None)==(status=='completed')
    assert (await test_db.get(ImageUpload,job_id) is None)==(status=='completed')
    assert (await test_db.get(ImageUploadItem,(job_id,UUID(image['id']))) is None)==(status=='completed')
    assert client.get('/api/images/'+image['id']+'/content',headers=auth_headers).status_code==(404 if status=='completed' else 200)


@pytest.mark.asyncio
@pytest.mark.parametrize('status',['pending','processing','completed','failed'])
async def test_classification_queue_blocks_only_its_source_collection(client,auth_headers,mock_image_data,test_db,status):
    image=upload(client,auth_headers,mock_image_data,'classifier-group').json()[0]
    upload(client,auth_headers,mock_image_data,'other-group')
    groups={row['name']:row for row in client.get('/api/images/collections',headers=auth_headers).json()}
    row=await test_db.get(LibraryImage,UUID(image['id']))
    row.scene_status=status;row.scene_config={'collection_id':groups['classifier-group']['id'],'revision':1,'config':{}}
    await test_db.commit()
    response=client.delete('/api/images/collections/'+groups['classifier-group']['id'],headers=auth_headers)
    assert response.status_code==(409 if status in ('pending','processing') else 200),response.text
    assert client.delete('/api/images/collections/'+groups['other-group']['id'],headers=auth_headers).status_code==200
    assert await test_db.scalar(select(func.count()).select_from(LibraryImage))==(1 if status in ('pending','processing') else 0)


def test_preview_and_delete_only_exclusive_images(client,auth_headers,mock_image_data,test_project):
    from PIL import Image
    from io import BytesIO
    groups={name:client.post('/api/images/collections',headers=auth_headers,json={'name':name}).json() for name in ['folder','other']}
    images=[]
    for color in ['red','blue','green']:
        content=BytesIO();Image.new('RGB',(64,48),color=color).save(content,format='JPEG');content.seek(0)
        images.append(upload(client,auth_headers,content,'folder').json()[0])
    client.post('/api/images/collections/assign',headers=auth_headers,json={'name':'other','image_ids':[images[1]['id']]})
    asset=client.post('/api/images/associate',headers=auth_headers,json={'project_id':str(test_project.id),'image_ids':[images[2]['id']]}).json()['asset_ids'][0]
    endpoint='/api/images/collections/'+groups['folder']['id']
    preview=client.get(endpoint+'/delete-preview',headers=auth_headers)
    assert preview.status_code==200,preview.text
    assert preview.json()=={'total_images':3,'deleted_images':1,'kept_images':2}
    response=client.delete(endpoint,headers=auth_headers)
    assert response.status_code==200,response.text
    assert response.json()==preview.json()
    rows=client.get('/api/images',headers=auth_headers).json()
    assert {row['id'] for row in rows['items']}=={images[1]['id'],images[2]['id']}
    assert client.get('/api/assets/'+asset,headers=auth_headers).status_code==200
    assert client.get('/api/images/'+images[0]['id']+'/content',headers=auth_headers).status_code==404
    assert client.get('/api/images/collections',headers=auth_headers).json()[0]['name']=='other'
    assert client.get(endpoint+'/delete-preview',headers=auth_headers).status_code==404


@pytest.mark.asyncio
async def test_bad_cross_tenant_membership_never_deletes_foreign_image(client,auth_headers,test_db,foreign_image):
    from models import ImageCollectionMember
    group=client.post('/api/images/collections',headers=auth_headers,json={'name':'bad-link'}).json()
    test_db.add(ImageCollectionMember(collection_id=UUID(group['id']),image_id=foreign_image.id));await test_db.commit()
    endpoint='/api/images/collections/'+group['id']
    assert client.get(endpoint+'/delete-preview',headers=auth_headers).json()['total_images']==0
    assert client.delete(endpoint,headers=auth_headers).json()['deleted_images']==0
    assert await test_db.get(LibraryImage,foreign_image.id) is not None


def test_project_attachment_after_preview_is_preserved(client,auth_headers,mock_image_data,test_project):
    image=upload(client,auth_headers,mock_image_data,'late-reference').json()[0]
    group=client.get('/api/images/collections',headers=auth_headers).json()[0]
    endpoint='/api/images/collections/'+group['id']
    assert client.get(endpoint+'/delete-preview',headers=auth_headers).json()['deleted_images']==1
    attached=client.post('/api/images/associate',headers=auth_headers,json={'project_id':str(test_project.id),'image_ids':[image['id']]})
    assert attached.status_code==200
    result=client.delete(endpoint,headers=auth_headers)
    assert result.status_code==200 and result.json()['deleted_images']==0 and result.json()['kept_images']==1
    assert client.get('/api/images/'+image['id']+'/content',headers=auth_headers).status_code==200
