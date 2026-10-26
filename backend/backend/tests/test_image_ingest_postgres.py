"""010 migration and concurrent claims use a uniquely owned disposable PostgreSQL database."""
import asyncio,os
from pathlib import Path
from uuid import uuid4
import httpx,psycopg2,pytest
from psycopg2 import sql
from alembic import command
from alembic.config import Config
from sqlalchemy import select,text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from sqlalchemy.pool import NullPool
from models import Base,Tenant,LibraryImage,ImageIngestSettings
from schemas.image_ingest import ImageIngestConfig
from services.image_scenes import classify_one
from config.settings import settings
from services.storage import reset_storage

@pytest.mark.skipif(os.environ.get('P3_POSTGRES_INTEGRATION')!='1',reason='explicit isolated PostgreSQL required')
@pytest.mark.asyncio
@pytest.mark.parametrize('preexisting_columns',[False,True])
async def test_010_migration_and_scene_claim(tmp_path,monkeypatch,preexisting_columns):
    name='codex_p3_'+uuid4().hex
    admin=psycopg2.connect(host=settings.DB_HOST,port=settings.DB_PORT,user=settings.DB_USER,
        password=settings.DB_PASSWORD,dbname=settings.DB_NAME);admin.autocommit=True
    engine=None;created=False
    try:
        with admin.cursor() as cur:cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        created=True
        engine=create_async_engine(make_url(settings.DATABASE_URL).set(database=name),poolclass=NullPool)
        async with engine.begin() as conn:
            await conn.run_sync(lambda c:Base.metadata.create_all(c,tables=[Tenant.__table__,LibraryImage.__table__]))
            await conn.execute(text("INSERT INTO tenants(id,tenant_name,is_active) VALUES(1,'isolated',true)"))
            image_id=uuid4()
            await conn.execute(text("INSERT INTO images(id,tenant_id,blob_path,content_hash,original_name,file_size,source_type) VALUES(:id,1,'fixture.png','preserved','fixture.png',100,'upload')"),{'id':image_id})
            if not preexisting_columns:
                await conn.execute(text('DROP INDEX idx_image_scene_status'))
                for field in ('scene_labels','scene_status','scene_error','scene_attempt','scene_metadata'):
                    await conn.execute(text('ALTER TABLE images DROP COLUMN '+field))
        monkeypatch.setattr(settings,'DB_NAME',name)
        cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
        await asyncio.to_thread(command.stamp,cfg,'009')
        await asyncio.to_thread(command.upgrade,cfg,'010')
        sessions=async_sessionmaker(engine,expire_on_commit=False)
        async with sessions() as db:
            image=await db.get(LibraryImage,image_id)
            assert image.content_hash=='preserved' and image.scene_status=='unclassified' and image.scene_labels==[]
            image.scene_status='pending'
            db.add(ImageIngestSettings(tenant_id=1,revision=1,config=ImageIngestConfig(classification_enabled=True,
                base_url='http://model.test/v1').model_dump()))
            await db.commit()
        from PIL import Image
        store=tmp_path/'storage';store.mkdir();Image.new('RGB',(40,30)).save(store/'fixture.png')
        monkeypatch.setattr(settings,'STORAGE_TYPE','local');monkeypatch.setattr(settings,'STORAGE_LOCAL_PATH',str(store));reset_storage()
        started=asyncio.Event();release=asyncio.Event();calls=[]
        async def provider(request):
            calls.append(request);started.set();await release.wait()
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"labels":["工厂"]}'}}]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as model:
            first=asyncio.create_task(classify_one(sessions,model))
            await asyncio.wait_for(started.wait(),5)
            assert await classify_one(sessions,model) is False
            release.set();await first
        assert len(calls)==1
        async with sessions() as db:
            assert (await db.get(LibraryImage,image_id)).scene_labels==['工厂']
            matches=(await db.scalars(select(LibraryImage.id).where(LibraryImage.scene_labels.contains(['工厂'])))).all()
            assert matches==[image_id]
    finally:
        reset_storage()
        if engine:await engine.dispose()
        if created:
            assert name.startswith('codex_p3_')
            with admin.cursor() as cur:cur.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
        admin.close()
