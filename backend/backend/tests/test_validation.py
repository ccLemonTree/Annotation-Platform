"""Validation evidence tests use only the isolated SQLite fixtures."""
import importlib.util
import hashlib
from io import BytesIO
from uuid import uuid4, UUID

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest_asyncio.fixture
async def validation_tables(test_engine):
    from models.model_service import ModelService
    from models.validation import ValidationSet, ValidationRun, ValidationItem
    async with test_engine.begin() as connection:
        await connection.run_sync(lambda conn: [table.create(conn, checkfirst=True) for table in
            (ModelService.__table__, ValidationSet.__table__, ValidationRun.__table__, ValidationItem.__table__)])


@pytest.fixture
def vclient(validation_tables, test_engine, test_user, tmp_path, monkeypatch):
    from api.validation_routes import router
    from database import get_db_session
    from config.settings import settings
    from services.storage import reset_storage
    app = FastAPI()
    app.include_router(router, prefix='/api/validation')
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    async def isolated_db():
        async with sessions() as db:
            yield db
    app.dependency_overrides[get_db_session] = isolated_db
    monkeypatch.setattr(settings, 'STORAGE_TYPE', 'local')
    monkeypatch.setattr(settings, 'STORAGE_LOCAL_PATH', str(tmp_path / 'validation-storage'))
    reset_storage()
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client
    reset_storage()


@pytest_asyncio.fixture
async def ready(vclient, test_db, test_project, test_asset, test_user):
    from models import Annotation
    from models.model_service import ModelService
    from services.storage import get_storage
    test_project.label_schema = {'labels': ['person', 'car']}
    picture = BytesIO()
    Image.new('RGB', (64, 48), 'white').save(picture, 'PNG')
    content = picture.getvalue()
    await get_storage().save_file(BytesIO(content), test_asset.blob_path)
    test_asset.content_hash = hashlib.sha256(content).hexdigest()
    annotation = Annotation(asset_id=test_asset.id, annotation_data={'boxes': [
        dict(label='person', x=1, y=2, width=10, height=20)]}, version=1,
        is_latest=True, review_status='approved', reviewer_id=test_user.id)
    model = ModelService(tenant_id=test_user.tenant_id, key='validation-det', name='Detector',
        kind='detection', protocol='model_service', base_url='http://det.test', endpoint='/detect',
        labels=[], label_mapping={}, api_key_env='MODEL_API_KEY_TEST')
    test_db.add_all([annotation, model])
    await test_db.commit()
    return dict(project=test_project, asset=test_asset, annotation=annotation, model=model, content=content)


def create_set(client, headers, ready, **changes):
    response = client.post('/api/validation/sets', headers=headers, json={
        'name': 'Human gold', 'project_id': str(ready['project'].id), **changes})
    assert response.status_code == 201, response.text
    return response.json()


def create_run(client, headers, ready, dataset=None):
    dataset = dataset or create_set(client, headers, ready)
    response = client.post('/api/validation/runs', headers=headers, json={
        'set_id': dataset['id'], 'model_id': str(ready['model'].id), 'config': {}})
    assert response.status_code == 201, response.text
    return response.json()


def test_creation_summary_pagination_and_no_internal_paths(vclient, auth_headers, ready):
    dataset = create_set(vclient, auth_headers, ready)
    assert dataset['total'] == 1 and dataset['labels'] == ['person', 'car']
    assert 'samples' not in dataset and 'blob_path' not in str(dataset)
    page = vclient.get('/api/validation/sets?page_size=1', headers=auth_headers).json()
    assert page['total'] == page['page'] == page['page_size'] == 1
    assert vclient.get('/api/validation/sets/' + dataset['id'], headers=auth_headers).json() == dataset
    run = create_run(vclient, auth_headers, ready, dataset)
    assert run['model'] == dict(id=str(ready['model'].id), revision=1, name='Detector')
    assert 'MODEL_API_KEY_TEST' not in str(run) and 'http://det.test' not in str(run)
    assert run['metrics']['coverage']['complete'] is False


@pytest.mark.asyncio
async def test_unscored_model_rejected_for_confidence_metrics(vclient,auth_headers,ready,test_db):
    ready['model'].endpoint='/detect/grounding';await test_db.commit()
    dataset=create_set(vclient,auth_headers,ready)
    response=vclient.post('/api/validation/runs',headers=auth_headers,json={
        'set_id':dataset['id'],'model_id':str(ready['model'].id),'config':{}})
    assert response.status_code==400 and '置信度' in response.json()['detail']
    assert vclient.get('/api/validation/runs',headers=auth_headers).json()['total']==0


@pytest.mark.asyncio
async def test_new_validation_set_respects_project_crop_scope(vclient,auth_headers,ready,test_db,test_user):
    from models import Asset,Annotation
    from models.validation import ValidationSet
    ready['project'].label_schema={'labels':['person','car'],'annotation_scope':'crops'}
    ready['asset'].source_type='model_crop'
    original=Asset(project_id=ready['project'].id,original_name='source.png',blob_path='owned/source.png',content_hash='source',
        source_type='upload',file_size=10,width=64,height=48)
    test_db.add(original);await test_db.flush()
    test_db.add(Annotation(asset_id=original.id,annotation_data={'boxes':[]},version=1,is_latest=True,review_status='approved',reviewer_id=test_user.id))
    await test_db.commit()
    created=create_set(vclient,auth_headers,ready)
    frozen=await test_db.get(ValidationSet,UUID(created['id']))
    assert [row['asset_id'] for row in frozen.samples]==[str(ready['asset'].id)]


@pytest.mark.asyncio
@pytest.mark.parametrize('status,reviewer', [('pending', 1), ('approved', None)])
async def test_only_human_approved_truth(vclient, auth_headers, ready, test_db, status, reviewer):
    ready['annotation'].review_status = status
    ready['annotation'].reviewer_id = reviewer
    await test_db.commit()
    for explicit in (False, True):
        body = dict(name='gold', project_id=str(ready['project'].id))
        if explicit: body['asset_ids'] = [str(ready['asset'].id)]
        response = vclient.post('/api/validation/sets', headers=auth_headers, json=body)
        assert response.status_code == 400
        assert '人工' in response.text or '人审' in response.text


@pytest.mark.parametrize('config', [dict(confidence=.0001), dict(match_iou=1.1), dict(nms_iou=-1),
    dict(label_mapping={'x': ''}), dict(labels=['']), dict(inference_confidence=2),
    dict(target_overlap=0),dict(target_overlap=1.01),dict(target_overlap=float('nan'))])
def test_config_validation(config):
    from schemas.validation import ValidationConfig
    with pytest.raises(ValueError): ValidationConfig(**config)


@pytest.mark.asyncio
async def test_rejects_invalid_truth_and_explicit_foreign_asset(vclient, auth_headers, ready, test_db):
    response = vclient.post('/api/validation/sets', headers=auth_headers, json=dict(
        name='gold', project_id=str(ready['project'].id), asset_ids=[str(uuid4())]))
    assert response.status_code == 404
    ready['annotation'].annotation_data = {'boxes': [dict(label='unknown', x=1, y=2, width=10, height=20)]}
    await test_db.commit()
    response = vclient.post('/api/validation/sets', headers=auth_headers, json=dict(name='gold', project_id=str(ready['project'].id)))
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_frozen_truth_requires_human_evidence_and_keeps_manifest_labels(vclient, auth_headers, ready, test_db):
    from models.annotation_event import ExportSnapshot
    asset, annotation = ready['asset'], ready['annotation']
    row = dict(asset_id=str(asset.id), original_name='frozen.png', content_hash=asset.content_hash,
        annotation_id=str(annotation.id), annotation_version=1, annotation_data=annotation.annotation_data,
        review_status='approved', width=64, height=48, split='val')
    snapshot = ExportSnapshot(project_id=ready['project'].id, user_id=1, export_format='coco',
        blob_path='not-read.zip', content_hash='0' * 64,
        manifest=dict(labels=['person'], assets=[row], approved_only=True))
    test_db.add(snapshot)
    await test_db.commit()
    dataset = create_set(vclient, auth_headers, ready, snapshot_id=str(snapshot.id))
    assert dataset['labels'] == ['person']
    annotation.reviewer_id = None
    await test_db.commit()
    response = vclient.post('/api/validation/sets', headers=auth_headers, json=dict(
        name='bad', project_id=str(ready['project'].id), snapshot_id=str(snapshot.id)))
    assert response.status_code == 400 and '人工' in response.text

@pytest.mark.asyncio
async def test_frozen_set_batches_database_reads(vclient, auth_headers, ready, test_db, test_engine):
    from sqlalchemy import event
    from models import Annotation, Asset, ExportSnapshot
    rows = []
    for index in range(25):
        asset = Asset(project_id=ready['project'].id, original_name=f'{index}.png', blob_path=f'fixture/{index}.png',
            content_hash=str(index), source_type='upload', file_size=1, width=64, height=48, format='PNG')
        test_db.add(asset); await test_db.flush()
        annotation = Annotation(asset_id=asset.id, annotation_data=ready['annotation'].annotation_data,
            version=1, is_latest=True, review_status='approved', reviewer_id=1)
        test_db.add(annotation); await test_db.flush()
        rows.append(dict(asset_id=str(asset.id), original_name=asset.original_name, content_hash=asset.content_hash,
            annotation_id=str(annotation.id), annotation_version=1, annotation_data=annotation.annotation_data,
            review_status='approved', width=64, height=48, split='val'))
    snapshot = ExportSnapshot(project_id=ready['project'].id, user_id=1, export_format='yolo',
        blob_path='not-read.zip', content_hash='0'*64, manifest=dict(labels=['person'], assets=rows))
    test_db.add(snapshot); await test_db.commit()
    reads = []
    def count_reads(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().upper().startswith('SELECT'): reads.append(statement)
    event.listen(test_engine.sync_engine, 'before_cursor_execute', count_reads)
    try:
        dataset = create_set(vclient, auth_headers, ready, snapshot_id=str(snapshot.id))
    finally:
        event.remove(test_engine.sync_engine, 'before_cursor_execute', count_reads)
    assert dataset['total'] == 25
    assert len(reads) <= 8, f'Frozen set used {len(reads)} reads for 25 images'


@pytest.mark.asyncio
async def test_worker_uses_frozen_truth_and_survives_asset_and_project_deletion(vclient, auth_headers, ready, test_engine, test_db, monkeypatch):
    from models import Annotation, Asset, Project
    from services import validation
    run = create_run(vclient, auth_headers, ready)
    await test_db.execute(delete(Annotation).where(Annotation.id == ready['annotation'].id))
    await test_db.execute(delete(Asset).where(Asset.id == ready['asset'].id))
    await test_db.execute(delete(Project).where(Project.id == ready['project'].id))
    await test_db.commit()
    async def infer(config, content, width, height):
        assert config.confidence == .001 and config.iou == .45
        assert content == ready['content'] and (width, height) == (64, 48)
        return dict(boxes=[dict(label='person', x=1, y=2, width=10, height=20, score=.99)], warnings=[])
    monkeypatch.setattr(validation, 'infer', infer)
    assert await validation.process_one(async_sessionmaker(test_engine, expire_on_commit=False))
    detail = vclient.get('/api/validation/runs/' + run['id'], headers=auth_headers).json()
    assert detail['status'] == 'completed' and detail['processed'] == 1
    assert detail['metrics']['coverage'] == dict(total=1, succeeded=1, failed=0, pending=0, canceled=0, complete=True)
    items = vclient.get('/api/validation/runs/' + run['id'] + '/items?error_type=correct', headers=auth_headers).json()
    assert items['total'] == 1
    item = items['items'][0]
    assert item['truth'][0]['label'] == 'person' and item['predictions'][0]['score'] == .99
    image = vclient.get('/api/validation/runs/' + run['id'] + '/items/' + item['id'] + '/image', headers=auth_headers)
    assert image.status_code == 200 and image.content == ready['content']
    report = vclient.get('/api/validation/runs/' + run['id'] + '/report', headers=auth_headers)
    assert 'attachment' in report.headers['content-disposition']
    assert 'blob_path' not in report.text and 'MODEL_API_KEY' not in report.text and 'http://det.test' not in report.text


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['unknown', 'hash', 'dimensions', 'revoked', 'revision'])
async def test_failures_excluded_from_metrics(vclient, auth_headers, ready, test_engine, test_db, monkeypatch, mode):
    from services import validation
    from services.storage import get_storage
    from models import User
    run = create_run(vclient, auth_headers, ready)
    if mode == 'hash':
        await get_storage().save_file(BytesIO(b'changed'), ready['asset'].blob_path)
    if mode == 'dimensions':
        from models.validation import ValidationSet
        dataset = await test_db.get(ValidationSet, UUID(run['set_id']))
        dataset.samples = [{**dataset.samples[0], 'width': 65}]
        await test_db.commit()
    async def infer(*args):
        async with async_sessionmaker(test_engine)() as db:
            if mode == 'revoked': (await db.get(User, 1)).is_active = False
            if mode == 'revision': (await db.get(type(ready['model']), ready['model'].id)).revision += 1
            await db.commit()
        return dict(boxes=[dict(label='unknown' if mode == 'unknown' else 'person', x=1, y=2, width=10, height=20, score=.9)], warnings=[])
    monkeypatch.setattr(validation, 'infer', infer)
    await validation.process_one(async_sessionmaker(test_engine, expire_on_commit=False))
    # Revocation deliberately disables this actor; inspect through an isolated session.
    from models.validation import ValidationRun
    async with async_sessionmaker(test_engine)() as db:
        row = await db.get(ValidationRun, UUID(run['id']))
        assert row.status == 'failed' and row.failed_count == 1
        assert row.metrics['coverage']['succeeded'] == 0 and row.metrics['coverage']['complete'] is False


@pytest.mark.asyncio
async def test_cancel_retry_discards_late_attempt_and_keeps_success(vclient, auth_headers, ready, test_engine, monkeypatch):
    from services import validation
    run = create_run(vclient, auth_headers, ready)
    path = '/api/validation/runs/' + run['id']
    async def late(*args):
        assert vclient.post(path + '/cancel', headers=auth_headers).status_code == 200
        assert vclient.post(path + '/retry', headers=auth_headers).status_code == 200
        return dict(boxes=[], warnings=[])
    monkeypatch.setattr(validation, 'infer', late)
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    await validation.process_one(sessions)
    assert vclient.get(path, headers=auth_headers).json()['status'] == 'pending'
    async def negative(*args): return dict(boxes=[], warnings=['模型未检出目标，不等于人工确认负样本'])
    monkeypatch.setattr(validation, 'infer', negative)
    await validation.process_one(sessions)
    detail = vclient.get(path, headers=auth_headers).json()
    assert detail['status'] == 'completed' and detail['failed_count'] == 0
    assert vclient.get(path + '/items?error_type=false_negative', headers=auth_headers).json()['total'] == 1
    assert vclient.post(path + '/retry', headers=auth_headers).status_code == 400


@pytest.mark.asyncio
async def test_permissions_and_tenant_reads(vclient, auth_headers, ready, test_db, test_user):
    from models import Tenant, User
    from services.auth import create_access_token
    run = create_run(vclient, auth_headers, ready)
    assert vclient.get('/api/validation/sets').status_code == 401
    test_user.role = 'user'
    await test_db.commit()
    assert vclient.get('/api/validation/runs', headers=auth_headers).status_code == 200
    assert vclient.post('/api/validation/runs/' + run['id'] + '/cancel', headers=auth_headers).status_code == 403
    test_db.add(Tenant(id=2, tenant_name='other'))
    await test_db.flush()
    test_db.add(User(id=2, tenant_id=2, username='other', email='other@test', password_hash='x', is_active=True, role='admin'))
    await test_db.commit()
    other = {'Authorization': 'Bearer ' + create_access_token({'user_id': 2, 'tenant_id': 2})}
    for path in ('/sets/' + run['set_id'], '/runs/' + run['id'], '/runs/' + run['id'] + '/items', '/runs/' + run['id'] + '/report'):
        assert vclient.get('/api/validation' + path, headers=other).status_code == 404
    assert vclient.get('/api/validation/runs', headers=other).json()['total'] == 0


def test_validation_modules_exist():
    for name in ('models.validation', 'schemas.validation', 'services.validation', 'api.validation_routes'):
        assert importlib.util.find_spec(name) is not None, f'{name} has not been implemented'


@pytest.mark.asyncio
async def test_gets_never_write_report_or_timestamps(vclient, auth_headers, ready, test_engine):
    from models.validation import ValidationRun
    run = create_run(vclient, auth_headers, ready)
    async with async_sessionmaker(test_engine)() as db:
        row = await db.get(ValidationRun, UUID(run['id']))
        baseline = (row.metrics, row.updated_at)
    for suffix in ('', '/report'):
        assert vclient.get('/api/validation/runs/' + run['id'] + suffix, headers=auth_headers).status_code == 200
    async with async_sessionmaker(test_engine)() as db:
        row = await db.get(ValidationRun, UUID(run['id']))
        assert (row.metrics, row.updated_at) == baseline


@pytest.mark.asyncio
async def test_per_run_mapping_overrides_defaults_and_unused_defaults_are_allowed(vclient, auth_headers, ready, test_db):
    ready['model'].label_mapping = {'det_person': 'default_person', 'det_fire': 'fire'}
    await test_db.commit()
    dataset = create_set(vclient, auth_headers, ready)
    response = vclient.post('/api/validation/runs', headers=auth_headers, json=dict(
        set_id=dataset['id'], model_id=str(ready['model'].id),
        config=dict(label_mapping={'det_person': 'person'})))
    assert response.status_code == 201, response.text
    assert response.json()['config']['label_mapping'] == {'det_person': 'person', 'det_fire': 'fire'}


@pytest.mark.asyncio
async def test_human_confirmed_negative_is_valid_truth(vclient, auth_headers, ready, test_db, test_engine, monkeypatch):
    from services import validation
    ready['annotation'].annotation_data = {'boxes': [], 'negative': True}
    await test_db.commit()
    run = create_run(vclient, auth_headers, ready)
    async def negative(*args): return dict(boxes=[], warnings=['no detections'])
    monkeypatch.setattr(validation, 'infer', negative)
    await validation.process_one(async_sessionmaker(test_engine, expire_on_commit=False))
    detail = vclient.get('/api/validation/runs/' + run['id'], headers=auth_headers).json()
    assert detail['metrics']['negative_images'] == 1 and detail['metrics']['map50'] is None
    assert detail['metrics']['coverage']['complete'] is True
    assert vclient.get('/api/validation/runs/' + run['id'] + '/items?error_type=correct', headers=auth_headers).json()['total'] == 1


@pytest.mark.asyncio
async def test_retry_reprocesses_only_failed_items(vclient, auth_headers, ready, test_db, test_engine, monkeypatch):
    from models import Asset, Annotation
    from services import validation
    from services.storage import get_storage
    picture = BytesIO()
    Image.new('RGB', (64, 48), 'red').save(picture, 'PNG')
    content = picture.getvalue()
    second = Asset(project_id=ready['project'].id, original_name='second.png', blob_path='second.png',
        content_hash=hashlib.sha256(content).hexdigest(), file_size=len(content),
        source_type='upload', width=64, height=48, format='PNG')
    test_db.add(second)
    await test_db.flush()
    test_db.add(Annotation(asset_id=second.id, annotation_data={'boxes': []}, version=1,
        is_latest=True, review_status='approved', reviewer_id=1))
    await test_db.commit()
    await get_storage().save_file(BytesIO(content), second.blob_path)
    run = create_run(vclient, auth_headers, ready)
    calls = []
    async def infer(config, image, *args):
        calls.append(image)
        if image == content: raise RuntimeError('private http://service secret /private/path')
        return dict(boxes=[], warnings=[])
    monkeypatch.setattr(validation, 'infer', infer)
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    assert await validation.process_one(sessions)
    assert await validation.process_one(sessions)
    path = '/api/validation/runs/' + run['id']
    failed = vclient.get(path, headers=auth_headers).json()
    assert failed['metrics']['coverage'] == dict(total=2, succeeded=1, failed=1, pending=0, canceled=0, complete=False)
    assert 'private' not in vclient.get(path + '/report', headers=auth_headers).text
    assert vclient.post(path + '/retry', headers=auth_headers).status_code == 200
    async def fixed(config, image, *args):
        calls.append(image)
        return dict(boxes=[], warnings=[])
    monkeypatch.setattr(validation, 'infer', fixed)
    assert await validation.process_one(sessions)
    assert not await validation.process_one(sessions)
    assert calls.count(ready['content']) == 1 and calls.count(content) == 2
    assert vclient.get(path, headers=auth_headers).json()['metrics']['coverage']['complete'] is True


@pytest.mark.asyncio
async def test_run_uses_pinned_truth_after_manual_update_and_model_disable_rejects_retry(vclient, auth_headers, ready, test_db, test_engine, monkeypatch):
    from services import validation
    dataset = create_set(vclient, auth_headers, ready)
    ready['annotation'].annotation_data = {'boxes': []}
    ready['annotation'].version = 2
    await test_db.commit()
    run = create_run(vclient, auth_headers, ready, dataset)
    async def infer(*args): return dict(boxes=[], warnings=[])
    monkeypatch.setattr(validation, 'infer', infer)
    await validation.process_one(async_sessionmaker(test_engine, expire_on_commit=False))
    path = '/api/validation/runs/' + run['id']
    assert vclient.get(path + '/items', headers=auth_headers).json()['items'][0]['truth'][0]['label'] == 'person'
    ready['model'].enabled = False
    await test_db.commit()
    assert vclient.post(path + '/retry', headers=auth_headers).status_code == 409


def test_schema_rejects_more_than_5000_explicit_images():
    from schemas.validation import ValidationSetCreate
    with pytest.raises(ValueError):
        ValidationSetCreate(name='too many', project_id=uuid4(), asset_ids=[uuid4() for _ in range(5001)])


@pytest.mark.asyncio
async def test_project_source_rejects_5001_without_reading_or_truncating(vclient, auth_headers, ready, test_db, monkeypatch):
    from models import Asset, Annotation
    from services import validation
    assets, annotations = [], []
    for index in range(5000):
        id = uuid4()
        assets.append(dict(id=id, project_id=ready['project'].id, original_name=str(index), blob_path='unread',
            content_hash=hashlib.sha256(str(index).encode()).hexdigest(), file_size=1, source_type='upload', width=64, height=48))
        annotations.append(dict(id=uuid4(), asset_id=id, annotation_data={'boxes': []}, version=1,
            is_latest=True, review_status='approved', reviewer_id=1))
    await test_db.execute(Asset.__table__.insert(), assets)
    await test_db.execute(Annotation.__table__.insert(), annotations)
    await test_db.commit()
    async def no_reads(*args): raise AssertionError('Cap must be checked before image I/O')
    monkeypatch.setattr(validation, 'read_sample', no_reads)
    response = vclient.post('/api/validation/sets', headers=auth_headers,
        json=dict(name='too big', project_id=str(ready['project'].id)))
    assert response.status_code == 400 and '5000' in response.text
    assert vclient.get('/api/validation/sets', headers=auth_headers).json()['total'] == 0


@pytest.mark.asyncio
async def test_confusion_filter_and_image_authorization_hash_check(vclient, auth_headers, ready, test_engine, monkeypatch):
    from services import validation
    from services.storage import get_storage
    run = create_run(vclient, auth_headers, ready)
    async def confusion(*args):
        return dict(boxes=[dict(label='car', x=1, y=2, width=10, height=20, score=.9)], warnings=[])
    monkeypatch.setattr(validation, 'infer', confusion)
    await validation.process_one(async_sessionmaker(test_engine, expire_on_commit=False))
    path = '/api/validation/runs/' + run['id']
    for filter in ('false_positive', 'false_negative', 'class_confusion'):
        assert vclient.get(path + '/items?error_type=' + filter, headers=auth_headers).json()['total'] == 1
    assert vclient.get(path + '/items?error_type=invalid', headers=auth_headers).status_code == 422
    item = vclient.get(path + '/items', headers=auth_headers).json()['items'][0]
    image_path = path + '/items/' + item['id'] + '/image'
    assert vclient.get(image_path).status_code == 401
    await get_storage().save_file(BytesIO(b'changed'), ready['asset'].blob_path)
    assert vclient.get(image_path, headers=auth_headers).status_code == 409


@pytest.mark.asyncio
async def test_startup_recovers_running_item_for_manual_retry(vclient, auth_headers, ready, test_engine, test_db, monkeypatch):
    import database
    from models.validation import ValidationRun, ValidationItem
    from services import validation
    run = create_run(vclient, auth_headers, ready)
    row = await test_db.get(ValidationRun, UUID(run['id']))
    item = await test_db.scalar(select(ValidationItem).where(ValidationItem.run_id == row.id))
    row.status, item.status = 'running', 'running'
    await test_db.commit()
    monkeypatch.setattr(database, 'AsyncSessionLocal', async_sessionmaker(test_engine, expire_on_commit=False))
    await validation.start_worker()
    await validation.stop_worker()
    result = vclient.get('/api/validation/runs/' + run['id'], headers=auth_headers).json()
    assert result['status'] == 'failed' and result['failed_count'] == 1
    assert result['metrics']['coverage']['complete'] is False


def test_set_creation_only_freezes_metadata_without_reading_images(vclient, auth_headers, ready, monkeypatch):
    from services import validation
    async def no_image_reads(*args):
        raise AssertionError('Metadata freezing must not read originals or hold locks during file I/O')
    monkeypatch.setattr(validation, 'read_sample', no_image_reads)
    assert create_set(vclient, auth_headers, ready)['total'] == 1


@pytest.mark.asyncio
async def test_report_metrics_and_items_share_snapshot_during_worker_commit(monkeypatch):
    import asyncio
    from copy import deepcopy
    from datetime import datetime
    import json
    from types import SimpleNamespace
    from api.validation_routes import report
    from services import validation
    from services.detection_metrics import aggregate_metrics
    from schemas.validation import ValidationView

    now = datetime(2026, 10, 16)
    run = SimpleNamespace(id=uuid4(), set_id=uuid4(), tenant_id=1, name='report', status='running',
        total=1, processed=0, failed_count=0, config=dict(confidence=.25, match_iou=.5), metrics={},
        error=None, created_at=now, updated_at=now, model_snapshot=dict(id=str(uuid4()), revision=1, name='detector'))
    dataset = SimpleNamespace(id=run.set_id, tenant_id=1, project_id=uuid4(), user_id=1,
        name='gold', labels=['person'], source={}, total=1, created_at=now)
    truth = [dict(label='person', x=1, y=2, width=10, height=20)]
    stored = SimpleNamespace(id=uuid4(), asset_id=uuid4(), original_name='sample.png', width=64, height=48,
        status='running', truth=truth, predictions=[], evaluation={}, error=None)
    first_read, committed = asyncio.Event(), asyncio.Event()

    async def scope(*args, **kwargs):
        return run

    async def set_scope(*args, **kwargs):
        return dataset

    class ReadCommittedDB:
        item_reads = 0

        async def snapshot(self):
            items = [deepcopy(stored)]
            self.item_reads += 1
            if self.item_reads == 1:
                first_read.set()
                await committed.wait()
            return items

        async def execute(self, query):
            items = await self.snapshot()
            return SimpleNamespace(all=lambda: [(item.status, item.truth, item.predictions) for item in items])

        async def scalars(self, query):
            items = await self.snapshot()
            return SimpleNamespace(all=lambda: items)

    async def worker_commit():
        await first_read.wait()
        stored.status = 'completed'
        stored.predictions = [{**truth[0], 'score': .9}]
        committed.set()

    monkeypatch.setattr(validation, 'run_scope', scope)
    monkeypatch.setattr(validation, 'set_scope', set_scope)
    db = ReadCommittedDB()
    response, _ = await asyncio.wait_for(asyncio.gather(
        report(run.id, db=db, user=SimpleNamespace(tenant_id=1), view=ValidationView()), worker_commit()), timeout=5)
    document = json.loads(response.body)
    completed = [item for item in document['items'] if item['status'] == 'completed']
    metrics = document['run']['metrics']
    assert metrics['images'] == len(completed)
    assert metrics == {**aggregate_metrics(completed, dataset.labels), 'coverage': dict(
        total=1, succeeded=len(completed), failed=0, pending=1-len(completed), canceled=0, complete=bool(completed))}
    assert stored.status == 'completed' and db.item_reads == 1
    assert run.metrics == {} and run.updated_at == now


@pytest.mark.asyncio
@pytest.mark.parametrize('supplied_statuses', [[], ['completed', 'failed', 'canceled', 'running', 'pending']])
async def test_supplied_report_items_bypass_terminal_metric_cache(monkeypatch, supplied_statuses):
    from copy import deepcopy
    from types import SimpleNamespace
    from services import validation

    run = SimpleNamespace(set_id=uuid4(), tenant_id=1, total=len(supplied_statuses), status='completed', model_snapshot={},
        config=dict(confidence=.25, match_iou=.5), metrics=dict(images=999, coverage=dict(complete=True)))
    original = deepcopy(run.metrics)
    truth = [dict(label='person', x=1, y=2, width=10, height=20)]
    items = [SimpleNamespace(status=status, truth=truth, predictions=[{**truth[0], 'score': .9}])
             for status in supplied_statuses]

    async def set_scope(*args, **kwargs):
        return SimpleNamespace(labels=['person'])

    monkeypatch.setattr(validation, 'set_scope', set_scope)
    # No session methods: supplied items must not trigger another item query.
    metrics = await validation.calculate_metrics(object(), run, items=items)
    succeeded = supplied_statuses.count('completed')
    assert metrics['images'] == metrics['tp'] == succeeded
    assert metrics['coverage'] == dict(total=len(items), succeeded=succeeded,
        failed=supplied_statuses.count('failed'), canceled=supplied_statuses.count('canceled'),
        pending=supplied_statuses.count('pending')+supplied_statuses.count('running'), complete=succeeded == len(items))
    assert run.metrics == original
