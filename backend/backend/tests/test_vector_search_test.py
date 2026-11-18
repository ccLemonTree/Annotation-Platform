def test_vector_test_catalog_is_a_separate_authenticated_route(client):
    response = client.get('/api/vector-test/sources')
    assert response.status_code in (401, 403)


async def test_project_vector_test_upload_stays_out_of_business_images(
    client, auth_headers, test_engine, test_db, test_project, test_asset, monkeypatch
):
    import asyncio
    from io import BytesIO
    from zipfile import ZipFile
    from PIL import Image
    from sqlalchemy import create_engine, func, select
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from config.settings import settings
    from integrations.smarteye import device_capture, runtime
    from integrations.smarteye.management_schema import devices
    from models import Asset, LibraryImage, ModelService
    from models.training_run import TrainingRun
    from models.vector_test import VectorTestDataset, VectorTestIndex, VectorTestModel, VectorTestSample, VectorTestSampleItem
    from services import vector_test, vector_test_data as samples

    async with test_engine.begin() as connection:
        for model in (ModelService, TrainingRun, VectorTestModel, VectorTestIndex, VectorTestDataset, VectorTestSample, VectorTestSampleItem):
            await connection.run_sync(model.__table__.create)
    base = f'/api/vector-test/datasets/{test_project.id}'
    image = BytesIO()
    Image.new('RGB', (20, 20), 'green').save(image, 'PNG')
    response = client.post(base+'/images', headers=auth_headers,
                           files={'file': ('test.png', image.getvalue(), 'image/png')})
    assert response.status_code == 201
    assert client.get(base+'/indexes?isolated=true', headers=auth_headers).json()['total'] == 1
    images = client.get(base+'/images', headers=auth_headers).json()['items']
    assert images[0]['original_name'] == 'test.png'
    assert client.get(base+'/images/'+images[0]['id']+'/content', headers=auth_headers).content == image.getvalue()
    archive = BytesIO()
    with ZipFile(archive, 'w') as output:
        output.writestr('images/copy.png', image.getvalue())
        output.writestr('images/broken.png', b'broken')
    assert client.post(base+'/images', headers=auth_headers, files={'file':('images.zip',archive.getvalue())}).json() == {
        'added': 0, 'duplicate': 1, 'invalid': 1}

    capture_root = create_engine(test_engine.url.set(drivername='sqlite'))
    with capture_root.connect() as connection:
        connection.exec_driver_sql('PRAGMA journal_mode=WAL')
    runtime.metadata.create_all(capture_root)
    worker = runtime.Runtime(capture_root, settings.STORAGE_LOCAL_PATH)
    monkeypatch.setattr(runtime, '_runtime', worker)
    monkeypatch.setattr(device_capture, 'list_platforms_internal', lambda: [{'plat_id':'test', 'state':'1'}])
    with capture_root.begin() as connection:
        connection.execute(devices.insert().values(tenant_id=1, plat_id='test', device_id='p1', payload={}))
    schedule_url = '/api/smarteye/device-capture/schedules'
    schedule = client.post(schedule_url, headers=auth_headers, json=dict(project_id=str(test_project.id),
        vector_test=True, name='检索测试抓图', interval_seconds=1, devices=[dict(plat_id='test', device_id='p1')]))
    assert schedule.status_code == 201, schedule.text
    assert client.get(schedule_url, headers=auth_headers).json()['data'] == []
    plans = client.get(f'{schedule_url}?vector_test=1&project_id={test_project.id}', headers=auth_headers).json()['data']
    assert [plan['sched_id'] for plan in plans] == [schedule.json()['data']['sched_id']]

    capture = BytesIO()
    Image.new('RGB', (20, 20), 'red').save(capture, 'PNG')
    owner = device_capture._get(plans[0]['sched_id'])['owner']
    worker.create('test-capture', 'device-capture', {'status': 'running'}, owner)
    key = await asyncio.to_thread(worker.save_image, 'test-capture', worker.root/'smarteye'/'test-capture'/'capture.png', capture.getvalue(),
        1, device_info=dict(plat_id='test', device_id='p1', device_name='测试摄像机'))
    assert key.startswith(f'vector-test/{test_project.id}/originals/')
    assert await asyncio.to_thread(worker.save_image, 'test-capture', worker.root/'smarteye'/'test-capture'/'capture.png',
                                   capture.getvalue(), 1) == key
    capture_root.dispose()
    assert client.get(base+'/indexes?isolated=true', headers=auth_headers).json()['originals'] == 2

    detector = ModelService(tenant_id=1, name='detector', key='isolated-test', kind='detection',
                            protocol='model_service', base_url='http://127.0.0.1:8000', endpoint='/detect')
    test_db.add(detector)
    await test_db.commit()
    response = client.post(base+'/detection?isolated=true', headers=auth_headers,
                           json={'model_id': str(detector.id), 'labels': ['car']})
    assert response.status_code == 201, response.text
    monkeypatch.setattr(samples, 'AsyncSessionLocal', async_sessionmaker(test_engine, expire_on_commit=False))

    async def infer(*args, **kwargs):
        return {'boxes': [{'label':'car','x':0,'y':0,'width':10,'height':10}]}

    monkeypatch.setattr(samples, 'infer', infer)
    assert await samples.detect_one(None)
    assert await samples.detect_one(None)
    assert not await samples.detect_one(None)
    targets = client.get(base+'/images?targets=true', headers=auth_headers).json()['items']
    assert len(targets) == 2 and all(row['parent_id'] for row in targets)
    for target in targets:
        preview = client.get(f'{base}/images/{target["id"]}/preview?isolated=true', headers=auth_headers)
        assert preview.status_code == 200
        with Image.open(BytesIO(preview.content)) as rendered:
            assert rendered.size == (20, 20)
            red, green, blue = rendered.getpixel((5, 0))
            assert red > 180 and green > 150 and blue < 140
    assert client.get(base+'/indexes?isolated=true', headers=auth_headers).json()['detection']['counts']['completed'] == 2

    run = TrainingRun(tenant_id=1, user_id=1, name='WeMM', kind='model_merge', status='completed',
                      config={}, source={}, server={}, output_dir='/fixture')
    test_db.add(run)
    await test_db.flush()
    models = [VectorTestModel(tenant_id=1, run_id=run.id, name=f'WeMM {number}', source={}, server={'name':'test'},
                              port=8000+number, state='ready') for number in range(2)]
    test_db.add_all(models)
    await test_db.commit()
    for model in models:
        assert client.put(f'{base}/indexes/{model.id}?isolated=true', headers=auth_headers).status_code == 200

    indexed = set()

    class Milvus:
        def __init__(self, client, collection):
            assert collection == vector_test.SAMPLE_COLLECTION

        async def upsert(self, key, vector, tenant, project, asset, snapshot, digest):
            assert str(asset) in {row['id'] for row in targets} and project == test_project.id
            indexed.add(key)

        async def search(self, vector, tenant, project, space, keys, limit):
            assert set(keys) <= indexed and len(keys) == 2
            return [{'id':key, 'distance':.8} for key in keys[:limit]]

    slow_started, release_slow, fast_indexed = asyncio.Event(), asyncio.Event(), asyncio.Event()
    slow, fast = sorted(models, key=lambda model: model.id)
    fast_keys = {vector_test.vector_id(fast, test_project.id, row.id, row.content_hash) for row in
                 (await test_db.scalars(select(VectorTestSample).where(VectorTestSample.parent_id.is_not(None)))).all()}

    async def vectors(db, model, inputs):
        if model.id == slow.id and not release_slow.is_set():
            slow_started.set()
            await release_slow.wait()
        elif model.id == fast.id:
            await slow_started.wait()
        return [{'vector':[1.0]*2048} for _ in inputs]

    upsert = Milvus.upsert

    async def track_fast_index(self, key, *args):
        await upsert(self, key, *args)
        if fast_keys <= indexed:
            fast_indexed.set()

    monkeypatch.setattr(Milvus, 'upsert', track_fast_index)

    monkeypatch.setattr(vector_test, 'AsyncSessionLocal', async_sessionmaker(test_engine, expire_on_commit=False))
    monkeypatch.setattr(vector_test, 'Milvus', Milvus)
    monkeypatch.setattr(vector_test, 'vectors', vectors)
    await test_db.commit()
    worker = asyncio.create_task(vector_test.index_loop())
    try:
        await asyncio.wait_for(fast_indexed.wait(), timeout=5)
        assert indexed == fast_keys
        release_slow.set()
        deadline = asyncio.get_running_loop().time() + 5
        while True:
            progress = client.get(base+'/indexes?isolated=true', headers=auth_headers).json()
            if all(row['complete'] == 2 for row in progress['items']):
                break
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(.05)
        assert len(indexed) == 4
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    assert not (await test_db.scalars(select(VectorTestIndex).where(VectorTestIndex.legacy_enabled))).all()
    model_ids = [str(model.id) for model in models]
    searches = [client.post(base+'/search?isolated=true', headers=auth_headers, data=dict(model_ids=model_ids,text='车辆')),
                client.post(base+'/search?isolated=true', headers=auth_headers, data=dict(model_ids=model_ids),
                            files={'image':('query.png',image.getvalue())})]
    for response in searches:
        assert response.status_code == 200, response.text
        assert response.json()['candidates'] == 2
        assert len(response.json()['results']) == 2
    assert await test_db.scalar(select(func.count()).select_from(Asset).where(Asset.project_id == test_project.id)) == 1
    assert await test_db.scalar(select(func.count()).select_from(LibraryImage)) == 0


async def test_vector_test_uses_existing_project_assets(client, auth_headers, test_engine, test_project, test_asset):
    from models.vector_test import VectorTestIndex

    async with test_engine.begin() as connection:
        await connection.run_sync(VectorTestIndex.__table__.create)

    response = client.get(f'/api/vector-test/datasets/{test_project.id}/indexes', headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == {'total': 1, 'items': []}


async def test_detection_indexes_only_crops_from_its_workflow(
    client, auth_headers, test_engine, test_db, test_project, test_asset
):
    from sqlalchemy import select
    from models import Asset, ModelService
    from models.project_work import ProjectWorkItem, ProjectWorkJob
    from models.vector_test import VectorTestIndex

    async with test_engine.begin() as connection:
        for model in (ModelService, ProjectWorkJob, ProjectWorkItem, VectorTestIndex):
            await connection.run_sync(model.__table__.create)
    detector = ModelService(tenant_id=1, name='detector', key='detect-test', kind='detection',
                            protocol='model_service', base_url='http://127.0.0.1:8000', endpoint='/detect')
    crop = Asset(project_id=test_project.id, original_name='target.png', blob_path='target.png',
                 content_hash='target', file_size=12, source_type='model_crop', width=16, height=16,
                 format='PNG', annotation_status='pending')
    test_db.add_all([detector, crop])
    await test_db.commit()
    base = f'/api/vector-test/datasets/{test_project.id}'
    response = client.post(base+'/detection', headers=auth_headers, json={'model_id': str(detector.id)})
    assert response.status_code == 201
    job_id = response.json()['id']
    from uuid import UUID
    rows = (await test_db.scalars(select(ProjectWorkItem).where(ProjectWorkItem.job_id == UUID(job_id)))).all()
    assert [row.asset_id for row in rows] == [test_asset.id]
    assert client.get(base+'/indexes', headers=auth_headers).json()['total'] == 0
    rows[0].status = 'completed'
    rows[0].result = {'asset_ids': [str(crop.id)]}
    await test_db.commit()
    assert client.get(base+'/indexes', headers=auth_headers).json()['total'] == 1


def test_shared_candidates_exclude_query_and_use_matching_content():
    from services.vector_test import shared_candidates

    assert shared_candidates([
        {'a': 'same', 'b': 'old', 'c': 'query', 'd': 'other'},
        {'a': 'same', 'b': 'new', 'c': 'query'},
    ], excluded_hash='query') == {'a'}


def test_only_completed_wemm_merges_can_be_deployed():
    from types import SimpleNamespace

    from services.vector_test import options

    run = SimpleNamespace(kind='model_merge', status='completed', config={'model_kind': 'wemm'})
    assert options(run)
    run.status = 'running'
    assert not options(run)
    run.status = 'completed'
    run.config['model_kind'] = 'swift_sft'
    assert not options(run)


def test_vector_ids_separate_asset_content_model_and_dataset():
    from types import SimpleNamespace

    from services.vector_test import vector_id

    one, two = SimpleNamespace(id='one'), SimpleNamespace(id='two')
    assert len({vector_id(one, 'data', 'image', 'digest'),
                vector_id(two, 'data', 'image', 'digest'),
                vector_id(one, 'other', 'image', 'digest'),
                vector_id(one, 'data', 'image', 'changed')}) == 4


def test_old_crop_recovers_exact_region_from_original_pixels():
    from PIL import Image
    from services.vector_test_data import locate_crop

    original = Image.new('RGB', (13, 9))
    original.putpixel((8, 4), (5, 30, 163))
    crop = original.crop((6, 3, 12, 8))
    assert locate_crop(original, crop) == (6, 3, 12, 8)
    assert locate_crop(original, original.crop((0, 0, 2, 2))) is None


def test_uncached_image_pull_survives_independent_status_checks(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from services import vector_test_executor as executor

    image = SimpleNamespace(returncode=1)
    monkeypatch.setattr(executor, 'docker', lambda *args: image)
    starts = []
    monkeypatch.setattr(executor, 'launch_image_pull', lambda stage: starts.append(stage) or 42)
    monkeypatch.setattr(executor, 'pull_running', lambda stage, pid: pid == 42)
    assert executor.ensure_image(tmp_path) == {'ready': False}
    assert executor.ensure_image(tmp_path) == {'ready': False}
    assert starts == [tmp_path]
    image.returncode = 0
    assert executor.ensure_image(tmp_path) == {'ready': True}
