import asyncio,hashlib,io,tarfile,zipfile
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

def archive(kind,items):
    output=io.BytesIO()
    if kind=='zip':
        with zipfile.ZipFile(output,'w') as file:
            for name,data in items:file.writestr(name,data)
    else:
        with tarfile.open(fileobj=output,mode='w') as file:
            for name,data in items:
                entry=tarfile.TarInfo(name);entry.size=len(data);file.addfile(entry,io.BytesIO(data))
    return output.getvalue()

def start(client,headers,data,name='test.zip',group='续传测试'):
    config=client.get('/api/image-uploads/config',headers=headers)
    assert config.status_code==200,config.text
    size=config.json()['chunk_size'];parts=[data[i:i+size] for i in range(0,len(data),size)]
    response=client.post('/api/image-uploads',headers=headers,json={'filename':name,'size':len(data),
        'chunk_hashes':[hashlib.sha256(part).hexdigest() for part in parts],'collection_name':group})
    assert response.status_code==200,response.text
    return response.json(),parts


def test_image_archive_upload_accepts_100_gib_boundary(client,auth_headers):
    limit=100*1024**3
    config=client.get('/api/image-uploads/config',headers=auth_headers).json()
    assert config['max_file_size']==config['max_expanded_size']==limit
    request={'filename':'large.zip','size':limit,'chunk_hashes':['0'*64]*(limit//config['chunk_size']),
        'collection_name':'大包边界测试'}
    accepted=client.post('/api/image-uploads',headers=auth_headers,json=request)
    assert accepted.status_code==200,accepted.text
    assert accepted.json()['size']==limit
    request['size']+=1
    request['chunk_hashes'].append('0'*64)
    assert client.post('/api/image-uploads',headers=auth_headers,json=request).status_code==413


@pytest.mark.asyncio
async def test_upload_batches_restore_separate_progress_and_survive_receipt_cleanup(client,auth_headers,test_project,test_engine,mock_image_data):
    from models import Base,ProjectWorkItem,ProjectWorkJob
    from services.image_uploads import process_one

    async with test_engine.begin() as db:
        await db.run_sync(lambda conn:Base.metadata.create_all(conn,tables=[ProjectWorkJob.__table__,ProjectWorkItem.__table__]))
    url='/api/image-uploads'
    keys=['0'*64,'1'*64,'2'*64]
    query={'project_id':str(test_project.id)}
    payload={**query,'collection_name':'上传批次测试','image_kind':'crops','file_keys':keys}
    created=client.post(url+'/batches',headers=auth_headers,json=payload)
    assert created.status_code==201,created.text
    batch=created.json()['id']
    other=client.post(url+'/batches',headers=auth_headers,json={**payload,'file_keys':['3'*64]}).json()['id']
    data=mock_image_data.getvalue()
    uploaded=client.post(url,headers=auth_headers,json={**query,'collection_name':payload['collection_name'],
        'image_kind':'crops','batch_id':batch,'file_key':keys[0],'filename':'batch.jpg','size':len(data),
        'chunk_hashes':[hashlib.sha256(data).hexdigest()]})
    assert uploaded.status_code==200,uploaded.text
    receipt=url+'/'+uploaded.json()['id']
    assert client.put(receipt+'/parts/0',headers=auth_headers,content=data).status_code==200
    assert client.post(receipt+'/complete',headers=auth_headers).status_code==200
    assert await process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    assert client.put(f'{url}/batches/{batch}/files/{keys[1]}/failed',headers=auth_headers).status_code==204
    assert client.delete(receipt,headers=auth_headers).status_code==204
    records={item['id']:item for item in client.get(url+'/batches',headers=auth_headers,params=query).json()}
    fields=('total','completed','failed','remaining')
    assert [records[batch][field] for field in fields]==[3,1,1,1]
    assert [records[other][field] for field in fields]==[1,0,0,1]
    resumed=client.post(url+'/batches',headers=auth_headers,json={**payload,'batch_id':batch,'file_keys':keys[::-1]})
    assert resumed.status_code==201 and resumed.json()['completed_keys']==[keys[0]]
    wrong=client.post(url+'/batches',headers=auth_headers,json={**payload,'batch_id':batch,'file_keys':keys[:2]})
    assert wrong.status_code==409


@pytest.mark.asyncio
async def test_ingest_row_locks_allow_upload_receipt_foreign_keys(test_db,test_project,monkeypatch):
    from models import LibraryImage
    from sqlalchemy.dialects import postgresql
    from services.image_library import assign_collection,attach_images

    image=LibraryImage(tenant_id=test_project.tenant_id,content_hash='lock-fixture',original_name='fixture.png',
        blob_path='fixture.png',source_type='upload',file_size=1)
    test_db.add(image)
    await test_db.flush()
    scalar=test_db.scalar
    locks={}

    async def inspect_lock(statement,*args,**kwargs):
        query=str(statement.compile(dialect=postgresql.dialect()))
        for table in ('image_collections','projects'):
            if f'FROM {table} ' in query and 'FOR ' in query:
                locks[table]=query.rsplit('FOR ',1)[-1]
        return await scalar(statement,*args,**kwargs)

    monkeypatch.setattr(test_db,'scalar',inspect_lock)
    await assign_collection(test_db,test_project.tenant_id,[image.id],'上传锁测试')
    await attach_images(test_db,test_project.id,test_project.tenant_id,[image.id])
    assert locks=={'image_collections':'NO KEY UPDATE','projects':'NO KEY UPDATE'}


@pytest.mark.asyncio
async def test_upload_batch_selection_includes_existing_original_without_adding_entire_collection(client, auth_headers, mock_image_data, test_engine):
    from services import image_uploads as module
    from sqlalchemy import select
    from models import LibraryImage
    async def ingest(group):
        job, parts = start(client, auth_headers, mock_image_data.getvalue(), 'sample.jpg', group)
        url = '/api/image-uploads/' + job['id']
        assert client.get(url + '/images', headers=auth_headers).status_code == 409
        for number, part in enumerate(parts):
            assert client.put(url + '/parts/' + str(number), headers=auth_headers, content=part).status_code == 200
        client.post(url + '/complete', headers=auth_headers)
        assert await module.process_one(async_sessionmaker(test_engine, expire_on_commit=False))
        return job, url
    first, _ = await ingest('原有图片集')
    second, url = await ingest('新上传图片集')
    assert first['id'] != second['id']
    result = client.get(url + '/images', headers=auth_headers)
    assert result.status_code == 200, result.text
    async with async_sessionmaker(test_engine)() as db:
        image = await db.scalar(select(LibraryImage))
        assert image.batch_id == first['id']
        assert result.json()['image_ids'] == [str(image.id)]
    assert result.json()['total'] == 1
    assert client.get(url + '/images', headers=auth_headers, params={'page_size': 1001}).status_code == 422

@pytest.mark.asyncio
async def test_uploaded_crop_receipt_keeps_kind_when_attached(client,auth_headers,mock_image_data,test_engine,test_project):
    from models import Base,ProjectWorkJob,ProjectWorkItem
    from sqlalchemy import select
    from services import image_uploads as module
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda db:Base.metadata.create_all(db,tables=[ProjectWorkJob.__table__,ProjectWorkItem.__table__]))
    data=mock_image_data.getvalue()
    sessions=async_sessionmaker(test_engine,expire_on_commit=False)
    async with sessions() as db:
        db.add(ProjectWorkJob(project_id=test_project.id,user_id=1,operation='attributes',model={},follow_uploads=True,
            options={'workflow_streaming':True,'workflow_image_kind':'crops'}))
        await db.commit()
    request=dict(filename='crop.jpg',size=len(data),chunk_hashes=[hashlib.sha256(data).hexdigest()],
        collection_name='手动裁剪图',project_id=str(test_project.id))
    def create(**kwargs):
        response=client.post('/api/image-uploads',headers=auth_headers,json={**request,**kwargs})
        assert response.status_code==200,response.text
        return response.json()
    original=create()
    crop=create(image_kind='crops')
    assert crop['min_long_edge']==0 and original['id']!=crop['id'] and create(image_kind='crops')['id']==crop['id']
    url='/api/image-uploads/'+crop['id']
    assert client.put(url+'/parts/0',headers=auth_headers,content=data).status_code==200
    assert client.post(url+'/complete',headers=auth_headers).status_code==200
    assert await module.process_one(sessions)
    project_assets=client.get('/api/assets',headers=auth_headers,params={'project_id':request['project_id'],'image_kind':'crops'}).json()
    assert project_assets['total']==1
    receipt=client.get(url,headers=auth_headers).json()
    assert receipt['result']['added_count']==1
    assert receipt['result']['queued_count']==1
    images=client.get(url+'/images',headers=auth_headers).json()['image_ids']
    association=client.post('/api/images/associate',headers=auth_headers,json={
        'project_id':request['project_id'],'upload_id':crop['id'],'image_ids':images})
    assert association.status_code==200,association.text
    async with sessions() as db:
        assert len((await db.scalars(select(ProjectWorkItem))).all())==1
    result=client.get('/api/assets',headers=auth_headers,params={'project_id':request['project_id'],'image_kind':'crops','scope':'annotation'}).json()
    assert result['total']==1 and result['items'][0]['device_info']['uploaded_crop']


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['zip','tar'])
async def test_resume_only_missing_chunks_and_import_unique_images(client,auth_headers,mock_image_data,test_engine,monkeypatch,kind):
    from services import image_uploads as module
    monkeypatch.setattr(module,'CHUNK_SIZE',128)
    content=archive(kind,[('nested/a.jpg',mock_image_data.getvalue()),('other/same.jpg',mock_image_data.getvalue()),('readme.txt',b'ignore')])
    job,parts=start(client,auth_headers,content,'test.'+kind)
    url='/api/image-uploads/'+job['id']
    assert client.put(url+'/parts/0',headers=auth_headers,content=parts[0]).status_code==200
    resumed,_=start(client,auth_headers,content,'test.'+kind)
    assert resumed['id']==job['id'] and resumed['uploaded_parts']==[0]
    assert client.post(url+'/complete',headers=auth_headers).status_code==409
    for index,part in enumerate(parts[1:],1):
        assert client.put(url+'/parts/'+str(index),headers=auth_headers,content=part).status_code==200
    assert client.post(url+'/complete',headers=auth_headers).status_code==200
    # A concurrent copy can finish sending a verified chunk after completion was submitted.
    assert client.put(url+'/parts/0',headers=auth_headers,content=parts[0]).status_code==200
    assert await module.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    result=client.get(url,headers=auth_headers).json()
    assert result['status']=='completed' and result['result']['imported']==1 and result['result']['duplicates']==1
    assert client.put(url+'/parts/0',headers=auth_headers,content=parts[0]).status_code==200
    assert client.put(url+'/parts/0',headers=auth_headers,content=b'wrong').status_code==422
    assert client.get('/api/images',headers=auth_headers).json()['total']==1
    finished,_=start(client,auth_headers,content,'test.'+kind)
    assert finished['id']==job['id'] and finished['status']=='completed'
    image_id=client.get(url+'/images',headers=auth_headers).json()['image_ids'][0]
    assert client.delete('/api/images/'+image_id,headers=auth_headers).status_code==204
    again,_=start(client,auth_headers,content,'test.'+kind)
    assert again['id']!=job['id'] and again['status']=='uploading'

def test_chunks_reject_wrong_bytes_and_foreign_owner(client,auth_headers,mock_image_data):
    job,parts=start(client,auth_headers,mock_image_data.getvalue(),'test.jpg')
    url='/api/image-uploads/'+job['id']
    assert client.put(url+'/parts/0',headers=auth_headers,content=b'wrong').status_code==422
    assert client.get(url).status_code==401
    from services.auth import create_access_token
    other={'Authorization':'Bearer '+create_access_token({'user_id':999,'tenant_id':1,'username':'other'})}
    assert client.get(url,headers=other).status_code in (401,404)
    assert client.get(url,headers=auth_headers).json()['uploaded_parts']==[]

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['zip','tar'])
async def test_archive_traversal_rejected_before_any_import(client,auth_headers,mock_image_data,test_engine,kind):
    from services.image_uploads import process_one
    data=archive(kind,[('safe.jpg',mock_image_data.getvalue()),('../escape.jpg',mock_image_data.getvalue())])
    job,parts=start(client,auth_headers,data,'unsafe.'+kind)
    url='/api/image-uploads/'+job['id']
    for index,part in enumerate(parts):assert client.put(url+'/parts/'+str(index),headers=auth_headers,content=part).status_code==200
    client.post(url+'/complete',headers=auth_headers)
    await process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    assert client.get(url,headers=auth_headers).json()['status']=='failed'
    assert client.get('/api/images',headers=auth_headers).json()['total']==0

@pytest.mark.asyncio
async def test_upload_uses_collection_threshold_and_shared_scene_queue(client,auth_headers,mock_image_data,test_engine):
    from services import image_uploads as module
    from models import LibraryImage
    from sqlalchemy import select
    config=client.get('/api/images/ingest-settings',headers=auth_headers).json()
    config.update(min_long_edge=64,classification_enabled=True,scenes=['工厂'])
    assert client.put('/api/images/ingest-settings',headers=auth_headers,json=config).status_code==200
    async def run(name):
        job,parts=start(client,auth_headers,mock_image_data.getvalue(),'test.jpg',name)
        url='/api/image-uploads/'+job['id']
        for i,part in enumerate(parts):assert client.put(url+'/parts/'+str(i),headers=auth_headers,content=part).status_code==200
        client.post(url+'/complete',headers=auth_headers)
        await module.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
        return client.get(url,headers=auth_headers).json()
    group=client.post('/api/images/collections',headers=auth_headers,json={'name':'边长等于阈值'}).json()
    path='/api/images/ingest-settings?collection_id='+group['id']
    assert client.get(path,headers=auth_headers).json()['classification_enabled'] is False
    assert client.put(path,headers=auth_headers,json=config).status_code==200
    accepted=await run('边长等于阈值')
    assert accepted['result']['imported']==1
    async with async_sessionmaker(test_engine,expire_on_commit=False)() as db:
        image=await db.scalar(select(LibraryImage))
        assert image.scene_status=='pending' and image.scene_config['config']['scenes']==['工厂']
        assert image.scene_config['collection_id']==accepted['collection_id']
    config['min_long_edge']=65
    client.put('/api/images/ingest-settings',headers=auth_headers,json=config)
    filtered=await run('边长不足阈值')
    assert filtered['result']['filtered']==1 and filtered['result']['imported']==0
    assert filtered['min_long_edge']==65
    receipt=next(item for item in client.get('/api/image-uploads',headers=auth_headers).json() if item['id']==filtered['id'])
    assert receipt['min_long_edge']==65 and receipt['result']['filtered']==1
    content=mock_image_data.getvalue()
    retry=client.post('/api/image-uploads',headers=auth_headers,json={'filename':'test.jpg','size':len(content),
        'chunk_hashes':[hashlib.sha256(content).hexdigest()],'collection_name':'边长不足阈值','min_long_edge':0}).json()
    assert retry['id']!=filtered['id'] and retry['min_long_edge']==0
    retry_url='/api/image-uploads/'+retry['id']
    assert client.put(retry_url+'/parts/0',headers=auth_headers,content=content).status_code==200
    assert client.post(retry_url+'/complete',headers=auth_headers).status_code==200
    await module.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    assert client.get(retry_url,headers=auth_headers).json()['result']['imported']==1
    assert client.get('/api/images',headers=auth_headers).json()['total']==1

@pytest.mark.asyncio
async def test_expiry_cleans_only_receipts_and_staging_not_originals(client,auth_headers,mock_image_data,test_engine):
    from datetime import datetime,timedelta
    from models import ImageUpload
    from services import image_uploads as module
    from uuid import UUID
    job,parts=start(client,auth_headers,mock_image_data.getvalue(),'test.jpg')
    url='/api/image-uploads/'+job['id']
    for i,part in enumerate(parts):client.put(url+'/parts/'+str(i),headers=auth_headers,content=part)
    client.post(url+'/complete',headers=auth_headers)
    sessions=async_sessionmaker(test_engine,expire_on_commit=False)
    await module.process_one(sessions)
    image_id=client.get(url+'/images',headers=auth_headers).json()['image_ids'][0]
    async with sessions() as db:
        row=await db.get(ImageUpload,UUID(job['id']));row.updated_at=datetime.utcnow()-timedelta(days=8);await db.commit()
    await module.expire(sessions)
    assert client.get(url,headers=auth_headers).status_code==404
    assert not module.staging(job['id']).exists()
    assert client.get('/api/images/'+image_id+'/content',headers=auth_headers).status_code==200

@pytest.mark.asyncio
@pytest.mark.parametrize('role,expected', [('user',403),('admin',404)])
async def test_real_other_user_cannot_read_or_write_parts(client,auth_headers,mock_image_data,test_db,test_user,role,expected):
    from models import User
    from services.auth import create_access_token
    test_db.add(User(id=2,tenant_id=1,username='other',email='other@test.local',password_hash=test_user.password_hash,role=role,is_active=True))
    await test_db.commit()
    job,parts=start(client,auth_headers,mock_image_data.getvalue(),'test.jpg')
    headers={'Authorization':'Bearer '+create_access_token({'user_id':2,'tenant_id':1,'username':'other'})}
    url='/api/image-uploads/'+job['id']
    for response in (client.get(url,headers=headers),client.put(url+'/parts/0',headers=headers,content=parts[0]),
        client.post(url+'/complete',headers=headers),client.delete(url,headers=headers)):
        assert response.status_code==expected

@pytest.mark.asyncio
async def test_corrupt_staged_chunk_is_requested_again(client,auth_headers,mock_image_data,test_engine):
    from services import image_uploads as module
    job,parts=start(client,auth_headers,mock_image_data.getvalue(),'test.jpg')
    url='/api/image-uploads/'+job['id']
    client.put(url+'/parts/0',headers=auth_headers,content=parts[0]);client.post(url+'/complete',headers=auth_headers)
    (module.staging(job['id'])/'0.part').write_bytes(b'x'*len(parts[0]))
    await module.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    status=client.get(url,headers=auth_headers).json()
    assert status['status']=='failed' and status['uploaded_parts']==[]
    assert client.put(url+'/parts/0',headers=auth_headers,content=parts[0]).status_code==200
    client.post(url+'/complete',headers=auth_headers)
    await module.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    assert client.get(url,headers=auth_headers).json()['status']=='completed'

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['zip','tar'])
async def test_restart_restores_cursor_without_duplicate_import(client,auth_headers,mock_image_data,test_engine,monkeypatch,kind):
    from models import ImageUpload
    from services import image_uploads as module
    from uuid import UUID
    content=archive(kind,[('a.jpg',mock_image_data.getvalue()),('same.jpg',mock_image_data.getvalue())])
    file_key='0'*64
    batch=client.post('/api/image-uploads/batches',headers=auth_headers,
        json={'collection_name':'续传测试','file_keys':[file_key]}).json()['id']
    job=client.post('/api/image-uploads',headers=auth_headers,json={'filename':f'test.{kind}','size':len(content),
        'chunk_hashes':[hashlib.sha256(content).hexdigest()],'collection_name':'续传测试','batch_id':batch,'file_key':file_key}).json()
    url='/api/image-uploads/'+job['id']
    client.put(url+'/parts/0',headers=auth_headers,content=content)
    client.post(url+'/complete',headers=auth_headers)
    original=module.next_item;calls=[]
    def fail_second(iterator):
        calls.append(1)
        if len(calls)==2:raise OSError('test interrupted import')
        return original(iterator)
    monkeypatch.setattr(module,'next_item',fail_second)
    sessions=async_sessionmaker(test_engine,expire_on_commit=False)
    await module.process_one(sessions)
    assert client.get(url,headers=auth_headers).json()['cursor']==1
    async with sessions() as db:
        row=await db.get(ImageUpload,UUID(job['id']));row.status='processing';await db.commit()
    summary=client.get('/api/image-uploads/batches',headers=auth_headers).json()[0]
    assert summary['completed']==0
    assert summary['archive_progress']=={'processed':1,'total':2}
    monkeypatch.setattr(module,'next_item',original)
    import database
    monkeypatch.setattr(database,'AsyncSessionLocal',sessions)
    await module.start_worker()
    try:
        for _ in range(100):
            if client.get(url,headers=auth_headers).json()['status']=='completed':break
            await asyncio.sleep(.03)
        status=client.get(url,headers=auth_headers).json()
        assert status['status']=='completed' and status['result']['imported']==1 and status['result']['duplicates']==1
    finally:await module.stop_worker()

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['zip','tar'])
async def test_archive_link_and_expansion_limits(client,auth_headers,mock_image_data,test_engine,monkeypatch,kind):
    from services import image_uploads as module
    from config.settings import settings
    async def run(data,name):
        job,parts=start(client,auth_headers,data,name);url='/api/image-uploads/'+job['id']
        for i,part in enumerate(parts):client.put(url+'/parts/'+str(i),headers=auth_headers,content=part)
        client.post(url+'/complete',headers=auth_headers)
        await module.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
        assert client.get(url,headers=auth_headers).json()['status']=='failed'
    output=io.BytesIO()
    if kind=='zip':
        with zipfile.ZipFile(output,'w') as file:
            info=zipfile.ZipInfo('link.jpg');info.create_system=3;info.external_attr=(0o120777<<16)
            file.writestr(info,'../outside.jpg')
    else:
        with tarfile.open(fileobj=output,mode='w') as file:
            info=tarfile.TarInfo('link.jpg');info.type=tarfile.SYMTYPE;info.linkname='../outside.jpg';file.addfile(info)
    await run(output.getvalue(),'link.'+kind)
    monkeypatch.setattr(settings,'MAX_IMAGE_ARCHIVE_EXPANDED_SIZE',10)
    await run(archive(kind,[('large.txt',b'x'*100)]),'expanded.'+kind)
    assert client.get('/api/images',headers=auth_headers).json()['total']==0


def test_default_archive_limit_accepts_200000_entries_and_rejects_more(client,auth_headers,monkeypatch):
    from services import image_uploads as module
    from config.settings import Settings,settings
    monkeypatch.setattr(settings,'MAX_ARCHIVE_FILES',Settings.model_fields['MAX_ARCHIVE_FILES'].default)
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as file:
        file.writestr('classes.txt','fixture')
        file.writestr('images/',b'')
        for index in range(99999):
            file.writestr(f'images/{index}.jpg',b'')
            file.writestr(f'labels/{index}.txt',b'')
    module.check_archive(output,'zip')
    assert client.get('/api/image-uploads/config',headers=auth_headers).json()['max_entries']==200000
    with zipfile.ZipFile(output,'a') as file:file.writestr('overflow/',b'')
    with pytest.raises(ValueError,match='条目数量.*200001.*200000'):
        module.check_archive(output,'zip')


@pytest.mark.parametrize('kind',['zip','tar'])
def test_archive_limit_errors_distinguish_entry_count_and_expanded_size(monkeypatch,kind):
    from services import image_uploads as module
    from config.settings import settings
    monkeypatch.setattr(settings,'MAX_ARCHIVE_FILES',1)
    with pytest.raises(ValueError,match='条目数量.*1'):
        module.check_archive(io.BytesIO(archive(kind,[('one.txt',b''),('two.txt',b'')])),kind)
    monkeypatch.setattr(settings,'MAX_ARCHIVE_FILES',2)
    monkeypatch.setattr(settings,'MAX_IMAGE_ARCHIVE_EXPANDED_SIZE',10)
    with pytest.raises(ValueError,match='展开大小.*10'):
        module.check_archive(io.BytesIO(archive(kind,[('one.txt',b'x'*11)])),kind)
    module.check_archive(io.BytesIO(archive(kind,[('one.txt',b'x'*10),('two.txt',b'')])),kind)
