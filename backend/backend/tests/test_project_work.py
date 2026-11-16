from io import BytesIO
from types import SimpleNamespace
from uuid import UUID,uuid4
import asyncio
import hashlib
import importlib.util
import json
import httpx
import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest.mark.parametrize('value',[0,129])
def test_worker_concurrency_setting_bounds(value):
    from config.settings import Settings
    from pydantic import ValidationError
    assert Settings.model_fields['PROJECT_WORK_CONCURRENCY'].default==4
    with pytest.raises(ValidationError):Settings(PROJECT_WORK_CONCURRENCY=value)


@pytest.mark.parametrize('value',[16,32,64,128])
def test_worker_concurrency_accepts_benchmark_levels(value):
    from config.settings import Settings
    assert Settings(PROJECT_WORK_CONCURRENCY=value).PROJECT_WORK_CONCURRENCY==value


@pytest.mark.asyncio
@pytest.mark.parametrize('limit',[1,4,16,64,128])
async def test_worker_bounded_parallelism_and_cancellation(monkeypatch,limit):
    from services import project_work as service
    client_factory=httpx.AsyncClient
    monkeypatch.setattr(service.httpx,'AsyncClient',lambda **kwargs:client_factory(transport=httpx.MockTransport(lambda request:httpx.Response(200)),**kwargs))
    monkeypatch.setattr(service,'settings',SimpleNamespace(PROJECT_WORK_CONCURRENCY=limit),raising=False)
    reached=asyncio.Event();blocked=asyncio.Event();active=0;clients=set()
    async def process(sessions,client):
        nonlocal active
        active+=1;clients.add(id(client))
        if active==limit:reached.set()
        try:await blocked.wait()
        finally:active-=1
        return True
    monkeypatch.setattr(service,'process_one',process)
    task=asyncio.create_task(service.run_worker(None))
    try:
        await asyncio.wait_for(reached.wait(),3)
        assert active==limit and len(clients)==1
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    assert active==0


@pytest.mark.asyncio
async def test_high_concurrency_has_enough_http_connections(monkeypatch):
    from services import project_work as service
    limit=128;reached=asyncio.Event();release=asyncio.Event();handlers=set();active=0
    async def respond(reader,writer):
        nonlocal active
        handlers.add(asyncio.current_task())
        try:
            await reader.readuntil(b'\r\n\r\n');active+=1
            if active==limit:reached.set()
            await release.wait()
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: close\r\n\r\n');await writer.drain()
        except ConnectionError:pass
        finally:
            active-=1;writer.close()
            try:await writer.wait_closed()
            except ConnectionError:pass
    server=await asyncio.start_server(respond,'127.0.0.1',0,backlog=256)
    url='http://127.0.0.1:'+str(server.sockets[0].getsockname()[1])
    monkeypatch.setattr(service,'settings',SimpleNamespace(PROJECT_WORK_CONCURRENCY=limit),raising=False)
    async def process(sessions,client):
        response=await client.get(url);assert response.status_code==200;return True
    monkeypatch.setattr(service,'process_one',process)
    task=asyncio.create_task(service.run_worker(None))
    try:
        await asyncio.wait_for(reached.wait(),3);assert active==limit
    finally:
        task.cancel();release.set();server.close();await server.wait_closed()
        with pytest.raises(asyncio.CancelledError):await task
        await asyncio.gather(*handlers,return_exceptions=True)


@pytest.mark.asyncio
async def test_worker_uses_fresh_connections_between_batches(monkeypatch):
    from services import project_work as service
    done=asyncio.Event();blocked=asyncio.Event();peers=[];handlers=set();writers=set();calls=0
    async def respond(reader,writer):
        handlers.add(asyncio.current_task());writers.add(writer)
        try:
            while await reader.readuntil(b'\r\n\r\n'):
                peers.append(writer.get_extra_info('peername'))
                writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 0\r\nConnection: keep-alive\r\n\r\n');await writer.drain()
        except (ConnectionError,asyncio.IncompleteReadError):pass
        finally:writer.close()
    server=await asyncio.start_server(respond,'127.0.0.1',0)
    url='http://127.0.0.1:'+str(server.sockets[0].getsockname()[1])
    monkeypatch.setattr(service,'settings',SimpleNamespace(PROJECT_WORK_CONCURRENCY=2),raising=False)
    async def process(sessions,client):
        nonlocal calls
        if calls>=4:await blocked.wait()
        response=await client.get(url);assert response.status_code==200
        calls+=1
        if calls==4:done.set()
        return True
    monkeypatch.setattr(service,'process_one',process)
    task=asyncio.create_task(service.run_worker(None))
    try:
        await asyncio.wait_for(done.wait(),3)
        assert len(peers)==len(set(peers))==4
    finally:
        task.cancel();server.close();await server.wait_closed()
        with pytest.raises(asyncio.CancelledError):await task
        for writer in writers:writer.close()
        await asyncio.gather(*handlers,return_exceptions=True)


@pytest.mark.asyncio
async def test_worker_recovers_only_after_other_requests_finish(monkeypatch):
    from services import project_work as service
    client_factory=httpx.AsyncClient
    monkeypatch.setattr(service.httpx,'AsyncClient',lambda **kwargs:client_factory(transport=httpx.MockTransport(lambda request:httpx.Response(200)),**kwargs))
    monkeypatch.setattr(service,'settings',SimpleNamespace(PROJECT_WORK_CONCURRENCY=4),raising=False)
    started=asyncio.Event();release=asyncio.Event();recovered=asyncio.Event();active=0;calls=0
    async def process(sessions,client):
        nonlocal active,calls
        calls+=1
        if calls==1:
            await started.wait()
            raise RuntimeError('isolated worker error')
        if calls>4:return False
        active+=1
        if active==3:started.set()
        try:await release.wait()
        finally:active-=1
        return True
    async def recover(sessions):
        assert active==0,'Recovery must not fail other requests that are still running'
        recovered.set()
    monkeypatch.setattr(service,'process_one',process);monkeypatch.setattr(service,'recover_running',recover)
    task=asyncio.create_task(service.run_worker(None))
    try:
        await asyncio.wait_for(started.wait(),1);assert not recovered.is_set()
        release.set();await asyncio.wait_for(recovered.wait(),1)
    finally:
        release.set();task.cancel()
        with pytest.raises(asyncio.CancelledError):await task


def test_project_work_contract_exists():
    assert importlib.util.find_spec('services.project_work'), 'project annotation processing is missing'


def test_crop_expands_each_side_clamps_and_rejects_empty():
    from services.project_work import crop_region
    assert crop_region({'x': 10, 'y': 5, 'width': 20, 'height': 10}, 100, 80, .2) == (6, 3, 34, 17)
    assert crop_region({'x': 0, 'y': 0, 'width': 100, 'height': 80}, 100, 80, 1) == (0, 0, 100, 80)


@pytest_asyncio.fixture
async def work_env(test_engine, test_db, test_project, test_asset, test_user, tmp_path, monkeypatch):
    from models import Base, ModelService
    from models.project_work import ProjectWorkJob, ProjectWorkItem, MatchQuery
    from services.storage import get_storage, reset_storage
    from config.settings import settings
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[ModelService.__table__,
            ProjectWorkJob.__table__, ProjectWorkItem.__table__, MatchQuery.__table__]))
    monkeypatch.setattr(settings, 'STORAGE_TYPE', 'local')
    monkeypatch.setattr(settings, 'STORAGE_LOCAL_PATH', str(tmp_path / 'files'))
    reset_storage()
    data=BytesIO();Image.new('RGB',(64,48),'red').save(data,'PNG');raw=data.getvalue()
    test_asset.content_hash=hashlib.sha256(raw).hexdigest();test_asset.file_size=len(raw);test_asset.format='PNG'
    await get_storage().save_file(BytesIO(raw),test_asset.blob_path)
    models=[]
    for kind,protocol,endpoint in [('detection','model_service','/detect'),('caption','openai_chat','/chat/completions'),('embedding','openai_embedding','/embeddings')]:
        model=ModelService(tenant_id=1,key=kind,name=kind,kind=kind,protocol=protocol,base_url='http://model.test',endpoint=endpoint,
            model_name=kind,labels=['person'],enabled=True,confidence=.3,iou=.5,timeout_seconds=30,label_mapping={})
        test_db.add(model);models.append(model)
    await test_db.commit()
    yield SimpleNamespace(db=test_db,project=test_project,asset=test_asset,user=test_user,models=models,
        sessions=async_sessionmaker(test_engine,expire_on_commit=False))
    reset_storage()


@pytest.mark.asyncio
async def test_delete_terminal_queue_job_preserves_crop_results(client,auth_headers,work_env):
    from models import Asset, ProjectWorkItem, ProjectWorkJob
    s=work_env
    path=f'/api/projects/{s.project.id}/work/jobs'
    created=client.post(path,headers=auth_headers,json={'operation':'crop','model_id':str(s.models[0].id),
        'asset_ids':[str(s.asset.id)],'follow_uploads':True})
    assert created.status_code==201,created.text
    job_id=UUID(created.json()['id'])
    url=f'{path}/{job_id}'
    assert client.delete(url,headers=auth_headers).status_code==409
    assert client.put(url+'/follow-uploads',headers=auth_headers,json={'enabled':False}).status_code==200
    assert client.delete(url,headers=auth_headers).status_code==409
    assert client.put(url+'/follow-uploads',headers=auth_headers,json={'enabled':True}).status_code==200
    crop=Asset(project_id=s.project.id,original_name='derived.png',blob_path=s.asset.blob_path,
        content_hash='test-queue-delete-crop',source_type='model_crop',file_size=1)
    async with s.sessions() as db:
        db.add(crop)
        await db.flush()
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job_id))
        item.status='completed'
        item.result={'asset_ids':[str(crop.id)]}
        item_id,crop_id=item.id,crop.id
        await db.commit()
    assert client.delete(url,headers=auth_headers).status_code==409
    assert client.put(url+'/follow-uploads',headers=auth_headers,json={'enabled':False}).status_code==200
    assert client.delete(url,headers=auth_headers).status_code==204
    assert client.get(path,headers=auth_headers).json()==[]
    assert client.get(url,headers=auth_headers).status_code==404
    assert client.post(url+'/retry',headers=auth_headers).status_code==404
    async with s.sessions() as db:
        assert await db.get(ProjectWorkJob,job_id)
        assert (await db.get(ProjectWorkItem,item_id)).result['asset_ids']==[str(crop_id)]
        assert await db.get(Asset,crop_id)
    response=client.get('/api/assets',headers=auth_headers,params={'project_id':str(s.project.id),'crop_job_id':str(job_id)})
    assert response.status_code==200 and response.json()['items'][0]['id']==str(crop_id)


@pytest.mark.asyncio
async def test_crop_job_all_detectors_provenance_retry_and_cancel(work_env):
    from services import project_work as service
    from schemas.project_work import WorkCreate
    from models import Asset,LibraryImage
    from models.project_work import ProjectWorkItem
    s=work_env
    request=WorkCreate(operation='crop',model_id=s.models[0].id,asset_ids=[s.asset.id],labels=['person'],expansion=.2)
    job=await service.create_job(s.db,s.project,request,s.user.id)
    def provider(request):
        assert request.url.path=='/detect'
        return httpx.Response(200,json={'detections':[{'class':'person','bbox':[10,5,30,15],'score':.9}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        assert await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
        assert item.status=='completed'
        crop=await db.scalar(select(Asset).where(Asset.source_type=='model_crop'))
        assert (crop.width,crop.height)==(28,14)
        assert crop.device_info['parent_image_id']==str(s.asset.id)
        assert crop.device_info['crop']['region']==[6,3,34,17]
        assert crop.annotation_status=='pending'
        assert crop.image_id is None,'Project crops must not be reusable library images'
        assert not await db.scalar(select(LibraryImage.id).where(LibraryImage.source_type=='model_crop'))
    job2=await service.create_job(s.db,s.project,request,s.user.id)
    await service.action_job(s.db,s.project,job2.id,'cancel')
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        assert not await service.process_one(s.sessions,client)


@pytest.mark.asyncio
async def test_full_frame_crop_still_creates_project_private_derivative(work_env):
    from services import project_work as service
    from schemas.project_work import WorkCreate
    from models import Asset
    s=work_env
    await service.create_job(s.db,s.project,WorkCreate(operation='crop',model_id=s.models[0].id,asset_ids=[s.asset.id],expansion=0),s.user.id)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={
        'detections':[{'class':'person','bbox':[0,0,64,48],'score':.9}]}))) as client:
        await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        crop=await db.scalar(select(Asset).where(Asset.project_id==s.project.id,Asset.source_type=='model_crop'))
        assert crop is not None and crop.id!=s.asset.id and crop.image_id is None


@pytest.mark.asyncio
@pytest.mark.parametrize('operation,scope,legacy_flag',[
    ('crop','all',True), ('crop','crops',False), ('detect_attributes','all',True),
])
async def test_cut_jobs_preserve_project_annotation_scope(work_env,operation,scope,legacy_flag):
    from models import Project
    from schemas.project_work import WorkCreate
    from services.project_work import create_job
    s=work_env
    schema={'annotation_scope':scope}
    options={}
    if operation=='detect_attributes':
        s.project.task_type='caption'
        schema['object_attributes']={'target_label':'person','fields':[{'key':'color','name':'颜色'}]}
        options['attribute_model_id']=s.models[1].id
    s.project.label_schema=schema
    await s.db.commit()
    await create_job(s.db,s.project,WorkCreate(operation=operation,model_id=s.models[0].id,
        asset_ids=[s.asset.id],crops_only=legacy_flag,**options),s.user.id)
    async with s.sessions() as db:
        assert (await db.get(Project,s.project.id)).label_schema==schema


@pytest.mark.asyncio
async def test_no_detection_leaves_crop_only_queue_empty(work_env):
    from services import project_work as service
    from services.annotation_scope import eligible_assets
    from schemas.project_work import WorkCreate
    from models import Asset,Project
    s=work_env
    s.project.label_schema={'annotation_scope':'crops'}
    await s.db.commit()
    await service.create_job(s.db,s.project,WorkCreate(operation='crop',model_id=s.models[0].id,asset_ids=[s.asset.id]),s.user.id)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'detections':[]}))) as client:
        await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        project=await db.get(Project,s.project.id)
        assert project.label_schema['annotation_scope']=='crops'
        assert not (await db.scalars(select(Asset).where(Asset.project_id==project.id,eligible_assets(project)))).all()
        assert await db.get(Asset,s.asset.id),'Original must remain as provenance'


@pytest.mark.asyncio
async def test_vector_description_and_export_need_no_embeddings_and_exclude_originals(work_env):
    from fastapi import HTTPException
    from models import Asset
    from schemas.project_work import WorkCreate
    from services.annotations import save_annotation
    from services.annotation_export import training_package
    from services.project_work import create_job
    import zipfile
    s=work_env;s.project.task_type='vector'
    await save_annotation(s.db,s.asset,{'description':'原图描述'},0,s.user.id)
    digest=s.asset.content_hash;s.asset.content_hash='original-fixture';await s.db.flush()
    crop=Asset(project_id=s.project.id,original_name='private.png',blob_path=s.asset.blob_path,
        content_hash=digest,source_type='model_crop',file_size=s.asset.file_size,width=64,height=48,format='PNG')
    s.db.add(crop);await s.db.flush()
    s.project.label_schema={'annotation_scope':'crops'}
    await save_annotation(s.db,crop,{'description':'只描述当前裁剪图'},0,s.user.id)
    await s.db.commit()
    with pytest.raises(HTTPException) as error:
        await create_job(s.db,s.project,WorkCreate(operation='index',model_id=s.models[2].id,asset_ids=[s.asset.id]),s.user.id)
    assert error.value.status_code==409
    stream,manifest=await training_package(s.db,s.project,approved_only=False)
    with stream,zipfile.ZipFile(stream) as archive:
        row=json.loads(archive.read('annotations.jsonl'))
        assert row['description']=='只描述当前裁剪图' and row['asset_id']==str(crop.id)
        assert archive.read('queries.jsonl')==b''
    assert [row['asset_id'] for row in manifest['assets']]==[str(crop.id)]


@pytest.mark.asyncio
async def test_caption_never_overwrites_manual_edit_during_inference(work_env):
    from services import project_work as service
    from schemas.project_work import WorkCreate
    from services.annotations import save_annotation
    from models import Project, Asset, Annotation
    from models.project_work import ProjectWorkItem
    s=work_env;s.project.task_type='caption';await s.db.commit()
    job=await service.create_job(s.db,s.project,WorkCreate(operation='caption',model_id=s.models[1].id,asset_ids=[s.asset.id]),s.user.id)
    async def provider(request):
        async with s.sessions() as db:
            asset=await db.get(Asset,s.asset.id)
            await save_annotation(db,asset,{'description':'人工描述','labels':[]},0,s.user.id)
            await db.commit()
        return httpx.Response(200,json={'choices':[{'message':{'content':'模型描述'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        ann=await db.scalar(select(Annotation).where(Annotation.is_latest.is_(True)))
        assert ann.annotation_data['description']=='人工描述'
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
        assert item.status=='failed' and '修改' in item.error


@pytest.mark.asyncio
async def test_matching_judgment_is_query_specific_and_versioned(work_env):
    from models.project_work import MatchQuery
    from services.project_work import judge
    from fastapi import HTTPException
    s=work_env;s.project.task_type='vector';await s.db.commit()
    ids=[]
    for text in ['黑色衣服','白色衣服']:
        q=MatchQuery(project_id=s.project.id,query={'text':text},space={'model':'test'},hits=[{'asset_id':str(s.asset.id),'score':.8,'judgment':'unknown'}])
        s.db.add(q);await s.db.flush();ids.append(q.id)
    await s.db.commit()
    first=await judge(s.db,s.project,ids[0],s.asset.id,'correct',1,s.user.id)
    assert first['version']==2 and first['hits'][0]['judgment']=='correct'
    second=await judge(s.db,s.project,ids[1],s.asset.id,'incorrect',1,s.user.id)
    assert second['hits'][0]['judgment']=='incorrect'
    with pytest.raises(HTTPException) as error:await judge(s.db,s.project,ids[0],s.asset.id,'incorrect',1,s.user.id)
    assert error.value.status_code==409
    await s.db.rollback()
    await s.db.refresh(s.project);await s.db.refresh(s.user)
    with pytest.raises(HTTPException) as error:await judge(s.db,s.project,ids[0],uuid4(),'correct',2,s.user.id)
    assert error.value.status_code==404


@pytest.mark.asyncio
async def test_milvus_requests_are_scoped_and_do_not_create_on_search():
    from services.milvus import Milvus
    calls=[]
    def provider(request):
        body=json.loads(request.content);calls.append((request.url.path,body))
        if request.url.path.endswith('/has'):return httpx.Response(200,json={'code':0,'data':{'has':False}})
        return httpx.Response(200,json={'code':0,'data':[]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        m=Milvus(client,collection='image_platform_test_owned')
        assert await m.search([1.]+[0.]*2047,1,uuid4(),'space',[],20)==[]
    assert not any(path.endswith('/create') for path,_ in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['cancel','model','delete','review'])
async def test_late_project_results_are_fenced(work_env,change):
    from services import project_work as service
    from services.annotations import save_annotation,review
    from schemas.project_work import WorkCreate
    from models import Annotation,Asset,ModelService,Project
    from models.project_work import ProjectWorkItem
    s=work_env;s.project.task_type='caption'
    await save_annotation(s.db,s.asset,{'description':'原描述'},0,s.user.id);await s.db.commit()
    job=await service.create_job(s.db,s.project,WorkCreate(operation='caption',model_id=s.models[1].id,asset_ids=[s.asset.id]),s.user.id)
    async def provider(request):
        async with s.sessions() as db:
            if change=='cancel':
                project=await db.get(Project,s.project.id);await service.action_job(db,project,job.id,'cancel')
            elif change=='model':
                model=await db.get(ModelService,s.models[1].id);model.revision+=1;await db.commit()
            elif change=='delete':
                await db.delete(await db.get(Asset,s.asset.id));await db.commit()
            else:
                ann=await db.scalar(select(Annotation).where(Annotation.is_latest.is_(True)))
                await review(db,ann,await db.get(Asset,s.asset.id),True,'人工通过',s.user.id);await db.commit()
        return httpx.Response(200,json={'choices':[{'message':{'content':'迟到描述'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
        assert item.status!='completed'


@pytest.mark.asyncio
async def test_project_routes_reject_foreign_assets_jobs_and_queries(work_env):
    from fastapi import FastAPI
    from api.project_work_routes import router
    from database import get_db_session
    from api.deps import get_current_user
    s=work_env;app=FastAPI();app.include_router(router,prefix='/api/projects')
    async def db():
        async with s.sessions() as session:yield session
    app.dependency_overrides[get_db_session]=db;app.dependency_overrides[get_current_user]=lambda:s.user
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://api.test') as client:
        missing=str(uuid4())
        assert (await client.get(f'/api/projects/{missing}/work/jobs')).status_code==404
        base=f'/api/projects/{s.project.id}'
        assert (await client.post(base+'/work/jobs',json={'operation':'crop','model_id':str(s.models[0].id),'asset_ids':[missing]})).status_code==404
        assert (await client.get(base+'/work/jobs/'+missing)).status_code==404
        assert (await client.put(base+'/matches/'+missing,json={'asset_id':str(s.asset.id),'judgment':'correct','base_version':1})).status_code==404


@pytest.mark.asyncio
async def test_index_search_and_pair_export(work_env,monkeypatch):
    from services import project_work as service
    from services.annotation_export import training_package
    from services.milvus import Milvus
    from schemas.project_work import WorkCreate,MatchSearch
    import zipfile
    s=work_env;s.project.task_type='vector';await s.db.commit()
    stored={}
    async def upsert(self,key,vector,tenant,project,asset,snapshot,content_hash):stored[key]={'id':key,'asset_id':str(asset),'content_hash':content_hash,'distance':1.}
    async def search(self,vector,tenant,project,space,keys,limit):return [stored[key] for key in keys][:limit]
    monkeypatch.setattr(Milvus,'upsert',upsert);monkeypatch.setattr(Milvus,'search',search)
    await service.create_job(s.db,s.project,WorkCreate(operation='index',model_id=s.models[2].id,asset_ids=[s.asset.id]),s.user.id)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'data':[{'embedding':[1.]+[0.]*2047}]}))) as client:
        await service.process_one(s.sessions,client)
        result=await service.search(s.db,s.project,MatchSearch(model_id=s.models[2].id,text='黑色衣服'),client)
    await s.db.refresh(s.project);await s.db.refresh(s.user)
    result=await service.judge(s.db,s.project,__import__('uuid').UUID(result['id']),s.asset.id,'incorrect',1,s.user.id)
    assert result['hits'][0]['judgment']=='incorrect'
    await service.judge(s.db,s.project,UUID(result['id']),s.asset.id,None,result['version'],s.user.id,True)
    stream,manifest=await training_package(s.db,s.project)
    with stream,zipfile.ZipFile(stream) as archive:
        pair=json.loads(archive.read('queries.jsonl').decode())
        assert pair['query']['text']=='黑色衣服' and len(pair['negatives'])==1 and not pair['positives']
