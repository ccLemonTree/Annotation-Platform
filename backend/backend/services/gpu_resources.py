"""Read-only GPU snapshots through the configured ssh-manager MCP."""
import csv
from collections import OrderedDict
import io
import json
import math
from pathlib import Path
import subprocess
from threading import Lock
from time import monotonic
from datetime import datetime, timezone

from fastapi import HTTPException
from config.settings import settings


def number(value, maximum=None):
    value = value.strip()
    if value in ('N/A', '[N/A]', 'Not Supported', '[Not Supported]', 'Unknown Error'):
        return None
    result = float(value)
    if not math.isfinite(result) or result < 0 or (maximum is not None and result > maximum):
        raise ValueError('Invalid GPU metric')
    return result


def rows(value, columns, limit):
    if not isinstance(value, str) or len(value) > 1024 * 1024:
        raise ValueError('Invalid GPU output')
    result = list(csv.reader(io.StringIO(value), skipinitialspace=True, strict=True))
    if len(result) > limit or any(len(row) != columns for row in result):
        raise ValueError('Incomplete GPU output')
    return [[field.strip() for field in row] for row in result]


def parse_sample(sample):
    if not isinstance(sample, dict) or sample.get('gpu_code') != 0:
        raise ValueError('GPU query failed')
    gpus, processes, warnings = [], [], []
    for row in rows(sample['gpus'], 12, 256):
        index, uuid, name, driver, total, used, free, util, temp, draw, power_limit, mig = row
        index = int(index)
        if index < 0 or not uuid.startswith('GPU-') or not name or not driver:
            raise ValueError('Invalid GPU identity')
        total, used, free = number(total), number(used), number(free)
        if total is not None and any(v is not None and v > total for v in (used, free)):
            raise ValueError('Invalid GPU memory')
        gpus.append(dict(index=index, uuid=uuid, name=name, driver_version=driver,
            memory_total_mib=total, memory_used_mib=used, memory_free_mib=free,
            utilization_percent=number(util, 100), temperature_c=number(temp),
            power_draw_w=number(draw), power_limit_w=number(power_limit), mig_mode=mig))
    if not gpus or len({g['uuid'] for g in gpus}) != len(gpus) or len({g['index'] for g in gpus}) != len(gpus):
        raise ValueError('Missing or repeated GPU')
    if any(g['mig_mode'] not in ('Disabled', 'N/A', '[N/A]', 'Not Supported', '[Not Supported]') for g in gpus):
        warnings.append('MIG已启用或状态未知；当前仅显示物理卡指标，不能据此分配MIG实例。')
    complete = sample.get('process_code') == 0
    if complete:
        for uuid, pid, name, memory in rows(sample['processes'], 4, 4096):
            pid = int(pid)
            if pid <= 0 or not uuid.startswith(('GPU-', 'MIG-')) or not name:
                raise ValueError('Invalid GPU process')
            processes.append(dict(gpu_uuid=uuid, pid=pid, name=name, memory_used_mib=number(memory)))
        if any(p['gpu_uuid'] not in {g['uuid'] for g in gpus} for p in processes):
            warnings.append('部分计算进程未匹配物理卡，可能为MIG实例或采集期间发生变化。')
    else:
        warnings.append('计算进程采集失败；进程列表未知，不能视为没有进程。')
    return dict(gpus=gpus, processes=processes, processes_complete=complete, warnings=warnings)


def read_sample(connection=None):
    entry = Path(settings.GPU_SSH_MANAGER_ENTRY)
    if not settings.GPU_SSH_MANAGER_ENTRY or not entry.is_absolute() or not entry.is_file():
        raise HTTPException(503, '未配置GPU采集连接，请管理员配置ssh-manager MCP入口。')
    bridge = Path(__file__).resolve().parents[1] / 'scripts' / 'gpu_mcp_bridge.mjs'
    args = [settings.GPU_SSH_NODE_BINARY, str(bridge), str(entry.resolve()),
        settings.GPU_SERVER_NAME if connection is None else 'platform_gpu']
    options = {}
    if connection is not None:
        args.append('--connection-stdin')
        options['input'] = json.dumps(connection, ensure_ascii=False)
    try:
        result = subprocess.run(args, **options,
            capture_output=True, text=True, encoding='utf-8', timeout=40,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
    except subprocess.TimeoutExpired:
        raise HTTPException(504, 'GPU采集超时，请稍后重试。') from None
    except OSError:
        raise HTTPException(503, 'GPU采集启动失败，请核对Node和ssh-manager配置。') from None
    if result.returncode == 2:
        raise HTTPException(504, 'GPU采集超时，请稍后重试。')
    if result.returncode != 0 or len(result.stdout) > 2 * 1024 * 1024:
        raise HTTPException(503, 'GPU采集失败，请核对ssh-manager连接、Python3及nvidia-smi。')
    try:
        return json.loads(result.stdout)
    except (ValueError, TypeError):
        raise HTTPException(503, 'GPU采集返回无效，未生成资源快照。') from None


class GpuMonitor:
    def __init__(self):
        self.lock = Lock()
        self.key = None
        self.updated = float('-inf')
        self.value = None
        self.error = None

    def invalidate(self):
        with self.lock:self.updated=float('-inf')

    def snapshot(self, reader=None, server_name=None, cache_key=None):
        # shortcut: 单进程15秒缓存；多实例部署时再接共享采集器。
        with self.lock:
            key = cache_key or (settings.GPU_SERVER_NAME, settings.GPU_SSH_MANAGER_ENTRY, settings.GPU_SSH_NODE_BINARY)
            if key == self.key and monotonic() - self.updated < 15:
                if self.error:
                    raise HTTPException(*self.error)
                return {**self.value, 'cached': True}
            try:
                parsed = parse_sample((reader or read_sample)())
            except (ValueError, KeyError, TypeError, csv.Error):
                self.error = (503, 'GPU采集返回不完整或格式无效，未生成资源快照。')
            except HTTPException as error:
                self.error = (error.status_code, error.detail)
            else:
                self.error = None
                self.value = dict(server_name=server_name or settings.GPU_SERVER_NAME,
                    collected_at=datetime.now(timezone.utc).isoformat(), cached=False, **parsed)
            self.key, self.updated = key, monotonic()
            if self.error:
                raise HTTPException(*self.error)
            return self.value


_connection_monitors = OrderedDict()
_connection_lock = Lock()


def invalidate_connection(server_id):
    with _connection_lock:
        _connection_monitors.pop(str(server_id), None)


def snapshot_connection(server_id, revision, name, connection):
    key = (str(server_id), revision, settings.GPU_SSH_MANAGER_ENTRY, settings.GPU_SSH_NODE_BINARY)
    with _connection_lock:
        previous = _connection_monitors.pop(str(server_id), None)
        monitor = previous[1] if previous and previous[0] == key else GpuMonitor()
        _connection_monitors[str(server_id)] = (key, monitor)
        # Bound memory even when many unsaved connection tests are requested.
        while len(_connection_monitors) > 200:
            _connection_monitors.popitem(last=False)
    # Credentials live only for this request, never in the cached monitor/reader.
    return monitor.snapshot(reader=lambda: read_sample(connection), server_name=name, cache_key=key)
