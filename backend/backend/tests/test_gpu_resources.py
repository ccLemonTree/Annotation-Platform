import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def sample(**changes):
    value = dict(gpu_code=0, process_code=0,
        gpus='0, GPU-aaa, NVIDIA A100-PCIE-40GB, 615.71, 40960, 37575, 2897, 0, 33, 37.76, 250, Disabled\n'
             '1, GPU-bbb, NVIDIA A100-PCIE-40GB, 615.71, 40960, 100, 40370, N/A, N/A, [Not Supported], 250, Enabled\n',
        processes='GPU-aaa, 42, VLLM::Worker_TP0, 36774\nGPU-bbb, 42, python3, N/A\n')
    return {**value, **changes}


def test_gpu_values_unknowns_and_same_pid_on_two_cards():
    from services.gpu_resources import parse_sample
    result = parse_sample(sample())
    assert len(result['gpus']) == 2
    first, second = result['gpus']
    assert first['memory_free_mib'] == 2897 and first['utilization_percent'] == 0
    assert first['power_draw_w'] == 37.76
    assert second['utilization_percent'] is None and second['power_draw_w'] is None
    assert second['mig_mode'] == 'Enabled'
    assert len(result['processes']) == 2
    assert {p['gpu_uuid'] for p in result['processes']} == {'GPU-aaa', 'GPU-bbb'}
    assert result['processes'][1]['memory_used_mib'] is None
    assert result['processes_complete'] is True
    assert result['warnings']  # MIG is not an allocation inventory.


@pytest.mark.parametrize('changes', [
    dict(gpu_code=1), dict(gpus=''), dict(gpus='truncated output'),
    dict(gpus='0, GPU-x, A100, driver, 40960, 90000, 0, 0, 30, 20, 250, Disabled'),
    dict(gpus='0, GPU-x, A100, driver, 40960, 0, 40960, NaN, 30, 20, 250, Disabled'),
    dict(gpus='0, GPU-x, A100, driver, 40960, 0, 40960, 101, 30, 20, 250, Disabled'),
    dict(processes='GPU-aaa, badpid, secret, 20'),
])
def test_invalid_or_truncated_samples_are_rejected(changes):
    from services.gpu_resources import parse_sample
    with pytest.raises(ValueError):
        parse_sample(sample(**changes))


def test_process_failure_is_distinct_from_empty_process_list():
    from services.gpu_resources import parse_sample
    empty = parse_sample(sample(processes=''))
    failed = parse_sample(sample(processes='', process_code=1))
    assert empty['processes_complete'] is True and empty['processes'] == []
    assert failed['processes_complete'] is False and failed['warnings']
    assert len(failed['gpus']) == 2


def test_cache_coalesces_concurrent_requests_and_caches_failures(monkeypatch):
    from services import gpu_resources as gpu
    calls = []
    def read():
        calls.append(1)
        return sample()
    monkeypatch.setattr(gpu, 'read_sample', read)
    monitor = gpu.GpuMonitor()
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: monitor.snapshot(), range(6)))
    assert len(calls) == 1 and sum(not r['cached'] for r in results) == 1
    assert results[0]['collected_at'] and results[0]['server_name']
    def failed():
        calls.append(1)
        raise HTTPException(504, '采集超时')
    monkeypatch.setattr(gpu, 'read_sample', failed)
    monitor = gpu.GpuMonitor()
    for _ in range(2):
        with pytest.raises(HTTPException) as error:
            monitor.snapshot()
        assert error.value.status_code == 504
    assert len(calls) == 2


def test_cache_expiry_recollects_and_never_relabels_old_data_as_current(monkeypatch):
    from services import gpu_resources as gpu
    now = [10]
    monkeypatch.setattr(gpu, 'monotonic', lambda: now[0])
    monkeypatch.setattr(gpu, 'read_sample', sample)
    monitor = gpu.GpuMonitor()
    assert monitor.snapshot()['cached'] is False
    now[0] += 14
    assert monitor.snapshot()['cached'] is True
    now[0] += 2
    monkeypatch.setattr(gpu, 'read_sample', lambda: sample(gpus='incomplete'))
    with pytest.raises(HTTPException) as error:
        monitor.snapshot()
    assert error.value.status_code == 503
    now[0] += 16
    monkeypatch.setattr(gpu, 'read_sample', sample)
    assert monitor.snapshot()['cached'] is False


def test_bridge_configuration_fixed_arguments_timeout_and_redaction(tmp_path, monkeypatch):
    from services import gpu_resources as gpu
    monkeypatch.setattr(gpu.settings, 'GPU_SSH_MANAGER_ENTRY', '')
    with pytest.raises(HTTPException) as error:
        gpu.read_sample()
    assert error.value.status_code == 503
    entry = tmp_path / 'index.js'; entry.write_text('// MCP entry')
    monkeypatch.setattr(gpu.settings, 'GPU_SSH_MANAGER_ENTRY', str(entry))
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(sample()), stderr='secret password')
    monkeypatch.setattr(gpu.subprocess, 'run', run)
    assert gpu.read_sample()['gpu_code'] == 0
    argv, kwargs = calls[0]
    assert argv[-2:] == [str(entry.resolve()), '绍兴a100'] and not kwargs.get('shell')
    assert kwargs['timeout'] < 45
    def failed(*args, **kwargs):
        return SimpleNamespace(returncode=1, stdout='secret', stderr='secret password')
    monkeypatch.setattr(gpu.subprocess, 'run', failed)
    with pytest.raises(HTTPException) as error:
        gpu.read_sample()
    assert 'secret' not in error.value.detail and error.value.status_code == 503
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired('secret', 40)
    monkeypatch.setattr(gpu.subprocess, 'run', timeout)
    with pytest.raises(HTTPException) as error:
        gpu.read_sample()
    assert error.value.status_code == 504 and 'secret' not in error.value.detail


@pytest.mark.asyncio
async def test_gpu_api_admin_only_and_read_only(client, auth_headers, test_db, test_user, monkeypatch):
    from api import gpu_resources_routes as routes
    from services.auth import create_access_token
    calls = []
    def snapshot():
        calls.append(1)
        return dict(server_name='绍兴a100', collected_at='2026-10-15T08:30:00+00:00', cached=False,
                    **__import__('services.gpu_resources', fromlist=['parse_sample']).parse_sample(sample()))
    monkeypatch.setattr(routes.monitor, 'snapshot', snapshot)
    url = '/api/gpu-resources'
    assert client.get(url).status_code == 401
    response = client.get(url, headers=auth_headers)
    assert response.status_code == 200, response.text
    assert len(response.json()['gpus']) == 2 and response.headers['cache-control'] == 'no-store'
    assert client.post(url, headers=auth_headers, json={'command': 'kill -9 1'}).status_code == 405
    test_user.role = 'annotator'; await test_db.commit()
    token = create_access_token({'user_id': test_user.id, 'tenant_id': test_user.tenant_id})
    assert client.get(url, headers={'Authorization': 'Bearer ' + token}).status_code == 403
    assert len(calls) == 1


def test_gpu_api_failure_does_not_report_empty_success(client, auth_headers, monkeypatch):
    from api import gpu_resources_routes as routes
    def failed():
        raise HTTPException(503, 'GPU采集失败，请重试')
    monkeypatch.setattr(routes.monitor, 'snapshot', failed)
    response = client.get('/api/gpu-resources', headers=auth_headers)
    assert response.status_code == 503 and 'GPU采集失败' in response.text
