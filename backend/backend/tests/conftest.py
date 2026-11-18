"""Isolated tests; never connect to the configured business database."""
import asyncio
import gc
import getpass
import os
import shutil
import tempfile
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
from sqlalchemy.schema import CreateIndex, CreateTable
from database import get_db_session
from main import app
from models import Base, Tenant, User, Project, Asset, TaskType, ProjectStatus
import models.annotation  # noqa: F401
import models.annotation_event  # noqa: F401
import models.asset  # noqa: F401
import models.automation  # noqa: F401
import models.gpu_server  # noqa: F401
import models.image  # noqa: F401
import models.image_collection  # noqa: F401
import models.image_ingest  # noqa: F401
import models.image_semantic  # noqa: F401
import models.image_upload  # noqa: F401
import models.model_deployment  # noqa: F401
import models.model_repository  # noqa: F401
import models.model_service  # noqa: F401
import models.object_attribute_template  # noqa: F401
import models.project  # noqa: F401
import models.project_member  # noqa: F401
import models.project_upload_key  # noqa: F401
import models.project_work  # noqa: F401
import models.smarteye  # noqa: F401
import models.tenant  # noqa: F401
import models.training  # noqa: F401
import models.training_run  # noqa: F401
import models.user  # noqa: F401
import models.validation  # noqa: F401
import models.vector_test  # noqa: F401
import models.work_session  # noqa: F401
from services.auth import create_access_token, get_password_hash
from services.storage import reset_storage
from integrations.smarteye import management_schema  # Include isolated device-management tables.

_TEST_PASSWORD_HASH = get_password_hash("testpassword")
_FALLBACK_BASETEMP: str | None = None


def pytest_configure(config):
    global _FALLBACK_BASETEMP
    if config.option.basetemp is not None:
        return
    default_root = os.path.join(tempfile.gettempdir(), f"pytest-of-{getpass.getuser() or 'unknown'}")
    try:
        os.makedirs(default_root, exist_ok=True)
        probe = os.path.join(default_root, f".probe-{os.getpid()}")
        os.mkdir(probe)
        os.rmdir(probe)
    except OSError:
        _FALLBACK_BASETEMP = tempfile.mkdtemp(prefix="pytest-run-")
        config.option.basetemp = _FALLBACK_BASETEMP


def pytest_unconfigure(config):
    global _FALLBACK_BASETEMP
    if _FALLBACK_BASETEMP:
        shutil.rmtree(_FALLBACK_BASETEMP, ignore_errors=True)
        _FALLBACK_BASETEMP = None

@compiles(JSONB, "sqlite")
def sqlite_jsonb(type_, compiler, **kwargs):
    return "JSON"

@compiles(BigInteger, "sqlite")
def sqlite_bigint(type_, compiler, **kwargs):
    return "INTEGER"

@compiles(CreateTable, "sqlite")
def sqlite_create_table_idempotent(element, compiler, **kwargs):
    return compiler.visit_create_table(element, **kwargs).replace(
        "CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1
    )

@compiles(CreateIndex, "sqlite")
def sqlite_create_index_idempotent(element, compiler, **kwargs):
    sql = compiler.visit_create_index(element, **kwargs)
    return sql.replace("CREATE UNIQUE INDEX ", "CREATE UNIQUE INDEX IF NOT EXISTS ", 1).replace(
        "CREATE INDEX ", "CREATE INDEX IF NOT EXISTS ", 1
    )

@pytest_asyncio.fixture(autouse=True)
async def _cleanup_script_sandbox():
    from services import script_sandbox
    before_rr = script_sandbox._rr
    yield
    if script_sandbox._slots or script_sandbox._rr != before_rr:
        transports = [getattr(s.proc, "_transport", None) for s in script_sandbox._slots if s.proc is not None]
        if script_sandbox._slots:
            await script_sandbox.shutdown_sandbox()
        for tr in transports:
            if tr is not None:
                tr.close()
        gc.collect()
        await asyncio.sleep(0)

def _build_template_db_bytes() -> bytes:
    import sqlite3
    from sqlalchemy import create_engine
    mem_engine = create_engine("sqlite:///:memory:")
    with mem_engine.begin() as conn:
        Base.metadata.create_all(conn)
        raw_conn = conn.connection.dbapi_connection
        assert isinstance(raw_conn, sqlite3.Connection)
        data = raw_conn.serialize()
    mem_engine.dispose()
    return data


_TEMPLATE_DB_BYTES: bytes | None = None


@pytest_asyncio.fixture
async def test_engine(tmp_path):
    global _TEMPLATE_DB_BYTES
    if _TEMPLATE_DB_BYTES is None:
        _TEMPLATE_DB_BYTES = _build_template_db_bytes()
    db_file = tmp_path / "test.db"
    db_file.write_bytes(_TEMPLATE_DB_BYTES)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}", poolclass=NullPool)
    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, record):
        connection.isolation_level = None
        connection.execute("PRAGMA foreign_keys=ON")
    @event.listens_for(engine.sync_engine, "begin")
    def begin(connection):
        connection.exec_driver_sql("BEGIN")
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
                password_hash=_TEST_PASSWORD_HASH, role="admin", is_active=True)
    test_db.add(user)
    await test_db.commit()
    return user

@pytest_asyncio.fixture
async def test_superuser(test_db, test_tenant):
    user = User(tenant_id=test_tenant.id, username="testsuperadmin", email="super@example.com",
                password_hash=_TEST_PASSWORD_HASH, role="admin", is_active=True)
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
    if hasattr(settings, "ALLOW_PUBLIC_REGISTER"):
        monkeypatch.setattr(settings, "ALLOW_PUBLIC_REGISTER", True)
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
    from integrations.smarteye.device_capture import shutdown_worker
    shutdown_worker()
    from integrations.smarteye.device_scenes import shutdown_worker as stop_device_scenes
    stop_device_scenes()
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

