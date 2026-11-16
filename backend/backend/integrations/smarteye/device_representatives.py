"""Fetch at most one existing capture per device, using shared image storage."""
import hashlib
import json
from urllib.parse import quote, urljoin, urlsplit

import requests

from integrations.smarteye.management import _cancelled, _SyncCancelled, now
from integrations.smarteye.runtime import execution, get_runtime, response_bytes


def ensure_representative(platform, token, device):
    runtime = get_runtime()
    old_key = device.get('representative_blob')
    if old_key:
        path = (runtime.root / old_key).resolve()
        if path.is_relative_to(runtime.root.resolve()) and path.is_file():
            return {'representative_status': 'ready', 'representative_error': None}, True
    changes = {'representative_blob': None, 'representative_url': None,
               'representative_checked_at': now()}
    try:
        from integrations.smarteye.api.ledger_image_downloader import _post_with_token, _TOKEN_EXPIRED
        from integrations.smarteye.management import login_platform
        payload = {'current': 1, 'size': 1, 'model': {'deviceId': device['device_id'],
            'startTime': None, 'endTime': None, 'success': True},
            'sort': 'id', 'order': 'descending', 'searchCount': True}
        if _cancelled():raise _SyncCancelled()
        response = _post_with_token(platform['base_url'], '/api/schedule/capture/stat/v1/log', payload, token)
        if response is _TOKEN_EXPIRED:
            fresh = login_platform(platform)
            if not fresh.get('success'):raise ValueError('平台登录已过期，未取得代表图')
            token.clear();token.update(fresh)
            if _cancelled():raise _SyncCancelled()
            response = _post_with_token(platform['base_url'], '/api/schedule/capture/stat/v1/log', payload, token)
        if not isinstance(response, dict) or str(response.get('code')) != '0':
            raise ValueError('抓图记录查询失败，未取得代表图')
        data = response.get('data')
        if not isinstance(data, dict) or not isinstance(data.get('records'), list):
            raise ValueError('抓图记录格式无效，未取得代表图')
        records = data['records']
        if not records:
            if int(data.get('total') or 0):raise ValueError('抓图记录不完整，未取得代表图')
            return {**changes, 'representative_status': 'missing',
                    'representative_error': '平台暂无成功抓图，将按设备名称分类'}, False
        record = records[0]
        if not isinstance(record, dict) or record.get('success') is False or (
                record.get('deviceId') is not None and str(record['deviceId']) != device['device_id']):
            raise ValueError('抓图记录与设备不匹配，未取得代表图')
        path = record.get('backPath') or record.get('capturePath')
        if not isinstance(path, str) or not path.strip():raise ValueError('最近抓图缺少图片地址，未取得代表图')
        url = urljoin(platform['base_url'].rstrip('/') + '/', path.strip())
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('最近抓图地址无效，未取得代表图')
        if _cancelled():raise _SyncCancelled()
        with requests.Session() as session:
            session.trust_env = False
            with session.get(url, timeout=(10, 30), stream=True) as downloaded:
                downloaded.raise_for_status()
                content = response_bytes(downloaded)
        task_id, attempt = execution.get()
        identity = hashlib.sha256(json.dumps([device['tenant_id'], device['plat_id'], device['device_id']]).encode()).hexdigest()
        key = runtime.save_image(task_id, runtime.root / 'smarteye' / 'device-previews' / (identity + '.jpg'),
            content, attempt, associate=False, device_info=device)
        return {**changes, 'representative_blob': key, 'representative_status': 'ready',
                'representative_error': None, 'representative_url': '/api/smarteye/devices/' +
                quote(device['plat_id'], safe='') + '/' + quote(device['device_id'], safe='') + '/preview'}, False
    except _SyncCancelled:
        raise
    except Exception as exc:
        if _cancelled():raise _SyncCancelled() from None
        message = '代表图下载或校验失败，将按设备名称分类'
        if isinstance(exc, requests.Timeout):message = '代表图请求超时，将按设备名称分类'
        elif isinstance(exc, ValueError) and str(exc) in (
                '平台登录已过期，未取得代表图', '抓图记录查询失败，未取得代表图',
                '抓图记录格式无效，未取得代表图', '抓图记录不完整，未取得代表图',
                '抓图记录与设备不匹配，未取得代表图', '最近抓图缺少图片地址，未取得代表图',
                '最近抓图地址无效，未取得代表图'):
            message = str(exc) + '；将按设备名称分类'
        return {**changes, 'representative_status': 'failed', 'representative_error': message}, False
