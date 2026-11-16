"""Persistent schedules for collecting existing device captures, never triggering cameras."""
import hashlib
import json
import logging
import threading
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from urllib.parse import urljoin

import requests
from flask import jsonify, request
from sqlalchemy import delete, insert, select, update

from integrations.smarteye.management import get_device, get_platform, ERRORS
from integrations.smarteye.management_schema import capture_schedules
from integrations.smarteye.runtime import actor, execution, get_runtime, response_bytes, tracked_run, device_source

_lock = threading.RLock()
_active = set()
_stop = threading.Event()
_thread = None
_pool = None
_platform_tokens = {}
_platform_token_locks = {}
_token_guard = threading.Lock()



class CaptureError(RuntimeError):
    """Only fixed messages from this module may be exposed to the UI."""


def _safe_error(exc):
    logging.getLogger(__name__).debug('Device capture error category: %s', type(exc).__name__)
    if isinstance(exc, CaptureError):
        return str(exc)
    if isinstance(exc, requests.Timeout):
        return '设备抓图请求超时，请检查平台网络'
    if isinstance(exc, requests.RequestException):
        return '设备抓图请求失败，请检查平台服务'
    return '设备抓图失败，请检查平台、图片或入库配置'


def _capture_token(key, platform, expired=None):
    from integrations.smarteye.api.smart_search import login_platform
    with _token_guard:
        lock = _platform_token_locks.setdefault(key, threading.Lock())
    with lock:
        token = _platform_tokens.get(key)
        # Upstream authentication decides expiry; expire_time is only an estimate.
        if token is not None and token is not expired:
            return token
        _platform_tokens.pop(key, None)
        token = login_platform(platform)
        if not token.get('success'):
            raise CaptureError('平台登录失败：' + ERRORS.get(token.get('error_kind'), '请检查平台响应和登录配置'))
        _platform_tokens[key] = token
        return token


def _get(sched_id):
    with get_runtime().engine.connect() as conn:
        row = conn.execute(select(capture_schedules).where(capture_schedules.c.sched_id == sched_id)).mappings().first()
    return {**row['payload'], 'sched_id': sched_id, 'tenant_id': row['tenant_id']} if row else None


def _save(item):
    with get_runtime().engine.begin() as conn:
        conn.execute(update(capture_schedules).where(capture_schedules.c.sched_id == item['sched_id']).values(payload=item))


def _interval_seconds(item):
    return item['interval_seconds'] if 'interval_seconds' in item else item['interval_minutes'] * 60


def _public(item):
    seconds = _interval_seconds(item)
    return {**{key: value for key, value in item.items() if key not in ('owner', 'tenant_id', 'cursors', 'cancel_requested')},
            'interval_seconds': seconds, 'interval_minutes': seconds / 60}


def _items(tenant_id=None):
    query = select(capture_schedules)
    if tenant_id is not None:
        query = query.where(capture_schedules.c.tenant_id == tenant_id)
    with get_runtime().engine.connect() as conn:
        return [{**row['payload'], 'sched_id': row['sched_id'], 'tenant_id': row['tenant_id']}
                for row in conn.execute(query).mappings()]


def platform_in_use(plat_id):
    return any((item['enabled'] or item['running'] or item['sched_id'] in _active) and any(d['plat_id'] == plat_id for d in item['devices'])
               for item in _items())


def _admin(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        owner = actor.get()
        if not owner or owner.get('role') != 'admin':
            return jsonify(code=1, message='仅管理员可管理设备抓图'), 403
        try:
            return fn(*args, **kwargs)
        except (ValueError, TypeError, KeyError) as exc:
            return jsonify(code=1, message=str(exc)), 400
    return wrapped


def _owned(sched_id):
    item = _get(sched_id)
    return item if item and item['tenant_id'] == actor.get()['tenant_id'] else None


def _destination(body, owner):
    from models import Project, ImageCollection
    result = {**owner}
    for key in ('project_id', 'collection_id', 'min_long_edge'):
        result.pop(key, None)
    with get_runtime().engine.connect() as conn:
        for key, model in (('project_id', Project), ('collection_id', ImageCollection)):
            value = body.get(key)
            if value:
                value = uuid.UUID(str(value))
                table = model.__table__
                if not conn.execute(select(table.c.id).where(table.c.id == value, table.c.tenant_id == owner['tenant_id'])).first():
                    raise ValueError('项目或图片集不存在')
                result[key] = str(value)
            else:
                result.pop(key, None)
    minimum = body.get('min_long_edge')
    if minimum is not None:
        if type(minimum) is not int or not 0 <= minimum <= 32768:
            raise ValueError('长边阈值须在0至32768之间')
        result['min_long_edge'] = minimum
    return result


def register_routes(app):
    @app.route('/device-capture/schedules', methods=['GET', 'POST'])
    @_admin
    def device_capture_schedules():
        owner = actor.get()
        if request.method == 'GET':
            return jsonify(code=0, data=[_public(item) for item in _items(owner['tenant_id'])])
        body = request.get_json(silent=True)
        if not isinstance(body, dict):
            raise ValueError('请求须为JSON对象')
        name, devices, interval = body.get('name'), body.get('devices'), body.get('interval_seconds')
        if not isinstance(name, str) or not name.strip() or len(name) > 160:
            raise ValueError('请输入不超过160字的名称')
        if 'interval_seconds' not in body and 'interval_minutes' in body:
            minutes = body['interval_minutes']
            if type(minutes) is not int or not 1 <= minutes <= 525600:
                raise ValueError('执行间隔须为1至525600分钟')
            interval = minutes * 60
        if type(interval) is not int or not 1 <= interval <= 31536000:
            raise ValueError('执行间隔须为1至31536000秒')
        if not isinstance(devices, list) or not 1 <= len(devices) <= 1000:
            raise ValueError('请选择1至1000台设备')
        selected = []
        for device in devices:
            if not isinstance(device, dict) or not isinstance(device.get('plat_id'), str) or not isinstance(device.get('device_id'), str):
                raise ValueError('设备参数无效')
            platform = get_platform(device['plat_id'])
            registered = get_device(owner['tenant_id'], device['plat_id'], device['device_id'])
            if not platform or not registered:
                raise ValueError('平台或设备不存在')
            if registered.get('identity_kind') == 'name':
                raise ValueError('名称分组没有真实设备ID，不能创建定时抓图')
            if str(platform.get('state')) != '1':
                raise ValueError('平台未启用')
            value = {'plat_id': device['plat_id'], 'device_id': device['device_id']}
            if value not in selected:
                selected.append(value)
        if type(body.get('enabled', False)) is not bool:
            raise ValueError('启用状态无效')
        destination = _destination(body, owner)
        item = dict(sched_id=str(uuid.uuid4()), tenant_id=owner['tenant_id'], name=name.strip(),
                    devices=selected, interval_seconds=interval, owner=destination,
                    project_id=destination.get('project_id'), collection_id=destination.get('collection_id'),
                    min_long_edge=destination.get('min_long_edge'), enabled=body.get('enabled', False),
                    running=False, last_task_id=None, last_run=None, created_at=time.time(), next_run=time.time(), last_error='', cursors={}, device_results=[])
        with get_runtime().engine.begin() as conn:
            conn.execute(insert(capture_schedules).values(sched_id=item['sched_id'], tenant_id=owner['tenant_id'], payload=item))
        return jsonify(code=0, data=_public(item)), 201

    @app.route('/device-capture/schedules/<sched_id>', methods=['DELETE'])
    @app.route('/device-capture/schedules/<sched_id>/<action>', methods=['POST'])
    @_admin
    def device_capture_action(sched_id, action=None):
        with _lock:
            item = _owned(sched_id)
            if not item:
                return jsonify(code=1, message='抓图计划不存在'), 404
            if request.method == 'DELETE' or action == 'stop':
                item['enabled'] = False
                item['cancel_requested'] = True
                if item.get('last_task_id') and item['running']:
                    get_runtime().cancel(item['last_task_id'])
                _save(item)
                if request.method == 'DELETE':
                    if item['running']:
                        return jsonify(code=1, message='请等待当前抓图停止后删除'), 409
                    with get_runtime().engine.begin() as conn:
                        conn.execute(delete(capture_schedules).where(capture_schedules.c.sched_id == sched_id))
            elif action == 'start':
                item.update(enabled=True, next_run=time.time(), last_error='')
                _save(item)
            elif action == 'run':
                if not _submit(sched_id):
                    return jsonify(code=1, message='当前计划正在执行或抓图队列已满'), 409
                item = _get(sched_id)
            else:
                raise ValueError('不支持的操作')
        return jsonify(code=0, data=_public(item))


def _capture_device(task_id, owner, device, start, end):
    from integrations.smarteye.api.ledger_image_downloader import query_ledger_record_page, _TOKEN_EXPIRED
    platform = get_platform(device['plat_id'])
    registered = get_device(owner['tenant_id'], device['plat_id'], device['device_id'])
    if not platform or not registered:
        raise CaptureError('平台或设备已删除')
    if registered.get('identity_kind') == 'name':
        raise CaptureError('名称分组没有真实设备ID，不能定时抓图')
    if str(platform.get('state')) != '1':
        raise CaptureError('平台未启用')
    config = hashlib.sha256(json.dumps([platform.get(key) for key in ('base_url','username','password','login_url')]).encode()).hexdigest()
    token_key = (owner['tenant_id'], owner['user_id'], device['plat_id'], config)
    token = _capture_token(token_key, platform)
    runtime, count, filtered, skipped, representative = get_runtime(), 0, 0, 0, None
    attempt = execution.get()[1]
    with requests.Session() as session:
        session.trust_env = False
        job = runtime.get(task_id)
        if not job or job['status'] in ('cancelled', 'interrupted') or job['attempt'] != attempt:
            raise CaptureError('抓图已停止')
        page = query_ledger_record_page(platform['base_url'], token, device['device_id'], None, None, 1, 1, latest_only=True)
        if page is _TOKEN_EXPIRED:
            token = _capture_token(token_key, platform, expired=token)
            page = query_ledger_record_page(platform['base_url'], token, device['device_id'], None, None, 1, 1, latest_only=True)
            if page is _TOKEN_EXPIRED:
                with _platform_token_locks[token_key]:
                    if _platform_tokens.get(token_key) is token:
                        _platform_tokens.pop(token_key, None)
                raise CaptureError('平台登录凭证失效，重新登录后仍未通过验证，请检查平台会话')
        records, total = page
        if not records and total:
            raise CaptureError('台账分页不完整')
        for record in records[:1]:
            if record.get('success') is False:
                skipped += 1
                continue
            path = record.get('backPath') or record.get('capturePath')
            if not path:
                raise CaptureError('台账缺少原图地址')
            url = urljoin(platform['base_url'].rstrip('/') + '/', path)
            with session.get(url, timeout=(10, 60), stream=True) as response:
                response.raise_for_status()
                content = response_bytes(response)
            key = hashlib.sha256(json.dumps([device['plat_id'], device['device_id'], url], separators=(',', ':')).encode()).hexdigest()
            saved = runtime.save_image(task_id, runtime.root / 'smarteye' / task_id / (key + '.jpg'),
                content, attempt, associate=True, collection_name=platform['platform_name'],
                device_info=device_source(platform['platform_name'], {**registered, **record}, device['plat_id'], device['device_id']))
            if saved:
                representative = representative or saved
                count += 1
            else:
                filtered += 1
    return {'downloaded': count, 'filtered': filtered, 'skipped': skipped, 'representative_blob_path': representative}


def _run_schedule(sched_id):
    with _lock:
        item = _get(sched_id)
        if not item or item.get('cancel_requested'):
            if item:
                item['running'] = False
                _save(item)
            return
        task_id, end = str(uuid.uuid4()), time.time()
        owner = item['owner']
        try:
            get_runtime().create(task_id, 'device-capture', {'status': 'pending', 'name': item['name'], 'progress': 0}, owner)
        except Exception as exc:
            item.update(running=False, last_error=_safe_error(exc), next_run=time.time() + _interval_seconds(item))
            _save(item)
            return
        item.update(running=True, last_task_id=task_id, last_run=end, device_results=[], last_error='')
        _save(item)
    def run(task):
        errors = []
        for index, device in enumerate(item['devices']):
            if get_runtime().get(task)['status'] in ('cancelled', 'interrupted'):
                break
            key = json.dumps([device['plat_id'], device['device_id']], separators=(',', ':'))
            try:
                result = _capture_device(task, owner, device, item.get('cursors', {}).get(key, item['created_at'] - _interval_seconds(item)), end)
                if get_runtime().get(task)['status'] in ('cancelled', 'interrupted'):
                    break
                item.setdefault('cursors', {})[key] = end
                result.update(status='completed', **device)
            except Exception as exc:
                error = _safe_error(exc)
                errors.append(error)
                result = {**device, 'status': 'failed', 'error': error}
            registered=get_device(owner['tenant_id'],device['plat_id'],device['device_id']) or {}
            platform=get_platform(device['plat_id']) or {}
            result.update(device_name=registered.get('device_name'),platform_name=platform.get('platform_name'))
            item['device_results'].append(result)
            with _lock:
                current = _get(sched_id)
                if current:
                    current.update(cursors=item.get('cursors', {}), device_results=item['device_results'])
                    _save(current)
            get_runtime().patch(task, {'progress': int((index + 1) * 100 / len(item['devices'])), 'device_results': item['device_results']}, execution.get()[1])
        get_runtime().patch(task, {'status': 'failed' if errors else 'completed', 'message': f'设备抓图结束：已处理 {len(item["device_results"])} 台，失败 {len(errors)} 台；具体原因见设备明细。' if errors else '设备抓图完成'}, execution.get()[1])
        return errors
    def safe_run(task):
        try:
            return run(task)
        except Exception as exc:
            raise CaptureError(_safe_error(exc)) from None
    try:
        errors = tracked_run(safe_run, task_id) or []
    except Exception as exc:
        errors = [_safe_error(exc)]
    finally:
        with _lock:
            current = _get(sched_id)
            if current:
                current.update(running=False, last_error='; '.join(f'{error}（{count} 台设备）' if count>1 else error for error,count in Counter(errors).items()), next_run=time.time() + _interval_seconds(item))
                _save(current)


def _submit(sched_id):
    global _pool
    with _lock:
        if sched_id in _active or len(_active) >= 2:
            return False
        item = _get(sched_id)
        if not item or item['running']:
            return False
        item.update(running=True, cancel_requested=False)
        _save(item)
        _active.add(sched_id)
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix='device-capture')
        def work():
            try:
                _run_schedule(sched_id)
            finally:
                with _lock:
                    _active.discard(sched_id)
        _pool.submit(work)
        return True


def start_worker():
    global _thread
    if _thread and _thread.is_alive():
        return
    for item in _items():
        if item['enabled'] or item['running']:
            item.update(enabled=False, running=False, last_error='服务重启，设备抓图计划已停用，请手动启动')
            _save(item)
    _stop.clear()
    def loop():
        while not _stop.wait(1):
            for item in _items():
                if item['enabled'] and not item['running'] and (item.get('next_run') or 0) <= time.time():
                    _submit(item['sched_id'])
    _thread = threading.Thread(target=loop, daemon=True, name='device-capture-scheduler')
    _thread.start()


def shutdown_worker():
    global _pool, _thread
    _stop.set()
    if _thread:
        _thread.join()
    with _lock:
        for sched_id in list(_active):
            item = _get(sched_id)
            if item and item.get('last_task_id'):
                get_runtime().cancel(item['last_task_id'])
    if _pool:
        _pool.shutdown(wait=True, cancel_futures=True)
    _pool = _thread = None
    with _token_guard:
        _platform_tokens.clear()
        _platform_token_locks.clear()
