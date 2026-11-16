from datetime import datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select

from models import Asset
from tests.test_project_reuse import reusable
from tests.test_project_work import work_env
from tests.test_device_capture import capture_client, schedule
from tests.test_annotation_analytics import activity_table, evidence


def test_shared_images_include_pending_annotations(client, auth_headers, reusable):
    _, _, images, _, _ = reusable
    listing = client.get('/api/images', headers=auth_headers)
    selection = client.get('/api/images/selection', headers=auth_headers)
    expected = {str(image.id) for image in images}
    assert {row['id'] for row in listing.json()['items']} == expected
    assert set(selection.json()['image_ids']) == expected


def test_pending_image_can_be_reused_with_independent_annotation(client, auth_headers, reusable, test_project):
    _, _, images, _, annotations = reusable
    response = client.post('/api/images/associate', headers=auth_headers, json={
        'project_id': str(test_project.id), 'image_ids': [str(images[1].id)]})
    assert response.status_code == 200 and response.json()['added_count'] == 1
    asset_id = response.json()['asset_ids'][0]
    assert client.get('/api/annotations', headers=auth_headers, params={'asset_id': asset_id}).json()['total'] == 0
    saved = client.post('/api/annotations', headers=auth_headers, json={
        'asset_id': asset_id, 'kind': 'detection', 'annotation_data': {'boxes': [{**annotations[1].annotation_data['boxes'][0], 'label': '人'}]},
    })
    assert saved.status_code == 201, saved.text
    source = client.get(f'/api/annotations/{annotations[1].id}', headers=auth_headers).json()
    assert source['review_status'] == 'pending' and source['annotation_data']['boxes'][0]['label'] == '车'


def test_existing_unreviewed_reference_remains_idempotent(client, auth_headers, reusable):
    source, _, images, _, _ = reusable
    response = client.post('/api/images/associate', headers=auth_headers, json={
        'project_id': str(source.id), 'image_ids': [str(images[1].id)]})
    assert response.status_code == 200 and response.json()['added_count'] == 0


@pytest.mark.asyncio
async def test_saving_new_version_keeps_image_shareable(client,auth_headers,reusable,test_project):
    _,_,images,assets,annotations=reusable
    response=client.put('/api/annotations/'+str(annotations[2].id),headers=auth_headers,json={
        'base_version':2,'annotation_data':annotations[2].annotation_data})
    assert response.status_code==200,response.text
    selection=client.get('/api/images/selection',headers=auth_headers).json()['image_ids']
    assert str(images[2].id) in selection
    assert client.get('/api/assets/'+str(assets[2].id),headers=auth_headers).status_code==200
    assert client.post('/api/images/associate',headers=auth_headers,json={
        'project_id':str(test_project.id),'image_ids':[str(images[2].id)]}).status_code==200


def test_capture_can_associate_pending_source_image(test_engine,reusable,test_project):
    from sqlalchemy import create_engine
    from integrations.smarteye.runtime import Runtime
    _,_,images,_,_=reusable
    engine=create_engine('sqlite:///'+str(test_engine.url.database))
    try:
        with engine.begin() as conn:
            Runtime._associate(None,conn,{'project_id':str(test_project.id),'tenant_id':1},
                str(images[1].id),images[1].blob_path,images[1].content_hash,
                {field:getattr(images[1],field) for field in ('original_name','file_size','width','height','format')},images[1].id)
            assert conn.scalar(select(Asset.id).where(Asset.project_id==test_project.id,Asset.image_id==images[1].id))
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_three_crop_rounds_keep_chain_and_root_coordinates(work_env):
    from services import project_work as service
    from schemas.project_work import WorkCreate
    from models.project_work import ProjectWorkItem
    s = work_env
    parent = s.asset
    ids = []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={
            'detections': [{'class': 'person', 'bbox': [2, 2, 20, 20], 'score': .9}]}))) as client:
        for depth in range(1, 4):
            job = await service.create_job(s.db, s.project, WorkCreate(operation='crop', model_id=s.models[0].id,
                asset_ids=[parent.id], expansion=0), s.user.id)
            assert await service.process_one(s.sessions, client)
            async with s.sessions() as db:
                item = await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id == job.id))
                assert item.status == 'completed', item.error
                crop = await db.get(Asset, UUID(item.result['asset_ids'][0]))
                metadata = crop.device_info['crop']
                assert metadata['depth'] == depth
                assert metadata['parent_asset_id'] == str(parent.id)
                assert metadata['root_region'] == [2 * depth, 2 * depth, 20, 20]
                assert len(crop.device_info['crop_chain']) == depth
                assert crop.device_info['crop_chain'][0]['parent_asset_id'] == str(s.asset.id)
                assert crop.image_id is None
                from api.asset_routes import asset_conditions
                filtered = await asset_conditions(db, s.project, 'all', 'all', crop_depth=depth)
                assert (await db.scalars(select(Asset.id).where(*filtered))).all() == [crop.id]
                ids.append(crop.id)
                parent = crop
    assert len(set(ids)) == 3


def test_capture_requests_only_newest_successful_record(capture_client, monkeypatch):
    from integrations.smarteye import device_capture as capture, runtime
    from integrations.smarteye.api import ledger_image_downloader as ledger, smart_search
    monkeypatch.setattr(smart_search, 'login_platform', lambda platform: {'success': True})
    queries, downloads, saves = [], [], []
    def post(base, path, payload, token):
        queries.append(payload)
        return {'code': 0, 'data': {'records': [{'deviceId': 'd', 'success': True, 'backPath': '/newest.jpg'}], 'total': 241}}
    monkeypatch.setattr(ledger, '_post_with_token', post)
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def raise_for_status(self): pass
    class Session(Response):
        def get(self, url, **kwargs):
            downloads.append(url)
            return Response()
    monkeypatch.setattr(capture.requests, 'Session', Session)
    monkeypatch.setattr(capture, 'response_bytes', lambda response: b'image')
    monkeypatch.setattr(runtime.get_runtime(), 'save_image', lambda *args, **kwargs: saves.append(args) or 'images/latest.jpg')
    item = schedule(capture_client).json['data']
    capture._run_schedule(item['sched_id'])
    assert len(queries) == len(downloads) == len(saves) == 1
    assert queries[0]['size'] == 1 and queries[0]['current'] == 1
    assert queries[0]['sort'] == 'id' and queries[0]['order'] == 'descending'
    assert queries[0]['model'] == {'deviceId': 'd', 'startTime': None, 'endTime': None, 'success': True}
    assert capture._get(item['sched_id'])['device_results'][0]['downloaded'] == 1


def test_rework_uses_matching_previous_version_and_earlier_rejection():
    from services.annotation_analytics import count_rework
    now = datetime(2026, 11, 4)
    event = lambda asset, version, at: SimpleNamespace(asset_id=asset, created_at=at, detail={'version': version})
    updates = [SimpleNamespace(asset_id='a', created_at=now, detail={'previous_version': 1}),
               SimpleNamespace(asset_id='b', created_at=now, detail={'previous_version': 1})]
    rejects = [event('a', 1, now - timedelta(seconds=1)), event('a', 1, now), event('b', 1, now + timedelta(seconds=1))]
    assert count_rework(updates, rejects) == 1


def test_activity_lookup_does_not_scan_every_interval():
    from services.annotation_analytics import during
    class Time:
        comparisons = 0
        def __init__(self, value): self.value = value
        def __lt__(self, other):
            Time.comparisons += 1
            return self.value < other.value
        def __le__(self, other):
            Time.comparisons += 1
            return self.value <= other.value
    intervals = [(Time(i * 3), Time(i * 3 + 1)) for i in range(2048)]
    assert during(Time(6141), intervals)
    assert Time.comparisons <= 20
    assert not during(Time(6142), intervals)


@pytest.mark.asyncio
async def test_analytics_does_not_load_annotation_payload_twice(test_db, test_engine, test_user, evidence, activity_table):
    from sqlalchemy import event
    from schemas.analytics import AnalyticsRange
    from services.annotation_analytics import annotation_analytics
    statements=[]
    def observe(conn,cursor,statement,parameters,context,many):
        if statement.lstrip().upper().startswith('SELECT') and 'annotations.annotation_data' in statement:
            statements.append(statement)
    event.listen(test_engine.sync_engine,'before_cursor_execute',observe)
    try:
        await annotation_analytics(test_db,test_user,None,AnalyticsRange())
    finally:
        event.remove(test_engine.sync_engine,'before_cursor_execute',observe)
    assert len(statements)==1
