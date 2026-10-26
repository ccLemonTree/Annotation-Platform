"""Opt-in PostgreSQL check; creates and removes only its own temporary database."""
import asyncio
import os
from io import BytesIO
from uuid import uuid4

import psycopg2
from psycopg2 import sql
import pytest
from fastapi import UploadFile, HTTPException
from PIL import Image
from sqlalchemy import select, func
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from api.asset_routes import register_upload, delete_asset
from api.batch_routes import batch_delete_assets
from config.settings import settings
from models import Base, Tenant, Project, Asset, TaskType
from services.storage import reset_storage


@pytest.mark.skipif(os.environ.get("P0_POSTGRES_INTEGRATION") != "1", reason="requires explicit temporary PostgreSQL database run")
@pytest.mark.asyncio
@pytest.mark.parametrize("deletion_mode", ["single", "batch"])
async def test_postgres_concurrent_upload_deduplicates(tmp_path, monkeypatch, deletion_mode):
    name = "codex_p0_" + uuid4().hex
    connection = psycopg2.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER,
                                  password=settings.DB_PASSWORD, dbname=settings.DB_NAME)
    connection.autocommit = True
    created = False
    engine = None
    try:
        with connection.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        created = True
        engine = create_async_engine(make_url(settings.DATABASE_URL).set(database=name))
        tables = [Base.metadata.tables[t] for t in ("tenants", "users", "projects", "images", "image_collections", "image_collection_members", "image_ingest_settings", "assets", "annotations")]
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
        sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        async with sessions() as session:
            session.add(Tenant(id=1, tenant_name="temporary test"))
            await session.flush()
            project = Project(tenant_id=1, project_name="concurrent", task_type=TaskType.DETECTION)
            session.add(project)
            await session.commit()
            project_id = project.id
        monkeypatch.setattr(settings, "STORAGE_TYPE", "local")
        monkeypatch.setattr(settings, "STORAGE_LOCAL_PATH", str(tmp_path / "storage"))
        reset_storage()
        image = BytesIO()
        Image.new("RGB", (64, 48), "red").save(image, format="JPEG")

        async def upload():
            async with sessions() as session:
                asset = await register_upload(UploadFile(filename="sample.jpg", file=BytesIO(image.getvalue())), project_id, session)
                await session.commit()
                return asset.id

        ids = await asyncio.gather(*(upload() for _ in range(5)))
        assert len(set(ids)) == 1
        async with sessions() as session:
            assert await session.scalar(select(func.count(Asset.id))) == 1
            assert (await session.get(Project, project_id)).total_assets == 1
        assert len(list((tmp_path / "storage").rglob("*.jpg"))) == 1

        async def delete():
            async with sessions() as session:
                if deletion_mode == "batch":
                    return await batch_delete_assets([ids[0]], tenant_id=1, db=session, current_user=None)
                return await delete_asset(ids[0], tenant_id=1, db=session, current_user=None)

        deleted = await asyncio.gather(*(delete() for _ in range(5)), return_exceptions=True)
        assert sum(not isinstance(item, BaseException) for item in deleted) == 1
        assert all(not isinstance(item, BaseException) or isinstance(item, HTTPException) and item.status_code == 404 for item in deleted)
        async with sessions() as session:
            assert (await session.get(Project, project_id)).total_assets == 0
    finally:
        reset_storage()
        if engine is not None:
            await engine.dispose()
        if created:
            assert name.startswith("codex_p0_") and name != settings.DB_NAME
            with connection.cursor() as cursor:
                cursor.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(name)))
        connection.close()
