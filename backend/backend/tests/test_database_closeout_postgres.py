"""013/bootstrap regression tests; only this fixture's disposable databases are mutated."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import psycopg2
from psycopg2 import sql
import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, insert, select, text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from config.settings import settings
from models import Base, Model, Project, Tenant, TrainingMetric, TrainingTask
from models.model_service import ModelService

ROOT = Path(__file__).resolve().parents[1]
TRAINING = ('training_tasks', 'training_metrics', 'models')
P3_TABLES = ('automation_jobs','automation_items','image_semantic_jobs','image_semantic_items','image_semantic_vectors','work_activity_intervals')
VALIDATION_TABLES = ('validation_sets', 'validation_runs', 'validation_items')
GPU_TABLES = ('gpu_servers',)
DOCKER_TRAINING_TABLES = ('training_runs','training_gpu_holds')
PROJECT_WORK_TABLES = ('project_work_jobs','project_work_items','match_queries')
CORE = (
    'tenants', 'users', 'projects', 'images', 'image_collections',
    'image_collection_members', 'image_ingest_settings', 'image_uploads',
    'image_upload_items', 'assets', 'annotations', 'annotation_events',
    'export_snapshots', 'model_services', *TRAINING, *P3_TABLES, *VALIDATION_TABLES, *GPU_TABLES, *DOCKER_TRAINING_TABLES, *PROJECT_WORK_TABLES,
)


def bootstrap_module():
    path = ROOT / 'scripts/bootstrap_database.py'
    assert path.exists(), 'missing safe empty-database bootstrap'
    spec = importlib.util.spec_from_file_location('p0_bootstrap', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_training_project_columns_match_uuid_projects():
    for model in (TrainingTask, Model):
        assert isinstance(model.__table__.c.project_id.type, UUID)
        assert model.__annotations__['project_id'] == Project.__annotations__['id']


def test_013_reserves_linear_chain_for_014():
    paths = list((ROOT / 'alembic/versions').glob('013_*.py'))
    assert len(paths) == 1, 'missing unique training recovery migration 013'
    spec = importlib.util.spec_from_file_location('p0_013', paths[0])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.revision == '013' and module.down_revision == '012'


@pytest.fixture
def isolated_pg(monkeypatch, request):
    if os.environ.get('P0_POSTGRES_INTEGRATION') != '1':
        pytest.skip('explicit isolated PostgreSQL run required')
    name = 'codex_p0_db_' + uuid4().hex
    original_name = settings.DB_NAME
    admin = psycopg2.connect(host=settings.DB_HOST, port=settings.DB_PORT,
                            user=settings.DB_USER, password=settings.DB_PASSWORD,
                            dbname='postgres', connect_timeout=5)
    admin.autocommit = True
    engine = None
    created = False
    owned_database = None
    try:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        created = True
        with admin.cursor() as cursor:
            cursor.execute('SELECT oid, datdba FROM pg_database WHERE datname=%s', (name,))
            owned_database = cursor.fetchone()
        request.node.user_properties.extend([('temporary_database', name), ('owned_oid', str(owned_database[0]))])
        url = make_url(settings.DATABASE_URL).set(drivername='postgresql+psycopg2', database=name)
        engine = create_engine(url, poolclass=NullPool)
        monkeypatch.setattr(settings, 'DB_NAME', name)
        config = Config(str(ROOT / 'alembic.ini'))
        config.set_main_option('script_location', str(ROOT / 'alembic'))
        yield engine, config
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            assert name.startswith('codex_p0_db_') and name != original_name
            with admin.cursor() as cursor:
                cursor.execute('SELECT oid, datdba FROM pg_database WHERE datname=%s', (name,))
                assert cursor.fetchone() == owned_database
                cursor.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
                cursor.execute('SELECT count(*) FROM pg_database WHERE datname=%s', (name,))
                assert cursor.fetchone()[0] == 0
                request.node.user_properties.append(('cleanup_verified', 'true'))
        admin.close()


def pre_013(engine, config):
    with engine.begin() as conn:
        Base.metadata.create_all(conn, tables=[Base.metadata.tables[t] for t in CORE if t not in (*TRAINING, 'model_services', *P3_TABLES, *VALIDATION_TABLES, *GPU_TABLES, *DOCKER_TRAINING_TABLES,*PROJECT_WORK_TABLES)])
        conn.execute(text('ALTER TABLE image_uploads DROP COLUMN annotation_format, DROP COLUMN trust_annotations'))
        conn.execute(text('ALTER TABLE images DROP COLUMN imported_annotation'))
        conn.execute(insert(Tenant).values(id=1, tenant_name='preserve me'))
        project = uuid4()
        conn.execute(insert(Project).values(id=project, tenant_id=1, project_name='preserve project', task_type='detection'))
    command.stamp(config, '012')
    return project


def add_training_rows(conn, project):
    task = conn.execute(insert(TrainingTask).values(project_id=project, task_name='isolated',
        framework='pytorch', model_type='test', config_yaml='{}', dataset_version='v1').returning(TrainingTask.id)).scalar_one()
    conn.execute(insert(TrainingMetric).values(task_id=task, epoch=1, step=1, loss=0.1))
    conn.execute(insert(Model).values(project_id=project, training_task_id=task, model_name='test',
        model_version='v1', model_type='test', framework='pytorch', model_path='test.pt', model_size=10))
    return task


def test_013_creates_tables_and_uuid_rows_without_changing_existing_data(isolated_pg):
    engine, config = isolated_pg
    project = pre_013(engine, config)
    command.upgrade(config, '013')
    with engine.begin() as conn:
        inspector = inspect(conn)
        for name in TRAINING:
            expected = Base.metadata.tables[name]
            assert {c['name'] for c in inspector.get_columns(name)} == set(expected.c.keys())
            assert {i['name'] for i in inspector.get_indexes(name)} == {i.name for i in expected.indexes}
            assert len(inspector.get_foreign_keys(name)) == len(expected.foreign_key_constraints)
        task = add_training_rows(conn, project)
        assert conn.scalar(select(TrainingTask.project_id).where(TrainingTask.id == task)) == project
        assert conn.scalar(select(Tenant.tenant_name)) == 'preserve me'
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == '013'
    command.upgrade(config, '013')
    with pytest.raises(RuntimeError, match='refus|review|data'):
        command.downgrade(config, '012')
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM training_metrics')) == 1
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == '013'


def test_013_preserves_compatible_populated_tables(isolated_pg):
    engine, config = isolated_pg
    project = pre_013(engine, config)
    with engine.begin() as conn:
        Base.metadata.create_all(conn, tables=[Base.metadata.tables[t] for t in TRAINING])
        task = add_training_rows(conn, project)
    command.upgrade(config, '013')
    with engine.connect() as conn:
        assert conn.scalar(select(TrainingTask.id)) == task
        assert conn.scalar(text('SELECT count(*) FROM models')) == 1


def test_013_fills_partial_compatible_schema(isolated_pg):
    engine, config = isolated_pg
    pre_013(engine, config)
    with engine.begin() as conn:
        TrainingTask.__table__.create(conn, checkfirst=True)
    command.upgrade(config, '013')
    with engine.connect() as conn:
        assert set(TRAINING) <= set(inspect(conn).get_table_names())


def test_012_through_current_migration_chain(isolated_pg):
    engine, config = isolated_pg
    pre_013(engine, config)
    command.upgrade(config, 'head')
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == ScriptDirectory.from_config(config).get_current_head() == '022'
        assert set((*TRAINING, 'model_services')) <= set(inspect(conn).get_table_names())


def test_022_detaches_legacy_crops_without_losing_annotations(isolated_pg):
    from models import Asset,Annotation,LibraryImage
    engine,config=isolated_pg
    project=pre_013(engine,config);command.upgrade(config,'021')
    image,crop,annotation=uuid4(),uuid4(),uuid4()
    with engine.begin() as conn:
        conn.execute(insert(LibraryImage).values(id=image,tenant_id=1,original_name='legacy.png',blob_path='keep/legacy.png',
            content_hash='crop-hash',source_type='model_crop',file_size=1))
        conn.execute(insert(Asset).values(id=crop,project_id=project,image_id=image,original_name='legacy.png',blob_path='keep/legacy.png',
            content_hash='crop-hash',source_type='model_crop',file_size=1))
        conn.execute(insert(Annotation).values(id=annotation,asset_id=crop,annotation_data={'boxes':[]},version=3,is_latest=True,review_status='approved',reviewer_id=1))
    command.upgrade(config,'022')
    with engine.connect() as conn:
        row=conn.execute(select(Asset.__table__).where(Asset.id==crop)).mappings().one()
        assert row['image_id'] is None and row['blob_path']=='keep/legacy.png' and row['content_hash']=='crop-hash'
        assert conn.scalar(select(Project.label_schema).where(Project.id==project))['annotation_scope']=='crops'
        assert conn.scalar(select(Annotation.version).where(Annotation.id==annotation))==3
        assert conn.scalar(select(LibraryImage.id).where(LibraryImage.id==image))==image


def test_015_additive_tables_match_models_and_preserve_rows(isolated_pg):
    engine,config=isolated_pg
    pre_013(engine,config)
    command.upgrade(config,'014')
    command.upgrade(config,'015')
    with engine.begin() as conn:
        inspector=inspect(conn)
        for name in P3_TABLES:
            expected=Base.metadata.tables[name]
            assert {c['name'] for c in inspector.get_columns(name)}==set(expected.c.keys())
            assert {i['name'] for i in inspector.get_indexes(name) if not i.get('duplicates_constraint')}=={i.name for i in expected.indexes}
            assert {i['name'] for i in inspector.get_unique_constraints(name)}=={c.name for c in expected.constraints if isinstance(c,UniqueConstraint)}
            assert len(inspector.get_foreign_keys(name))==len(expected.foreign_key_constraints)
        assert conn.scalar(select(Tenant.tenant_name))=='preserve me'
    with pytest.raises(RuntimeError,match='禁止|refus'):
        command.downgrade(config,'014')


@pytest.mark.asyncio
async def test_automation_postgres_two_jobs_never_overwrite_same_asset(isolated_pg,tmp_path,monkeypatch):
    import asyncio,hashlib
    from io import BytesIO
    from PIL import Image
    import httpx
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
    from models import User,Asset,Annotation
    from models.automation import AutomationJob,AutomationItem
    from api.automation_routes import router
    from api.deps import get_current_user,get_current_superuser
    from database import get_db_session
    from services import automation
    from services.storage import get_storage,reset_storage
    engine,config=isolated_pg
    with engine.begin() as conn:bootstrap_module().bootstrap_database(conn)
    async_engine=create_async_engine(engine.url.set(drivername='postgresql+asyncpg'))
    sessions=async_sessionmaker(async_engine,expire_on_commit=False)
    monkeypatch.setattr(settings,'STORAGE_TYPE','local');monkeypatch.setattr(settings,'STORAGE_LOCAL_PATH',str(tmp_path/'storage'));reset_storage()
    image=BytesIO();Image.new('RGB',(64,48),'white').save(image,'JPEG');content=image.getvalue()
    await get_storage().save_file(BytesIO(content),'images/pg.jpg')
    try:
        async with sessions() as db:
            db.add(Tenant(id=1,tenant_name='test'));await db.flush()
            user=User(tenant_id=1,username='pgadmin',email='pg@example.com',role='admin',is_active=True,password_hash='test-only')
            project=Project(tenant_id=1,project_name='PG detection',task_type='detection',label_schema={'labels':['person']})
            db.add_all([user,project]);await db.flush()
            asset=Asset(project_id=project.id,original_name='pg.jpg',blob_path='images/pg.jpg',content_hash=hashlib.sha256(content).hexdigest(),
                file_size=len(content),source_type='upload',width=64,height=48,annotation_status='pending')
            detector=ModelService(tenant_id=1,key='det',name='det',kind='detection',protocol='model_service',base_url='http://det.test',endpoint='/detect')
            reviewer=ModelService(tenant_id=1,key='qwen',name='Qwen',kind='caption',protocol='openai_chat',base_url='http://qwen.test/v1',endpoint='/chat/completions',model_name='Qwen')
            db.add_all([asset,detector,reviewer]);await db.commit()
        app=FastAPI();app.include_router(router,prefix='/api/automation')
        async def db_override():
            async with sessions() as db:yield db
        app.dependency_overrides[get_db_session]=db_override
        app.dependency_overrides[get_current_user]=lambda:user
        app.dependency_overrides[get_current_superuser]=lambda:user
        waiting=[];ready=asyncio.Event()
        async def detect(*args,**kwargs):
            waiting.append(1)
            if len(waiting)==2:ready.set()
            await asyncio.wait_for(ready.wait(),10)
            return dict(boxes=[dict(label='person',x=1,y=2,width=10,height=20,score=.95)],warnings=[])
        async def audit(*args,**kwargs):return dict(boxes=[dict(index=0,verdict='pass',reason='正确')],completeness='pass',negative='uncertain',reason='完整',rounds=[])
        monkeypatch.setattr(automation,'infer',detect);monkeypatch.setattr(automation,'audit_image',audit)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            request=dict(project_id=str(project.id),kind='preannotate',detector_id=str(detector.id),reviewer_id=str(reviewer.id),config={})
            ids=[]
            for _ in range(2):
                response=await client.post('/api/automation/jobs',json=request)
                assert response.status_code==201,response.text
                ids.append(response.json()['id'])
            assert all(await asyncio.gather(automation.process_one(sessions),automation.process_one(sessions)))
            states=[(await client.get('/api/automation/jobs/'+id)).json() for id in ids]
            assert sum(row['approved_count'] for row in states)==1
        async with sessions() as db:
            assert await db.scalar(select(func.count()).select_from(Annotation))==1
            statuses=(await db.scalars(select(AutomationItem.status))).all()
            assert set(statuses)=={'approved','skipped'}
            saved=await db.scalar(select(Annotation))
            assert saved.version==1 and saved.review_status=='approved' and saved.annotation_source=='model'
    finally:
        await async_engine.dispose();reset_storage()


def test_013_preflights_later_incompatible_table_before_creating_missing_metrics(isolated_pg):
    engine, config = isolated_pg
    project = pre_013(engine, config)
    with engine.begin() as conn:
        TrainingTask.__table__.create(conn, checkfirst=True)
        conn.execute(insert(TrainingTask).values(project_id=project, task_name='preserve task',
            framework='pytorch', model_type='test', config_yaml='{}', dataset_version='v1'))
        conn.execute(text('CREATE TABLE models (id bigint PRIMARY KEY, note text)'))
        conn.execute(text("INSERT INTO models VALUES (987, 'preserve model')"))
    with pytest.raises(RuntimeError, match='Incompatible.*models'):
        command.upgrade(config, '013')
    with engine.connect() as conn:
        assert not inspect(conn).has_table('training_metrics')
        assert conn.scalar(text('SELECT task_name FROM training_tasks')) == 'preserve task'
        assert conn.scalar(text('SELECT note FROM models')) == 'preserve model'
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == '012'


@pytest.mark.parametrize('incompatible', ['bigint_project', 'missing_column', 'wrong_type', 'nullable', 'missing_fk', 'missing_index', 'missing_id_default', 'enum'])
def test_013_refuses_incompatible_existing_tables_without_repair_or_stamp(isolated_pg, incompatible):
    engine, config = isolated_pg
    pre_013(engine, config)
    with engine.begin() as conn:
        if incompatible == 'bigint_project':
            conn.execute(text('CREATE TABLE training_tasks (id bigint PRIMARY KEY, project_id bigint, note text)'))
            conn.execute(text("INSERT INTO training_tasks VALUES (987, 123, 'keep legacy row')"))
        else:
            TrainingTask.__table__.create(conn, checkfirst=True)
            if incompatible == 'missing_column':
                conn.execute(text('ALTER TABLE training_tasks DROP COLUMN config_yaml'))
            elif incompatible == 'wrong_type':
                conn.execute(text('ALTER TABLE training_tasks ALTER COLUMN framework TYPE varchar(10)'))
            elif incompatible == 'nullable':
                conn.execute(text('ALTER TABLE training_tasks ALTER COLUMN project_id DROP NOT NULL'))
            elif incompatible == 'missing_fk':
                key = inspect(conn).get_foreign_keys('training_tasks')[0]['name']
                conn.exec_driver_sql('ALTER TABLE training_tasks DROP CONSTRAINT "' + key + '"')
            elif incompatible == 'missing_index':
                conn.execute(text('DROP INDEX idx_training_task_project'))
            elif incompatible == 'missing_id_default':
                conn.execute(text('ALTER TABLE training_tasks ALTER COLUMN id DROP DEFAULT'))
            else:
                conn.execute(text("ALTER TYPE trainingstatus ADD VALUE 'UNEXPECTED'"))
        before = inspect(conn).get_columns('training_tasks')
    with pytest.raises(RuntimeError, match='[Ii]ncompatible.*training'):
        command.upgrade(config, '013')
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == '012'
        assert not set(('training_metrics', 'models')) & set(inspect(conn).get_table_names())
        assert [str(c) for c in inspect(conn).get_columns('training_tasks')] == [str(c) for c in before]
        if incompatible == 'bigint_project':
            assert conn.scalar(text('SELECT note FROM training_tasks')) == 'keep legacy row'


def test_bootstrap_empty_database_cli_and_exact_stamp(isolated_pg, tmp_path):
    module = bootstrap_module()
    engine, config = isolated_pg
    env = {**os.environ, 'DATABASE_BOOTSTRAP_URL': engine.url.render_as_string(hide_password=False)}
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/bootstrap_database.py')], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    with engine.begin() as conn:
        inspector = inspect(conn)
        from integrations.smarteye.runtime import metadata
        assert set(inspector.get_table_names()) == set(CORE) | set(metadata.tables) | {'alembic_version'}
        assert set(inspector.get_table_names(schema='smarteye')) == {
            'platforms', 'alarm_types', 'ai_image_tasks', 'ai_image_task_items',
            'devices', 'ledger_watermarks', 'ledger_watch_jobs', 'ledger_watch_watermarks'}
        assert conn.scalar(text('SELECT version_num FROM alembic_version')) == ScriptDirectory.from_config(config).get_current_head() == '022'
        assert not {'smarteye_tasks', 'smarteye_downloads'} & set(inspector.get_table_names())
        conn.execute(insert(Tenant).values(id=1, tenant_name='bootstrap'))
        project = uuid4()
        conn.execute(insert(Project).values(id=project, tenant_id=1, project_name='bootstrapped', task_type='detection'))
        add_training_rows(conn, project)
        columns = {c['name']: c for c in inspector.get_columns('model_services')}
        assert set(columns) == set(ModelService.__table__.c.keys())
        assert isinstance(columns['id']['type'], UUID)
        assert all(isinstance(columns[field]['type'], JSONB) for field in ('labels', 'label_mapping'))
        service_id = uuid4()
        conn.execute(insert(ModelService).values(id=service_id, tenant_id=1, key='pg-fixture',
            name='PG schema fixture', kind='detection', protocol='yolo', base_url='https://example.invalid',
            endpoint='/predict', labels=['person', '烟雾'], label_mapping={'0': 'person', '1': '烟雾'}))
    with engine.connect() as conn:
        service = conn.execute(select(ModelService.__table__).where(ModelService.id == service_id)).mappings().one()
        assert service['id'] == service_id and service['tenant_id'] == 1
        assert service['labels'] == ['person', '烟雾'] and service['label_mapping'] == {'0': 'person', '1': '烟雾'}
        assert service['enabled'] is True and service['timeout_seconds'] == 60 and service['revision'] == 1
    command.upgrade(config, 'head')
    with pytest.raises(RuntimeError, match='empty'):
        with engine.begin() as conn:
            module.bootstrap_database(conn)
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM models')) == 1


@pytest.mark.parametrize('object_sql', [
    'CREATE TABLE public.business_data (id integer)',
    'CREATE SCHEMA private; CREATE TABLE private.business_data (id integer)',
    'CREATE SCHEMA pgapp; CREATE TABLE pgapp.business_data (id integer)',
    "CREATE TYPE public.business_state AS ENUM ('active')",
    'CREATE SEQUENCE public.business_counter',
])
def test_bootstrap_refuses_nonempty_databases(isolated_pg, object_sql):
    module = bootstrap_module()
    engine, _ = isolated_pg
    with engine.begin() as conn:
        conn.exec_driver_sql(object_sql)
    with pytest.raises(RuntimeError, match='empty'):
        with engine.begin() as conn:
            module.bootstrap_database(conn)
    with engine.connect() as conn:
        assert 'alembic_version' not in inspect(conn).get_table_names()


def test_bootstrap_failure_rolls_back_all_ddl_and_stamp(isolated_pg, monkeypatch):
    module = bootstrap_module()
    engine, _ = isolated_pg
    def fail(conn):
        raise RuntimeError('injected SmartEye DDL failure')
    monkeypatch.setattr(module, 'upgrade_schema', fail)
    with pytest.raises(RuntimeError, match='injected'):
        with engine.begin() as conn:
            module.bootstrap_database(conn)
    with engine.connect() as conn:
        assert inspect(conn).get_table_names() == []
        assert inspect(conn).get_enums() == []


def test_bootstrap_cli_requires_explicit_url(tmp_path):
    bootstrap_module()
    env = {k: v for k, v in os.environ.items() if k != 'DATABASE_BOOTSTRAP_URL'}
    result = subprocess.run([sys.executable, str(ROOT / 'scripts/bootstrap_database.py')], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert 'DATABASE_BOOTSTRAP_URL' in result.stderr


def test_bootstrap_can_provision_first_admin_atomically(isolated_pg):
    from models import User
    from schemas.user import UserCreate
    from services.auth import verify_password
    module=bootstrap_module();engine,_=isolated_pg
    admin=UserCreate(username='initial-admin',email='initial@example.com',password='initial-test-password')
    with engine.begin() as conn:
        module.bootstrap_database(conn,admin=admin)
    with engine.connect() as conn:
        row=conn.execute(select(User.__table__)).mappings().one()
        assert row['tenant_id']==settings.DEFAULT_TENANT_ID and row['role']=='admin' and row['is_active']
        assert verify_password('initial-test-password',row['password_hash'])
        assert conn.scalar(select(Tenant.id))==settings.DEFAULT_TENANT_ID
    with pytest.raises(RuntimeError,match='empty'):
        with engine.begin() as conn:module.bootstrap_database(conn,admin=admin)
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT count(*) FROM users'))==1


def test_bootstrap_admin_cli_rejects_missing_password_before_connecting(tmp_path):
    env={key:value for key,value in os.environ.items() if key!='BOOTSTRAP_ADMIN_PASSWORD'}
    result=subprocess.run([sys.executable,str(ROOT/'scripts/bootstrap_database.py'),
        '--database-url','postgresql://invalid/unused','--admin-username','first-admin','--admin-email','first@example.com'],
        cwd=tmp_path,env=env,capture_output=True,text=True,timeout=15)
    assert result.returncode!=0
    assert 'BOOTSTRAP_ADMIN_PASSWORD' in result.stderr
