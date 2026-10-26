from uuid import uuid4
import pytest
from models import LibraryImage,ImageCollection,ImageCollectionMember,Asset,Tenant,Project,TaskType

@pytest.mark.asyncio
async def test_selection_covers_all_pages_and_uses_same_filters(client,auth_headers,test_db,test_user,test_project):
    group=ImageCollection(tenant_id=test_user.tenant_id,name='selection');test_db.add(group);await test_db.flush()
    ids=[]
    for i in range(25):
        image=LibraryImage(tenant_id=test_user.tenant_id,blob_path=f'{i}.jpg',original_name=f'pick-{i}.jpg',content_hash=f'pick-{i}',
            file_size=1,source_type='upload',scene_status='completed',scene_labels=['工厂'] if i%2==0 else ['电梯'])
        test_db.add(image);await test_db.flush();ids.append(image.id)
        test_db.add(ImageCollectionMember(collection_id=group.id,image_id=image.id))
    test_db.add(Asset(project_id=test_project.id,image_id=ids[0],blob_path='0.jpg',original_name='pick-0.jpg',content_hash='pick-0',file_size=1,source_type='upload'))
    # A legacy NULL image reference must not make NOT IN exclude every image.
    test_db.add(Asset(project_id=test_project.id,image_id=None,blob_path='legacy.jpg',original_name='legacy.jpg',content_hash='legacy',file_size=1,source_type='upload'))
    test_db.add(Tenant(id=99,tenant_name='foreign'));await test_db.flush()
    foreign_project=Project(tenant_id=99,project_name='foreign',task_type=TaskType.DETECTION)
    foreign_group=ImageCollection(tenant_id=99,name='foreign')
    test_db.add_all([foreign_project,foreign_group,LibraryImage(tenant_id=99,blob_path='foreign.jpg',original_name='pick-foreign.jpg',
        content_hash='foreign',file_size=1,source_type='upload')]);await test_db.flush()
    foreign_project_id=str(foreign_project.id);foreign_group_id=str(foreign_group.id)
    project_id=str(test_project.id);group_id=str(group.id);await test_db.commit()
    response=client.get('/api/images/selection',headers=auth_headers)
    assert response.status_code==200,response.text
    assert set(response.json()['image_ids'])==set(map(str,ids)) and response.json()['total']==25
    params={'collection_id':group_id,'scene':'工厂','scene_status':'completed','source_type':'upload','q':'pick-'}
    listing=client.get('/api/images',headers=auth_headers,params=params).json()
    selection=client.get('/api/images/selection',headers=auth_headers,params=params).json()
    assert selection['total']==listing['total']==13
    assert set(selection['image_ids'])=={item['id'] for item in listing['items']}
    available=client.get('/api/images/selection',headers=auth_headers,params={**params,'exclude_project_id':project_id}).json()
    assert available['total']==12 and str(ids[0]) not in available['image_ids']
    assert client.get('/api/images/selection').status_code==401
    assert client.get('/api/images/selection',headers=auth_headers,params={'exclude_project_id':str(uuid4())}).status_code==404
    assert client.get('/api/images/selection',headers=auth_headers,params={'exclude_project_id':foreign_project_id}).status_code==404
    assert client.get('/api/images/selection',headers=auth_headers,params={'collection_id':foreign_group_id}).status_code==404
    assert client.get('/api/images/selection',headers=auth_headers,params={'batch_id':'not-present'}).json()['total']==0
    assert client.get('/api/images/selection',headers=auth_headers,params={'created_to':'2000-01-01T00:00:00Z'}).json()['total']==0
    assert client.get('/api/images/selection',headers=auth_headers,params={'scene_status':'wrong'}).status_code==422
    assert client.get('/api/images/selection',headers=auth_headers,params={'created_from':'2026-10-16','created_to':'2026-10-15'}).status_code==400
