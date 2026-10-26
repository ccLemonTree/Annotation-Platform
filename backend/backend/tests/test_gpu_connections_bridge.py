"""Dynamic credentials use MCP stdin and never enter command arguments."""
import json
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from tests.test_gpu_resources import sample


@pytest.mark.parametrize('mig', ['N/A', '[N/A]', 'Not Supported', '[Not Supported]'])
def test_nvidia_cards_without_mig_do_not_report_an_unknown_mig_state(mig):
    from services.gpu_resources import parse_sample
    row = f'0, GPU-t4, NVIDIA T4, 550, 15360, 100, 15260, 0, 35, N/A, N/A, {mig}'
    result = parse_sample(sample(gpus=row, processes=''))
    assert result['gpus'][0]['power_draw_w'] is None
    assert result['warnings'] == []


def test_dynamic_connection_is_stdin_only(tmp_path, monkeypatch):
    from services import gpu_resources as gpu
    assert hasattr(gpu, 'snapshot_connection'), '缺少服务器隔离采集'
    entry = tmp_path / 'manager.js'
    entry.write_text('// fixture')
    monkeypatch.setattr(gpu.settings, 'GPU_SSH_MANAGER_ENTRY', str(entry))
    secret = 'fixture-only 密码\n"$`\\'
    connection = dict(host='127.0.0.1', port=2222, username='root', password=secret)
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(sample()), stderr=secret)
    monkeypatch.setattr(gpu.subprocess, 'run', run)
    result = gpu.snapshot_connection('stdin-fixture', 1, '独立服务器', connection)
    assert result['server_name'] == '独立服务器'
    args, kwargs = calls[0]
    assert secret not in str(args) and connection['host'] not in str(args)
    assert json.loads(kwargs['input']) == connection
    assert '--connection-stdin' in args and not kwargs.get('shell')
    assert secret not in json.dumps(result, ensure_ascii=False)
    assert secret not in repr(gpu._connection_monitors)


def test_connection_cache_isolated_and_invalidated(tmp_path, monkeypatch):
    from services import gpu_resources as gpu
    assert hasattr(gpu, 'snapshot_connection'), '缺少服务器隔离采集'
    entry = tmp_path / 'manager.js'; entry.write_text('// fixture')
    monkeypatch.setattr(gpu.settings, 'GPU_SSH_MANAGER_ENTRY', str(entry))
    calls = []
    def read(connection=None):
        calls.append(connection['host'])
        return sample(gpus=sample()['gpus'].replace('GPU-aaa', 'GPU-' + connection['host']))
    monkeypatch.setattr(gpu, 'read_sample', read)
    a = dict(host='192.0.2.1', port=22, username='root', password='test-a')
    b = dict(host='192.0.2.2', port=22, username='root', password='test-b')
    first = gpu.snapshot_connection('cache-fixture-a', 1, 'A', a)
    assert gpu.snapshot_connection('cache-fixture-a', 1, 'A', a)['cached'] is True
    second = gpu.snapshot_connection('cache-fixture-b', 1, 'B', b)
    assert first['gpus'][0]['uuid'] != second['gpus'][0]['uuid']
    gpu.snapshot_connection('cache-fixture-a', 2, 'A2', b)
    gpu.invalidate_connection('cache-fixture-a')
    gpu.snapshot_connection('cache-fixture-a', 2, 'A2', b)
    assert calls == [a['host'], b['host'], b['host'], b['host']]


def test_dynamic_bridge_error_is_redacted(tmp_path, monkeypatch):
    from services import gpu_resources as gpu
    assert hasattr(gpu, 'snapshot_connection'), '缺少服务器隔离采集'
    entry = tmp_path / 'manager.js'; entry.write_text('// fixture')
    monkeypatch.setattr(gpu.settings, 'GPU_SSH_MANAGER_ENTRY', str(entry))
    secret = 'fixture-private-password'
    monkeypatch.setattr(gpu.subprocess, 'run', lambda *a, **k: SimpleNamespace(returncode=1, stdout=secret, stderr=secret))
    with pytest.raises(HTTPException) as error:
        gpu.snapshot_connection('failed-fixture', 1, 'private', dict(host='192.0.2.3', port=22, username='root', password=secret))
    assert secret not in str(error.value.detail) and error.value.status_code == 503
