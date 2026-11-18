from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
from zipfile import ZipFile
from uuid import uuid4

import pytest
from PIL import Image
from sqlalchemy import select, update

from models import Annotation, Asset, ProjectUploadKey
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
