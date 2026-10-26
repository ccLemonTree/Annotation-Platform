"""Lifecycle, ownership and durable task operations around the retained Flask routes."""
import re
import uuid
from pathlib import Path
from flask import request, jsonify
from sqlalchemy import select, insert, update, delete
from integrations.smarteye.runtime import actor, get_runtime, schedules, tracked_run

_installed = False

def install_hooks(app):
    global _installed
    if _installed:
        return
    _installed = True

    @app.before_request
    def before():
        owner = actor.get()
        if not owner:
            return jsonify(code=401, message='请登录'), 401
        data = request.get_json(silent=True) or {}
        requested_tenant = data.get('tenant_id') or request.args.get('tenant_id')
        if requested_tenant is not None and str(requested_tenant) != str(owner['tenant_id']):
            return jsonify(code=403, message='不能操作其他租户的数据'), 403
        owner['request'] = data
        if request.path in ('/download', '/platforms'):
            if data.get('sourceType', request.args.get('sourceType', 'alarm')) not in ('alarm', 'capture', 'reject'):
                return jsonify(code=400, message='不支持的数据类型'), 400
        owner['session_id'] = data.get('session_id') or request.form.get('session_id')
        if request.path == '/download-images':
            owner['session_id'] = uuid.uuid4().hex
            get_runtime().create(owner['session_id'], 'selection', {'status': 'running'}, owner)
        if request.path.startswith('/ai-image/') and request.path.rsplit('/', 1)[-1] in ('upload', 'upload-folder', 'import-urls', 'import-existing'):
            owner['session_id'] = owner['session_id'] or uuid.uuid4().hex
        for name in ('task_id', 'job_id', 'sched_id'):
            value = (request.view_args or {}).get(name) or data.get(name)
            if value and ('scheduler' not in request.path or name == 'task_id'):
                job = get_runtime().get(value)
                # Watch jobs use their original SQL table, scoped to the single main tenant.
                if name != 'job_id' and (not job or job['tenant_id'] != owner['tenant_id']):
                    return jsonify(code=404, message='任务不存在'), 404
        if '/ledger-image/stop/' in request.path:
            get_runtime().cancel(request.view_args['task_id'])
            return jsonify(code=0, message='任务已取消')
        if owner['session_id']:
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,160}', owner['session_id']):
                return jsonify(code=400, message='无效素材会话'), 400
            job = get_runtime().get(owner['session_id'])
            if job and job['tenant_id'] != owner['tenant_id']:
                return jsonify(code=404, message='素材会话不存在'), 404
            get_runtime().create(owner['session_id'], 'material', {'status': 'completed'}, owner)
        if request.path.startswith('/ai-image/') and data:
            for asset in data.get('assets', []):
                path = asset.get('local_path')
                if path and not Path(path).resolve().is_relative_to(get_runtime().root / 'smarteye'):
                    return jsonify(code=400, message='非法素材路径'), 400
        if '/scheduler/' in request.path and request.path.endswith('/start'):
            sched_id = data.get('sched_id')
            with get_runtime().engine.connect() as conn:
                payload = conn.execute(select(schedules.c.payload).where(schedules.c.sched_id == sched_id)).scalar()
            if payload:
                owner.update(payload.get('owner', {}))
        if request.path.endswith('/scheduler/configure'):
            interval = data.get('interval_minutes', 120)
            duration = data.get('task_duration_hours', 0)
            if not isinstance(interval, (int, float)) or interval < 1 or not isinstance(duration, (int, float)) or duration < 0:
                return jsonify(code=400, message='调度间隔至少1分钟，时长不得为负'), 400

    @app.after_request
    def after(response):
        if request.path == '/download-images' and actor.get().get('session_id'):
            get_runtime().patch(actor.get()['session_id'], {'status': 'completed' if response.status_code == 200 else 'failed'}, 1)
        if response.is_json:
            content = response.get_json()
            task_id = (request.view_args or {}).get('task_id')
            if task_id and isinstance(content.get('data'), dict):
                state = get_runtime().get(task_id)
                if state and state['status'] in ('cancelled', 'interrupted'):
                    content['data'].update(status=state['status'], message=state.get('message', '任务已中断'))
                    response.set_data(app.json.dumps(content))
            if content.get('code') == 0 and '/scheduler/' in request.path:
                persist_schedulers()
        return response

    @app.errorhandler(Exception)
    def error(exc):
        from werkzeug.exceptions import HTTPException
        if isinstance(exc, HTTPException):
            return jsonify(code=exc.code, message=exc.description), exc.code
        app.logger.error('SmartEye operation failed: %s', type(exc).__name__)
        return jsonify(code=500, message='SmartEye操作失败，请检查服务配置'), 500

    @app.route('/tasks', methods=['GET'])
    def list_tasks():
        return jsonify(code=0, data=get_runtime().list(tenant_id=actor.get()['tenant_id']))

    @app.route('/tasks/<task_id>', methods=['GET'])
    def task_detail(task_id):
        return jsonify(code=0, data=get_runtime().get(task_id))

    @app.route('/tasks/<task_id>/cancel', methods=['POST'])
    def cancel(task_id):
        job = get_runtime().get(task_id)
        get_runtime().cancel(task_id)
        if job['kind'] == 'watch':
            from integrations.smarteye.api.ledger_watch_downloader import stop_watch_job
            stop_watch_job(task_id)
        from integrations.smarteye.api.ledger_image_downloader import cancel_task
        cancel_task(task_id)
        return jsonify(code=0, message='任务已取消')

    @app.route('/tasks/<task_id>/retry', methods=['POST'])
    def retry(task_id):
        from integrations.smarteye import app as source
        from integrations.smarteye.api import smart_search, ledger_image_downloader, tool_tasks
        job = get_runtime().get(task_id)
        if job['kind'] not in ('detection', 'ledger', 'smart-search', 'tool'):
            return jsonify(code=400, message='此任务不支持自动重试，请从对应页面重新提交'), 400
        params = job.get('request', {})
        try:
            get_runtime().retry(task_id)
        except ValueError as exc:
            return jsonify(code=400, message=str(exc)), 400
        owner = actor.get()
        owner['project_id'] = job.get('project_id')
        if job['kind'] == 'detection':
            source.tasks.pop(task_id, None)
            fn, args = source.run_download_task, (job.get('params', params),)
        elif job['kind'] == 'ledger':
            ledger_image_downloader.ledger_image_tasks.pop(task_id, None)
            fn, args = ledger_image_downloader.run_ledger_image_download_task, (
                params.get('platform_ids'), params.get('alarm_type_filter'), params.get('start_time'),
                params.get('end_time'), params.get('small_model_mode', 'crop'))
        elif job['kind'] == 'smart-search':
            smart_search.smart_search_tasks.pop(task_id, None)
            from integrations.smarteye.api.db_utils import get_smart_search_platforms
            platforms = [p for p in get_smart_search_platforms(False) if p['plat_id'] in params.get('platform_ids', [])]
            fn, args = smart_search.run_smart_search_task, (platforms, {
                k: params.get(k) for k in ('text', 'limit', 'startTime', 'endTime')})
        elif job['kind'] == 'tool':
            tool_tasks.tool_tasks.pop(task_id, None)
            fn, args = tool_tasks.run_tool_task, tuple(params.get(k) for k in
                ('platform_name', 'device_id', 'start_time', 'end_time', 'max_pages'))
        else:
            return jsonify(code=400, message='此任务不支持重试'), 400
        source.task_executor.submit(tracked_run, fn, task_id, *args)
        return jsonify(code=0, data={'task_id': task_id})

def persist_schedulers():
    from integrations.smarteye.api.ledger_image_downloader import get_all_schedulers
    owner = actor.get()
    with get_runtime().engine.begin() as conn:
        current = get_all_schedulers()
        ids = [s['sched_id'] for s in current]
        conn.execute(delete(schedules).where(schedules.c.tenant_id == owner['tenant_id'],
                                             schedules.c.sched_id.not_in(ids)))
        for sched in current:
            previous = conn.execute(select(schedules.c.payload).where(schedules.c.sched_id == sched['sched_id'])).scalar()
            saved_owner = (previous or {}).get('owner', owner)
            payload = {**sched, 'owner': saved_owner}
            if previous is None:
                conn.execute(insert(schedules).values(sched_id=sched['sched_id'], tenant_id=owner['tenant_id'], payload=payload))
            else:
                conn.execute(update(schedules).where(schedules.c.sched_id == sched['sched_id']).values(payload=payload))

def startup():
    runtime = get_runtime()
    runtime.recover()
    from integrations.smarteye.app import app
    install_hooks(app)
    from integrations.smarteye.api.ledger_image_downloader import configure_scheduler
    with runtime.engine.connect() as conn:
        saved = conn.execute(select(schedules)).mappings().all()
    for row in saved:
        payload = row['payload']
        configure_scheduler(sched_id=row['sched_id'], enabled=False, **{k: payload.get(k) for k in
            ('name', 'interval_minutes', 'task_duration_hours', 'platform_ids', 'alarm_type_filter', 'small_model_mode')})

def shutdown():
    from integrations.smarteye.api.ledger_image_downloader import get_all_schedulers, stop_scheduler
    from integrations.smarteye.api.ledger_watch_downloader import _stop_events
    for sched in get_all_schedulers():
        stop_scheduler(sched['sched_id'])
    for event in _stop_events.values():
        event.set()
    get_runtime().recover()
    from integrations.smarteye.app import task_executor
    task_executor.shutdown(wait=False, cancel_futures=True)
    from integrations.smarteye.api.db_cache import _connection_pool
    if _connection_pool:
        _connection_pool.closeall()
