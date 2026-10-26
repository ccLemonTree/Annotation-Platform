"""Real locks and migration on a uniquely owned temporary PostgreSQL database."""
import asyncio
import os
from uuid import uuid4
from pathlib import Path
import psycopg2
from psycopg2 import sql
import pytest
from fastapi import HTTPException
from sqlalchemy import select, func, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from alembic import command
from alembic.config import Config
from config.settings import settings
from models import Base, Tenant, User, Project, Asset, Annotation, AnnotationEvent
from api.annotation_routes import create_annotation, update_annotation, review_annotation
from schemas.annotation import AnnotationCreate, AnnotationUpdate, AnnotationReview
from services.auth import get_password_hash

@pytest.mark.skipif(os.environ.get('P2_POSTGRES_INTEGRATION') != '1', reason='explicit isolated PostgreSQL required')
@pytest.mark.asyncio
async def test_migration_and_concurrent_version_writes(tmp_path, monkeypatch):
    name = 'codex_p2_' + uuid4().hex
    admin = psycopg2.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER,
        password=settings.DB_PASSWORD, dbname=settings.DB_NAME)
    admin.autocommit = True
    created = False; engine = None
    try:
        with admin.cursor() as cursor: cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        created = True
        engine = create_async_engine(make_url(settings.DATABASE_URL).set(database=name), poolclass=NullPool)
        async with engine.begin() as conn:
            tables = [Base.metadata.tables[t] for t in ('tenants','users','projects','images','image_collections','image_collection_members','image_ingest_settings','assets','annotations','automation_jobs','automation_items')]
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
            await conn.execute(text('DROP INDEX uq_annotation_latest'))
            await conn.execute(text('DROP INDEX uq_annotation_asset_version'))
        monkeypatch.setattr(settings, 'DB_NAME', name)
        config = Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
        await asyncio.to_thread(command.stamp, config, '006')
        await asyncio.to_thread(command.upgrade, config, '007')
        sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with sessions() as db:
            db.add(Tenant(id=1,tenant_name='isolated')); await db.flush()
            user = User(id=1,tenant_id=1,username='isolated',email='p2@test.local',role='admin',
                password_hash=get_password_hash('isolated'))
            db.add(user)
            project = Project(tenant_id=1,project_name='p2',task_type='detection',total_assets=1,annotated_count=0,reviewed_count=0)
            db.add(project); await db.flush()
            asset = Asset(project_id=project.id,original_name='sample.png',blob_path='sample.png',content_hash='sample',
                file_size=1,width=100,height=100,format='PNG',source_type='upload',annotation_status='pending')
            db.add(asset); await db.commit()
        data = {'boxes':[{'x':10,'y':10,'width':20,'height':20,'label':'人'}]}
        async def initial():
            async with sessions() as db:
                try: return await create_annotation(AnnotationCreate(asset_id=asset.id, annotation_data=data),db=db,current_user=user)
                except HTTPException as e: await db.rollback(); return e.status_code
        results = await asyncio.gather(*(initial() for _ in range(5)))
        winners = [r for r in results if isinstance(r, Annotation)]
        assert len(winners) == 1 and results.count(409) == 4
        first = winners[0]
        async def modify():
            async with sessions() as db:
                try: return await update_annotation(first.id, AnnotationUpdate(base_version=1, annotation_data=data),db=db,current_user=user)
                except HTTPException as e: await db.rollback(); return e.status_code
        results = await asyncio.gather(*(modify() for _ in range(5)))
        winners = [r for r in results if isinstance(r, Annotation)]
        assert len(winners) == 1 and results.count(409) == 4
        async def approve():
            async with sessions() as db:
                return await review_annotation(winners[0].id,AnnotationReview(approved=True),db=db,current_user=user)
        await asyncio.gather(*(approve() for _ in range(5)))
        async with sessions() as db:
            assert await db.scalar(select(func.count(Annotation.id))) == 2
            assert await db.scalar(select(func.count(Annotation.id)).where(Annotation.is_latest.is_(True))) == 1
            p = await db.get(Project,project.id)
            assert p.annotated_count == p.reviewed_count == 1
            assert await db.scalar(select(func.count(AnnotationEvent.id))) == 7
            assert await db.scalar(text('SELECT version_num FROM alembic_version')) == '007'
        from api.project_routes import update_project
        from schemas.project import ProjectUpdate
        async def rename():
            async with sessions() as db:
                return await update_project(project.id, ProjectUpdate(
                    project_name='renamed p2', label_schema={'labels':['person']},
                    label_renames={'人':'person'}, base_label_schema=None),
                    tenant_id=1, db=db, current_user=user)
        async def stale_edit():
            async with sessions() as db:
                try:
                    return await update_annotation(winners[0].id,
                        AnnotationUpdate(base_version=2, annotation_data=data),db=db,current_user=user)
                except HTTPException as error:
                    await db.rollback(); return error.status_code
        await asyncio.wait_for(asyncio.gather(rename(), stale_edit()), timeout=15)
        async with sessions() as db:
            p = await db.get(Project,project.id)
            assert p.project_name == 'renamed p2' and p.label_schema == {'labels':['person']}
            latest = await db.scalar(select(Annotation).where(Annotation.asset_id==asset.id,Annotation.is_latest.is_(True)))
            assert latest.annotation_data['boxes'][0]['label']=='person'
            assert p.annotated_count==1 and p.reviewed_count==0
        # Simulate the pre-008 assets schema only inside this owned temporary DB.
        async with engine.begin() as conn:
            from integrations.smarteye.runtime import metadata
            await conn.run_sync(metadata.create_all)
            baseline=(await conn.execute(select(Annotation.__table__).order_by(Annotation.id))).mappings().all()
            await conn.execute(text('ALTER TABLE assets DROP COLUMN image_id'))
            await conn.execute(text('DROP TABLE image_collection_members'))
            await conn.execute(text('DROP TABLE image_collections'))
            await conn.execute(text('DROP TABLE images'))
        await asyncio.to_thread(command.upgrade,config,'008')
        from models import LibraryImage
        from services.image_library import attach_images
        async with sessions() as db:
            original=await db.get(Asset,asset.id)
            image=await db.scalar(select(LibraryImage))
            assert original.image_id==image.id and original.blob_path==image.blob_path
            after=(await db.execute(select(Annotation.__table__).order_by(Annotation.id))).mappings().all()
            assert baseline==after
            other=Project(tenant_id=1,project_name='shared original',task_type='detection',total_assets=0,annotated_count=0,reviewed_count=0)
            db.add(other);await db.commit()
        async def attach():
            async with sessions() as db:
                _,added=await attach_images(db,other.id,1,[image.id])
                await db.commit();return added
        added=await asyncio.wait_for(asyncio.gather(attach(),attach()),timeout=15)
        assert sorted(added)==[0,1]
        async with sessions() as db:
            assert await db.scalar(select(func.count(Asset.id)).where(Asset.project_id==other.id))==1
            assert (await db.get(Project,other.id)).total_assets==1
        await asyncio.to_thread(command.upgrade,config,'009')
        from models import ImageCollection,ImageCollectionMember
        from services.image_library import assign_collection
        async with sessions() as db:
            group=await db.scalar(select(ImageCollection))
            assert group.name=='历史图片'
            assert await db.scalar(select(func.count()).select_from(ImageCollectionMember))==1
            assert baseline==(await db.execute(select(Annotation.__table__).order_by(Annotation.id))).mappings().all()
        async def classify():
            async with sessions() as db:
                _,added=await assign_collection(db,1,[image.id],'1013')
                await db.commit();return added
        assert sorted(await asyncio.wait_for(asyncio.gather(classify(),classify()),timeout=15))==[0,1]
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(ImageCollectionMember))==2
    finally:
        if engine: await engine.dispose()
        if created:
            assert name.startswith('codex_p2_')
            with admin.cursor() as cursor: cursor.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
        admin.close()
