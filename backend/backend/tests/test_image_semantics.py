"""Semantic jobs use isolated fixtures, never the business database."""
import importlib.util
import json
from io import BytesIO
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from PIL import Image
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import async_sessionmaker


def test_semantics_contract_exists():
    assert importlib.util.find_spec('services.image_semantics') is not None, 'durable semantic service is missing'


@pytest_asyncio.fixture
async def semantic(test_engine, test_db, test_user, tmp_path, monkeypatch):
    from models.model_service import ModelService
    from models.image_semantic import ImageSemanticJob, ImageSemanticItem, ImageSemanticVector
    from models import LibraryImage, ImageCollection, ImageCollectionMember
    from schemas.model_service import ModelServiceConfig
    from services.storage import get_storage, reset_storage
    from config.settings import settings
    from api.image_semantic_routes import router
    from database import get_db_session
    from api.deps import get_current_user
    tables = [ModelService.__table__, ImageSemanticJob.__table__, ImageSemanticItem.__table__, ImageSemanticVector.__table__]
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync: tables[0].metadata.create_all(sync, tables=tables))
    monkeypatch.setattr(settings, 'STORAGE_TYPE', 'local')
    monkeypatch.setattr(settings, 'STORAGE_LOCAL_PATH', str(tmp_path / 'semantic-storage'))
    reset_storage()
    image = LibraryImage(tenant_id=1, original_name='semantic.png', blob_path='semantic.png', content_hash='semantic', file_size=80, source_type='upload')
    group = ImageCollection(tenant_id=1, name='semantic')
    test_db.add_all([image, group]); await test_db.flush()
    test_db.add(ImageCollectionMember(collection_id=group.id, image_id=image.id))
    models=[]
    for kind, protocol, endpoint in [('caption','openai_chat','/chat/completions'), ('embedding','openai_embedding','/embeddings')]:
        config=ModelServiceConfig(key=kind, name=kind, kind=kind, protocol=protocol, endpoint=endpoint, base_url='http://model.test/v1', model_name=kind)
        model=ModelService(tenant_id=1, **config.model_dump());test_db.add(model);models.append(model)
    await test_db.commit()
    content=BytesIO();Image.new('RGB',(12,10),'red').save(content,'PNG');content.seek(0)
    await get_storage().save_file(content,image.blob_path)
    sessions=async_sessionmaker(test_engine,expire_on_commit=False)
    app=FastAPI();app.include_router(router,prefix='/api/image-semantics')
    async def db():
        async with sessions() as session:yield session
    app.dependency_overrides[get_db_session]=db
    app.dependency_overrides[get_current_user]=lambda:test_user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://api.test') as client:
        yield dict(client=client, sessions=sessions, db=test_db, image=image, group=group, caption=models[0], embedding=models[1])
    reset_storage()


def payload(s, **changes):
    return dict(image_ids=[str(s['image'].id)], caption_model_id=str(s['caption'].id), embedding_model_id=str(s['embedding'].id), **changes)


@pytest.mark.asyncio
async def test_pre_prompt_snapshot_defaults_remain_compatible_and_edits_are_rejected(semantic):
    from services.image_semantics import model_snapshot, validate_snapshots
    from fastapi import HTTPException
    s=semantic;db=s['db']
    snapshot=await model_snapshot(db,1,s['caption'].id,'caption')
    snapshot['config'].pop('chat_prompts')
    await validate_snapshots(db,1,[snapshot])
    model=await db.get(type(s['caption']),s['caption'].id)
    model.chat_prompts={'caption':'修改后的描述'};model.revision+=1;await db.commit()
    with pytest.raises(HTTPException) as error:await validate_snapshots(db,1,[snapshot])
    assert error.value.status_code==409


def provider(request):
    body=json.loads(request.content)
    assert body['messages'][0]['content']
    if request.url.path.endswith('/embeddings'):
        return httpx.Response(200,json={'data':[{'embedding':[1.0]+[0.0]*2047}]})
    return httpx.Response(200,json={'choices':[{'message':{'content':'一张红色图片'}}]})


@pytest.mark.asyncio
async def test_persistent_queue_search_and_no_vector_body(semantic):
    from services.image_semantics import process_one
    from models.image_semantic import ImageSemanticVector
    s=semantic;c=s['client']
    created=await c.post('/api/image-semantics/jobs',json=payload(s));assert created.status_code==201,created.text
    job=created.json();assert job['counts']['pending']==1
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as model:
        assert await process_one(s['sessions'],model)
        assert not await process_one(s['sessions'],model)
    detail=(await c.get('/api/image-semantics/jobs/'+job['id'])).json()
    assert detail['counts']['completed']==1 and detail['items'][0]['caption']=='一张红色图片'
    vector=await s['db'].scalar(select(ImageSemanticVector))
    assert len(vector.vector)==2048 and 'vector' not in detail['items'][0]


@pytest.mark.asyncio
@pytest.mark.parametrize('mutation',['cancel','revision','delete','membership','content_hash'])
async def test_late_results_never_write(semantic,mutation):
    from services.image_semantics import process_one
    from models.image_semantic import ImageSemanticVector
    from models import LibraryImage, ImageCollectionMember
    from models.model_service import ModelService
    s=semantic;c=s['client']
    job=(await c.post('/api/image-semantics/jobs',json=payload(s,collection_id=str(s['group'].id)))).json()
    changed=False
    async def late(request):
        nonlocal changed
        if not changed:
            changed=True
            if mutation=='cancel':
                response=await c.post('/api/image-semantics/jobs/'+job['id']+'/cancel');assert response.status_code==200
            else:
                async with s['sessions']() as db:
                    if mutation=='revision':await db.execute(update(ModelService).where(ModelService.id==s['embedding'].id).values(revision=2))
                    elif mutation=='delete':await db.delete(await db.get(LibraryImage,s['image'].id))
                    elif mutation=='content_hash':await db.execute(update(LibraryImage).where(LibraryImage.id==s['image'].id).values(content_hash='replacement'))
                    else:await db.delete(await db.get(ImageCollectionMember,(s['group'].id,s['image'].id)))
                    await db.commit()
        return provider(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(late)) as model:await process_one(s['sessions'],model)
    assert await s['db'].scalar(select(func.count()).select_from(ImageSemanticVector))==0
    detail=(await c.get('/api/image-semantics/jobs/'+job['id'])).json()
    assert detail['counts'].get('completed',0)==0


@pytest.mark.asyncio
async def test_scope_retry_and_invalid_queries(semantic):
    from models import Tenant, LibraryImage, ImageCollection
    from services.image_semantics import process_one
    s=semantic;c=s['client'];db=s['db']
    db.add(Tenant(id=99,tenant_name='foreign'));await db.flush()
    foreign=LibraryImage(tenant_id=99,original_name='secret',blob_path='secret',content_hash='secret',file_size=1,source_type='upload')
    group=ImageCollection(tenant_id=99,name='foreign');db.add_all([foreign,group]);await db.commit()
    for change in [{'image_ids':[str(foreign.id)]},{'collection_id':str(group.id)},{'embedding_model_id':str(uuid4())}]:
        request=payload(s);request.update(change)
        assert (await c.post('/api/image-semantics/jobs',json=request)).status_code==404
    job=(await c.post('/api/image-semantics/jobs',json=payload(s))).json()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'data':[{'embedding':[.1]*8}]}))) as model:
        await process_one(s['sessions'],model)
    detail=(await c.get('/api/image-semantics/jobs/'+job['id'])).json();assert detail['counts']['failed']==1
    item=detail['items'][0]['id']
    assert (await c.post('/api/image-semantics/jobs/'+job['id']+'/retry',json={'item_ids':[str(uuid4())]})).status_code==404
    assert (await c.post('/api/image-semantics/jobs/'+job['id']+'/retry',json={'item_ids':[item]})).status_code==200
    assert (await c.post('/api/image-semantics/search',json={'embedding_model_id':str(s['embedding'].id)})).status_code==422


@pytest.mark.asyncio
async def test_cosine_search_text_image_and_space_isolation(semantic,monkeypatch):
    from services import image_semantics as service
    from schemas.image_semantic import SemanticSearch
    from models.image_semantic import ImageSemanticVector
    from models import LibraryImage
    s=semantic;c=s['client'];db=s['db']
    await c.post('/api/image-semantics/jobs',json=payload(s))
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as model:await service.process_one(s['sessions'],model)
    image=LibraryImage(tenant_id=1,original_name='second',blob_path='unused',content_hash='second',file_size=1,source_type='upload')
    db.add(image);await db.flush()
    values=dict(tenant_id=1,image_id=image.id,content_hash=image.content_hash,model_id=s['embedding'].id,revision=1,dimensions=2048,preprocessing=service.PREPROCESSING,vector=[0.,1.]+[0.]*2046)
    db.add(ImageSemanticVector(**values))
    for override in [dict(revision=2),dict(dimensions=3,vector=[1.,0.,0.]),dict(preprocessing='other')]:
        db.add(ImageSemanticVector(**{**values,**override}))
    await db.commit()
    calls=[]
    def record(request):calls.append(json.loads(request.content));return provider(request)
    image_id,model_id,group_id=s['image'].id,s['embedding'].id,s['group'].id
    async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as model:
        for input in [dict(text='红色图片'),dict(image_id=image_id)]:
            async with s['sessions']() as search_db:
                result=await service.search(search_db,1,SemanticSearch(embedding_model_id=model_id,top_k=1,**input),model)
            assert result['indexed_count']==2 and len(result['items'])==1
            assert result['items'][0]['image_id']==str(image_id) and result['items'][0]['score']==1
            assert 'vector' not in result['items'][0]
        request=SemanticSearch(embedding_model_id=model_id,collection_id=group_id,text='test')
        async with s['sessions']() as search_db:result=await service.search(search_db,1,request,model)
        assert result['indexed_count']==1
    assert calls[0]['messages'][0]['content']==[{'type':'text','text':'红色图片'}]
    assert calls[1]['messages'][0]['content'][1]['type']=='image_url'
    monkeypatch.setattr(service,'MAX_INDEX_RECORDS',1)
    async def no_infer(*args,**kwargs):raise AssertionError('over-limit search must fail before inference')
    monkeypatch.setattr(service,'embedding',no_infer)
    response=await c.post('/api/image-semantics/search',json={'embedding_model_id':str(model_id),'text':'test'})
    assert response.status_code==409 and '5000' in response.json()['detail']


@pytest.mark.parametrize('value',[[.1]*8,[0.]*2048,[float('nan')]*2048,[True]*2048,[float('inf')]*2048])
def test_invalid_vectors_are_rejected(value):
    from services.image_semantics import full_vector
    from fastapi import HTTPException
    with pytest.raises(HTTPException):full_vector(value)


@pytest.mark.asyncio
@pytest.mark.parametrize('moment',['before_claim','after_completed'])
async def test_hash_changes_invalidate_work_and_old_index(semantic,moment):
    from models import LibraryImage
    from services.image_semantics import process_one,search
    from schemas.image_semantic import SemanticSearch
    s=semantic;c=s['client'];db=s['db']
    job=(await c.post('/api/image-semantics/jobs',json=payload(s))).json()
    image_id,model_id=s['image'].id,s['embedding'].id
    if moment=='after_completed':
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:await process_one(s['sessions'],client)
    await db.execute(update(LibraryImage).where(LibraryImage.id==image_id).values(content_hash='replaced'));await db.commit()
    if moment=='before_claim':
        def forbidden(request):raise AssertionError('changed image must not be sent to provider')
        async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as client:await process_one(s['sessions'],client)
        assert (await c.get('/api/image-semantics/jobs/'+job['id'])).json()['counts']['failed']==1
    else:
        async with s['sessions']() as session,httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            result=await search(session,1,SemanticSearch(embedding_model_id=model_id,text='test'),client)
            assert result['indexed_count']==0 and result['items']==[]


@pytest.mark.asyncio
async def test_actual_5001_records_fail_explicitly(semantic,monkeypatch):
    from models import LibraryImage
    from models.image_semantic import ImageSemanticVector
    from services import image_semantics as service
    s=semantic;db=s['db']
    ids=[uuid4() for _ in range(5001)]
    await db.execute(LibraryImage.__table__.insert(),[dict(id=image_id,tenant_id=1,original_name=str(i),blob_path='unused',
        content_hash='cap-'+str(i),file_size=1,source_type='upload') for i,image_id in enumerate(ids)])
    await db.execute(ImageSemanticVector.__table__.insert(),[dict(tenant_id=1,image_id=image_id,content_hash='cap-'+str(i),
        model_id=s['embedding'].id,revision=1,dimensions=2048,preprocessing=service.PREPROCESSING,vector=[1.]) for i,image_id in enumerate(ids)])
    await db.commit()
    async def forbidden(*args,**kwargs):raise AssertionError('over-limit search must not invoke provider')
    monkeypatch.setattr(service,'embedding',forbidden)
    response=await s['client'].post('/api/image-semantics/search',json={'embedding_model_id':str(s['embedding'].id),'text':'test'})
    assert response.status_code==409 and '未截断' in response.json()['detail']


@pytest.mark.asyncio
async def test_description_only_and_embedding_only(semantic):
    from services.image_semantics import process_one
    from models.image_semantic import ImageSemanticVector
    s=semantic;c=s['client']
    for kind in ['caption','embedding']:
        request=payload(s);request.pop('embedding_model_id' if kind=='caption' else 'caption_model_id')
        job=(await c.post('/api/image-semantics/jobs',json=request)).json()
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:await process_one(s['sessions'],client)
        detail=(await c.get('/api/image-semantics/jobs/'+job['id'])).json()
        assert detail['counts']['completed']==1
        assert bool(detail['items'][0]['caption'])==(kind=='caption')
        async with s['sessions']() as db:
            assert await db.scalar(select(func.count()).select_from(ImageSemanticVector))==(1 if kind=='embedding' else 0)


@pytest.mark.asyncio
async def test_restart_recovery_and_retry(semantic):
    from services.image_semantics import recover_running,process_one
    from models.image_semantic import ImageSemanticItem
    s=semantic;c=s['client'];db=s['db']
    job=(await c.post('/api/image-semantics/jobs',json=payload(s))).json()
    await db.execute(update(ImageSemanticItem).where(ImageSemanticItem.job_id==UUID(job['id'])).values(status='running',attempt=1))
    await db.commit();await recover_running(s['sessions'])
    detail=(await c.get('/api/image-semantics/jobs/'+job['id'])).json()
    assert detail['counts']['failed']==1 and detail['items'][0]['attempt']==2
    assert (await c.post('/api/image-semantics/jobs/'+job['id']+'/retry')).status_code==200
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as model:await process_one(s['sessions'],model)
    assert (await c.get('/api/image-semantics/jobs/'+job['id'])).json()['counts']['completed']==1


@pytest.mark.asyncio
async def test_foreign_jobs_are_inaccessible(semantic):
    from models import Tenant
    from models.image_semantic import ImageSemanticJob
    from services.image_semantics import PREPROCESSING
    s=semantic;db=s['db'];c=s['client']
    db.add(Tenant(id=99,tenant_name='other'));await db.flush()
    job=ImageSemanticJob(tenant_id=99,preprocessing=PREPROCESSING,prompt='test');db.add(job);await db.commit()
    assert (await c.get('/api/image-semantics/jobs')).json()==[]
    url='/api/image-semantics/jobs/'+str(job.id)
    assert (await c.get(url)).status_code==404
    assert (await c.post(url+'/cancel')).status_code==404
    assert (await c.post(url+'/retry')).status_code==404


@pytest.mark.asyncio
async def test_cancelled_attempt_cannot_overwrite_retried_success(semantic):
    from services.image_semantics import finish_item,PREPROCESSING,process_one
    from models.image_semantic import ImageSemanticJob,ImageSemanticItem,ImageSemanticVector
    s=semantic;c=s['client'];db=s['db']
    result=(await c.post('/api/image-semantics/jobs',json=payload(s))).json()
    job=await db.get(ImageSemanticJob,UUID(result['id']));item=await db.scalar(select(ImageSemanticItem).where(ImageSemanticItem.job_id==job.id))
    item.status='running';item.attempt=1;await db.commit()
    work=dict(item_id=item.id,job_id=job.id,image_id=s['image'].id,tenant_id=1,collection_id=None,attempt=1,
        caption_model=job.caption_model,embedding_model=job.embedding_model,dimensions=2048,preprocessing=PREPROCESSING,content_hash=s['image'].content_hash)
    await c.post('/api/image-semantics/jobs/'+result['id']+'/cancel')
    await c.post('/api/image-semantics/jobs/'+result['id']+'/retry')
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as model:await process_one(s['sessions'],model)
    await finish_item(s['sessions'],work,'late wrong caption',[0.,1.]+[0.]*2046,None)
    await db.refresh(item)
    vector=await db.scalar(select(ImageSemanticVector))
    assert item.caption=='一张红色图片' and vector.vector[0]==1
