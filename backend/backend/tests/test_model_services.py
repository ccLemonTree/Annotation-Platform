import json
from io import BytesIO
import httpx
import pytest
import pytest_asyncio
from PIL import Image


@pytest_asyncio.fixture(autouse=True)
async def model_table(test_engine):
    from models.model_service import ModelService
    async with test_engine.begin() as connection:
        await connection.run_sync(lambda conn: ModelService.__table__.create(conn, checkfirst=True))


def config(**changes):
    return dict(key='detector', name='Detector', kind='detection', protocol='model_service',
                base_url='http://detector.test', endpoint='/detect/test', **changes)


def test_model_config_crud_and_validation(client, auth_headers):
    response = client.post('/api/model-services', headers=auth_headers, json=config())
    assert response.status_code == 201, response.text
    record = response.json()
    assert record['revision'] == 1
    assert client.get('/api/model-services', headers=auth_headers).json()[0]['id'] == record['id']
    assert client.post('/api/model-services', headers=auth_headers, json=config()).status_code == 409
    for values in [dict(base_url='file:///etc'), dict(endpoint='//other.test/detect'),
                   dict(endpoint='/../admin'), dict(protocol='openai_embedding'), dict(confidence=2),
                   dict(api_key_env='DB_PASSWORD')]:
        payload=config();payload.update(values)
        assert client.post('/api/model-services', headers=auth_headers, json=payload).status_code == 422
    payload=config();payload['enabled']=False
    updated=client.put('/api/model-services/'+record['id'],headers=auth_headers,json=payload)
    assert updated.status_code == 200, updated.text
    assert updated.json()['revision'] == 2
    assert client.get('/api/model-services',params={'enabled':True},headers=auth_headers).json()==[]
    assert client.delete('/api/model-services/'+record['id'],headers=auth_headers).status_code==204
    assert client.get('/api/model-services').status_code==401


@pytest.mark.asyncio
async def test_detection_adapter_uses_multipart_and_normalizes_boxes():
    from schemas.model_service import ModelServiceConfig
    from services.model_inference import infer
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.headers['content-type'].startswith('multipart/form-data; boundary=')
        assert b'name="image"' in request.content and b'name="conf"' in request.content
        return httpx.Response(200,json={'detections':[{'class':'raw_fire','bbox':[-1,2,45,50],'score':.9}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model=ModelServiceConfig(**config(label_mapping={'raw_fire':'fire'}))
        result=await infer(model,b'image',40,30,client=client)
    assert result['boxes']==[dict(label='fire',x=0,y=2,width=40,height=28,score=.9)]
    assert result['warnings'] and len(calls)==1


@pytest.mark.asyncio
async def test_grounding_prompt_and_uncalibrated_confidence_warning():
    from schemas.model_service import ModelServiceConfig
    from services.model_inference import infer
    def handler(request):
        assert b'name="prompt"' in request.content and b'electric bicycle' in request.content
        assert b'name="conf"' not in request.content and b'name="nms"' not in request.content
        return httpx.Response(200, json={'detections':[
            {'class':'electric bicycle','bbox':[1,2,10,20],'score':.5}], 'score_type':'uncalibrated'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        values=config(confidence=.99);values['endpoint']='/detect/grounding'
        result=await infer(ModelServiceConfig(**values), b'image', 30, 30,
                           prompt='electric bicycle', client=client)
    assert len(result['boxes'])==1 and 'score' not in result['boxes'][0]
    assert result['score_type']=='uncalibrated'
    assert any('未提供' in message for message in result['warnings'])


@pytest.mark.parametrize('endpoint,score_type', [('/detect/test','uncalibrated'),('/detect/grounding',None)])
def test_unscored_results_do_not_require_or_filter_a_placeholder(endpoint,score_type):
    from schemas.model_service import ModelServiceConfig
    from services.model_inference import detection_result
    values=config(confidence=.99);values['endpoint']=endpoint
    rows=[{'class':'car','bbox':[1,2,10,20]}, {'class':'car','bbox':[2,3,11,21],'score':None}]
    result=detection_result({'detections':rows,'score_type':score_type},ModelServiceConfig(**values),30,30)
    assert len(result['boxes'])==2 and all('score' not in box for box in result['boxes'])


@pytest.mark.asyncio
async def test_embedding_and_chat_use_multimodal_messages_and_validate_results():
    from schemas.model_service import ModelServiceConfig
    from services.model_inference import infer
    def handler(request):
        body=json.loads(request.content)
        assert body['messages'][0]['content'][1]['image_url']['url'].startswith('data:image/jpeg;base64,')
        return httpx.Response(200,json={'data':[{'embedding':[.1,.2,.3]}]})
    image=BytesIO();Image.new('RGB',(10,10)).save(image,'PNG')
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model=ModelServiceConfig(key='embed',name='Embedding',kind='embedding',protocol='openai_embedding',
            base_url='http://embed.test/v1',endpoint='/embeddings',model_name='/models/wemm_embedding_2b')
        result=await infer(model,image.getvalue(),10,10,client=client)
    assert result['embedding_dimensions']==3 and result['embedding_preview']==[.1,.2,.3]


@pytest.mark.asyncio
async def test_predict_is_readonly_scoped_and_rejects_stale_config(client,auth_headers,test_db,test_engine,test_asset,mock_image_data,monkeypatch):
    from sqlalchemy import select,func,update
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from models import Annotation, ModelService, Tenant
    from services.storage import get_storage
    from api import model_service_routes
    await get_storage().save_file(mock_image_data,test_asset.blob_path)
    record=client.post('/api/model-services',headers=auth_headers,json=config()).json()
    calls=[]
    async def fake_infer(*args):
        calls.append(args)
        return {'kind':'detection','boxes':[dict(label='fire',x=1,y=2,width=3,height=4,score=.8)],'warnings':[]}
    monkeypatch.setattr(model_service_routes,'infer',fake_infer)
    url='/api/model-services/'+record['id']+'/predict'
    result=client.post(url,headers=auth_headers,json={'asset_id':str(test_asset.id)})
    assert result.status_code==200,result.text
    assert result.json()['config_revision']==1 and len(calls)==1
    assert await test_db.scalar(select(func.count(Annotation.id)))==0
    assert client.post(url,headers=auth_headers,json={}).status_code==422
    assert client.post(url,headers=auth_headers,json={'asset_id':str(test_asset.id),'image_id':str(test_asset.id)}).status_code==422
    test_db.add(Tenant(id=99,tenant_name='foreign'));await test_db.flush()
    foreign=ModelService(tenant_id=99,**{**config(),'key':'foreign'})
    test_db.add(foreign);await test_db.commit()
    assert client.post('/api/model-services/'+str(foreign.id)+'/predict',headers=auth_headers,json={'asset_id':str(test_asset.id)}).status_code==404
    async def stale_infer(*args):
        async with async_sessionmaker(test_engine)() as db:
            await db.execute(update(ModelService).where(ModelService.key=='detector',ModelService.tenant_id==1).values(revision=2))
            await db.commit()
        return await fake_infer(*args)
    monkeypatch.setattr(model_service_routes,'infer',stale_infer)
    assert client.post(url,headers=auth_headers,json={'asset_id':str(test_asset.id)}).status_code==409
    payload=config();payload['enabled']=False
    client.put('/api/model-services/'+record['id'],headers=auth_headers,json=payload)
    assert client.post(url,headers=auth_headers,json={'asset_id':str(test_asset.id)}).status_code==400


@pytest.mark.asyncio
async def test_normal_user_cannot_modify_model_config(client,auth_headers,test_db,test_user):
    from services.auth import create_access_token
    test_user.role='annotator';await test_db.commit()
    token=create_access_token({'user_id':test_user.id,'tenant_id':test_user.tenant_id,'username':test_user.username})
    headers={'Authorization':'Bearer '+token}
    assert client.get('/api/model-services',headers=headers).status_code==200
    assert client.post('/api/model-services',headers=headers,json=config()).status_code==403
    assert client.post('/api/model-services/defaults',headers=headers).status_code==403


@pytest.mark.asyncio
async def test_invalid_model_responses_and_timeout_do_not_leak_upstream_details():
    from fastapi import HTTPException
    from schemas.model_service import ModelServiceConfig
    from services.model_inference import infer
    model=ModelServiceConfig(**config())
    for data in [{'detections':[{'class':'fire','bbox':[1,1,0,2],'score':.8}]},
                 {'detections':[{'class':'fire','bbox':[1,1,2,2],'score':'secret'}]}, {'unexpected':'secret'}]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=data))) as client:
            with pytest.raises(HTTPException) as error:await infer(model,b'image',10,10,client=client)
            assert error.value.status_code==502 and 'secret' not in error.value.detail
    def timeout(request):raise httpx.ReadTimeout('secret',request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(HTTPException) as error:await infer(model,b'image',10,10,client=client)
        assert error.value.status_code==504 and 'secret' not in error.value.detail


def test_known_defaults_are_idempotent_and_do_not_overwrite_edits(client,auth_headers):
    response=client.post('/api/model-services/defaults',headers=auth_headers)
    assert response.status_code==200,response.text
    records=response.json();assert len(records)==9
    assert next(row for row in records if row['key']=='rfdetr_fire_sahi')['enabled'] is False
    qwen=next(row for row in records if row['key']=='qwen')
    payload={key:value for key,value in qwen.items() if key not in ('id','revision')};payload['name']='My Qwen'
    assert client.put('/api/model-services/'+qwen['id'],headers=auth_headers,json=payload).status_code==200
    again=client.post('/api/model-services/defaults',headers=auth_headers).json()
    assert len(again)==9 and next(row for row in again if row['key']=='qwen')['name']=='My Qwen'


def test_unregistered_training_contract_accepts_project_uuid():
    from uuid import uuid4
    from api.training_routes import CreateTrainingRequest
    project_id=uuid4()
    request=CreateTrainingRequest(project_id=str(project_id),task_name='test',model_type='yolo',config_yaml='{}',dataset_version='v1')
    assert request.project_id==project_id
