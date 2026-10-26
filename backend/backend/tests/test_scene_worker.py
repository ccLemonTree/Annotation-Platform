import asyncio,json
import httpx,pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from services.image_scenes import classify_one

def queued(client,headers,image):
    config=client.get('/api/images/ingest-settings',headers=headers).json()
    assert client.put('/api/images/ingest-settings',headers=headers,json={**config,'classification_enabled':True,
        'base_url':'http://model.test/v1','scenes':['工厂','高位摄像头']}).status_code==200
    result=client.post('/api/images/batch-upload',headers=headers,files={'files':('fixture.jpg',image.getvalue(),'image/jpeg')})
    assert result.status_code==201,result.text
    image=result.json()[0]
    image['collection_id']=client.get('/api/images/collections',headers=headers).json()[0]['id']
    return image

@pytest.mark.asyncio
async def test_scene_worker_multilabel_and_filter(client,auth_headers,mock_image_data,test_engine):
    image=queued(client,auth_headers,mock_image_data)
    async def provider(request):
        body=json.loads(request.content)
        assert request.url.path=='/v1/chat/completions' and body['model']=='Qwen3.8-27B'
        assert body['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,')
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"labels":["工厂","高位摄像头"]}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as model:
        assert await classify_one(async_sessionmaker(test_engine,expire_on_commit=False),model)
    for name in ('工厂','高位摄像头'):
        rows=client.get('/api/images',headers=auth_headers,params={'scene':name}).json()
        assert rows['total']==1 and rows['items'][0]['scene_status']=='completed'
    assert client.get('/api/images',headers=auth_headers,params={'scene':'厂'}).json()['total']==0
    assert client.get('/api/images/'+image['id']+'/content',headers=auth_headers).content==mock_image_data.getvalue()


@pytest.mark.asyncio
async def test_collection_prompt_reaches_scene_request_and_unknown_labels_still_fail(client,auth_headers,mock_image_data,test_engine):
    image=queued(client,auth_headers,mock_image_data)
    params={'collection_id':image['collection_id']}
    config=client.get('/api/images/ingest-settings',headers=auth_headers,params=params).json()
    assert client.put('/api/images/ingest-settings',headers=auth_headers,params=params,
        json={**config,'scene_prompt':'仅根据摄像头视角分类。'}).status_code==200
    assert client.post('/api/images/scene-classify',headers=auth_headers,json={
        'image_ids':[image['id']],'collection_id':image['collection_id']}).status_code==200
    seen=[]
    def provider(request):
        text=json.loads(request.content)['messages'][0]['content'][0]['text'];seen.append(text)
        assert '仅根据摄像头视角分类。' in text and '工厂' in text and '"labels"' in text
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"labels":["名单外标签"]}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as model:
        assert await classify_one(async_sessionmaker(test_engine,expire_on_commit=False),model)
    row=client.get('/api/images',headers=auth_headers).json()['items'][0]
    assert len(seen)==1 and row['scene_status']=='failed' and row['scene_labels']==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('payload,status',[('{"labels":["未知场景"]}',200),('not json',200),('{}',503),(None,200)])
async def test_failed_classification_retains_image_and_can_retry(client,auth_headers,mock_image_data,test_engine,payload,status):
    image=queued(client,auth_headers,mock_image_data)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(status,json={'choices':[]} if payload is None else {'choices':[{'message':{'content':payload}}]}))) as model:
        await classify_one(async_sessionmaker(test_engine,expire_on_commit=False),model)
    row=client.get('/api/images',headers=auth_headers).json()['items'][0]
    assert row['scene_status']=='failed' and row['scene_error']
    assert client.get('/api/images/'+image['id']+'/content',headers=auth_headers).status_code==200
    retry=client.post('/api/images/scene-classify',headers=auth_headers,json={'image_ids':[image['id']],'collection_id':image['collection_id']})
    assert retry.status_code==200 and retry.json()['queued_count']==1

@pytest.mark.asyncio
@pytest.mark.parametrize('change',['retry','settings'])
async def test_late_response_cannot_overwrite_new_classification_attempt(client,auth_headers,mock_image_data,test_engine,change):
    image=queued(client,auth_headers,mock_image_data)
    started=asyncio.Event();release=asyncio.Event()
    async def slow(request):
        started.set();await release.wait()
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"labels":["工厂"]}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as model:
        task=asyncio.create_task(classify_one(async_sessionmaker(test_engine,expire_on_commit=False),model))
        await asyncio.wait_for(started.wait(),5)
        if change=='retry':
            assert client.post('/api/images/scene-classify',headers=auth_headers,json={'image_ids':[image['id']],'collection_id':image['collection_id']}).status_code==200
        else:
            params={'collection_id':image['collection_id']}
            config=client.get('/api/images/ingest-settings',headers=auth_headers,params=params).json()
            assert client.put('/api/images/ingest-settings',headers=auth_headers,params=params,json={**config,'scenes':['河边']}).status_code==200
        release.set();await task
    row=client.get('/api/images',headers=auth_headers).json()['items'][0]
    assert row['scene_status']==('pending' if change=='retry' else 'failed') and row['scene_labels']==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('limit',[1,4])
async def test_worker_bounded_parallelism_and_cancellation(monkeypatch,limit):
    from services import image_scenes as module
    from types import SimpleNamespace
    monkeypatch.setattr(module,'settings',SimpleNamespace(SCENE_CLASSIFICATION_CONCURRENCY=limit))
    reached=asyncio.Event();blocked=asyncio.Event();active=0;clients=set()
    async def classify(sessions,client):
        nonlocal active
        active+=1;clients.add(id(client))
        if active==limit:reached.set()
        try:await blocked.wait()
        finally:active-=1
        return True
    monkeypatch.setattr(module,'classify_one',classify)
    task=asyncio.create_task(module.run_worker(None))
    try:
        await asyncio.wait_for(reached.wait(),2)
        assert active==limit and len(clients)==1
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    assert active==0
