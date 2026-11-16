import pytest
import contextlib
import json
from services import k8s_training_executor as executor


def test_manifest_has_coordinated_jobs_and_shared_output():
    config = dict(kind='rfdetr_medium', image='fixture:latest', dataset_path='/adb-ssd-mount/ai/chen/training/data', model_path='/adb-ssd-mount/ai/chen/training/model.pth', output_root='/adb-ssd-mount/ai/chen/training/runs', output_subdir='train', shm_size_gb=32, parameters={}, environment={}, k8s=dict(nodes=['node-a', 'node-b', 'node-c'], gpus_per_node=7))
    resources = executor.build_manifest(config, '12345678-1234-1234-1234-123456789abc', 'print(1)')
    jobs = [item for item in resources if item['kind'] == 'Job']
    assert len(jobs) == 3
    assert all(job['spec']['template']['spec']['nodeSelector']['kubernetes.io/hostname'] == node for job, node in zip(jobs, config['k8s']['nodes']))
    assert all(job['spec']['template']['spec']['containers'][0]['resources']['limits']['nvidia.com/gpu'] == 7 for job in jobs)
    assert any(item['kind'] == 'Service' and item['spec']['publishNotReadyAddresses'] for item in resources)
    mounts = jobs[0]['spec']['template']['spec']['containers'][0]['volumeMounts']
    assert next(m for m in mounts if m['mountPath'] == '/output')['subPath'] == 'chen/training/runs/12345678-1234-1234-1234-123456789abc'
    env = {v['name']: v['value'] for v in jobs[0]['spec']['template']['spec']['containers'][0]['env']}
    assert env['NCCL_SOCKET_IFNAME'] == 'eth0' and env['NCCL_IB_DISABLE'] == '1'


def test_snapshot_manifest_mounts_inputs_readonly():
    cfg = config(); cfg.update(snapshot_id='snapshot', dataset_path='')
    job = executor.build_manifest(cfg, '12345678-1234-1234-1234-123456789abc', 'pass')[2]
    mounts = job['spec']['template']['spec']['containers'][0]['volumeMounts']
    assert {m['mountPath'] for m in mounts if m.get('readOnly')} >= {'/output/dataset', '/output/prepared-dataset'}


def test_rejects_path_escape():
    with pytest.raises(ValueError):
        executor.training_relative('/adb-ssd-mount/ai/chen/training/../other')


def test_wemm_dataset_alternate_root_is_not_allowed_for_other_kinds():
    path = executor.EXPORT + '/chen/train/wemm/dataset/20260918/wemm_embedding_train.jsonl'
    assert executor.dataset_relative(path, 'wemm') == 'chen/train/wemm/dataset/20260918/wemm_embedding_train.jsonl'
    with pytest.raises(ValueError): executor.dataset_relative(path, 'yolo26')
    with pytest.raises(ValueError): executor.dataset_relative(executor.EXPORT + '/chen/other/data.jsonl', 'wemm')
    with pytest.raises(ValueError): executor.dataset_relative(executor.EXPORT + '/chen/train/wemm/dataset/../outside.jsonl', 'wemm')


def test_config_hash_is_valid_label_value_and_changes_with_config():
    digest = executor.config_hash({'image': 'one'})
    assert len(digest) <= 63
    assert digest != executor.config_hash({'image': 'two'})


def test_resource_quantities():
    assert executor.quantity('1500m', 'cpu') == 1500
    assert executor.quantity('16', 'cpu') == 16000
    assert executor.quantity('64Gi', 'memory') == 64 * 1024 ** 3


def test_unicode_error_message_handles_bytes_and_text():
    assert executor.error_text(RuntimeError('集群不可用')) == '集群不可用'
    assert executor.error_text(RuntimeError('集群不可用'.encode('utf-8'))) == '集群不可用'


def test_never_accepts_non_gpu_taints():
    with pytest.raises(ValueError):
        executor.node_tolerations([{'key': 'dedicated', 'value': 'other', 'effect': 'NoSchedule'}])


def test_failed_rank_stops_remaining_jobs_after_saving_logs(monkeypatch, tmp_path):
    config = dict(kind='rfdetr_medium', image='x', dataset_path=executor.ROOT + '/data', model_path=executor.EXPORT + '/model', output_root=executor.ROOT + '/runs', output_subdir='train', shm_size_gb=32, parameters={}, environment={}, k8s=dict(nodes=['node-a', 'node-b'], gpus_per_node=7))
    payload = dict(action='status', config=config, run_id='12345678-1234-1234-1234-123456789abc')
    pod = {'metadata': {'name': 'p0', 'labels': {'platform.training.rank': '0'}}, 'spec': {'nodeName': 'node-a'}, 'status': {'phase': 'Running'}}
    active = dict(jobs=[{'metadata': {'name': 'job0'}, 'status': {'failed': 1}}], pods=[pod], services=[], configmaps=[])
    empty = dict(jobs=[], pods=[], services=[], configmaps=[])
    states = iter([active, active, empty])
    operations = []
    monkeypatch.setattr(executor, 'run_path', lambda *args, **kwargs: str(tmp_path))
    monkeypatch.setattr(executor, 'locked', lambda path: contextlib.nullcontext())
    monkeypatch.setattr(executor, 'objects', lambda *args: next(states))
    monkeypatch.setattr(executor, 'kubectl', lambda args, data=None: operations.append(args) or 'worker log')
    result = executor.execute(payload)
    assert result['status'] == 'failed'
    assert (tmp_path / '.logs-0').read_text() == 'worker log'
    assert operations[0][2] == 'logs'
    assert any('delete' in operation for operation in operations[1:])


def test_artifact_rejects_symlink_and_path_escape(tmp_path, monkeypatch):
    (tmp_path / 'weights.pt').write_bytes(b'weights')
    assert executor.safe_path(str(tmp_path), 'weights.pt', True).endswith('weights.pt')
    with pytest.raises(ValueError):
        executor.safe_path(str(tmp_path), '../outside', True)
    original = executor.os.path.islink
    monkeypatch.setattr(executor.os.path, 'islink', lambda p: p.endswith('weights.pt') or original(p))
    with pytest.raises(ValueError):
        executor.safe_path(str(tmp_path), 'weights.pt', True)


def config():
    return dict(kind='rfdetr_medium', image='x', dataset_path=executor.ROOT + '/data', model_path=executor.EXPORT + '/model', output_root=executor.ROOT + '/custom/runs', output_subdir='train', shm_size_gb=32, parameters={}, environment={}, k8s=dict(nodes=['node-a', 'node-b'], gpus_per_node=7))


def test_async_unpack_deduplicates_and_never_overwrites_partial(monkeypatch,tmp_path):
    cfg=config();cfg.update(snapshot_id='snapshot',dataset_path='')
    (tmp_path/'run_config.json').write_text(json.dumps(cfg))
    payload=dict(action='unpack_begin',config=cfg,run_id='12345678-1234-1234-1234-123456789abc',snapshot_hash='a'*64,snapshot_code='fixture')
    monkeypatch.setattr(executor,'run_path',lambda *a,**kw:str(tmp_path))
    monkeypatch.setattr(executor,'try_locked',lambda path:contextlib.nullcontext(True),raising=False)
    monkeypatch.setattr(executor,'process_identity',lambda pid:('boot',123),raising=False)
    monkeypatch.setattr(executor,'PLATFORM_SOURCE',b'fixture',raising=False)
    spawned=[]
    monkeypatch.setattr(executor.subprocess,'Popen',lambda *a,**kw:spawned.append((a,kw)) or type('Process',(),{'pid':42})())
    assert executor.execute(payload)['state']=='running'
    assert executor.execute(payload)['state']=='running'
    assert len(spawned)==1
    status=dict(payload,action='unpack_status');status.pop('snapshot_code')
    assert executor.execute(status)['state']=='running'
    (tmp_path/'.dataset-ready').write_text('a'*64)
    (tmp_path/'dataset').mkdir(); (tmp_path/'prepared-dataset').mkdir()
    assert executor.execute(status)['state']=='ready'
    assert executor.execute(dict(status,snapshot_hash='b'*64))['state']=='unknown'


def test_async_unpack_preserves_ambiguous_and_partial_state(monkeypatch,tmp_path):
    cfg=config();cfg.update(snapshot_id='snapshot',dataset_path='')
    (tmp_path/'run_config.json').write_text(json.dumps(cfg))
    payload=dict(action='unpack_begin',config=cfg,run_id='12345678-1234-1234-1234-123456789abc',snapshot_hash='a'*64,snapshot_code='fixture')
    monkeypatch.setattr(executor,'run_path',lambda *a,**kw:str(tmp_path))
    monkeypatch.setattr(executor,'try_locked',lambda path:contextlib.nullcontext(True),raising=False)
    monkeypatch.setattr(executor.subprocess,'Popen',lambda *a,**kw:pytest.fail('must not start another worker'))
    (tmp_path/'dataset').mkdir()
    assert executor.execute(payload)['state']=='unknown'
    assert (tmp_path/'dataset').is_dir()
    (tmp_path/'dataset').rmdir()
    (tmp_path/'.unpack-owner').write_text(json.dumps({'config_hash':executor.config_hash(cfg),'snapshot_hash':'a'*64}))
    assert executor.execute(payload)['state']=='unknown'


def test_async_unpack_worker_checks_identity_and_records_failure(monkeypatch,tmp_path):
    cfg=config();cfg.update(snapshot_id='snapshot',dataset_path='')
    (tmp_path/'run_config.json').write_text(json.dumps(cfg))
    payload=dict(action='unpack_worker',config=cfg,run_id='12345678-1234-1234-1234-123456789abc',snapshot_hash='a'*64,worker_token='token',snapshot_code="def remote_snapshot_ops(*args): raise ValueError('invalid dataset')")
    owner=dict(config_hash=executor.config_hash(cfg),snapshot_hash='a'*64,token='token',pid=executor.os.getpid(),boot_id='boot',start_time=123)
    (tmp_path/'.unpack-owner').write_text(json.dumps(owner))
    monkeypatch.setattr(executor,'run_path',lambda *a,**kw:str(tmp_path))
    monkeypatch.setattr(executor,'locked',lambda path:contextlib.nullcontext())
    monkeypatch.setattr(executor,'mount_root',lambda:str(tmp_path))
    monkeypatch.setattr(executor,'process_identity',lambda pid:('boot',123))
    assert executor.execute(dict(payload,worker_token='wrong'))['state']=='unknown'
    assert not (tmp_path/'.unpack-result').exists()
    assert executor.execute(payload)['state']=='failed'
    result=json.loads((tmp_path/'.unpack-result').read_text())
    assert result['config_hash']==owner['config_hash'] and result['snapshot_hash']==owner['snapshot_hash']
    assert result['pid']==owner['pid'] and result['start_time']==owner['start_time']
    assert executor.execute(dict(payload,action='unpack_status'))['state']=='failed'


def test_async_unpack_reused_pid_and_locked_begin_are_unknown(monkeypatch,tmp_path):
    cfg=config();cfg.update(snapshot_id='snapshot',dataset_path='')
    (tmp_path/'run_config.json').write_text(json.dumps(cfg))
    payload=dict(action='unpack_begin',config=cfg,run_id='12345678-1234-1234-1234-123456789abc',snapshot_hash='a'*64,snapshot_code='fixture')
    owner=dict(config_hash=executor.config_hash(cfg),snapshot_hash='a'*64,token='token',pid=42,boot_id='boot',start_time=123)
    (tmp_path/'.unpack-owner').write_text(json.dumps(owner))
    monkeypatch.setattr(executor,'run_path',lambda *a,**kw:str(tmp_path))
    monkeypatch.setattr(executor,'try_locked',lambda path:contextlib.nullcontext(False))
    monkeypatch.setattr(executor,'process_identity',lambda pid:('boot',456))
    monkeypatch.setattr(executor.subprocess,'Popen',lambda *a,**kw:pytest.fail('must not spawn'))
    assert executor.execute(payload)['state']=='unknown'


def test_async_unpack_worker_publishes_ready_and_honors_cancel(monkeypatch,tmp_path):
    cfg=config();cfg.update(snapshot_id='snapshot',dataset_path='')
    (tmp_path/'run_config.json').write_text(json.dumps(cfg))
    payload=dict(action='unpack_worker',config=cfg,run_id='12345678-1234-1234-1234-123456789abc',snapshot_hash='a'*64,worker_token='token',snapshot_code="def remote_snapshot_ops(payload, path, cache):\n    open(path+'/.dataset-ready', 'w').write(payload['snapshot_hash'])")
    owner=dict(config_hash=executor.config_hash(cfg),snapshot_hash='a'*64,token='token',pid=executor.os.getpid(),boot_id='boot',start_time=123)
    (tmp_path/'.unpack-owner').write_text(json.dumps(owner))
    monkeypatch.setattr(executor,'run_path',lambda *a,**kw:str(tmp_path))
    monkeypatch.setattr(executor,'locked',lambda path:contextlib.nullcontext())
    monkeypatch.setattr(executor,'mount_root',lambda:str(tmp_path))
    monkeypatch.setattr(executor,'process_identity',lambda pid:('boot',123))
    (tmp_path/'.canceled').touch()
    assert executor.execute(payload)['state']=='canceled'
    assert not (tmp_path/'.dataset-ready').exists()
    (tmp_path/'.canceled').unlink()
    (tmp_path/'dataset').mkdir(); (tmp_path/'prepared-dataset').mkdir()
    assert executor.execute(payload)['state']=='ready'
    assert executor.execute(dict(payload,action='unpack_status'))['state']=='ready'
    (tmp_path/'.canceled').touch()
    assert executor.execute(dict(payload,action='unpack_status'))['state']!='ready'


def test_async_unpack_accepts_legacy_ready_only_with_matching_inputs(monkeypatch,tmp_path):
    cfg=config();cfg.update(snapshot_id='snapshot',dataset_path='')
    payload=dict(action='unpack_begin',config=cfg,run_id='12345678-1234-1234-1234-123456789abc',snapshot_hash='a'*64,snapshot_code='fixture')
    (tmp_path/'run_config.json').write_text(json.dumps(cfg))
    (tmp_path/'.dataset-ready').write_text('a'*64)
    (tmp_path/'dataset').mkdir(); (tmp_path/'prepared-dataset').mkdir()
    monkeypatch.setattr(executor,'run_path',lambda *a,**kw:str(tmp_path))
    monkeypatch.setattr(executor,'try_locked',lambda path:contextlib.nullcontext(True))
    monkeypatch.setattr(executor.subprocess,'Popen',lambda *a,**kw:pytest.fail('must not spawn'))
    assert executor.execute(payload)['state']=='ready'
    assert executor.execute(dict(payload,action='unpack_status'))['state']=='ready'
    assert executor.execute(dict(payload,snapshot_hash='b'*64))['state']=='unknown'
    (tmp_path/'prepared-dataset').rmdir()
    assert executor.execute(payload)['state']=='unknown'
    (tmp_path/'prepared-dataset').mkdir()
    (tmp_path/'.canceled').touch()
    assert executor.execute(payload)['state']!='ready'
    (tmp_path/'.canceled').unlink()
    (tmp_path/'run_config.json').write_text('{}')
    assert executor.execute(payload)['state']=='unknown'


def test_async_unpack_worker_rejects_config_changed_since_stage(monkeypatch,tmp_path):
    cfg=config();cfg.update(snapshot_id='snapshot',dataset_path='')
    (tmp_path/'run_config.json').write_text('{}')
    owner=dict(config_hash=executor.config_hash(cfg),snapshot_hash='a'*64,token='token',pid=executor.os.getpid(),boot_id='boot',start_time=123)
    (tmp_path/'.unpack-owner').write_text(json.dumps(owner))
    payload=dict(action='unpack_worker',config=cfg,run_id='12345678-1234-1234-1234-123456789abc',snapshot_hash='a'*64,worker_token='token',snapshot_code='raise AssertionError')
    monkeypatch.setattr(executor,'run_path',lambda *a,**kw:str(tmp_path))
    monkeypatch.setattr(executor,'locked',lambda path:contextlib.nullcontext())
    monkeypatch.setattr(executor,'process_identity',lambda pid:('boot',123))
    assert executor.execute(payload)['state']=='unknown'
    assert not (tmp_path/'.unpack-result').exists()


def test_async_unpack_malformed_owner_is_unknown(monkeypatch,tmp_path):
    cfg=config();cfg.update(snapshot_id='snapshot',dataset_path='')
    (tmp_path/'run_config.json').write_text(json.dumps(cfg))
    (tmp_path/'.unpack-owner').write_text('[]')
    payload=dict(action='unpack_status',config=cfg,run_id='12345678-1234-1234-1234-123456789abc',snapshot_hash='a'*64)
    monkeypatch.setattr(executor,'run_path',lambda *a,**kw:str(tmp_path))
    assert executor.execute(payload)['state']=='unknown'


def test_process_identity_ignores_zombies(monkeypatch):
    from io import StringIO
    import builtins
    original=builtins.open
    def read(path,*args,**kwargs):
        if path=='/proc/sys/kernel/random/boot_id': return StringIO('boot')
        if path=='/proc/42/stat': return StringIO('42 (worker) Z ' + '0 '*18 + '123')
        return original(path,*args,**kwargs)
    monkeypatch.setattr(builtins,'open',read)
    assert executor.process_identity(42) is None


@pytest.mark.parametrize('pid',['../42',True,0,-1])
def test_process_identity_rejects_invalid_pid(pid,monkeypatch):
    monkeypatch.setattr('builtins.open',lambda *a,**kw:pytest.fail('invalid pid must not read proc'))
    assert executor.process_identity(pid) is None


def test_custom_output_nfs_mapping(monkeypatch, tmp_path):
    monkeypatch.setattr(executor, 'mount_root', lambda: str(tmp_path))
    rid = '12345678-1234-1234-1234-123456789abc'
    assert executor.run_path(config(), rid).endswith('chen' + executor.os.sep + 'training' + executor.os.sep + 'custom' + executor.os.sep + 'runs' + executor.os.sep + rid)
    job = executor.build_manifest(config(), rid, 'pass')[2]
    mounts = job['spec']['template']['spec']['containers'][0]['volumeMounts']
    assert next(m for m in mounts if m['mountPath'] == '/output')['subPath'] == 'chen/training/custom/runs/' + rid


def test_canceled_and_partial_start_never_create(monkeypatch, tmp_path):
    payload = dict(action='start', config=config(), run_id='12345678-1234-1234-1234-123456789abc')
    empty = dict(jobs=[], pods=[], services=[], configmaps=[])
    monkeypatch.setattr(executor, 'run_path', lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(executor, 'locked', lambda path: contextlib.nullcontext())
    monkeypatch.setattr(executor, 'objects', lambda *a: empty)
    monkeypatch.setattr(executor, 'kubectl', lambda *a, **k: pytest.fail('must not call kubectl'))
    (tmp_path / '.canceled').write_text('')
    assert executor.execute(payload)['status'] == 'canceled'
    (tmp_path / '.canceled').unlink()
    empty['jobs'] = [{'metadata': {'name': 'one'}}]
    with pytest.raises(RuntimeError, match='部分任务'):
        executor.execute(payload)


def test_snapshot_start_requires_matching_ready_marker(monkeypatch, tmp_path):
    cfg = config(); cfg.update(snapshot_id='snapshot', dataset_path='')
    payload = dict(action='start', config=cfg, run_id='12345678-1234-1234-1234-123456789abc', snapshot_hash='a' * 64)
    monkeypatch.setattr(executor, 'run_path', lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(executor, 'locked', lambda path: contextlib.nullcontext())
    monkeypatch.setattr(executor, 'objects', lambda *a: dict(jobs=[], pods=[], services=[], configmaps=[]))
    monkeypatch.setattr(executor, 'precheck', lambda *a: pytest.fail('unready snapshot must not start'))
    with pytest.raises(ValueError): executor.execute(payload)
    (tmp_path / '.dataset-ready').write_text('b' * 64)
    with pytest.raises(ValueError): executor.execute(payload)


def test_stop_marks_canceled_before_waiting_for_lock(monkeypatch, tmp_path):
    payload = dict(action='stop', config=config(), run_id='12345678-1234-1234-1234-123456789abc')
    monkeypatch.setattr(executor, 'run_path', lambda *a, **k: str(tmp_path))
    def lock(path):
        assert (tmp_path / '.canceled').exists()
        return contextlib.nullcontext()
    monkeypatch.setattr(executor, 'locked', lock)
    monkeypatch.setattr(executor, 'objects', lambda *a: dict(jobs=[], pods=[], services=[], configmaps=[]))
    assert executor.execute(payload)['status'] == 'canceled'


def test_foreign_owner_cannot_be_deleted(monkeypatch):
    monkeypatch.setattr(executor, 'get_json', lambda args: {'items': [{'metadata': {'labels': {'platform.training.id': '12345678-1234-1234-1234-123456789abc', 'platform.training.config': 'foreign'}}}]})
    with pytest.raises(ValueError, match='所有权'):
        executor.objects('12345678-1234-1234-1234-123456789abc', config())


def test_logs_select_requested_node(monkeypatch, tmp_path):
    payload = dict(action='logs', config=config(), run_id='12345678-1234-1234-1234-123456789abc', node='node-b')
    pods = [{'metadata': {'name': name}, 'spec': {'nodeName': node}} for name, node in [('a', 'node-a'), ('b', 'node-b')]]
    monkeypatch.setattr(executor, 'run_path', lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(executor, 'objects', lambda *a: dict(jobs=[], pods=pods, services=[], configmaps=[]))
    monkeypatch.setattr(executor, 'kubectl', lambda args: args[3])
    result = executor.execute(payload)
    assert result['text'] == 'b' and result['instances'][0]['node'] == 'node-b'


def test_pending_logs_show_waiting_reason_and_status_lists_ranks(monkeypatch, tmp_path):
    payload = dict(action='logs', config=config(), run_id='12345678-1234-1234-1234-123456789abc')
    pod = {'metadata': {'name': 'p0', 'labels': {'platform.training.rank': '0'}}, 'spec': {'nodeName': 'node-a'}, 'status': {'phase': 'Pending', 'containerStatuses': [{'state': {'waiting': {'reason': 'ContainerCreating'}}}]}}
    state = dict(jobs=[{'status': {}}, {'status': {}}], pods=[pod], services=[], configmaps=[])
    monkeypatch.setattr(executor, 'run_path', lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(executor, 'objects', lambda *a: state)
    monkeypatch.setattr(executor, 'kubectl', lambda *a: pytest.fail('pending pod has no logs'))
    assert 'ContainerCreating' in executor.execute(payload)['text']
    payload['action'] = 'status'
    status = executor.execute(payload)
    assert status['job_count'] == 2
    assert status['instances'][0]['waitingReason'] == 'ContainerCreating'


def test_wemm_precheck_requires_validation_file(monkeypatch, tmp_path):
    cfg = config()
    cfg['kind'] = 'wemm'
    cfg['parameters']['val_dataset'] = executor.ROOT + '/missing.jsonl'
    monkeypatch.setattr(executor, 'mount_root', lambda: str(tmp_path))
    monkeypatch.setattr(executor, 'get_json', lambda args: {'spec': {'volumeName': 'pv'}} if 'pvc' in args else {})
    monkeypatch.setattr(executor, 'nodes', lambda: [])
    monkeypatch.setattr(executor, 'kubectl', lambda args: 'yes')
    result = executor.precheck(dict(config=cfg, run_id='12345678-1234-1234-1234-123456789abc'))
    assert not next(c for c in result['checks'] if c['name'] == 'val_dataset')['ok']


def test_2xlarge_precheck_requires_license_confirmation(monkeypatch, tmp_path):
    cfg = config(); cfg['kind'] = 'rfdetr_2xlarge'
    monkeypatch.setattr(executor, 'mount_root', lambda: str(tmp_path))
    monkeypatch.setattr(executor, 'get_json', lambda args: {'spec': {'volumeName': 'pv'}} if 'pvc' in args else {})
    monkeypatch.setattr(executor, 'nodes', lambda: [])
    monkeypatch.setattr(executor, 'kubectl', lambda args: 'yes')
    payload = dict(config=cfg, run_id='12345678-1234-1234-1234-123456789abc')
    assert not next(c for c in executor.precheck(payload)['checks'] if c['name'] == '2XLarge许可证')['ok']
    cfg['accept_platform_model_license'] = True
    assert next(c for c in executor.precheck(payload)['checks'] if c['name'] == '2XLarge许可证')['ok']


def test_save_logs_failure_does_not_block_stop(monkeypatch, tmp_path):
    def unavailable(*args): raise RuntimeError('unavailable')
    monkeypatch.setattr(executor, 'kubectl', unavailable)
    executor.save_logs(str(tmp_path), [{'metadata': {'name': 'a', 'labels': {'platform.training.rank': '0'}}, 'status': {'phase': 'Running'}}])
    assert 'unavailable' in (tmp_path / '.logs-0').read_text(encoding='utf-8')


def test_completion_requires_all_terminated_pods_and_weights(monkeypatch, tmp_path):
    payload = dict(action='status', config=config(), run_id='12345678-1234-1234-1234-123456789abc')
    jobs = [{'status': {'succeeded': 1}}, {'status': {'succeeded': 1}}]
    pods = [{'metadata': {'labels': {'platform.training.rank': str(rank)}}, 'status': {'containerStatuses': [{'state': {'terminated': {'exitCode': 0}}}]}} for rank in range(2)]
    state = dict(jobs=jobs, pods=pods[:1], services=[], configmaps=[])
    monkeypatch.setattr(executor, 'run_path', lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(executor, 'objects', lambda *a: state)
    assert executor.execute(payload)['status'] == 'running'
    state['pods'] = pods
    assert executor.execute(payload)['status'] == 'failed'
    (tmp_path / 'train').mkdir()
    (tmp_path / 'train' / 'model.pth').write_bytes(b'weights')
    assert executor.execute(payload)['status'] == 'completed'


def test_missing_jobs_with_live_pods_never_reports_stopped(monkeypatch, tmp_path):
    payload = dict(action='status', config=config(), run_id='12345678-1234-1234-1234-123456789abc')
    (tmp_path / '.failed').write_text('')
    state = dict(jobs=[], pods=[{'metadata': {'name': 'orphan'}}], services=[], configmaps=[])
    monkeypatch.setattr(executor, 'run_path', lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(executor, 'objects', lambda *a: state)
    assert executor.execute(payload)['status'] == 'unknown'


def test_partial_jobs_status_is_unknown(monkeypatch, tmp_path):
    payload = dict(action='status', config=config(), run_id='12345678-1234-1234-1234-123456789abc')
    state = dict(jobs=[{'status': {}}], pods=[], services=[], configmaps=[])
    monkeypatch.setattr(executor, 'run_path', lambda *a, **k: str(tmp_path))
    monkeypatch.setattr(executor, 'objects', lambda *a: state)
    assert executor.execute(payload)['status'] == 'unknown'


def test_metric_files_only_returns_supported_names_and_bounded_files(tmp_path):
    import base64
    (tmp_path / 'metrics.csv').write_bytes(b'epoch,loss\n1,0.5\n')
    (tmp_path / 'model.pth').write_bytes(b'weights')
    (tmp_path / 'unrelated.json').write_bytes(b'{}')
    (tmp_path / 'metrics.json').write_bytes(b'x' * (4 * 1024 * 1024 + 1))
    (tmp_path / 'log.txt').write_bytes(b'{"epoch":1}')
    result = executor.metric_files(str(tmp_path))
    assert result['truncated']
    assert [f['path'] for f in result['files']] == ['metrics.csv', 'log.txt']
    assert base64.b64decode(result['files'][0]['data']) == b'epoch,loss\n1,0.5\n'
    assert result['files'][0]['mtime'] == (tmp_path / 'metrics.csv').stat().st_mtime


def test_metric_files_ignores_symlinks(tmp_path, monkeypatch):
    (tmp_path / 'metrics.json').write_bytes(b'{}')
    original = executor.os.path.islink
    monkeypatch.setattr(executor.os.path, 'islink', lambda p: p.endswith('metrics.json') or original(p))
    assert executor.metric_files(str(tmp_path))['files'] == []
