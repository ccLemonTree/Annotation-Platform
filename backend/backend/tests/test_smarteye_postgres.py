"""Opt-in PostgreSQL end-to-end test in a disposable, uniquely named database."""
import os
import uuid
from io import BytesIO
from pathlib import Path
import psycopg2
from psycopg2 import sql
import pytest
from PIL import Image
from sqlalchemy import create_engine, select, func, text
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from config.settings import settings
from database import get_db_session
from integrations.smarteye import runtime as module
from integrations.smarteye.schema import upgrade_schema
from integrations.smarteye.runtime import metadata, Runtime
from models import Base, Tenant, User, Project, Asset, TaskType
from services.auth import create_access_token, get_password_hash
from services.storage import reset_storage
from main import app

@pytest.mark.skipif(os.environ.get('P1_POSTGRES_INTEGRATION') != '1', reason='explicit isolated PostgreSQL run required')
def test_postgres_migration_direct_association_and_shared_delete(tmp_path, monkeypatch):
    name = 'codex_p1_' + uuid.uuid4().hex
    admin = psycopg2.connect(host=settings.DB_HOST, port=settings.DB_PORT, user=settings.DB_USER,
                            password=settings.DB_PASSWORD, dbname=settings.DB_NAME)
    admin.autocommit = True
    engine = None
    async_engine = None
    created = False
    try:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        created = True
        url = URL.create('postgresql+psycopg2', username=settings.DB_USER, password=settings.DB_PASSWORD,
                         host=settings.DB_HOST, port=settings.DB_PORT, database=name)
        engine = create_engine(url)
        monkeypatch.setattr(settings, 'DB_NAME', name)
        tables = [Base.metadata.tables[t] for t in ('tenants', 'users', 'projects', 'images', 'image_collections', 'image_collection_members', 'image_ingest_settings', 'assets', 'annotations')]
        with engine.begin() as conn:
            Base.metadata.create_all(conn, tables=tables)
        from alembic import command
        from alembic.config import Config
        config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
        command.stamp(config, '005')
        command.upgrade(config, '006')
        with engine.begin() as conn:
            metadata.create_all(conn)
            upgrade_schema(conn)
            upgrade_schema(conn)
            assert conn.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema='smarteye'")).scalar() == 8
            conn.execute(Tenant.__table__.insert().values(id=1, tenant_name='isolated'))
            conn.execute(User.__table__.insert().values(id=1, tenant_id=1, username='isolated', email='isolated@example.test',
                password_hash=get_password_hash('testpassword'), role='admin', is_active=True))
            project_id = uuid.uuid4()
            conn.execute(Project.__table__.insert().values(id=project_id, tenant_id=1, project_name='direct', task_type='detection'))
        root = tmp_path / 'storage'
        monkeypatch.setattr(module, '_runtime', Runtime(engine, root))
        monkeypatch.setattr(settings, 'STORAGE_LOCAL_PATH', str(root))
        monkeypatch.setattr(settings, 'STORAGE_TYPE', 'local')
        monkeypatch.setattr(settings, 'DEFAULT_TENANT_ID', 1)
        from integrations.smarteye.configuration import connection_kwargs
        from integrations.smarteye.api import db_utils, ai_image, ledger_image_downloader as ledger
        monkeypatch.setattr(db_utils, 'connection_kwargs', lambda: {**connection_kwargs(), 'database': name})
        async_engine = create_async_engine(url.set(drivername='postgresql+asyncpg'), poolclass=NullPool)
        sessions = async_sessionmaker(async_engine, expire_on_commit=False)
        async def isolated():
            async with sessions() as session: yield session
        app.dependency_overrides[get_db_session] = isolated
        token = create_access_token({'user_id': 1, 'tenant_id': 1, 'username': 'isolated'})
        headers = {'Authorization': 'Bearer ' + token, 'X-SmartEye-Purpose': 'annotation',
                   'X-SmartEye-Project': str(project_id)}
        from integrations.smarteye import app as source
        monkeypatch.setattr(source, 'AI_IMAGE_DOWNLOAD_ROOT', root / 'smarteye/ai_gen')
        monkeypatch.setattr(ai_image, 'AI_IMAGE_DOWNLOAD_ROOT', root / 'smarteye/ai_gen')
        image = BytesIO(); Image.new('RGB', (64, 48), 'red').save(image, format='PNG')
        reset_storage()
        with TestClient(app) as client:
            for _ in range(2):
                response = client.post('/api/smarteye/ai-image/upload', headers=headers,
                    files={'files': ('reference.png', image.getvalue(), 'image/png')})
                assert response.status_code == 200, response.text
            asset = response.json()['data']['assets'][0]
            with engine.connect() as conn:
                assert conn.execute(select(func.count(Asset.__table__.c.id))).scalar() == 1
                assert conn.execute(select(Project.__table__.c.total_assets).where(Project.__table__.c.id == project_id)).scalar() == 1
                row = conn.execute(select(Asset.__table__)).mappings().one()
                assert row['source_type'] == 'smarteye'
                assert (root / row['blob_path']).samefile(Path(asset['local_path']))
            result = client.get(f'/api/assets/{row["id"]}/content', headers=headers)
            assert result.content == image.getvalue()
            assert client.delete(f'/api/assets/{row["id"]}', headers=headers).status_code == 204
            assert client.get(asset['preview_url'], headers=headers).content == image.getvalue()
            assert (root / row['blob_path']).is_file()
            assert client.get('/api/smarteye/ai-image/history', headers=headers).status_code == 200
            assert client.get('/api/smarteye/ledger-watch/jobs', headers=headers).status_code == 200
            # Provider fixture runs the complete retained AI worker, with no paid network calls.
            monkeypatch.setattr(ai_image, '_volc_post', lambda action, payload: {'code': 10000, 'data': {'task_id': 'fake'}})
            monkeypatch.setattr(ai_image, '_poll_result', lambda _: {'code': 10000, 'data': {'status': 'done',
                'binary_data_base64': [__import__('base64').b64encode(image.getvalue()).decode()]}})
            response = client.post('/api/smarteye/ai-image/start', headers=headers, json={'prompt': 'isolated', 'repeat_count': 1})
            assert response.status_code == 200, response.text
            task_id = response.json()['data']['task_id']
            import time
            deadline = time.monotonic() + 5
            while source.task_executor._work_queue.qsize() and time.monotonic() < deadline:
                time.sleep(.02)
            while module.get_runtime().get(task_id)['status'] in ('pending', 'running') and time.monotonic() < deadline:
                time.sleep(.02)
            assert module.get_runtime().get(task_id)['status'] == 'completed'
            history = client.get(f'/api/smarteye/ai-image/history/{task_id}', headers=headers)
            assert history.status_code == 200
            assert history.json()['data']['items'][0]['result_files_json']
            # A real HTTP image download is automatically registered, with no import API step.
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            import threading
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200); self.end_headers(); self.wfile.write(image.getvalue())
                def log_message(self, *args): pass
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            monkeypatch.setattr(source, 'DOWNLOAD_PATH', str(root / 'smarteye'))
            monkeypatch.setattr(source, 'get_automated_token', lambda: ('test', '1'))
            monkeypatch.setattr(source, 'get_alarm_image_data', lambda *args, **kw: {'type_url_map': {
                'local': [f'http://127.0.0.1:{server.server_port}/sample.png']}})
            try:
                response = client.post('/api/smarteye/download', headers=headers, json={'platName': 'local'})
                assert response.status_code == 200
                task_id = response.json()['data']['task_id']
                deadline = time.monotonic() + 5
                while module.get_runtime().get(task_id)['status'] in ('pending', 'running') and time.monotonic() < deadline:
                    time.sleep(.02)
                assert module.get_runtime().get(task_id)['status'] == 'completed'
                with engine.connect() as conn:
                    assert conn.execute(select(func.count(Asset.__table__.c.id))).scalar() == 1
                    assert conn.execute(select(Project.__table__.c.total_assets)).scalar() == 1
                    assert conn.execute(select(Asset.__table__.c.content_hash)).scalar() == __import__('hashlib').sha256(image.getvalue()).hexdigest()
                response = client.get(f'/api/smarteye/download-zip/{task_id}', headers=headers)
                assert response.status_code == 200
                import zipfile
                with zipfile.ZipFile(BytesIO(response.content)) as archive:
                    assert archive.read(archive.namelist()[0]) == image.getvalue()
            finally:
                server.shutdown(); server.server_close(); thread.join(timeout=2)
    finally:
        app.dependency_overrides.clear(); reset_storage()
        if async_engine:
            import asyncio
            asyncio.run(async_engine.dispose())
        if engine: engine.dispose()
        if created:
            assert name.startswith('codex_p1_')
            with admin.cursor() as cursor:
                cursor.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
        admin.close()
