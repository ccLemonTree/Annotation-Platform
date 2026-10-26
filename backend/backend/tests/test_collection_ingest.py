from io import BytesIO
import json
import httpx,pytest
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker

def picture(size):
    data=BytesIO();Image.new('RGB',size,'blue').save(data,format='PNG');return data.getvalue()

def collection(client,headers,name,minimum=720,scenes=None):
    response=client.post('/api/images/collections',headers=headers,json={'name':name})
    assert response.status_code==201,response.text
    group=response.json()
    config=client.get('/api/images/ingest-settings',headers=headers,params={'collection_id':group['id']}).json()
    response=client.put('/api/images/ingest-settings',headers=headers,params={'collection_id':group['id']},
        json={**config,'min_long_edge':minimum,'classification_enabled':bool(scenes),'scenes':scenes or ['工厂'],
            'base_url':'http://'+name+'.test/v1'})
    assert response.status_code==200,response.text
    return group

def upload(client,headers,name,size):
    response=client.post('/api/images/batch-upload',headers=headers,data={'collection_name':name},
        files={'files':('fixture.png',picture(size),'image/png')})
    assert response.status_code==201,response.text
    return response

def test_collection_thresholds_are_independent(client,auth_headers):
    a=collection(client,auth_headers,'low',720)
    b=collection(client,auth_headers,'high',1080)
    accepted=upload(client,auth_headers,'low',(720,30))
    skipped=upload(client,auth_headers,'high',(720,30))
    assert len(accepted.json())==1 and skipped.json()==[] and skipped.headers['x-images-skipped']=='1'
    assert client.get('/api/images',headers=auth_headers,params={'collection_id':b['id']}).json()['total']==0
    defaults=client.get('/api/images/ingest-settings',headers=auth_headers).json()
    client.put('/api/images/ingest-settings',headers=auth_headers,json={**defaults,'min_long_edge':2000})
    assert client.get('/api/images/ingest-settings',headers=auth_headers,params={'collection_id':a['id']}).json()['min_long_edge']==720

@pytest.mark.asyncio
async def test_same_original_keeps_scene_and_one_membership_per_collection(client,auth_headers,test_engine,tmp_path):
    from services.image_scenes import classify_one
    a=collection(client,auth_headers,'first',720,['工厂'])
    b=collection(client,auth_headers,'second',720,['河边'])
    first=upload(client,auth_headers,'first',(720,720)).json()[0]
    second=upload(client,auth_headers,'second',(720,720)).json()[0]
    assert first['id']==second['id']
    assert upload(client,auth_headers,'first',(720,720)).json()[0]['id']==first['id']
    calls=[]
    def provider(request):
        calls.append(str(request.url));label='工厂' if request.url.host=='first.test' else '河边'
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'labels':[label]})}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as model:
        sessions=async_sessionmaker(test_engine,expire_on_commit=False)
        assert await classify_one(sessions,model)
        assert not await classify_one(sessions,model)
    for group,labels in ((a,['工厂']),(b,['工厂'])):
        row=client.get('/api/images',headers=auth_headers,params={'collection_id':group['id']}).json()['items'][0]
        assert row['scene_status']=='completed' and row['scene_labels']==labels
        assert client.get('/api/images/collections',headers=auth_headers).json()[0]['image_count']==1
    assert len(calls)==1 and len(list((tmp_path/'storage/images').rglob('*.png')))==1
    assert client.post('/api/images/scene-classify',headers=auth_headers,json={'image_ids':[first['id']]}).status_code==400
    retry=client.post('/api/images/scene-classify',headers=auth_headers,json={'collection_id':b['id'],'image_ids':[first['id']]})
    assert retry.status_code==200
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as model:
        assert await classify_one(sessions,model)
    for group in (a,b):
        assert client.get('/api/images',headers=auth_headers,params={'collection_id':group['id']}).json()['items'][0]['scene_labels']==['河边']

def test_duplicate_files_in_one_upload_return_one_image(client,auth_headers):
    collection(client,auth_headers,'duplicates',720)
    data=picture((720,720))
    response=client.post('/api/images/batch-upload',headers=auth_headers,data={'collection_name':'duplicates'},
        files=[('files',('first.png',data,'image/png')),('files',('renamed.png',data,'image/png'))])
    assert response.status_code==201,response.text
    assert len(response.json())==1
    assert client.get('/api/images/collections',headers=auth_headers).json()[0]['image_count']==1

@pytest.mark.asyncio
async def test_collection_config_scope_and_membership_checks(client,auth_headers,test_db):
    from models import Tenant,ImageCollection
    from uuid import uuid4
    group=collection(client,auth_headers,'owned',0,['工厂'])
    image=upload(client,auth_headers,'owned',(30,30)).json()[0]
    other=collection(client,auth_headers,'empty',0,['河边'])
    assert client.post('/api/images/scene-classify',headers=auth_headers,json={'collection_id':other['id'],'image_ids':[image['id']]}).status_code==404
    tenant=Tenant(id=2,tenant_name='foreign');test_db.add(tenant);await test_db.flush()
    foreign=ImageCollection(id=uuid4(),tenant_id=2,name='foreign');test_db.add(foreign);await test_db.commit()
    params={'collection_id':str(foreign.id)}
    assert client.get('/api/images/ingest-settings',headers=auth_headers,params=params).status_code==404
    config=client.get('/api/images/ingest-settings',headers=auth_headers,params={'collection_id':group['id']}).json()
    assert client.put('/api/images/ingest-settings',headers=auth_headers,params=params,json=config).status_code==404
