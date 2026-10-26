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
    assert await module.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    result=client.get(url,headers=auth_headers).json()
    assert result['status']=='completed' and result['result']['imported']==1 and result['result']['duplicates']==1
    assert client.get('/api/images',headers=auth_headers).json()['total']==1
    finished,_=start(client,auth_headers,content,'test.'+kind)
    assert finished['id']==job['id'] and finished['status']=='completed'
    image_id=client.get('/api/images',headers=auth_headers).json()['items'][0]['id']
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
    async with sessions() as db:
        row=await db.get(ImageUpload,UUID(job['id']));row.updated_at=datetime.utcnow()-timedelta(days=8);await db.commit()
    await module.expire(sessions)
    assert client.get(url,headers=auth_headers).status_code==404
    assert not module.staging(job['id']).exists()
    image=client.get('/api/images',headers=auth_headers).json()['items'][0]
    assert client.get('/api/images/'+image['id']+'/content',headers=auth_headers).status_code==200

@pytest.mark.asyncio
async def test_real_other_user_cannot_read_or_write_parts(client,auth_headers,mock_image_data,test_db,test_user):
    from models import User
    from services.auth import create_access_token
    test_db.add(User(id=2,tenant_id=1,username='other',email='other@test.local',password_hash=test_user.password_hash,role='viewer',is_active=True))
    await test_db.commit()
    job,parts=start(client,auth_headers,mock_image_data.getvalue(),'test.jpg')
    headers={'Authorization':'Bearer '+create_access_token({'user_id':2,'tenant_id':1,'username':'other'})}
    url='/api/image-uploads/'+job['id']
    for response in (client.get(url,headers=headers),client.put(url+'/parts/0',headers=headers,content=parts[0]),
        client.post(url+'/complete',headers=headers),client.delete(url,headers=headers)):
        assert response.status_code==404

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
async def test_restart_restores_cursor_without_duplicate_import(client,auth_headers,mock_image_data,test_engine,monkeypatch):
    from models import ImageUpload
    from services import image_uploads as module
    from uuid import UUID
    content=archive('zip',[('a.jpg',mock_image_data.getvalue()),('same.jpg',mock_image_data.getvalue())])
    job,parts=start(client,auth_headers,content)
    url='/api/image-uploads/'+job['id']
    for i,part in enumerate(parts):client.put(url+'/parts/'+str(i),headers=auth_headers,content=part)
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
    monkeypatch.setattr(settings,'MAX_ARCHIVE_EXPANDED_SIZE',10)
    await run(archive(kind,[('large.txt',b'x'*100)]),'expanded.'+kind)
    assert client.get('/api/images',headers=auth_headers).json()['total']==0


def test_default_archive_limit_accepts_images_and_yolo_labels_together(monkeypatch):
    from services import image_uploads as module
    from config.settings import Settings,settings
    monkeypatch.setattr(settings,'MAX_ARCHIVE_FILES',Settings.model_fields['MAX_ARCHIVE_FILES'].default)
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as file:
        file.writestr('classes.txt','fixture')
        for index in range(7684):
            file.writestr(f'images/{index}.jpg',b'')
            file.writestr(f'labels/{index}.txt',b'')
    module.check_archive(output,'zip')


@pytest.mark.parametrize('kind',['zip','tar'])
def test_archive_limit_errors_distinguish_entry_count_and_expanded_size(monkeypatch,kind):
    from services import image_uploads as module
    from config.settings import settings
    monkeypatch.setattr(settings,'MAX_ARCHIVE_FILES',1)
    with pytest.raises(ValueError,match='条目数量.*1'):
        module.check_archive(io.BytesIO(archive(kind,[('one.txt',b''),('two.txt',b'')])),kind)
    monkeypatch.setattr(settings,'MAX_ARCHIVE_FILES',2)
    monkeypatch.setattr(settings,'MAX_ARCHIVE_EXPANDED_SIZE',10)
    with pytest.raises(ValueError,match='展开大小.*10'):
        module.check_archive(io.BytesIO(archive(kind,[('one.txt',b'x'*11)])),kind)
    module.check_archive(io.BytesIO(archive(kind,[('one.txt',b'x'*10),('two.txt',b'')])),kind)
