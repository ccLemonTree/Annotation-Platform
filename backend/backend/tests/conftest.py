"""Isolated tests; never connect to the configured business database."""
import os
from io import BytesIO
os.environ.setdefault("JWT_SECRET_KEY", "test-only-jwt-key-not-for-production-20261013")
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import BigInteger, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import NullPool
from database import get_db_session
from main import app
from models import Base, Tenant, User, Project, Asset, TaskType, ProjectStatus
from services.auth import create_access_token, get_password_hash
from services.storage import reset_storage

@compiles(JSONB, "sqlite")
def sqlite_jsonb(type_, compiler, **kwargs):
    return "JSON"

@compiles(BigInteger, "sqlite")
def sqlite_bigint(type_, compiler, **kwargs):
    return "INTEGER"

@pytest_asyncio.fixture
async def test_engine(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}", poolclass=NullPool)
    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, record):
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")
    @event.listens_for(engine.sync_engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")
    tables = [Base.metadata.tables[name] for name in ("tenants", "users", "projects", "images", "image_collections", "image_collection_members", "image_ingest_settings", "image_uploads", "image_upload_items", "assets", "annotations", "annotation_events", "export_snapshots", "automation_jobs", "automation_items")]
    async with engine.begin() as connection:
        await connection.run_sync(lambda conn: Base.metadata.create_all(conn, tables=tables))
    yield engine
    await engine.dispose()

@pytest_asyncio.fixture
async def test_db(test_engine):
    async with async_sessionmaker(test_engine, expire_on_commit=False)() as session:
        yield session

@pytest_asyncio.fixture
async def test_tenant(test_db):
    tenant = Tenant(id=1, tenant_name="test")
    test_db.add(tenant)
    await test_db.commit()
    return tenant

@pytest_asyncio.fixture
async def test_user(test_db, test_tenant):
    user = User(id=1, tenant_id=test_tenant.id, username="testadmin", email="test@example.com",
                password_hash=get_password_hash("testpassword"), role="admin", is_active=True)
    test_db.add(user)
    await test_db.commit()
    return user

@pytest_asyncio.fixture
async def test_project(test_db, test_tenant):
    project = Project(tenant_id=test_tenant.id, project_name="test project", task_type=TaskType.DETECTION,
                      status=ProjectStatus.ACTIVE, total_assets=0, annotated_count=0, reviewed_count=0)
    test_db.add(project)
    await test_db.commit()
    return project

@pytest_asyncio.fixture
async def test_asset(test_db, test_project):
    asset = Asset(project_id=test_project.id, original_name="sample.jpg", blob_path="sample.jpg",
                  content_hash="sample", file_size=10, source_type="upload", width=64, height=48,
                  format="JPEG", annotation_status="pending")
    test_db.add(asset)
    test_project.total_assets += 1
    await test_db.commit()
    return asset

@pytest.fixture
def client(test_engine, test_user, tmp_path, monkeypatch):
    from config.settings import settings
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    async def isolated_db():
        async with sessions() as session:
            yield session
    monkeypatch.setattr(settings, "STORAGE_LOCAL_PATH", str(tmp_path / "storage"))
    monkeypatch.setattr(settings, "UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(settings, "STORAGE_TYPE", "local")
    monkeypatch.setattr(settings, "DEFAULT_TENANT_ID", 1)
    app.dependency_overrides[get_db_session] = isolated_db
    reset_storage()
    from integrations.smarteye import runtime
    from sqlalchemy import create_engine
    sync_engine = create_engine(f"sqlite:///{tmp_path / 'smarteye.db'}")
    runtime.metadata.create_all(sync_engine)
    Base.metadata.create_all(sync_engine, tables=[Base.metadata.tables[t] for t in ('tenants','images','image_collections','image_collection_members','image_ingest_settings')])
    monkeypatch.setattr(runtime, '_runtime', runtime.Runtime(sync_engine, tmp_path / 'storage'))
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    reset_storage()
    sync_engine.dispose()

@pytest.fixture
def auth_headers(test_user):
    token = create_access_token({"user_id": test_user.id, "tenant_id": test_user.tenant_id,
                                 "username": test_user.username})
    return {"Authorization": f"Bearer {token}"}

@pytest.fixture
def mock_image_data():
    content = BytesIO()
    Image.new("RGB", (64, 48), color="red").save(content, format="JPEG")
    content.seek(0)
    return content

@pytest.fixture
def mock_annotation_data():
    return {"boxes": [{"x": 4, "y": 5, "width": 10, "height": 10, "label": "person"}]}

