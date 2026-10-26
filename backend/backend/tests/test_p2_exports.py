from io import BytesIO
import hashlib
import json
import zipfile
import pytest
from PIL import Image
from tests.test_p0_contracts import foreign_annotation

def populate(client, headers, project_id):
    ids = []
    for color, boxes in [('red', [
        {'x': 10, 'y': 5, 'width': 20, 'height': 15, 'label': '人'},
        {'x': 40, 'y': 10, 'width': 10, 'height': 10, 'label': '猫'}]), ('blue', [])]:
        data = BytesIO(); Image.new('RGB', (100, 50), color).save(data, format='PNG')
        uploaded = client.post('/api/assets/upload', headers=headers, params={'project_id': str(project_id)},
            files={'file': ('same.png', data.getvalue(), 'image/png')})
        assert uploaded.status_code == 201
        asset_id = uploaded.json()['asset_id']
        result = client.post('/api/annotations', headers=headers, json={'asset_id': asset_id, 'annotation_data': {'boxes': boxes}})
        assert result.status_code == 201
        ann = result.json()
        assert client.post('/api/annotations/' + ann['id'] + '/review', headers=headers, json={'approved': True}).status_code == 200
        ids.append((asset_id, ann))
    return ids

@pytest.mark.parametrize('format', ['yolo', 'coco'])
def test_training_zip_filter_negative_and_same_name(client, auth_headers, test_project, format):
    ids = populate(client, auth_headers, test_project.id)
    response = client.get(f'/api/export/projects/{test_project.id}/export', headers=auth_headers,
        params={'format': format, 'labels': '人'})
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['labels'] == ['人'] and len(manifest['assets']) == 2
        assert manifest['approved_only'] is True
        for asset in manifest['assets']:
            assert hashlib.sha256(archive.read(asset['image_path'])).hexdigest() == asset['content_hash']
            assert asset['annotation_version'] == 1
        if format == 'yolo':
            rows = archive.read(f'labels/all/{ids[0][0]}.txt').decode().splitlines()
            assert len(rows) == 1
            assert list(map(float, rows[0].split())) == pytest.approx([0, .2, .25, .2, .3])
            assert archive.read(f'labels/all/{ids[1][0]}.txt') == b''
            assert '0: "人"' in archive.read('data.yaml').decode()
        else:
            coco = json.loads(archive.read('annotations/instances_all.json'))
            assert len(coco['images']) == 2 and len(coco['annotations']) == 1
            assert coco['categories'] == [{'id': 1, 'name': '人', 'supercategory': 'object'}]
            assert coco['annotations'][0]['bbox'] == [10, 5, 20, 15]
            assert coco['annotations'][0]['area'] == 300

def test_only_approved_latest_and_export_auth(client, auth_headers, test_project):
    ids = populate(client, auth_headers, test_project.id)
    old = ids[0][1]
    response = client.put('/api/annotations/' + old['id'], headers=auth_headers,
        json={'base_version': 1, 'annotation_data': {'boxes': []}})
    assert response.status_code == 200
    path = f'/api/export/projects/{test_project.id}/export'
    assert client.get(path, params={'format': 'yolo'}).status_code == 401
    response = client.get(path, headers=auth_headers, params={'format': 'yolo'})
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert [a['asset_id'] for a in manifest['assets']] == [ids[1][0]]
    result = client.get('/api/export/annotations/' + old['id'] + '/export', headers=auth_headers)
    assert result.status_code == 200

def test_frozen_export_survives_edit_and_has_explicit_split(client, auth_headers, test_project):
    ids = populate(client, auth_headers, test_project.id)
    response = client.post(f'/api/export/projects/{test_project.id}/snapshots', headers=auth_headers,
        json={'format': 'yolo', 'labels': ['人'], 'splits': {ids[0][0]: 'train', ids[1][0]: 'test'}})
    assert response.status_code == 201, response.text
    snapshot = response.json()
    listing = client.get(f'/api/export/projects/{test_project.id}/snapshots?summary_only=true', headers=auth_headers)
    assert listing.status_code == 200
    assert listing.json()[0]['asset_count'] == 2
    assert 'manifest' not in listing.json()[0]
    assert client.get(f'/api/export/projects/{test_project.id}/snapshots', headers=auth_headers).json()[0]['manifest'] == snapshot['manifest']
    url = '/api/export/snapshots/' + snapshot['id'] + '/download'
    before = client.get(url, headers=auth_headers)
    assert before.status_code == 200
    with zipfile.ZipFile(BytesIO(before.content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert {a['split'] for a in manifest['assets']} == {'train', 'test'}
    client.put('/api/annotations/' + ids[0][1]['id'], headers=auth_headers,
        json={'base_version': 1, 'annotation_data': {'boxes': []}})
    assert client.get(url, headers=auth_headers).content == before.content
    assert client.get(url).status_code == 401

def test_simple_selection_preview_and_package_supports_ratio_train_only_and_budget(client, auth_headers, test_project, monkeypatch):
    ids = populate(client, auth_headers, test_project.id)
    import api.export_routes as export_routes
    monkeypatch.setattr(export_routes, 'preview_splits', lambda *args, **kwargs: pytest.fail('default selection must skip near-duplicate checks'))
    preview_path = f'/api/export/projects/{test_project.id}/splits/preview'
    selection = {
        'mode': 'ratio',
        'ratios': {'train': 50, 'val': 50, 'test': 0},
        'budgets': {'人': None},
        'check_duplicates': False,
    }
    response = client.post(preview_path, headers=auth_headers,
        json={'labels': ['人'], 'selection': selection})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report['images_by_split'] == {'train': 1, 'val': 1}
    assert report['labels_by_split']['train']['人'] + report['labels_by_split']['val']['人'] == 1

    response = client.post(f'/api/export/projects/{test_project.id}/packages', headers=auth_headers,
        json={'format': 'yolo', 'labels': ['人'], 'selection': selection})
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['selection']['near_duplicate_checked'] is False
        assert {asset['split'] for asset in manifest['assets']} == {'train', 'val'}

    response = client.post(preview_path, headers=auth_headers,
        json={'labels': ['人'], 'selection': {
            'mode': 'train_only',
            'budgets': {'人': 1},
        }})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report['images_by_split'] == {'train': 2}
    assert report['labels_by_split']['train']['人'] <= 1

def test_bad_split_unknown_label_and_no_approved_data_rejected(client, auth_headers, test_project):
    ids = populate(client, auth_headers, test_project.id)
    path = f'/api/export/projects/{test_project.id}'
    assert client.post(path + '/snapshots', headers=auth_headers,
        json={'format': 'yolo', 'splits': {ids[0][0]: 'train'}}).status_code == 400
    assert client.get(path + '/export', headers=auth_headers,
        params={'format': 'yolo', 'labels': '不存在'}).status_code == 400
    for _, ann in ids:
        client.post('/api/annotations/' + ann['id'] + '/review', headers=auth_headers, json={'approved': False})
    assert client.get(path + '/export', headers=auth_headers, params={'format': 'yolo'}).status_code == 400

def test_foreign_annotation_export_hidden(client, auth_headers, foreign_annotation):
    assert client.get(f'/api/export/annotations/{foreign_annotation.id}/export', headers=auth_headers).status_code == 404

def test_distribution_has_real_categories_and_unknown_metadata(client, auth_headers, test_project):
    populate(client, auth_headers, test_project.id)
    response = client.get(f'/api/stats/projects/{test_project.id}/distribution', headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert data['boxes_by_label'] == {'人': 1, '猫': 1}
    assert data['images_by_device'] == {'未知': 2}
    assert data['images_by_quality'] == {'未评分': 2}
    assert data['images_by_status'] == {'reviewed': 2}

def test_export_summary_counts_latest_images_without_reading_files(client, auth_headers, test_project, monkeypatch):
    ids = populate(client, auth_headers, test_project.id)
    from services import detection_export
    monkeypatch.setattr(detection_export, 'get_storage', lambda: pytest.fail('summary must not read image files'))
    path = f'/api/export/projects/{test_project.id}/summary'
    response = client.get(path, headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json() == {'labels': ['人', '猫'], 'all': {'images': 2, 'images_by_label': {'人': 1, '猫': 1}},
        'approved': {'images': 2, 'images_by_label': {'人': 1, '猫': 1}}}
    updated = client.put('/api/annotations/' + ids[0][1]['id'], headers=auth_headers,
        json={'base_version': 1, 'annotation_data': {'boxes': [
            {'x': 10, 'y': 5, 'width': 20, 'height': 15, 'label': '人'},
            {'x': 40, 'y': 10, 'width': 10, 'height': 10, 'label': '人'}]}})
    assert updated.status_code == 200
    data = client.get(path, headers=auth_headers).json()
    assert data['labels'] == ['人']
    assert data['all'] == {'images': 2, 'images_by_label': {'人': 1}}
    assert data['approved'] == {'images': 1, 'images_by_label': {}}
    assert client.get(path).status_code == 401

@pytest.mark.parametrize('format', ['yolo', 'coco'])
def test_validation_only_package_and_freeze(client, auth_headers, test_project, format):
    populate(client, auth_headers, test_project.id)
    body = {'format': format, 'labels': ['人'], 'selection': {'mode': 'val_only', 'budgets': {'人': 1}}}
    path = f'/api/export/projects/{test_project.id}'
    preview = client.post(path + '/splits/preview', headers=auth_headers, json={key:value for key,value in body.items() if key != 'format'})
    assert preview.status_code == 200, preview.text
    assert preview.json()['images_by_split'] == {'val': 2}
    package = client.post(path + '/packages', headers=auth_headers, json=body)
    assert package.status_code == 200, package.text
    with zipfile.ZipFile(BytesIO(package.content)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        assert manifest['selection']['mode'] == 'val_only'
        assert {row['split'] for row in manifest['assets']} == {'val'}
        if format == 'yolo':
            assert 'train: []\nval: images/val\ntest: []' in archive.read('data.yaml').decode()
        else:
            assert 'annotations/instances_val.json' in archive.namelist()
    frozen = client.post(path + '/snapshots', headers=auth_headers, json=body)
    assert frozen.status_code == 201, frozen.text
    assert {row['split'] for row in frozen.json()['manifest']['assets']} == {'val'}

@pytest.mark.asyncio
async def test_delete_snapshot_preserves_assets_and_blocks_references(client, auth_headers, test_project, test_user, test_db, test_engine):
    from models import Base, ExportSnapshot, TrainingRun, ValidationSet
    from services.storage import get_storage
    async with test_engine.begin() as connection:
        await connection.run_sync(lambda conn: Base.metadata.create_all(conn, tables=[TrainingRun.__table__, ValidationSet.__table__]))
    populate(client, auth_headers, test_project.id)
    frozen = client.post(f'/api/export/projects/{test_project.id}/snapshots', headers=auth_headers,
        json={'labels': ['人'], 'selection': {'mode': 'train_only'}}).json()
    from uuid import UUID
    snapshot_id = UUID(frozen['id'])
    snapshot = await test_db.get(ExportSnapshot, snapshot_id)
    path = '/api/export/snapshots/' + frozen['id']
    training = TrainingRun(tenant_id=test_user.tenant_id, user_id=test_user.id, name='reference', kind='yolo26',
        status='draft', config={}, source={'kind':'snapshot','id':frozen['id']}, server={}, output_dir='/unused')
    test_db.add(training); await test_db.commit()
    assert client.delete(path, headers=auth_headers).status_code == 409
    await test_db.delete(training); await test_db.commit()
    dataset = ValidationSet(tenant_id=test_user.tenant_id, user_id=test_user.id, project_id=test_project.id,
        name='reference', labels=['人'], samples=[], source={'snapshot_id':frozen['id']}, total=0)
    test_db.add(dataset); await test_db.commit()
    assert client.delete(path, headers=auth_headers).status_code == 409
    await test_db.delete(dataset); await test_db.commit()
    assert client.get(path + '/download', headers=auth_headers).status_code == 200
    assert client.delete(path).status_code == 401
    assert client.delete(path, headers=auth_headers).status_code == 204
    assert client.get(path + '/download', headers=auth_headers).status_code == 404
    assert client.get(f'/api/export/projects/{test_project.id}/snapshots', headers=auth_headers).json() == []
    assert not await get_storage().file_exists(snapshot.blob_path)
    assert client.get('/api/assets', headers=auth_headers, params={'project_id':str(test_project.id)}).json()['total'] == 2
    assert client.get('/api/annotations', headers=auth_headers, params={'project_id':str(test_project.id)}).json()['total'] == 2

@pytest.mark.asyncio
async def test_export_summary_hides_other_tenants(client, auth_headers, foreign_annotation, test_db):
    from uuid import uuid4
    from models import Asset
    asset = await test_db.get(Asset, foreign_annotation.asset_id)
    assert client.get(f'/api/export/projects/{asset.project_id}/summary', headers=auth_headers).status_code == 404
    assert client.get(f'/api/export/projects/{uuid4()}/summary', headers=auth_headers).status_code == 404
    from models import ExportSnapshot
    snapshot = ExportSnapshot(project_id=asset.project_id, user_id=1, export_format='yolo',
        blob_path='not-owned.zip', content_hash='unused', manifest={'assets':[]})
    test_db.add(snapshot); await test_db.commit()
    assert client.delete(f'/api/export/snapshots/{snapshot.id}', headers=auth_headers).status_code == 404
    await test_db.refresh(snapshot)

def test_near_duplicates_cannot_cross_frozen_splits(client, auth_headers, test_project):
    import random
    ids=[]
    rng = random.Random(42)
    original=Image.frombytes('L',(100,100),bytes(rng.randrange(256) for _ in range(10000))).convert('RGB')
    for quality in (95,85):
        data=BytesIO(); original.save(data,format='JPEG',quality=quality)
        response=client.post('/api/assets/upload',headers=auth_headers,params={'project_id':str(test_project.id)},
            files={'file':('near.jpg',data.getvalue(),'image/jpeg')})
        asset_id=response.json()['asset_id']; ids.append(asset_id)
        ann=client.post('/api/annotations',headers=auth_headers,json={'asset_id':asset_id,'annotation_data':{'boxes':[]}}).json()
        client.post('/api/annotations/'+ann['id']+'/review',headers=auth_headers,json={'approved':True})
    assert len(set(ids))==2
    response=client.post(f'/api/export/projects/{test_project.id}/snapshots',headers=auth_headers,
        json={'format':'yolo','splits':{ids[0]:'train',ids[1]:'test'}})
    assert response.status_code==409, response.text

def test_schema_change_does_not_silently_drop_existing_boxes(client, auth_headers, test_project):
    populate(client, auth_headers, test_project.id)
    client.put(f'/api/projects/{test_project.id}',headers=auth_headers,json={'label_schema':{'labels':['人']}})
    result=client.get(f'/api/export/projects/{test_project.id}/export',headers=auth_headers,params={'format':'coco'})
    with zipfile.ZipFile(BytesIO(result.content)) as archive:
        document=json.loads(archive.read('annotations/instances_all.json'))
        assert {c['name'] for c in document['categories']}=={'人','猫'}
        assert len(document['annotations'])==2

@pytest.mark.asyncio
async def test_zip_work_does_not_block_request_loop(monkeypatch):
    import asyncio, time
    from types import SimpleNamespace
    from services import detection_export as module
    image=BytesIO(); Image.new('RGB',(100,50),'red').save(image,format='PNG')
    content=image.getvalue()
    asset=SimpleNamespace(id='one',original_name='one.png',format='PNG',width=100,height=50,
        content_hash=hashlib.sha256(content).hexdigest(),blob_path='one.png',source_type='upload')
    annotation=SimpleNamespace(id='ann',version=1,review_status='approved',annotation_data={'boxes':[]})
    async def rows(*args): return [(asset,annotation)],[]
    class Storage:
        async def get_file(self,path): return BytesIO(content)
    class SlowZip(zipfile.ZipFile):
        def __init__(self,*args,**kwargs):
            time.sleep(.2); super().__init__(*args,**kwargs)
    monkeypatch.setattr(module,'export_rows',rows)
    monkeypatch.setattr(module,'get_storage',lambda:Storage())
    monkeypatch.setattr(module.zipfile,'ZipFile',SlowZip)
    start=time.monotonic()
    async def tick():
        await asyncio.sleep(.03)
        return time.monotonic()-start
    package,elapsed=await asyncio.gather(module.training_package(None,SimpleNamespace(id='project',label_schema=None),'yolo'),tick())
    package[0].close()
    assert elapsed < .15
