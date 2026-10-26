"""Actual 012 migration + TCP transport on an owned disposable PostgreSQL database."""
import asyncio,hashlib,io,os,socket,zipfile
from pathlib import Path
from uuid import uuid4
import httpx,psycopg2,pytest,uvicorn
from fastapi import FastAPI
from psycopg2 import sql
from alembic import command
from alembic.config import Config
from sqlalchemy import insert,text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from sqlalchemy.pool import NullPool
from models import Base,Tenant,User,LibraryImage,ImageCollection,ImageCollectionMember,ImageIngestSettings,Project,Asset
from config.settings import settings
from database import get_db_session
from services.auth import create_access_token
from services.storage import reset_storage

@pytest.mark.skipif(os.environ.get('P3_POSTGRES_INTEGRATION')!='1',reason='explicit isolated PostgreSQL required')
@pytest.mark.asyncio
async def test_012_and_real_http_resume(tmp_path,monkeypatch,mock_image_data):
    from api.image_upload_routes import router as uploads_router
    from api.image_routes import router as images_router
    from api.asset_routes import router as assets_router
    from services import image_uploads as uploads
    name='codex_p3_'+uuid4().hex;engine=None;created=False;server=None;serving=None;listener=None
    admin=psycopg2.connect(host=settings.DB_HOST,port=settings.DB_PORT,user=settings.DB_USER,password=settings.DB_PASSWORD,dbname=settings.DB_NAME);admin.autocommit=True
    try:
        with admin.cursor() as cursor:cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        created=True
        engine=create_async_engine(make_url(settings.DATABASE_URL).set(database=name),poolclass=NullPool)
        async with engine.begin() as connection:
            await connection.run_sync(lambda c:Base.metadata.create_all(c,tables=[Tenant.__table__,User.__table__,LibraryImage.__table__,ImageCollection.__table__,ImageCollectionMember.__table__,ImageIngestSettings.__table__,Project.__table__,Asset.__table__]))
            await connection.execute(text('ALTER TABLE images DROP COLUMN imported_annotation'))
            await connection.execute(insert(Tenant).values(id=1,tenant_name='isolated'))
            await connection.execute(insert(User).values(id=1,tenant_id=1,username='isolated',email='isolated@test.local',password_hash='not-a-login-password',role='admin',is_active=True))
        monkeypatch.setattr(settings,'DB_NAME',name)
        cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
        cfg.set_main_option('script_location',str(Path(__file__).resolve().parents[1]/'alembic'))
        await asyncio.to_thread(command.stamp,cfg,'011');await asyncio.to_thread(command.upgrade,cfg,'head')
        monkeypatch.setattr(settings,'STORAGE_TYPE','local');monkeypatch.setattr(settings,'STORAGE_LOCAL_PATH',str(tmp_path/'storage'))
        monkeypatch.setattr(uploads,'CHUNK_SIZE',128);reset_storage()
        sessions=async_sessionmaker(engine,expire_on_commit=False)
        async def db():
            async with sessions() as session:yield session
        app=FastAPI();app.include_router(uploads_router,prefix='/api/image-uploads');app.include_router(images_router,prefix='/api/images');app.include_router(assets_router,prefix='/api/assets')
        app.dependency_overrides[get_db_session]=db
        listener=socket.socket();listener.bind(('127.0.0.1',0));listener.listen(32)
        url='http://127.0.0.1:'+str(listener.getsockname()[1])
        server=uvicorn.Server(uvicorn.Config(app,log_level='error',lifespan='off'));serving=asyncio.create_task(server.serve(sockets=[listener]))
        for _ in range(100):
            if server.started:break
            await asyncio.sleep(.02)
        assert server.started
        output=io.BytesIO()
        with zipfile.ZipFile(output,'w') as archive:
            archive.writestr('nested/a.jpg',mock_image_data.getvalue());archive.writestr('same.jpg',mock_image_data.getvalue())
        data=output.getvalue();parts=[data[i:i+128] for i in range(0,len(data),128)]
        payload={'filename':'tcp.zip','size':len(data),'collection_name':'HTTP fixture','chunk_hashes':[hashlib.sha256(part).hexdigest() for part in parts]}
        headers={'Authorization':'Bearer '+create_access_token({'user_id':1,'tenant_id':1,'username':'isolated'})}
        async with httpx.AsyncClient(base_url=url,headers=headers) as client:
            first,second=await asyncio.gather(client.post('/api/image-uploads',json=payload),client.post('/api/image-uploads',json=payload))
            assert first.status_code==second.status_code==200
            assert first.json()['id']==second.json()['id']
            id=first.json()['id'];path='/api/image-uploads/'+id
            assert (await client.put(path+'/parts/0',content=parts[0])).status_code==200
        # New TCP client simulates interruption/reconnection with no browser state.
        async with httpx.AsyncClient(base_url=url,headers=headers) as client:
            resumed=await client.post('/api/image-uploads',json=payload)
            assert resumed.json()['uploaded_parts']==[0]
            for i,part in enumerate(parts[1:],1):assert (await client.put(path+'/parts/'+str(i),content=part)).status_code==200
            assert (await client.post(path+'/complete')).status_code==200
            await uploads.process_one(sessions)
            result=(await client.get(path)).json();assert result['status']=='completed' and result['result']['imported']==1 and result['result']['duplicates']==1
            assert (await client.get('/api/images')).json()['total']==1
            target=await client.post('/api/images/collections',json={'name':'HTTP merged'})
            assert target.status_code==201
            from services.image_library import merge_collections
            from uuid import UUID
            async with sessions() as session:
                await merge_collections(session,1,UUID(result['collection_id']),UUID(target.json()['id']));await session.commit()
            cached=await client.post('/api/image-uploads',json={**payload,'collection_name':'HTTP merged'})
            assert cached.status_code==200 and cached.json()['id']==id and cached.json()['status']=='completed'
            assert len((await client.get('/api/images/collections')).json())==1
            assert (await client.delete(path)).status_code==204
            assert (await client.get('/api/images')).json()['total']==1
            project_id=uuid4();ids=[uuid4() for _ in range(25)]
            async with sessions() as session:
                await session.execute(insert(Project).values(id=project_id,tenant_id=1,project_name='selection',task_type='detection'))
                for image_id in ids:
                    await session.execute(insert(LibraryImage).values(id=image_id,tenant_id=1,blob_path='selection.png',content_hash=str(image_id),
                        original_name='selection-'+str(image_id)+'.png',file_size=1,source_type='upload',scene_status='completed',scene_labels=['工厂']))
                await session.execute(insert(Asset).values(project_id=project_id,image_id=ids[0],blob_path='selection.png',content_hash=str(ids[0]),original_name='used.png',file_size=1,source_type='upload'))
                await session.execute(insert(Asset).values(project_id=project_id,blob_path='legacy.png',content_hash='legacy',original_name='legacy.png',file_size=1,source_type='upload'))
                await session.commit()
            params={'q':'selection-','scene':'工厂','scene_status':'completed','source_type':'upload'}
            assert (await client.get('/api/images',params=params)).json()['total']==25
            chosen=await client.get('/api/images/selection',params=params)
            assert chosen.status_code==200 and set(chosen.json()['image_ids'])==set(map(str,ids))
            available=(await client.get('/api/images/selection',params={**params,'exclude_project_id':str(project_id)})).json()
            assert available['total']==24 and str(ids[0]) not in available['image_ids']
            from datetime import datetime
            tied_ids=sorted([uuid4() for _ in range(205)],reverse=True)
            async with sessions() as session:
                await session.execute(insert(Asset),[dict(id=id,project_id=project_id,blob_path='tied.png',original_name='tied.png',
                    content_hash=str(id),file_size=1,source_type='upload',created_at=datetime(2026,1,1)) for id in tied_ids])
                await session.commit()
            ordered=[]
            for page in range(1,4):
                response=await client.get('/api/assets',params={'project_id':str(project_id),'page':page,'page_size':100})
                assert response.status_code==200,response.text
                ordered.extend(item['id'] for item in response.json()['items'])
            assert len(ordered)==len(set(ordered))==207 and ordered[2:]==list(map(str,tied_ids))
            for index in (0,99,100,206):
                response=await client.get('/api/assets/'+ordered[index]+'/neighbors')
                assert response.status_code==200,response.text
                assert response.json()=={'position':index+1,'total':207,
                    'previous_id':ordered[index-1] if index else None,'next_id':ordered[index+1] if index<206 else None}
    finally:
        if server:server.should_exit=True
        if serving:await serving
        if listener:listener.close()
        reset_storage()
        if engine:await engine.dispose()
        if created:
            assert name.startswith('codex_p3_')
            with admin.cursor() as cursor:cursor.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
        admin.close()
