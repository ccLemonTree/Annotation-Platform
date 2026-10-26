"""P2-04 PostgreSQL proof in a uniquely owned database, never business tables."""
import asyncio
import hashlib
from io import BytesIO
import os
from uuid import uuid4

from PIL import Image
import psycopg2
from psycopg2 import sql
import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from api.export_routes import (SplitPreviewRequest, SnapshotRequest, preview_project_splits,
                               update_split_source, freeze_export)
from config.settings import settings
from models import Base, Tenant, User, Project, Asset, Annotation, ExportSnapshot
from services.dataset_splits import SplitSource
from services.detection_export import export_rows
from services.storage import get_storage, reset_storage


@pytest.mark.skipif(os.environ.get('P2_DATASET_POSTGRES_INTEGRATION') != '1', reason='explicit temporary PostgreSQL required')
@pytest.mark.asyncio
async def test_jsonb_source_freeze_evidence_and_source_edit_lock(tmp_path, monkeypatch):
    name = 'codex_p2_dataset_' + uuid4().hex
    admin = psycopg2.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER,
                             password=settings.DB_PASSWORD, dbname='postgres', connect_timeout=5)
    admin.autocommit = True
    created, engine = False, None
    try:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        created = True
        engine = create_async_engine(make_url(settings.DATABASE_URL).set(database=name), poolclass=NullPool)
        async with engine.begin() as connection:
            tables = [Base.metadata.tables[key] for key in ('tenants', 'users', 'projects', 'images', 'assets',
                                                            'annotations', 'annotation_events', 'export_snapshots', 'automation_jobs','automation_items')]
            await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        monkeypatch.setattr(settings, 'STORAGE_TYPE', 'local')
        monkeypatch.setattr(settings, 'STORAGE_LOCAL_PATH', str(tmp_path / 'storage'))
        reset_storage()
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            db.add(Tenant(id=1, tenant_name='isolated P2-04'))
            await db.flush()
            user = User(id=1, tenant_id=1, username='isolated', email='p2-04@test.local', password_hash='unused', role='admin')
            project = Project(tenant_id=1, project_name='P2-04 synthetic', task_type='detection')
            db.add_all([user, project])
            await db.flush()
            assets = []
            for index, color in enumerate(('red', 'blue', 'green')):
                image = BytesIO()
                Image.new('RGB', (100, 50), color).save(image, format='PNG')
                content = image.getvalue()
                key = f'fixture/{index}.png'
                await get_storage().save_file(BytesIO(content), key)
                asset = Asset(project_id=project.id, original_name='same.png', blob_path=key,
                              content_hash=hashlib.sha256(content).hexdigest(), file_size=len(content),
                              width=100, height=50, format='PNG', source_type='upload',
                              device_info={'vendor': 'keep', 'group_id': str(index)})
                db.add(asset)
                await db.flush()
                db.add(Annotation(asset_id=asset.id, annotator_id=user.id, annotation_source='manual', version=1,
                                  is_latest=True, review_status='approved', annotation_data={'boxes': [
                                      {'x': 1, 'y': 1, 'width': 10, 'height': 10, 'label': '人'}]}))
                assets.append(asset)
            await db.commit()
        splits = {str(asset.id): split for asset, split in zip(assets, ('train', 'val', 'test'))}
        async with sessions() as db:
            result = await preview_project_splits(project.id, SplitPreviewRequest(labels=['人'], splits=splits), db=db, user=user)
            assert result['can_freeze'] and result['near_duplicate_checked']
            assert result['images_by_label_by_split']['train'] == {'人': 1}
            assert await db.scalar(text('SELECT pg_typeof(device_info)::text FROM assets LIMIT 1')) == 'jsonb'
            frozen = await freeze_export(project.id, SnapshotRequest(labels=['人'], splits=splits, policy=result['policy']), db=db, user=user)
            assert frozen['manifest']['split_evidence']['policy']['training_budget_per_class'] == 100
        async with sessions() as locker:
            await export_rows(locker, project, True)

            async def edit():
                async with sessions() as db:
                    return await update_split_source(project.id, assets[0].id, SplitSource(parent_image_id='verified-parent'), db=db, user=user)

            task = asyncio.create_task(edit())
            try:
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(asyncio.shield(task), .15)
            finally:
                await locker.rollback()
            assert (await asyncio.wait_for(task, 5))['source']['parent_image_id'] == 'verified-parent'
        async with sessions() as db:
            fresh = await db.get(Asset, assets[0].id)
            assert fresh.device_info == {'vendor': 'keep', 'group_id': '0', 'parent_image_id': 'verified-parent'}
            snapshot = await db.scalar(select(ExportSnapshot))
            assert snapshot.manifest == frozen['manifest']
            assert 'parent_image_id' not in snapshot.manifest['split_evidence']['sources'][str(fresh.id)]
    finally:
        reset_storage()
        if engine:
            await engine.dispose()
        if created:
            assert name.startswith('codex_p2_dataset_')
            with admin.cursor() as cursor:
                cursor.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
                cursor.execute('SELECT count(*) FROM pg_database WHERE datname = %s', (name,))
                assert cursor.fetchone()[0] == 0
            print(f'P2-04 temporary PostgreSQL database removed: {name}')
        admin.close()
