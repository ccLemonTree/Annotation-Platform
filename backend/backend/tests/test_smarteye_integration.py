"""Contract and source regression tests, using only isolated state."""
import threading
import time
from io import BytesIO
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest
from PIL import Image
from integrations.smarteye.runtime import get_runtime, actor, tracked_run

def test_real_route_inventory_and_auth(client):
    from integrations.smarteye.app import app
    required = {'/platforms', '/platform-list', '/download', '/smart-search/start',
        '/ledger/device/log', '/ledger-image/start', '/ledger-image/scheduler/configure',
        '/ledger-watch/jobs', '/ai-image/upload', '/ai-image/start', '/ai-image/history',
        '/ai-image/reuse/<task_id>', '/tool-tasks/create', '/tool-tasks/<task_id>/download'}
    assert required <= {rule.rule for rule in app.url_map.iter_rules()}
    for path in ('platform-list', 'tasks', 'ai-image/history', 'ledger-watch/jobs', 'tool-tasks'):
        assert client.get('/api/smarteye/' + path).status_code == 401
    schema = client.get('/openapi.json').json()
    operations = schema['paths']['/api/smarteye/{path}'].values()
    ids = [operation['operationId'] for operation in operations]
    assert len(ids) == len(set(ids))

def test_real_platform_configuration_not_mock(client, auth_headers, monkeypatch):
    from integrations.smarteye import app
    monkeypatch.setattr(app, 'get_combined_platform_mapping', lambda: {'测试平台': {'platId': 'local', 'alarmTypes': {'人': '1'}}})
    response = client.get('/api/smarteye/platform-list', headers=auth_headers)
    assert response.status_code == 200
    assert response.json()['data'] == [{'label': '测试平台', 'value': 'local', 'alarmTypes': {'人': '1'}}]

def test_smart_search_sync_uses_login_origin_and_complete_auth_headers(client, auth_headers, monkeypatch):
    from integrations.smarteye import app
    monkeypatch.setattr(app, 'API_BASE_URL', 'http://configured-login.example:8890')
    monkeypatch.setattr(app, 'get_automated_token', lambda: ('session-token', 'current-tenant'))
    monkeypatch.setattr(app, 'get_common_headers', lambda token: {
        'token': token, 'authorization': 'application-auth', 'applicationid': 'app-id', 'tenantid': 'configured-tenant'})
    records=[{'id':'fixture-platform','name':'隔离平台','baseUrl':'http://fixture.example'}]
    synced=[]
    def post(url, **values):
        assert url == 'http://configured-login.example:8890/api/defPlatform/page'
        assert values['headers']['token'] == 'session-token'
        assert values['headers']['authorization'] == 'application-auth'
        assert values['headers']['applicationid'] == 'app-id'
        assert values['headers']['tenantid'] == 'current-tenant'
        class Response:
            def raise_for_status(self):pass
            def json(self):return {'code':0,'data':{'records':records}}
        return Response()
    monkeypatch.setattr(app.requests, 'post', post)
    monkeypatch.setattr(app, 'sync_remote_platforms', lambda rows: synced.extend(rows) or len(rows))
    response=client.post('/api/smarteye/smart-search/sync-platforms',headers=auth_headers)
    assert response.status_code==200,response.text
    assert response.json()['count']==1 and synced==records

def test_annotation_destination_validated_before_task(client, auth_headers):
    headers = {**auth_headers, 'X-SmartEye-Purpose': 'annotation'}
    response = client.post('/api/smarteye/download', headers=headers, json={'platName': 'test'})
    assert response.status_code == 400
    assert get_runtime().list() == []

def test_task_list_cancel_and_retry_contract(client, auth_headers, monkeypatch):
    from integrations.smarteye import app
    runtime = get_runtime()
    runtime.create('local-task', 'detection', {'status': 'running', 'params': {'platName': 'test'}},
                   {'tenant_id': 1, 'user_id': 1})
    assert client.get('/api/smarteye/tasks', headers=auth_headers).json()['data'][0]['task_id'] == 'local-task'
    assert client.post('/api/smarteye/tasks/local-task/cancel', headers=auth_headers).status_code == 200
    assert runtime.get('local-task')['status'] == 'cancelled'
    calls = []
    monkeypatch.setattr(app.task_executor, 'submit', lambda *args: calls.append(args))
    assert client.post('/api/smarteye/tasks/local-task/retry', headers=auth_headers).status_code == 200
    assert runtime.get('local-task')['attempt'] == 2
    assert calls

def test_ai_upload_preview_is_authenticated_shared_and_path_safe(client, auth_headers, monkeypatch, tmp_path):
    from integrations.smarteye import app
    from integrations.smarteye.api import ai_image
    monkeypatch.setattr(app, 'cleanup_expired_ai_image_tasks', lambda: 0)
    ai_root = tmp_path / 'storage/smarteye/ai_gen'
    monkeypatch.setattr(app, 'AI_IMAGE_DOWNLOAD_ROOT', ai_root)
    monkeypatch.setattr(ai_image, 'AI_IMAGE_DOWNLOAD_ROOT', ai_root)
    image = BytesIO(); Image.new('RGB', (30, 20), 'red').save(image, format='PNG')
    result = client.post('/api/smarteye/ai-image/upload', headers=auth_headers,
        files={'files': ('sample.png', image.getvalue(), 'image/png')})
    assert result.status_code == 200, result.text
    asset = result.json()['data']['assets'][0]
    preview = client.get(asset['preview_url'], headers=auth_headers)
    assert preview.status_code == 200
    assert preview.content == image.getvalue()
    assert client.get(asset['preview_url']).status_code == 401
    canonical = next((tmp_path / 'storage/images').rglob('*.png'))
    assert canonical.samefile(Path(asset['local_path']))
    bad = client.post('/api/smarteye/ai-image/upload', headers=auth_headers,
        data={'session_id': '../outside'}, files={'files': ('x.png', image.getvalue())})
    assert bad.status_code == 400

def test_independent_schedulers_persist_and_stop_separately(client, auth_headers, monkeypatch):
    from integrations.smarteye.api import ledger_image_downloader as ledger
    ledger._schedulers.clear()
    completed = []
    def fake_run(task_id, *args, **kwargs):
        completed.append(task_id)
        ledger.ledger_image_tasks[task_id]['status'] = 'completed'
    monkeypatch.setattr(ledger, 'run_ledger_image_download_task', fake_run)
    ids = []
    for platform in ('one', 'two'):
        response = client.post('/api/smarteye/ledger-image/scheduler/configure', headers=auth_headers,
            json={'name': platform, 'platform_ids': [platform], 'interval_minutes': 60, 'enabled': True})
        assert response.status_code == 200, response.text
        ids.append(response.json()['data']['sched_id'])
    deadline = time.monotonic() + 3
    while len(completed) < 2 and time.monotonic() < deadline:
        time.sleep(.01)
    assert len(completed) == 2
    client.post('/api/smarteye/ledger-image/scheduler/stop', headers=auth_headers, json={'sched_id': ids[0]})
    assert ledger._schedulers[ids[0]].enabled is False
    assert ledger._schedulers[ids[1]].enabled is True
    client.post('/api/smarteye/ledger-image/scheduler/stop', headers=auth_headers, json={'sched_id': ids[1]})
    for sched in ledger._schedulers.values():
        sched._thread.join(timeout=2)
    from integrations.smarteye.hooks import startup
    ledger._schedulers.clear()
    startup()
    assert set(ledger._schedulers) == set(ids)
    assert all(not s.enabled for s in ledger._schedulers.values())
    ledger._schedulers.clear()

def test_all_filtered_detection_completes_without_retrying_image(client,auth_headers,monkeypatch,tmp_path):
    from integrations.smarteye import app
    monkeypatch.setattr(app,'DOWNLOAD_PATH',str(tmp_path/'storage/smarteye'))
    monkeypatch.setattr(app,'get_automated_token',lambda:('test','1'))
    monkeypatch.setattr(app,'get_alarm_image_data',lambda *a,**k:{'type_url_map':{'test':['http://fixture/small.jpg']}})
    content=BytesIO();Image.new('RGB',(719,30),'blue').save(content,format='JPEG');calls=[]
    class Response:
        def raise_for_status(self):pass
        def iter_content(self,size):yield content.getvalue()
    def fetch(*a,**k):calls.append(1);return Response()
    monkeypatch.setattr(app.requests.Session,'get',fetch)
    response=client.post('/api/smarteye/download',headers={**auth_headers,'X-SmartEye-Min-Long-Edge':'720'},json={'platName':'test'})
    assert response.status_code==200,response.text
    task_id=response.json()['data']['task_id']
    deadline=time.monotonic()+5
    while get_runtime().get(task_id)['status'] in ('pending','running') and time.monotonic()<deadline:time.sleep(.01)
    task=get_runtime().get(task_id)
    assert task['status']=='completed' and task['filtered_count']==1,task
    assert len(calls)==1 and not list((tmp_path/'storage/images').rglob('*.jpg'))

def test_real_http_download_zip_and_retry_cache(client, auth_headers, monkeypatch, tmp_path):
    from integrations.smarteye import app
    root = tmp_path / 'storage/smarteye'
    monkeypatch.setattr(app, 'DOWNLOAD_PATH', str(root))
    monkeypatch.setattr(app, 'get_automated_token', lambda: ('test', '1'))
    content = BytesIO(); Image.new('RGB', (40, 30), 'blue').save(content, format='JPEG')
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path); self.send_response(200); self.end_headers(); self.wfile.write(content.getvalue())
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f'http://127.0.0.1:{server.server_port}/sample.jpg'
    monkeypatch.setattr(app, 'get_alarm_image_data', lambda *a, **k: {'type_url_map': {'test': [url]}})
    try:
        response = client.post('/api/smarteye/download', headers=auth_headers, json={'platName': 'test'})
        assert response.status_code == 200, response.text
        task_id = response.json()['data']['task_id']
        deadline = time.monotonic() + 5
        while get_runtime().get(task_id)['status'] in ('pending', 'running') and time.monotonic() < deadline:
            time.sleep(.01)
        task = get_runtime().get(task_id)
        assert task['status'] == 'completed', task
        import zipfile
        response = client.get(f'/api/smarteye/download-zip/{task_id}', headers=auth_headers)
        assert response.status_code == 200, response.text
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            assert len(archive.namelist()) == 1
            assert archive.read(archive.namelist()[0]) == content.getvalue()
        assert len(requests) == 1
        target = next(root.rglob('sample.jpg'))
        canonical = next((tmp_path / 'storage/images').rglob('*.jpg'))
        assert canonical.samefile(target)
        get_runtime().cancel(task_id); get_runtime().retry(task_id)
        owner = actor.set({'tenant_id': 1, 'user_id': 1})
        try: tracked_run(app.run_download_task, task_id, {'platName': 'test', 'sourceType': 'alarm'})
        finally: actor.reset(owner)
        assert len(requests) == 1
        assert canonical.samefile(target)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)

@pytest.mark.parametrize('kind', ['smart-search', 'ledger', 'ledger-crop', 'tool'])
def test_retained_download_workers_use_real_http_and_shared_storage(client, monkeypatch, tmp_path, kind):
    from integrations.smarteye.api import smart_search, ledger_image_downloader as ledger, tool_tasks
    root = tmp_path / 'storage/smarteye'
    root.mkdir(parents=True, exist_ok=True)
    content = BytesIO(); Image.new('RGB', (40, 30), 'green').save(content, format='JPEG')
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(content.getvalue())
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    url = f'http://127.0.0.1:{server.server_port}/sample.jpg'
    platform = {'plat_id': 'local', 'platform_name': 'local', 'base_url': 'http://local', 'state': '1'}
    token = actor.set({'tenant_id': 1, 'user_id': 1})
    task_id = 'test-' + kind
    try:
        if kind == 'smart-search':
            monkeypatch.setattr(smart_search, 'SMART_SEARCH_DOWNLOAD_PATH', str(root))
            monkeypatch.setattr(smart_search, 'login_platform', lambda p: {'success': True})
            monkeypatch.setattr(smart_search, 'search_platform', lambda *args: {'success': True, 'images': [url]})
            smart_search.smart_search_tasks[task_id] = {'status': 'pending'}
            tracked_run(smart_search.run_smart_search_task, task_id, [platform], {'text': 'test'})
        elif kind == 'tool':
            monkeypatch.setattr(tool_tasks, 'DOWNLOAD_PATH', str(root))
            monkeypatch.setattr(tool_tasks, 'get_smart_search_platforms', lambda **kw: [platform])
            monkeypatch.setattr(tool_tasks, 'login_platform', lambda p: {'success': True, 'token': 'test'})
            monkeypatch.setattr(tool_tasks, 'fetch_page_data', lambda *args: ([{'backPath': url, 'capturePath': 'sample.jpg'}], 1))
            tool_tasks.tool_tasks[task_id] = {'status': 'pending', 'downloaded': 0}
            tracked_run(tool_tasks.run_tool_task, task_id, 'local', 'device', 'start', 'end', 1)
        else:
            monkeypatch.setattr(ledger, 'DOWNLOAD_PATH', str(root))
            monkeypatch.setattr(ledger, 'LEDGER_ENABLE_DEDUP', False)
            monkeypatch.setattr(ledger, 'get_smart_search_platforms', lambda **kw: [platform])
            monkeypatch.setattr(ledger, 'get_combined_platform_mapping', lambda: {'local': {'alarmTypes': {'测试大模型': '1'}}})
            monkeypatch.setattr(ledger, 'login_platform', lambda p: {'success': True, 'token': 'test'})
            monkeypatch.setattr(ledger, 'query_alarm_candidates', lambda *args: [
                {'id': i, 'device_id': 'device', 'alarm_time': '2026-10-13 01:00:00', 'alarm_type_id': '1'} for i in (1, 2)])
            monkeypatch.setattr(ledger, 'query_alarm_type_labels', lambda *args: [])
            monkeypatch.setattr(ledger, 'query_ledger_record_page', lambda *args: ([{
                'analyseId': 'one', 'deviceId': 'device', 'captureTime': '2026-10-13 01:00:00',
                'backPath': url, 'analyseRes': 'test'}], 1))
            if kind == 'ledger-crop':
                monkeypatch.setattr(ledger, 'get_combined_platform_mapping', lambda: {'local': {'alarmTypes': {'测试': '1'}}})
            monkeypatch.setattr(ledger, 'parse_analyse_res', lambda s: {'model_type': '小模型', 'model_name': 'test',
                'labels': ['target'], 'bboxes': [{'ltx': 5, 'lty': 4, 'width': 20, 'height': 15}]})
            monkeypatch.setattr(ledger, '_record_matches_alarm', lambda *args: True)
            ledger.ledger_image_tasks[task_id] = {'status': 'pending', 'progress': 0}
            tracked_run(ledger.run_ledger_image_download_task, task_id, ['local'], None, '2026-10-13 00:59:00', '2026-10-13 01:01:00', 'crop' if kind == 'ledger-crop' else 'full')
        task = get_runtime().get(task_id)
        assert task['status'] == 'completed', task
        import zipfile
        with zipfile.ZipFile(task['zip_path']) as archive:
            names = [name for name in archive.namelist() if name.endswith('.jpg')]
            assert names
            saved = archive.read(names[0])
            if kind == 'ledger-crop':
                assert Image.open(BytesIO(saved)).size == (20, 15)
                assert all('_crop_' in name for name in names)
            else:assert saved == content.getvalue()
        import hashlib
        canonical = next((tmp_path / 'storage/images').rglob(hashlib.sha256(saved).hexdigest()+'.jpg'))
        assert all(canonical.samefile(p) for p in root.rglob('*.jpg'))
        if kind.startswith('ledger'):
            result = task['platform_results'][0]
            assert result['alarm_count'] == result['downloaded_alarm_count'] == 2
            assert result['unmatched_alarm_count'] == result['failed_alarm_count'] == 0
    finally:
        actor.reset(token); server.shutdown(); server.server_close(); thread.join(timeout=2)

@pytest.mark.parametrize('source_type', ['alarm', 'capture', 'reject'])
def test_retained_query_types_return_upstream_records(client, auth_headers, monkeypatch, source_type):
    from integrations.smarteye import app
    monkeypatch.setattr(app, 'get_automated_token', lambda: ('test', '1'))
    fn = {'alarm': 'get_alarm_image_data', 'capture': 'get_capture_image_data', 'reject': 'get_reject_image_data'}[source_type]
    monkeypatch.setattr(app, fn, lambda *args, **kw: {'records': [{'id': 'record', 'deviceId': 'device'}], 'total': 1, 'pages': 1})
    response = client.post('/api/smarteye/platforms', headers=auth_headers, json={'platName': 'local', 'sourceType': source_type})
    assert response.status_code == 200
    assert response.json()['data']['records'][0]['id'] == 'record'

def test_stale_queued_attempt_never_runs(client):
    runtime = get_runtime()
    runtime.create('queued', 'detection', {'status': 'pending'}, {'tenant_id': 1, 'user_id': 1})
    runtime.cancel('queued'); runtime.retry('queued')
    calls = []
    tracked_run(lambda task_id: calls.append(task_id), 'queued', _expected_attempt=1)
    assert not calls

def test_watch_worker_preserves_owner_and_stop_cancels_late_images(client, monkeypatch, tmp_path):
    from integrations.smarteye.api import ledger_watch_downloader as watch
    reached = threading.Event()
    owners = []
    def fake_loop(job_id):
        owners.append(actor.get())
        reached.set()
        watch._stop_events[job_id].wait(timeout=2)
    monkeypatch.setattr(watch, '_watch_loop', fake_loop)
    monkeypatch.setattr(watch, 'get_watch_jobs', lambda *args, **kw: [{'job_id': 'watch', 'name': 'local', 'poll_interval_seconds': 1}])
    token = actor.set({'tenant_id': 1, 'user_id': 1})
    try:
        watch.start_watch_job('watch')
        assert reached.wait(timeout=2)
        watch.stop_watch_job('watch')
        watch._running_jobs['watch'].join(timeout=2)
        assert owners[0]['user_id'] == 1
        assert get_runtime().get('watch')['status'] == 'cancelled'
        image = BytesIO(); Image.new('RGB', (20, 20), 'red').save(image, format='PNG')
        with pytest.raises(RuntimeError):
            get_runtime().save_image('watch', tmp_path / 'storage/smarteye/watch/a.png', image.getvalue(), attempt=1)
    finally:
        actor.reset(token)

def test_selected_download_zip_uses_shared_storage(client, auth_headers, monkeypatch, tmp_path):
    import zipfile
    from integrations.smarteye import app
    from integrations.smarteye.runtime import save_image_content
    monkeypatch.setattr(app, 'DOWNLOAD_PATH', str(tmp_path / 'storage/smarteye'))
    content = BytesIO(); Image.new('RGB', (20, 20), 'red').save(content, format='PNG')
    def download(url, path, session, collection_name=None):
        save_image_content(path, content.getvalue(),collection_name=collection_name)
        return True
    monkeypatch.setattr(app, 'download_file', download)
    response = client.post('/api/smarteye/download-images', headers=auth_headers,
        json={'urls': ['http://one/a.png', 'http://two/a.png'],'collection_name':'选择下载平台'})
    assert response.status_code == 200, response.text
    with zipfile.ZipFile(BytesIO(response.content)) as archive:
        assert len(archive.namelist()) == 2
        assert all(archive.read(name) == content.getvalue() for name in archive.namelist())
    assert len(list((tmp_path / 'storage/images').rglob('*.png'))) == 1
    from models import ImageCollection
    from sqlalchemy import select
    with get_runtime().engine.connect() as conn:
        assert conn.execute(select(ImageCollection.__table__.c.name)).scalar_one()=='选择下载平台'
    job, = get_runtime().list()
    assert job['kind'] == 'selection' and job['status'] == 'completed'

def test_selected_download_failure_is_not_empty_success(client, auth_headers, monkeypatch, tmp_path):
    from integrations.smarteye import app
    monkeypatch.setattr(app, 'DOWNLOAD_PATH', str(tmp_path / 'storage/smarteye'))
    monkeypatch.setattr(app, 'download_file', lambda *args: False)
    response = client.post('/api/smarteye/download-images', headers=auth_headers,
        json={'urls': ['http://local/failure.jpg']})
    assert response.status_code == 502
    assert get_runtime().list()[0]['status'] == 'failed'

def test_watch_progress_survives_memory_clear(client, monkeypatch):
    from integrations.smarteye.api import ledger_watch_downloader as watch
    from integrations.smarteye.runtime import execution
    get_runtime().create('persist-watch', 'watch', {'status': 'running'}, {'tenant_id': 1, 'user_id': 1})
    state = {'total_downloaded': 5, 'recent_logs': [{'msg': 'downloaded'}], 'status': 'polling'}
    watch.watch_jobs_state['persist-watch'] = state
    token = execution.set(('persist-watch', 1))
    try:
        watch._persist_state('persist-watch')
    finally:
        execution.reset(token)
        watch.watch_jobs_state.pop('persist-watch')
    get_runtime().recover()
    monkeypatch.setattr(watch, 'get_watch_jobs', lambda: [{'job_id': 'persist-watch'}])
    restored, = watch.get_all_watch_states()
    assert restored['total_downloaded'] == 5
    assert restored['recent_logs'] == state['recent_logs']
    assert restored['status'] == 'interrupted' and not restored['is_running']

def test_bad_source_type_rejected_before_task(client, auth_headers):
    response = client.post('/api/smarteye/download', headers=auth_headers,
        json={'platName': 'local', 'sourceType': '../outside'})
    assert response.status_code == 400
    assert get_runtime().list() == []

def test_generic_cancel_stops_watch_polling(client, auth_headers, monkeypatch):
    from integrations.smarteye.api import ledger_watch_downloader as watch
    get_runtime().create('cancel-watch', 'watch', {'status': 'running'}, {'tenant_id': 1, 'user_id': 1})
    stopped = threading.Event()
    monkeypatch.setitem(watch._stop_events, 'cancel-watch', stopped)
    result = client.post('/api/smarteye/tasks/cancel-watch/cancel', headers=auth_headers)
    assert result.status_code == 200
    assert stopped.is_set()


def test_watch_creation_uses_alarm_database_and_fixed_window(client, auth_headers, monkeypatch):
    from integrations.smarteye.api import db_utils
    saved=[]
    monkeypatch.setattr(db_utils,'ensure_ledger_watch_tables',lambda:None)
    monkeypatch.setattr(db_utils,'upsert_watch_job',lambda job:saved.append(job) or True)
    response=client.post('/api/smarteye/ledger-watch/jobs',headers=auth_headers,
        json={'name':'bounded-watch','source_types':'reject','time_offset_seconds':60,'enabled':False,'poll_interval_seconds':120})
    assert response.status_code==200,response.text
    assert saved[0]['source_types']=='alarm' and saved[0]['time_offset_seconds']==15
    assert saved[0]['poll_interval_seconds']==120 and not saved[0]['enabled']
