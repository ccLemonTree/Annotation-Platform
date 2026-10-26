"""Multimodal validation never writes business captions or vector indexes."""
import hashlib,json
from types import SimpleNamespace
from uuid import UUID
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.test_validation import validation_tables,vclient,ready,create_set
from tests.test_model_preview import pclient,detector,upload


def test_multimodal_checkpoint_accepts_directories_and_lora():
    from schemas.validation import CheckpointInput
    request=CheckpointInput(kind='caption',weight_path='/models/merged',device='cpu')
    assert request.image and request.kind=='caption'
    assert CheckpointInput(kind='embedding',weight_path='/models/lora',base_model_path='/models/base').base_model_path=='/models/base'
    with pytest.raises(ValueError):CheckpointInput(kind='caption',weight_path='/models/../etc')


def test_retrieval_excludes_self_and_keeps_multiple_positives():
    from services.multimodal_validation import retrieval
    rows=[dict(asset_id='self',content_hash='same',vector=[1,0]),dict(asset_id='copy',content_hash='same',vector=[1,0]),
          dict(asset_id='yes',content_hash='other',vector=[.9,.1]),dict(asset_id='unknown',content_hash='unknown',vector=[.8,.2])]
    query=dict(id='q',kind='image',content_hash='same',positives=['self','yes'],negatives=[])
    result=retrieval(rows,[dict(**query,vector=[1,0])])
    q=result['queries'][0]
    assert [h['asset_id'] for h in q['hits']]==['yes','unknown']
    assert q['hits'][1]['judgment']=='unknown'
    assert result['image']['hit_at_1']==result['image']['recall_at_1']==1
    assert result['image']['mrr']==1


def test_retrieval_does_not_score_missing_positive_or_failed_candidates():
    from services.multimodal_validation import retrieval
    result=retrieval([dict(asset_id='x',content_hash='x',vector=[1,0])],[
        dict(id='none',kind='text',vector=[1,0],positives=[],negatives=[]),
        dict(id='missing',kind='text',vector=[1,0],positives=['not-completed'],negatives=[])])
    assert result['text']['evaluated_queries']==0 and result['text']['hit_at_1'] is None
    assert len(result['queries'])==2 and all(q['excluded_reason'] for q in result['queries'])


def test_mixed_dimensions_leave_explicit_unscored_query():
    from services.multimodal_validation import retrieval
    result=retrieval([dict(asset_id='a',content_hash='a',vector=[1,0]),dict(asset_id='b',content_hash='b',vector=[1,0,0])],
        [dict(id='q',kind='text',vector=[1,0],positives=['a'],negatives=[])])
    assert result['text']['evaluated_queries']==0 and '维度' in result['queries'][0]['excluded_reason']


@pytest.mark.asyncio
async def test_running_embedding_progress_does_not_load_full_vectors():
    from services.multimodal_validation import metrics
    run=SimpleNamespace(model_snapshot={'task_type':'embedding'},status='running',metrics={'coverage':{'total':5000,'succeeded':20}})
    result=await metrics(object(),run)
    assert result['coverage']['succeeded']==20 and result['queries']==[]


@pytest.mark.parametrize('value',[[0,0],[float('nan'),1],[True,1],[],[float('inf')]])
def test_invalid_vectors_are_rejected(value):
    from services.multimodal_validation import vector
    with pytest.raises(ValueError):vector(value)


@pytest.mark.asyncio
async def test_caption_set_worker_review_and_report(vclient,auth_headers,ready,test_db,test_engine,monkeypatch):
    from models import Project
    from models.project import TaskType
    from services import validation,multimodal_validation as mm
    ready['project'].task_type=TaskType.CAPTION
    ready['annotation'].annotation_data={'description':'穿黑色上衣的人'}
    ready['model'].kind='caption';ready['model'].endpoint='/caption'
    await test_db.commit()
    dataset=create_set(vclient,auth_headers,ready)
    assert dataset['source']['task_type']=='caption'
    created=vclient.post('/api/validation/runs',headers=auth_headers,json={'set_id':dataset['id'],'model_id':str(ready['model'].id),'config':{'prompt':'描述可见内容'}})
    assert created.status_code==201,created.text
    run=created.json()
    async def infer(*args):return {'caption':'一名身穿黑衣的行人'}
    monkeypatch.setattr(mm,'service_infer',infer)
    assert await validation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    report=vclient.get('/api/validation/runs/'+run['id']+'/report',headers=auth_headers).json()
    item=report['items'][0]
    assert item['evaluation']['caption']=='一名身穿黑衣的行人'
    assert item['truth']==[{'description':'穿黑色上衣的人'}]
    assert report['run']['metrics']['task_type']=='caption' and report['run']['metrics']['reviewed']==0
    review=vclient.post('/api/validation/runs/'+run['id']+'/items/'+item['id']+'/review',headers=auth_headers,json={'verdict':'correct','comment':'语义一致'})
    assert review.status_code==200,review.text
    report=vclient.get('/api/validation/runs/'+run['id']+'/report',headers=auth_headers).json()
    assert report['run']['metrics']['pass_rate']==1 and report['run']['metrics']['reviewed']==1
    await test_db.refresh(ready['annotation'])
    assert ready['annotation'].annotation_data=={'description':'穿黑色上衣的人'}


def test_directory_identity_detects_changed_shard(tmp_path):
    from services.checkpoint_executor import weight_identity
    root=tmp_path/'model';root.mkdir();(root/'config.json').write_text('{}');shard=root/'model.safetensors';shard.write_bytes(b'a')
    config={'kind':'caption','weight_path':str(root)}
    first=weight_identity(config);shard.write_bytes(b'b')
    assert first!=weight_identity(config)


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['caption','embedding'])
async def test_multimodal_preview_persists_results_without_vectors_in_api(kind,pclient,detector,auth_headers,mock_image_data,test_engine,test_db,monkeypatch):
    from services import validation,multimodal_validation as mm
    detector.kind=kind
    if kind=='embedding':detector.protocol='openai_embedding';detector.endpoint='/embeddings';detector.model_name='fixture'
    await test_db.commit()
    request=dict(model_id=str(detector.id),task_type=kind,prompt='描述图片')
    if kind=='embedding':request['query_text']='红色物体'
    response=upload(pclient,auth_headers,mock_image_data.getvalue(),request,count=2)
    assert response.status_code==201,response.text
    run=response.json();root='/api/model-previews/'+run['id']
    async def infer(config,content,sample,policy):
        return {'caption':'红色背景'} if kind=='caption' else {'vector':[1,0], 'queries':{id:[1,0] for id in sample.get('query_texts',{})}}
    monkeypatch.setattr(mm,'service_infer',infer)
    sessions=async_sessionmaker(test_engine,expire_on_commit=False)
    for _ in range(2):assert await validation.process_one(sessions)
    detail=pclient.get(root,headers=auth_headers).json()
    assert detail['status']=='completed' and detail['model']['task_type']==kind
    assert pclient.get('/api/model-previews',headers=auth_headers).json()['total']==0
    assert pclient.get('/api/model-previews?task_type='+kind,headers=auth_headers).json()['total']==1
    if kind=='embedding':
        assert len(detail['metrics']['queries'][0]['hits'])==2
        assert all(i['evaluation']['dimensions']==2 and 'vector' not in i['evaluation'] and 'queries' not in i['evaluation'] for i in detail['items'])
    else:assert detail['items'][0]['evaluation']['caption']=='红色背景'
    assert pclient.delete(root,headers=auth_headers).status_code==204
    assert pclient.get(root,headers=auth_headers).status_code==404


@pytest.mark.asyncio
async def test_vector_freezes_both_query_modes_and_ignores_unreviewed_hits(vclient,auth_headers,ready,test_db,test_engine,monkeypatch):
    from uuid import uuid4
    from models import Asset
    from models.project import TaskType
    from models.project_work import MatchQuery
    from models.validation import ValidationSet
    from services import validation,multimodal_validation as mm
    from services.storage import get_storage
    from io import BytesIO
    from PIL import Image
    async with test_engine.begin() as connection:await connection.run_sync(lambda c:MatchQuery.__table__.create(c,checkfirst=True))
    ready['project'].task_type=TaskType.VECTOR
    other=Asset(project_id=ready['project'].id,source_type='upload',original_name='other.png',blob_path='fixture/other.png',content_hash='',width=64,height=48,file_size=10,format='PNG')
    content=BytesIO();Image.new('RGB',(64,48),'black').save(content,'PNG');other.content_hash=hashlib.sha256(content.getvalue()).hexdigest()
    await get_storage().save_file(BytesIO(content.getvalue()),other.blob_path)
    test_db.add(other);await test_db.flush()
    hit=dict(asset_id=str(other.id),content_hash=other.content_hash,score=.1,judgment='correct',review_status='approved',reviewer_id=1)
    queries=[MatchQuery(project_id=ready['project'].id,query={'text':'黑色'},space={},hits=[hit]),
        MatchQuery(project_id=ready['project'].id,query={'image_id':str(uuid4()),'content_hash':ready['asset'].content_hash},space={},hits=[hit]),
        MatchQuery(project_id=ready['project'].id,query={'text':'不可用'},space={},hits=[{**hit,'review_status':'pending'}])]
    test_db.add_all(queries);ready['model'].kind='embedding';ready['model'].protocol='openai_embedding';ready['model'].model_name='fixture';ready['model'].endpoint='/embeddings';await test_db.commit()
    dataset=create_set(vclient,auth_headers,ready)
    assert dataset['source']['query_count']==2 and 'queries' not in dataset['source']
    assert vclient.get('/api/validation/sets',headers=auth_headers).json()['total']==0
    assert vclient.get('/api/validation/sets?task_type=embedding',headers=auth_headers).json()['total']==1
    saved=await test_db.get(ValidationSet,UUID(dataset['id']))
    assert {q['kind'] for q in saved.source['queries']}=={'text','image'}
    model_id=str(ready['model'].id);await test_db.rollback()
    run=vclient.post('/api/validation/runs',headers=auth_headers,json={'set_id':dataset['id'],'model_id':model_id}).json()
    async def infer(config,content,sample,policy):return {'vector':[1,0],'queries':{id:[1,0] for id in sample.get('query_texts',{})}}
    monkeypatch.setattr(mm,'service_infer',infer)
    for _ in range(2):assert await validation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    report=vclient.get('/api/validation/runs/'+run['id']+'/report',headers=auth_headers).json()
    assert report['run']['status']=='completed'
    metrics=report['run']['metrics']
    assert metrics['text']['evaluated_queries']==metrics['image']['evaluated_queries']==1
    assert metrics['image']['hit_at_1']==1
    assert not any('vector' in item['evaluation'] for item in report['items'])


@pytest.mark.parametrize('kind',['caption','embedding'])
def test_offline_runner_loads_once_and_isolates_bad_images(kind,tmp_path):
    from services.multimodal_predict import predict_batch
    from PIL import Image
    root=tmp_path;(root/'input').mkdir();Image.new('RGB',(2,2),'red').save(root/'input/a.png')
    content=(root/'input/a.png').read_bytes();(root/'input/bad.png').write_bytes(b'bad')
    config={'kind':kind,'policy':{},'device':'cpu'};(root/'config.json').write_text(json.dumps(config))
    samples=[dict(asset_id='a',file='a.png',sha256=hashlib.sha256(content).hexdigest(),width=2,height=2),dict(asset_id='bad',file='bad.png',sha256=hashlib.sha256(b'bad').hexdigest(),width=2,height=2)]
    (root/'input/manifest.json').write_text(json.dumps(samples));loads=[]
    def factory(config):
        loads.append(config)
        return lambda **kwargs:'红色图片' if kind=='caption' else [1.,0.]
    predict_batch(root,factory)
    assert len(loads)==1
    assert 'error' not in json.loads((root/'results/a.json').read_text())
    assert 'error' in json.loads((root/'results/bad.json').read_text())


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['caption','embedding'])
async def test_service_adapter_sends_prompt_and_preserves_complete_vectors(kind,mock_image_data,monkeypatch):
    import httpx
    from uuid import uuid4
    from services import multimodal_validation as mm
    from schemas.model_service import ModelServiceConfig
    from schemas.validation import ValidationConfig
    requests=[]
    def handle(request):
        body=json.loads(request.content);requests.append(body)
        if kind=='caption':return httpx.Response(200,json={'choices':[{'message':{'content':'红色图片'}}]})
        return httpx.Response(200,json={'data':[{'embedding':[1.0]*32}]})
    original=httpx.AsyncClient
    monkeypatch.setattr(mm.httpx,'AsyncClient',lambda:original(transport=httpx.MockTransport(handle)))
    config=ModelServiceConfig(key='fixture',name='fixture',kind=kind,protocol='openai_chat' if kind=='caption' else 'openai_embedding',base_url='http://fixture.test',endpoint='/infer',model_name='fixture')
    id=str(uuid4());sample={'query_texts':{id:'红色物体'}}
    result=await mm.service_infer(config,mock_image_data.getvalue(),sample,ValidationConfig(prompt='请描述颜色',max_tokens=27))
    if kind=='caption':assert result['caption']=='红色图片' and requests[0]['max_tokens']==27 and requests[0]['messages'][0]['content'][0]['text']=='请描述颜色'
    else:
        assert len(result['vector'])==len(result['queries'][id])==32
        assert len(requests)==2 and requests[1]['messages'][0]['content'][0]['text']=='红色物体'


@pytest.mark.asyncio
async def test_caption_preview_isolates_http_failure_and_retries_only_failed(pclient,detector,auth_headers,mock_image_data,test_db,test_engine,monkeypatch):
    from fastapi import HTTPException
    from services import validation,multimodal_validation as mm
    detector.kind='caption';await test_db.commit()
    response=upload(pclient,auth_headers,mock_image_data.getvalue(),{'model_id':str(detector.id),'task_type':'caption'},count=2)
    assert response.status_code==201
    url='/api/model-previews/'+response.json()['id'];calls=[]
    async def infer(*args):
        calls.append(1)
        if len(calls)==1:raise HTTPException(502,'模型服务返回 HTTP 502：网关暂时不可用')
        return {'caption':'正常图片描述'}
    monkeypatch.setattr(mm,'service_infer',infer);sessions=async_sessionmaker(test_engine,expire_on_commit=False)
    for _ in range(2):assert await validation.process_one(sessions)
    failed=pclient.get(url,headers=auth_headers).json()
    assert failed['failed_count']==1 and any('HTTP 502' in (i['error'] or '') for i in failed['items'])
    assert pclient.post(url+'/retry',headers=auth_headers).status_code==200
    assert await validation.process_one(sessions)
    final=pclient.get(url,headers=auth_headers).json()
    assert final['status']=='completed' and final['failed_count']==0 and len(calls)==3
