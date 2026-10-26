"""011 is non-destructive on an actual isolated 010 PostgreSQL schema."""
import asyncio,os
from pathlib import Path
from uuid import uuid4
import psycopg2,pytest
from psycopg2 import sql
from alembic import command
from alembic.config import Config
from sqlalchemy import select,text,insert
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from sqlalchemy.pool import NullPool
from models import Base,Tenant,LibraryImage,ImageIngestSettings,ImageCollection,ImageCollectionMember
from schemas.image_ingest import ImageIngestConfig
from config.settings import settings

@pytest.mark.skipif(os.environ.get('P3_POSTGRES_INTEGRATION')!='1',reason='explicit isolated PostgreSQL required')
@pytest.mark.asyncio
async def test_011_preserves_shared_scene_and_snapshots_collection_defaults(monkeypatch):
    name='codex_p3_'+uuid4().hex
    admin=psycopg2.connect(host=settings.DB_HOST,port=settings.DB_PORT,user=settings.DB_USER,
        password=settings.DB_PASSWORD,dbname=settings.DB_NAME);admin.autocommit=True
    engine=None;created=False
    try:
        with admin.cursor() as cur:cur.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        created=True
        engine=create_async_engine(make_url(settings.DATABASE_URL).set(database=name),poolclass=NullPool)
        a,b,completed,pending=uuid4(),uuid4(),uuid4(),uuid4()
        config=ImageIngestConfig(classification_enabled=True,min_long_edge=720,base_url='http://model.test/v1').model_dump()
        async with engine.begin() as conn:
            await conn.run_sync(lambda c:Base.metadata.create_all(c,tables=[Tenant.__table__,LibraryImage.__table__,ImageIngestSettings.__table__,ImageCollection.__table__,ImageCollectionMember.__table__]))
            await conn.execute(insert(Tenant).values(id=1,tenant_name='isolated'))
            await conn.execute(insert(ImageIngestSettings).values(tenant_id=1,config=config,revision=3))
            for id,group in ((a,'a'),(b,'b')):await conn.execute(insert(ImageCollection).values(id=id,tenant_id=1,name=group))
            for id,status in ((completed,'completed'),(pending,'pending')):
                await conn.execute(insert(LibraryImage).values(id=id,tenant_id=1,blob_path='same.png',content_hash=str(id),
                    original_name='same.png',file_size=100,source_type='upload',scene_status=status,
                    scene_labels=['工厂'] if status=='completed' else [],scene_attempt=2,scene_metadata={'model':'old-model'}))
                for group in (a,b):await conn.execute(insert(ImageCollectionMember).values(collection_id=group,image_id=id))
            await conn.execute(text('ALTER TABLE image_collections DROP COLUMN ingest_config'))
            await conn.execute(text('ALTER TABLE image_collections DROP COLUMN ingest_revision'))
            await conn.execute(text('ALTER TABLE images DROP COLUMN scene_config'))
        monkeypatch.setattr(settings,'DB_NAME',name)
        cfg=Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
        await asyncio.to_thread(command.stamp,cfg,'010')
        await asyncio.to_thread(command.upgrade,cfg,'011')
        sessions=async_sessionmaker(engine,expire_on_commit=False)
        async with sessions() as db:
            old=await db.get(LibraryImage,completed)
            assert old.scene_labels==['工厂'] and old.scene_metadata=={'model':'old-model'} and old.scene_config is None
            assert (await db.get(LibraryImage,pending)).scene_config['config']==config
            groups=(await db.scalars(select(ImageCollection))).all()
            assert len(groups)==2 and all(group.ingest_config==config for group in groups)
            groups[0].ingest_config={**config,'min_long_edge':1080};await db.commit()
            assert groups[1].ingest_config['min_long_edge']==720
            assert len((await db.scalars(select(ImageCollectionMember))).all())==4
        # Re-running the idempotent upgrade must not overwrite a customized collection.
        await asyncio.to_thread(command.stamp,cfg,'010')
        await asyncio.to_thread(command.upgrade,cfg,'011')
        async with sessions() as db:
            assert sorted((await db.scalars(select(ImageCollection.ingest_config))).all(),key=lambda x:x['min_long_edge'])[1]['min_long_edge']==1080
    finally:
        if engine:await engine.dispose()
        if created:
            assert name.startswith('codex_p3_')
            with admin.cursor() as cur:cur.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
        admin.close()
