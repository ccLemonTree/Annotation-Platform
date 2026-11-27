import asyncio
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from zipfile import ZipFile
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select, update

from models import Annotation, Asset, ImageUpload, ProjectUploadKey
from services import image_uploads
from schemas.project_work import WorkflowCreate
from services.project_workflow import create
from tests.test_project_work import work_env
from tests.test_resumable_upload import archive


@pytest.mark.asyncio
async def test_import_reviewed_description_json(client,auth_headers,work_env):
    env=work_env
    env.project.task_type='caption'
    env.asset.original_name='1028_1.jpg'
    await env.db.commit()
    path=f'/api/projects/{env.project.id}/description-labels'
    def row(name,answer):
        return {'image':'step3/lprrec/'+name,'conversations':[
            {'from':'human','value':'<image>\n车牌号识别'},{'from':'gpt','value':answer}]}
    def upload(rows):
        return client.post(path,headers=auth_headers,files={'file':('output.json',
            json.dumps(rows,ensure_ascii=False).encode())})
    first=upload([row('1028_1.JPG','车牌模糊或车牌不完整'),row('missing.jpg','说明')])
    assert first.status_code==200,first.text
    assert [r['status'] for r in first.json()['items']]==['imported','missing']
    annotations=client.get('/api/annotations',headers=auth_headers,params={'asset_id':str(env.asset.id)}).json()['items']
    assert len(annotations)==1
    assert annotations[0]['annotation_data']=={'description':'车牌模糊或车牌不完整'}
    assert annotations[0]['review_status']=='approved'
    assert upload([row('1028_1.jpg','车牌模糊或车牌不完整')]).json()['items'][0]['status']=='unchanged'
    assert upload([row('1028_1.jpg','不同答案')]).json()['items'][0]['status']=='conflict'
    assert upload([row('1028_1.jpg','说明'),row('1028_1.JPG','另一个答案')]).json()['items'][0]['status']=='conflict'


@pytest.mark.asyncio
@pytest.mark.parametrize('external', [True, False])
async def test_import_annotation_json(client, auth_headers, work_env, test_engine, external):
    env = work_env
    env.project.task_type = 'vector'
    env.project.label_schema = {}
    env.asset.original_name = 'original.jpg'
    env.asset.width = 100
    env.asset.height = 80
    crop = Asset(project_id=env.project.id, original_name='crop.jpg', blob_path='crop.jpg',
                 content_hash='crop', width=40, height=30, file_size=1, source_type='model_crop')
    env.db.add(crop)
    for name in ('CROP.JPG', 'bad.jpg', 'dup.jpg', 'DUP.JPG'):
        env.db.add(Asset(project_id=env.project.id, original_name=name, blob_path=name,
            content_hash=name, width=40, height=30, file_size=1, source_type='upload'))
    await env.db.commit()
    base = f'/api/projects/{env.project.id}'
    url, headers = base + '/annotation-labels', auth_headers
    if external:
        async with test_engine.begin() as conn:
            await conn.run_sync(ProjectUploadKey.__table__.create)
        token = client.post(base + '/upload-keys', headers=auth_headers, json={'name': 'fixture'}).json()['token']
        url, headers = base + '/ingest/annotation-labels', {'Authorization': 'Bearer ' + token}
        assert client.post(url, headers=auth_headers, files={'file': ('labels.json', b'{}')}).status_code == 401
        other = f'/api/projects/{uuid4()}/ingest/annotation-labels'
        assert client.post(other, headers=headers, files={'file': ('labels.json', b'{}')}).status_code == 401

    original = [
        {'box': [1, 2, 21, 32], 'label': ['车辆', '公交'], 'attribute': {'颜色': '蓝'}, 'description': '蓝色公交'},
        {'box': [25, 10, 42, 79], 'label': ['行人', '白天'], 'attribute': {}, 'description': '背对镜头'},
    ]
    cropped = {'label': ['公交', '车辆'], 'attribute': {'颜色': '蓝', '车牌': ''}, 'description': '车牌不可见'}
    records = {'bad.jpg': [{'box': [1, 1, 1, 5], 'label': ['无效框类别']}],
               'incoming/ORIGINAL.JPG': original, 'crop.jpg': cropped, 'missing.jpg': cropped,
               'crop.png': cropped, 'dup.jpg': original}

    def upload(data):
        return client.post(url, headers=headers,
            files={'file': ('labels.json', json.dumps(data, ensure_ascii=False).encode())})

    response = upload(records)
    assert response.status_code == 200, response.text
    assert [item['status'] for item in response.json()['items']] == [
        'invalid', 'imported', 'imported', 'missing', 'missing', 'conflict']
    assert [item['status'] for item in upload(records).json()['items'][1:3]] == ['unchanged', 'unchanged']
    for listing_url, id_field in [('/api/assets', 'id'), ('/api/annotations', 'asset_id')]:
        result = client.get(listing_url, headers=auth_headers, params={'project_id': str(env.project.id), 'label': '公交'})
        assert result.status_code == 200, result.text
        assert {row[id_field] for row in result.json()['items']} == {str(env.asset.id), str(crop.id)}
    neighbors = client.get(f'/api/assets/{env.asset.id}/neighbors', headers=auth_headers, params={'label': '公交'})
    assert neighbors.json()['total'] == 2
    filtered = client.get('/api/annotations', headers=auth_headers,
        params={'project_id': str(env.project.id), 'scope': 'annotation', 'label': '白天'}).json()
    assert [row['asset_id'] for row in filtered['items']] == [str(env.asset.id)]
    assert upload({'crop.jpg': {**cropped, 'description': '新描述'}}).json()['items'][0]['status'] == 'conflict'
    assert upload({'crop.jpg': cropped, 'elsewhere/CROP.JPG': cropped}).json()['items'][0]['status'] == 'conflict'
    assert client.post(url, headers=headers, files={'file': ('labels.json', b'{"crop.jpg": {}, "crop.jpg": {}}')}).status_code == 422
    async with env.sessions() as db:
        project = await db.get(type(env.project), env.project.id)
        assert set(project.label_schema['labels']) == {'车辆', '公交', '行人'}
        assert project.label_schema['imported_box_labels'] == ['公交', '白天']
        annotations = {ann.asset_id: ann for ann in (await db.scalars(select(Annotation).where(
            Annotation.asset_id.in_([env.asset.id, crop.id])))).all()}
        assert len(annotations) == 2
        assert {ann.review_status for ann in annotations.values()} == {'pending'}
        boxes = annotations[env.asset.id].annotation_data['boxes']
        assert boxes[0] == {'x': 1, 'y': 2, 'width': 20, 'height': 30, 'label': '车辆',
                            'labels': ['车辆', '公交'], 'attributes': {'颜色': '蓝'}, 'description': '蓝色公交'}
        assert [box['description'] for box in boxes] == ['蓝色公交', '背对镜头']
        image = annotations[crop.id].annotation_data
        assert image['description'] == '车牌不可见'
        assert image['labels'] == ['公交', '车辆']
        assert {field['name']: image['attributes'][field['key']]['value']
                for field in image['attribute_schema']['fields']} == {'颜色': '蓝', '车牌': None}
        crop_annotation_id = annotations[crop.id].id
    response = client.put(f'/api/annotations/{crop_annotation_id}', headers=auth_headers, json={
        'kind': 'image', 'base_version': 1, 'annotation_data': {'description': '人工核对后更新',
            'attributes': image['attributes'], 'attribute_schema': image['attribute_schema']},
    })
    assert response.status_code == 200, response.text
    assert response.json()['annotation_data']['labels'] == cropped['label']


@pytest.mark.asyncio
@pytest.mark.parametrize('external', [True, False])
@pytest.mark.parametrize('zipped', [True, False])
@pytest.mark.parametrize('with_classes', [True, False])
async def test_yolo_label_folder_import(client, auth_headers, work_env, test_engine, external, zipped, with_classes):
    env = work_env
    env.project.task_type = 'detection'
    env.project.label_schema = {}
    ids = {}
    for name in ('Car.JPG', 'background.png', 'dup.jpg', 'dup.png'):
        asset = Asset(project_id=env.project.id, original_name=name, blob_path=name,
            content_hash=name, width=200, height=100, file_size=1, source_type='upload')
        env.db.add(asset)
        await env.db.flush()
        ids[name] = asset.id
    await env.db.commit()
    async with test_engine.begin() as conn:
        await conn.run_sync(ProjectUploadKey.__table__.create)
    base = f'/api/projects/{env.project.id}'
    if external:
        token = client.post(base + '/upload-keys', headers=auth_headers, json={'name':'fixture'}).json()['token']
        headers = {'Authorization': 'Bearer ' + token}
        url = base + '/ingest/yolo-labels'
        assert client.post(url, headers=auth_headers).status_code == 401
    else:
        headers = auth_headers
        url = base + '/yolo-labels'
    tables = [('classes', ('classes.txt', b'car\ntruck'))] if with_classes else []
    files = list(tables)
    files += [('files', (name, data)) for name, data in [
        ('Car.txt', b'0 0.5 0.5 0.2 0.4\n1 0.7 0.7 0.1 0.1'), ('background.txt', b''),
        ('dup.txt', b'0 0.5 0.5 0.2 0.4'), ('absent.txt', b'0 0.5 0.5 0.2 0.4')]]
    upload = [('archive', ('labels.zip', archive('zip', [
        ('labels/' + name, content) for _, (name, content) in files]), 'application/zip'))] if zipped else files
    response = client.post(url, headers=headers, files=upload)
    assert response.status_code == 200, response.text
    assert [item['status'] for item in response.json()['items']] == ['imported', 'imported', 'conflict', 'missing']
    repeated = client.post(url, headers=headers, files=files).json()
    assert [item['status'] for item in repeated['items'][:2]] == ['unchanged', 'unchanged']
    changed = tables + [('files', ('Car.txt', b'1 0.5 0.5 0.2 0.4'))]
    assert client.post(url, headers=headers, files=changed).json()['items'][0]['status'] == 'conflict'
    if zipped:
        unsafe = archive('zip', [('classes.txt',b'car'), ('../Car.txt',b'')])
        assert client.post(url, headers=headers, files={'archive':('labels.zip',unsafe)}).status_code == 422
    async with env.sessions() as db:
        rows = {row.asset_id:row for row in (await db.scalars(select(Annotation).where(
            Annotation.asset_id.in_(ids.values())))).all()}
        assert len(rows) == 2
        car = rows[ids['Car.JPG']]
        assert car.review_status == 'pending' and car.annotation_source == 'import_yolo'
        box = car.annotation_data['boxes'][0]
        assert box['width'] == pytest.approx(40)
        if with_classes:
            assert box['label'] == 'car'
        else:
            assert box['yolo_class_id'] == 0
        assert rows[ids['background.png']].annotation_data['boxes'] == []
    if not with_classes:
        project = client.get(base, headers=auth_headers).json()
        assert not project['label_schema'].get('labels')
        review_url = f'/api/annotations/{car.id}/review'
        review = {'kind': 'detection', 'approved': True}
        assert client.post(review_url, headers=auth_headers, json=review).status_code == 400
        summary_url = f'/api/export/projects/{env.project.id}/summary'
        assert client.get(summary_url, headers=auth_headers).json()['all']['images'] == 1
        added = client.post(base + '/labels', headers=auth_headers, json={'labels': ['car', 'truck']})
        assert added.status_code == 200
        first, second = car.annotation_data['boxes']
        assert second['yolo_class_id'] == 1
        first = dict(first, label='car')
        del first['yolo_class_id']
        partial = client.put(f'/api/annotations/{car.id}', headers=auth_headers, json={
            'kind': 'detection', 'base_version': car.version,
            'annotation_data': {'boxes': [first, second]},
        })
        assert partial.status_code == 200, partial.text
        assert client.get(summary_url, headers=auth_headers).json()['all']['images'] == 1
        current = partial.json()
        second = dict(second, label='truck')
        del second['yolo_class_id']
        saved = client.put(f"/api/annotations/{current['id']}", headers=auth_headers, json={
            'kind': 'detection', 'base_version': current['version'],
            'annotation_data': {'boxes': [first, second]},
        })
        assert saved.status_code == 200, saved.text
        assert client.post(f"/api/annotations/{saved.json()['id']}/review", headers=auth_headers, json=review).status_code == 200
        assert client.get(summary_url, headers=auth_headers).json()['all']['images'] == 2


@pytest.mark.asyncio
async def test_map_yolo_classes_by_upload_batch(client, auth_headers, work_env):
    env = work_env
    env.project.task_type = 'detection'
    env.project.label_schema = {'labels': ['manual']}
    assets = {}
    for name in ('one', 'two', 'other', 'legacy'):
        asset = Asset(project_id=env.project.id, original_name=name+'.jpg', blob_path=name,
            content_hash=name, width=200, height=100, file_size=1, source_type='upload')
        env.db.add(asset)
        await env.db.flush()
        assets[name] = asset.id
    await env.db.commit()
    base = f'/api/projects/{env.project.id}'
    selected, other = str(uuid4()), str(uuid4())
    for name, batch in [('one', selected), ('two', selected), ('other', other), ('legacy', None)]:
        response = client.post(base+'/yolo-labels', headers=auth_headers,
            data={'batch_id': batch} if batch else {},
            files={'files': (name+'.txt', b'0 .5 .5 .2 .4\n1 .7 .7 .1 .1')})
        assert response.json()['items'][0]['status'] == 'imported'
    async with env.sessions() as db:
        legacy = await db.scalar(select(Annotation).where(Annotation.asset_id==assets['legacy']))
        provenance = dict(legacy.annotation_data['import_source'])
        del provenance['batch_id']
        legacy.annotation_data = {**legacy.annotation_data, 'import_source': provenance}
        await db.commit()
    endpoint = base+'/yolo-class-mapping'
    batches = client.get(endpoint, headers=auth_headers)
    assert batches.status_code == 200
    assert {row['batch_id']: row['images'] for row in batches.json()} == {selected: 2, other: 1, None: 1}
    assert all(row['classes'] == [{'id': 0, 'boxes': row['images']}, {'id': 1, 'boxes': row['images']}]
        for row in batches.json())
    async with env.sessions() as db:
        initial = {row.asset_id: row for row in (await db.scalars(select(Annotation).where(
            Annotation.asset_id.in_(assets.values())))).all()}
    one = initial[assets['one']]
    first, second = one.annotation_data['boxes']
    manual = {key: value for key, value in second.items() if key != 'yolo_class_id'}
    partial = client.put(f'/api/annotations/{one.id}', headers=auth_headers, json={
        'kind': 'detection', 'base_version': one.version,
        'annotation_data': {'boxes': [first, {**manual, 'label': 'manual'}]},
    })
    assert partial.status_code == 200
    request = {'batch_id': selected, 'labels': ['person']}
    result = client.post(endpoint, headers=auth_headers, json=request)
    assert result.status_code == 200, result.text
    assert (result.json()['updated_images'], result.json()['updated_boxes']) == (2, 2)
    assert client.post(endpoint, headers=auth_headers, json=request).json()['updated_boxes'] == 0
    async with env.sessions() as db:
        current = {row.asset_id: row for row in (await db.scalars(select(Annotation).where(
            Annotation.asset_id.in_(assets.values()), Annotation.is_latest.is_(True)))).all()}
        for name in ('one', 'two'):
            box = current[assets[name]].annotation_data['boxes'][0]
            assert box == {**{key: value for key, value in first.items() if key != 'yolo_class_id'}, 'label': 'person'}
            assert current[assets[name]].review_status == 'pending'
        assert current[assets['one']].annotation_data['boxes'][1]['label'] == 'manual'
        assert current[assets['two']].annotation_data['boxes'][1]['yolo_class_id'] == 1
        for name in ('other', 'legacy'):
            assert current[assets[name]].id == initial[assets[name]].id
        assert not (await db.get(Annotation, one.id)).is_latest
    legacy = client.post(endpoint, headers=auth_headers, json={'batch_id': None, 'labels': ['person', 'car']})
    assert legacy.json()['updated_boxes'] == 2
    remaining = next(row for row in client.get(endpoint, headers=auth_headers).json() if row['batch_id'] == selected)
    assert remaining['classes'] == [{'id': 1, 'boxes': 1}]
    sparse = client.post(endpoint, headers=auth_headers, json={'batch_id': selected, 'class_labels': {'1': 'car'}})
    assert sparse.status_code == 200, sparse.text
    assert sparse.json() == {'updated_images': 1, 'updated_boxes': 1}
    assert [row['batch_id'] for row in client.get(endpoint, headers=auth_headers).json()] == [other]


@pytest.mark.asyncio
@pytest.mark.parametrize('already_mapped', [False, True])
async def test_merged_yolo_categories_export_as_one_class(client, auth_headers, work_env, already_mapped):
    env = work_env
    env.project.task_type = 'detection'
    env.project.label_schema = {'labels': []}
    env.asset.original_name = 'fixture.png'
    await env.db.commit()
    project_id, asset_id, batch_id = env.project.id, env.asset.id, str(uuid4())
    base = f'/api/projects/{project_id}'
    imported = client.post(base+'/yolo-labels', headers=auth_headers, data={'batch_id': batch_id},
        files={'files': ('fixture.txt', b'0 .25 .25 .25 .25\n1 .75 .75 .25 .25')})
    assert imported.status_code == 200 and imported.json()['items'][0]['status'] == 'imported'
    mapped = client.post(base+'/yolo-class-mapping', headers=auth_headers,
        json={'batch_id': batch_id, 'class_labels': {'0': 'smoke', '1': 'fire' if already_mapped else 'smoke'}})
    assert mapped.status_code == 200 and mapped.json() == {'updated_images': 1, 'updated_boxes': 2}
    if already_mapped:
        merged = client.put(base, headers=auth_headers, json={
            'base_label_schema': {'labels': ['smoke', 'fire']},
            'label_schema': {'labels': ['smoke']}, 'label_renames': {'fire': 'smoke'},
        })
        assert merged.status_code == 200, merged.text
    exported = client.get(f'/api/export/projects/{project_id}/export', headers=auth_headers,
        params={'format': 'yolo', 'approved_only': False})
    assert exported.status_code == 200, exported.text
    with ZipFile(BytesIO(exported.content)) as package:
        assert package.read(f'labels/all/{asset_id}.txt').decode().splitlines() == [
            '0 0.2500000000 0.2500000000 0.2500000000 0.2500000000',
            '0 0.7500000000 0.7500000000 0.2500000000 0.2500000000',
        ]
        assert package.read('data.yaml').decode().split('names:\n')[1] == '  0: "smoke"\n'


@pytest.mark.asyncio
@pytest.mark.parametrize('split_workflow',[False,True])
async def test_project_ingest_scopes_key_and_queues_new_images_once(client, auth_headers, work_env, test_engine, split_workflow):
    env = work_env
    async with test_engine.begin() as conn:
        await conn.run_sync(ProjectUploadKey.__table__.create)
    base = f'/api/projects/{env.project.id}'
    steps=[dict(operation='caption',model_id=env.models[1].id)]
    if split_workflow:steps.insert(0,dict(operation='crop',model_id=env.models[0].id))
    jobs=await create(env.db,env.project,WorkflowCreate(image_kind='originals' if split_workflow else 'crops',
        follow_uploads=True,steps=steps),env.user.id)
    flow=jobs[-1]
    if split_workflow:
        configured=client.put(f'{base}/work/jobs/{jobs[0].id}/follow-uploads',headers=auth_headers,
            json={'enabled':True,'uploaded_crop_job_id':str(flow.id)})
        assert configured.status_code==200,configured.text
    keys = base + '/upload-keys'
    image = BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(image, 'PNG')
    files = [('files', ('crop.png', image.getvalue(), 'image/png'))]
    assert client.post(base + '/ingest/images', headers=auth_headers, files=files).status_code == 401
    issued = client.post(keys, headers=auth_headers, json={'name': 'external fixture'})
    assert issued.status_code == 201, issued.text
    assert issued.headers['cache-control'] == 'no-store'
    key = issued.json()
    token_headers = {'Authorization': f"Bearer {key['token']}"}
    listing = client.get(keys, headers=auth_headers).json()
    assert [item['id'] for item in listing] == [key['id']]
    assert 'token' not in listing[0] and 'digest' not in listing[0]
    assert client.get('/api/projects', headers=token_headers).status_code == 401
    assert client.post(f'/api/projects/{uuid4()}/ingest/images', headers=token_headers, files=files).status_code == 401

    def upload():
        return client.post(base + '/ingest/images', headers=token_headers, data={'image_kind': 'crops'}, files=files)

    created = upload()
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body['added_count'], body['duplicate_count'], body['queued_count']) == (1, 0, 1)
    asset = client.get('/api/assets/' + body['asset_ids'][0], headers=auth_headers).json()
    assert asset['source_type'] == 'model_crop' and asset['device_info']['uploaded_crop']
    repeated = upload().json()
    assert repeated['asset_ids'] == body['asset_ids']
    assert (repeated['added_count'], repeated['duplicate_count'], repeated['queued_count']) == (0, 1, 0)
    assert client.get(f'{base}/work/jobs/{flow.id}', headers=auth_headers).json()['total'] == 1

    original = BytesIO()
    Image.new('RGB', (800, 20), 'green').save(original, 'PNG')
    original_file = ('files', ('original.png', original.getvalue(), 'image/png'))
    batch = client.post(base + '/ingest/images', headers=token_headers, files=[original_file, original_file])
    assert batch.status_code == 201, batch.text
    added = batch.json()
    assert (added['added_count'], added['duplicate_count'], added['queued_count']) == (1, 1, int(split_workflow))
    assert added['asset_ids'][0] == added['asset_ids'][1]

    revoked = client.post(keys, headers=auth_headers, json={'name': 'revoke fixture'}).json()
    assert client.delete(keys + '/' + revoked['id'], headers=auth_headers).status_code == 204
    assert client.post(base + '/ingest/images', headers={'Authorization': f"Bearer {revoked['token']}"}, files=files).status_code == 401
    await env.db.execute(update(ProjectUploadKey).values(expires_at=datetime.now(timezone.utc) - timedelta(days=1)))
    await env.db.commit()
    assert upload().status_code == 401
    assert client.delete(keys + '/' + key['id'], headers=auth_headers).status_code == 204
    assert client.get(keys, headers=auth_headers).json() == []


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', [
    'multipart', 'zip', 'tar', 'labels_only', 'zip_only', 'yolo_txt', 'yolo_zip', 'yolo_tar', 'yolo_tar_only',
])
async def test_shared_external_ingest(work_env, test_engine, client, auth_headers, monkeypatch, mode):
    env = work_env
    yolo = mode.startswith('yolo_')
    env.project.task_type = 'detection' if yolo else 'vector'
    labels_only = mode.endswith('_only')
    if labels_only:
        env.asset.original_name = 'paired.png'
        env.asset.source_type = 'upload' if yolo else 'model_crop'
    await env.db.commit()
    flow = None
    if mode in ('multipart', 'zip'):
        flow, = await create(env.db, env.project, WorkflowCreate(image_kind='crops', follow_uploads=True,
            steps=[dict(operation='caption', model_id=env.models[1].id)]), env.user.id)
        from api import project_ingest_routes
        from services import upload_follow
        real_enqueue = upload_follow.enqueue_uploads
        interrupted = False

        async def check_enqueue(db, job, assets, **kwargs):
            nonlocal interrupted
            if job is not None and assets:
                saved = await db.scalars(select(Annotation.annotation_source).where(
                    Annotation.asset_id.in_([asset.id for asset in assets]), Annotation.is_latest.is_(True)))
                assert list(saved) == ['import_json']
                if mode == 'zip' and not interrupted:
                    interrupted = True
                    raise asyncio.CancelledError()
            return await real_enqueue(db, job, assets, **kwargs)

        monkeypatch.setattr(upload_follow, 'enqueue_uploads', check_enqueue)
        monkeypatch.setattr(project_ingest_routes, 'enqueue_uploads', check_enqueue)
    async with test_engine.begin() as conn:
        await conn.run_sync(ProjectUploadKey.__table__.create)
        # Older databases reserve assets.batch_id for legacy import_batches.
        await conn.exec_driver_sql("""
            CREATE TRIGGER legacy_assets_import_batch BEFORE UPDATE OF batch_id ON assets
            WHEN NEW.batch_id IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'legacy import_batches foreign key'); END
        """)
    base = f'/api/projects/{env.project.id}'
    token = client.post(base + '/upload-keys', headers=auth_headers, json={'name': 'fixture'}).json()['token']
    headers = {'Authorization': f'Bearer {token}'}
    image = BytesIO()
    Image.new('RGB', (800, 20) if yolo else (24, 20), 'purple').save(image, 'PNG')
    raw = image.getvalue()
    annotations = json.dumps({'paired.png': {'label': ['外部标签'], 'description': '模型描述'}}).encode()
    kind = 'originals' if yolo else 'crops'
    if mode in ('multipart', 'labels_only', 'yolo_txt'):
        files = [] if labels_only else [('files', ('paired.png', raw))]
        if yolo:
            files.extend([('labels', ('paired.txt', b'0 0.5 0.5 0.2 0.2')), ('classes', ('classes.txt', b'car'))])
        else:
            files.append(('annotations', ('data.json', annotations)))
        response = client.post(base + '/ingest/images', headers=headers, files=files, data={'image_kind': kind})
        assert response.status_code == 201, response.text
        assert response.json()['annotations']['items'][0]['status'] == 'imported'
        repeated = client.post(base + '/ingest/images', headers=headers, files=files, data={'image_kind': kind}).json()
        assert repeated['added_count'] == 0 and repeated['annotations']['items'][0]['status'] == 'unchanged'
    else:
        archive_kind = mode.removeprefix('yolo_').removesuffix('_only')
        entries = [('nested/paired.txt', b'0 0.5 0.5 0.2 0.2'), ('classes.txt', b'car')] if yolo else [
            ('nested/annotations.json', annotations)]
        data = archive(archive_kind, entries if labels_only else [('nested/paired.png', raw), *entries])
        response = client.post(base + '/ingest/archives', headers=headers, content=data,
            params={'filename': f'bundle.{archive_kind}', 'image_kind': kind, 'annotation_format': 'yolo' if yolo else 'json'})
        assert response.status_code == 202, response.text
        if mode == 'zip':
            with pytest.raises(asyncio.CancelledError):
                await image_uploads.process_one(env.sessions)
            async with env.sessions() as db:
                await db.execute(update(ImageUpload).where(ImageUpload.status=='processing').values(status='pending'))
                await db.commit()
        assert await image_uploads.process_one(env.sessions)
        done = client.get(response.headers['location'], headers=headers).json()
        assert done['status'] == 'completed', done
        assert done['result']['annotations']['imported'] == 1
        assert done['result']['added_count'] == int(not labels_only)
    label = 'car' if yolo else '外部标签'
    rows = client.get('/api/annotations', headers=auth_headers, params={'project_id': str(env.project.id), 'label': label}).json()
    assert rows['total'] == 1
    assert rows['items'][0]['annotation_data'].get('description') == (None if yolo else '模型描述')
    if flow is not None:
        assert client.get(f'{base}/work/jobs/{flow.id}', headers=auth_headers).json()['total'] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['zip', 'tar'])
async def test_external_archive_import_attaches_and_queues(work_env, test_engine, client, auth_headers, kind):
    env = work_env
    async with test_engine.begin() as conn:
        await conn.run_sync(ProjectUploadKey.__table__.create)
    flow, = await create(env.db, env.project, WorkflowCreate(image_kind='crops', follow_uploads=True,
        steps=[dict(operation='caption', model_id=env.models[1].id)]), env.user.id)
    base = f'/api/projects/{env.project.id}'
    url = base + '/ingest/archives'
    token = client.post(base + '/upload-keys', headers=auth_headers, json={'name': 'archive'}).json()['token']
    headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/octet-stream'}
    image = BytesIO()
    Image.new('RGB', (8, 8), 'blue').save(image, 'PNG')
    raw = image.getvalue()
    package = archive(kind, [('nested/image.png', raw), ('duplicate.png', raw), ('readme.txt', b'ignored')])
    response = client.post(url, headers=headers, params={'filename': f'images.{kind}', 'image_kind': 'crops'}, content=package)
    assert response.status_code == 202, response.text
    status = response.headers['location']
    assert client.get(status, headers=headers).json()['status'] == 'pending'
    assert client.get(status).status_code == 401
    assert client.get(status.replace(str(env.project.id), str(uuid4())), headers=headers).status_code == 401
    assert await image_uploads.process_one(env.sessions)
    done = client.get(status, headers=headers).json()
    assert done['status'] == 'completed', done
    assert done['result']['added_count'] == done['result']['queued_count'] == 1
    assert done['result']['duplicates'] == done['result']['ignored'] == 1
    crops = client.get('/api/assets', headers=auth_headers, params={'project_id': str(env.project.id), 'image_kind': 'crops'}).json()
    assert crops['total'] == 1 and crops['items'][0]['device_info']['uploaded_crop']
    assert client.get(f'{base}/work/jobs/{flow.id}', headers=auth_headers).json()['total'] == 1

    unsafe = archive(kind, [('ok.png', raw), ('../escape.png', raw)])
    rejected = client.post(url, headers=headers, params={'filename': f'unsafe.{kind}'}, content=unsafe)
    assert rejected.status_code == 202
    assert await image_uploads.process_one(env.sessions)
    failed = client.get(rejected.headers['location'], headers=headers).json()
    assert failed['status'] == 'failed' and failed['result']['added_count'] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(('transport','generate'), [('multipart',True), ('zip',False), ('tar',True)])
async def test_mixed_json_resolves_image_types_and_optional_crops(work_env, test_engine, client, auth_headers, transport, generate):
    env = work_env
    async with test_engine.begin() as conn:
        await conn.run_sync(ProjectUploadKey.__table__.create)
    base = f'/api/projects/{env.project.id}'
    token = client.post(base + '/upload-keys', headers=auth_headers, json={'name':'fixture'}).json()['token']
    headers = {'Authorization':f'Bearer {token}'}
    original = BytesIO()
    Image.new('RGB',(960,200),'blue').save(original,'PNG')
    crop = BytesIO()
    Image.new('RGB',(24,20),'purple').save(crop,'PNG')
    second_box = [10,12,50,60] if transport=='multipart' else [70,12,100,40]
    second_label = '公交' if transport=='multipart' else '行人'
    labels = json.dumps({
        'original.png':[
            {'box':[10,12,50,60], 'label':['公交','蓝色车辆'], 'attribute':{'颜色':'蓝','车牌':None}, 'description':'框描述'},
            {'box':second_box, 'label':[second_label], 'attribute':{'帽子':'白'}, 'description':'第二个框'},
        ],
        'crop.png':{'label':['公交'], 'description':'整图描述'},
    },ensure_ascii=False).encode()
    entries = [('original.png',original.getvalue()),('crop.png',crop.getvalue())]
    flags = {'image_kind':'originals','crop_from_boxes':str(generate).lower()}
    if transport=='multipart':
        response = client.post(base+'/ingest/images',headers=headers,data=flags,
            files=[('files',entry) for entry in entries]+[('annotations',('annotations.json',labels))])
        assert response.status_code==201,response.text
        assert [item['status'] for item in response.json()['annotations']['items']]==['imported','imported']
    else:
        response = client.post(base+'/ingest/archives',headers=headers,
            params={**flags,'filename':f'mixed.{transport}','annotation_format':'json'},
            content=archive(transport,[*entries,('annotations.json',labels)]))
        assert response.status_code==202,response.text
        assert await image_uploads.process_one(env.sessions)
        done=client.get(response.headers['location'],headers=headers).json()
        assert done['status']=='completed' and done['result']['annotations']['imported']==2,done
    async with env.sessions() as db:
        rows = (await db.scalars(select(Asset).where(Asset.project_id==env.project.id,Asset.id!=env.asset.id))).all()
        assert len(rows)==2+2*int(generate)
        by_name={row.original_name:row for row in rows}
        assert by_name['original.png'].source_type=='upload' and by_name['crop.png'].source_type=='model_crop'
        derived=[row for row in rows if (row.device_info or {}).get('crop')]
        if generate:
            assert len(derived)==2
            assert {(row.width,row.height) for row in derived}==({(40,48)} if transport=='multipart' else {(40,48),(30,28)})
            assert all(row.device_info['crop']['parent_asset_id']==str(by_name['original.png'].id) for row in derived)
            saved = (await db.scalars(select(Annotation).where(Annotation.asset_id.in_([row.id for row in derived])))).all()
            assert len(saved)==2
            data={row.annotation_data['description']:row.annotation_data for row in saved}
            assert data['框描述']['labels']==['公交','蓝色车辆']
            assert data['第二个框']['labels']==[second_label]
            assert all(row.review_status=='pending' and 'boxes' not in row.annotation_data for row in saved)
            attrs={field['name']:data['框描述']['attributes'][field['key']]['value']
                for field in data['框描述']['attribute_schema']['fields']}
            assert attrs=={'颜色':'蓝','车牌':None}
            project=await db.get(type(env.project),env.project.id)
            assert '蓝色车辆' not in project.label_schema['labels']
            approved=next(row for row in saved if row.annotation_data['description']=='框描述')
            missing=next(row for row in saved if row.id!=approved.id)
            missing_asset_id=missing.asset_id
            await db.delete(missing)
            approved.annotation_data={**approved.annotation_data,'description':'人工修订'}
            approved.review_status='approved'
            await db.commit()
            approved_id=approved.id
            derived_ids=[row.id for row in derived]
        else:
            assert not derived
    if generate:
        repeated=client.post(base+'/ingest/annotation-labels',headers=headers,data={'crop_from_boxes':'true'},
            files={'file':('annotations.json',labels)}).json()
        assert repeated['crop_count']==2
        assert [item['status'] for item in repeated['items']]==['unchanged','unchanged']
        async with env.sessions() as db:
            saved=(await db.scalars(select(Annotation).where(Annotation.asset_id.in_(derived_ids)))).all()
            assert len(saved)==2
            current=await db.get(Annotation,approved_id)
            assert current.review_status=='approved' and current.annotation_data['description']=='人工修订'
            assert any(row.asset_id==missing_asset_id and row.annotation_data['description']=='第二个框' for row in saved)
