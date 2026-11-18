# -*- coding: utf-8 -*-
"""Fixed Kubernetes training actions executed by the control host's Python 3."""
from __future__ import print_function
import base64
import hashlib
import contextlib
import decimal
import json
import os
import re
import stat
import subprocess
import sys
import uuid

NAMESPACE = 'aisfprod'
PVC = 'nfs-pvc-ai'
SERVER = '172.22.132.169'
EXPORT = '/adb-ssd-mount/ai'
ROOT = EXPORT + '/chen/training'
CONTROLLER_MOUNT = '/mnt/data-platform-training'


def error_text(error):
    value = error.args[0] if error.args else ''
    if isinstance(value, bytes): return value.decode('utf-8', 'replace')
    return value if isinstance(value, type(u'')) else type(u'')(value)


def training_relative(path):
    if not isinstance(path, type(u'')) and not isinstance(path, str):
        raise ValueError('路径非法')
    if not path.startswith(ROOT + '/') or '\\' in path or any(p in ('', '.', '..') for p in path.split('/')[1:]):
        raise ValueError('路径必须位于训练目录内')
    return path[len(EXPORT) + 1:]


def model_relative(path):
    if not path.startswith(EXPORT + '/') or '\\' in path or any(p in ('', '.', '..') for p in path.split('/')[1:]):
        raise ValueError('模型路径必须位于 NFS PV 内')
    return path[len(EXPORT) + 1:]


def dataset_relative(path, kind):
    alternate = EXPORT + '/chen/train/wemm/dataset/'
    if kind == 'wemm' and path.startswith(alternate):
        return model_relative(path)
    return training_relative(path)


def node_tolerations(taints):
    result = []
    for taint in taints:
        if taint.get('effect') not in ('NoSchedule', 'NoExecute'):
            continue
        if taint.get('key') not in ('aisfprod-gpu-taint', 'vcs-gpu-taint', 'embedding') or taint.get('value') != 'yes' or taint.get('effect') != 'NoSchedule':
            raise ValueError('节点包含未授权自动容忍的污点')
        result.append({'key': taint['key'], 'operator': 'Equal', 'value': taint['value'], 'effect': taint['effect']})
    return result


def config_hash(config):
    return hashlib.sha256(json.dumps(config, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode('utf-8')).hexdigest()[:32]


def name_for(run_id):
    return 'platform-train-' + str(uuid.UUID(run_id))


def kubectl(args, data=None):
    proc = subprocess.Popen(['kubectl', '--request-timeout=15s'] + args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate(data.encode('utf-8') if data is not None else None)
    if proc.returncode:
        raise RuntimeError((err or out).decode('utf-8', 'replace')[-2000:])
    return out.decode('utf-8', 'replace')


def get_json(args):
    return json.loads(kubectl(args + ['-o', 'json']))


def mount_root():
    candidates = []
    with open('/proc/mounts') as stream:
        for line in stream:
            cols = line.split()
            if len(cols) >= 3 and cols[0] == SERVER + ':' + EXPORT and cols[2] in ('nfs', 'nfs4'):
                target = cols[1].replace('\\040', ' ').replace('\\011', '\t').replace('\\134', '\\')
                if target == CONTROLLER_MOUNT:
                    return target
                candidates.append(target)
    if len(candidates) != 1:
        raise ValueError('未找到唯一匹配的训练 NFS 挂载')
    return candidates[0]


def safe_path(root, relative, must_exist=False):
    if not relative or relative.startswith('/') or '\\' in relative or any(p in ('', '.', '..') for p in relative.split('/')):
        raise ValueError('路径非法')
    current = root
    if os.path.islink(current):
        raise ValueError('挂载目录不能是符号链接')
    for part in relative.split('/'):
        current = os.path.join(current, part)
        if os.path.islink(current):
            raise ValueError('路径不能包含符号链接')
    if must_exist and not os.path.exists(current):
        raise ValueError('路径不存在')
    return current


def run_path(config, run_id, create=False):
    name_for(run_id)
    path = safe_path(mount_root(), training_relative(config['output_root']) + '/' + run_id)
    if create and not os.path.isdir(path):
        os.makedirs(path)
    return path


def validate_config(config, run_id):
    name_for(run_id)
    if not config.get('snapshot_id'): dataset_relative(config['dataset_path'], config['kind'])
    elif config.get('dataset_path'): raise ValueError('冻结数据不能同时使用服务器数据路径')
    model_relative(config['model_path'])
    training_relative(config['output_root'])
    if not re.match(r'^[A-Za-z0-9_.-]+$', config['output_subdir']) or config['output_subdir'] in ('.', '..'):
        raise ValueError('产物子目录非法')
    nodes = config['k8s']['nodes']
    count = config['k8s']['gpus_per_node']
    if not nodes or len(set(nodes)) != len(nodes) or any(not re.match(r'^[a-z0-9]([-a-z0-9.]*[a-z0-9])?$', n) for n in nodes):
        raise ValueError('节点列表非法')
    if isinstance(count, bool) or not isinstance(count, int) or count < 1 or count > 16:
        raise ValueError('每节点 GPU 数量非法')
    if config['kind'] not in ('rfdetr_medium', 'rfdetr_2xlarge', 'yolo26', 'swift_sft', 'wemm'):
        raise ValueError('不支持此训练类型')


def build_manifest(config, run_id, script, assets=None, tolerations=None):
    validate_config(config, run_id)
    name = name_for(run_id)
    digest = config_hash(config)
    labels = {'platform.training.id': run_id, 'platform.training.config': digest}
    meta = lambda n: {'name': n, 'namespace': NAMESPACE, 'labels': labels.copy()}
    data = {'train.py': script}
    for key, value in (assets or {}).items():
        if not re.match(r'^[A-Za-z0-9_.-]+$', key) or key in data:
            raise ValueError('配置文件名称非法')
        data[key] = value
    resources = [{'apiVersion': 'v1', 'kind': 'ConfigMap', 'metadata': meta(name), 'data': data},
                 {'apiVersion': 'v1', 'kind': 'Service', 'metadata': meta(name), 'spec': {'clusterIP': 'None', 'publishNotReadyAddresses': True, 'selector': {'platform.training.id': run_id, 'platform.training.rank': '0'}, 'ports': [{'name': 'master', 'port': 29500}]}}]
    nodes = config['k8s']['nodes']
    gpu = config['k8s']['gpus_per_node']
    for rank, node in enumerate(nodes):
        env = dict(config.get('environment', {}))
        env.update(NCCL_SOCKET_IFNAME='eth0', NCCL_IB_DISABLE='1', NCCL_DEBUG='WARN')
        env.update(NNODES=str(len(nodes)), NPROC_PER_NODE=str(gpu), NODE_RANK=str(rank), MASTER_ADDR=name, MASTER_PORT='29500')
        command = ['python3', '-u', '/config/train.py'] if config['kind'] in ('swift_sft', 'wemm') else ['torchrun', '--nnodes=' + str(len(nodes)), '--nproc_per_node=' + str(gpu), '--node_rank=' + str(rank), '--master_addr=' + name, '--master_port=29500', '/config/train.py']
        podlabels = labels.copy(); podlabels['platform.training.rank'] = str(rank)
        container = {'name': 'training', 'image': config['image'], 'command': command,
                     'env': [{'name': k, 'value': str(v)} for k, v in sorted(env.items())],
                     'resources': {'requests': {'cpu': '16', 'memory': '64Gi', 'nvidia.com/gpu': gpu}, 'limits': {'cpu': '16', 'memory': '64Gi', 'nvidia.com/gpu': gpu}},
                     'volumeMounts': [{'name': 'training', 'mountPath': '/ai', 'readOnly': True}, {'name': 'training', 'mountPath': '/output', 'subPath': training_relative(config['output_root']) + '/' + run_id}, {'name': 'config', 'mountPath': '/config', 'readOnly': True}, {'name': 'shm', 'mountPath': '/dev/shm'}]}
        if config['kind'] == 'wemm': container['resources']['limits'].update(cpu='32', memory='128Gi')
        if config.get('snapshot_id'):
            subpath = training_relative(config['output_root']) + '/' + run_id
            container['volumeMounts'].append({'name': 'training', 'mountPath': '/output/dataset', 'subPath': subpath + '/dataset', 'readOnly': True})
            if config['kind'] not in ('wemm', 'swift_sft'):
                container['volumeMounts'].append({'name': 'training', 'mountPath': '/output/prepared-dataset', 'subPath': subpath + '/prepared-dataset', 'readOnly': True})
        spec = {'restartPolicy': 'Never', 'nodeSelector': {'kubernetes.io/hostname': node}, 'tolerations': (tolerations or {}).get(node, []), 'containers': [container], 'volumes': [{'name': 'training', 'persistentVolumeClaim': {'claimName': PVC}}, {'name': 'config', 'configMap': {'name': name}}, {'name': 'shm', 'emptyDir': {'medium': 'Memory', 'sizeLimit': str(config['shm_size_gb']) + 'Gi'}}]}
        resources.append({'apiVersion': 'batch/v1', 'kind': 'Job', 'metadata': meta(name + '-r' + str(rank)), 'spec': {'backoffLimit': 0, 'template': {'metadata': {'labels': podlabels}, 'spec': spec}}})
    return resources


def quantity(value, kind):
    match = re.match(r'^([0-9]+(?:\.[0-9]+)?)(m|Ki|Mi|Gi|Ti|K|M|G|T)?$', str(value))
    if not match: raise ValueError('无法解析节点资源数量')
    number = decimal.Decimal(match.group(1)); unit = match.group(2)
    factors = {None: 1, 'm': decimal.Decimal('0.001'), 'Ki': 1024, 'Mi': 1024 ** 2, 'Gi': 1024 ** 3, 'Ti': 1024 ** 4, 'K': 1000, 'M': 1000 ** 2, 'G': 1000 ** 3, 'T': 1000 ** 4}
    return int(number * factors[unit] * (1000 if kind == 'cpu' else 1))


def nodes():
    node_list = get_json(['get', 'nodes'])['items']
    pods = get_json(['get', 'pods', '--all-namespaces'])['items']
    result = []
    for node in node_list:
        metadata = node['metadata']; status = node.get('status', {})
        name = metadata['name']
        capacity = int(status.get('allocatable', {}).get('nvidia.com/gpu', '0'))
        if not capacity: continue
        used = 0; used_cpu = 0; used_memory = 0
        for pod in pods:
            if pod.get('spec', {}).get('nodeName') != name or pod.get('status', {}).get('phase') in ('Succeeded', 'Failed'):
                continue
            spec = pod['spec']
            regular = sum(int(c.get('resources', {}).get('requests', {}).get('nvidia.com/gpu', 0)) for c in spec.get('containers', []))
            initial = max([int(c.get('resources', {}).get('requests', {}).get('nvidia.com/gpu', 0)) for c in spec.get('initContainers', [])] or [0])
            used += max(regular, initial)
            for resource in ('cpu', 'memory'):
                regular_resource = sum(quantity(c.get('resources', {}).get('requests', {}).get(resource, '0'), resource) for c in spec.get('containers', []))
                init_resource = max([quantity(c.get('resources', {}).get('requests', {}).get(resource, '0'), resource) for c in spec.get('initContainers', [])] or [0])
                amount = max(regular_resource, init_resource) + quantity(spec.get('overhead', {}).get(resource, '0'), resource)
                if resource == 'cpu': used_cpu += amount
                else: used_memory += amount
        allocatable = status.get('allocatable', {})
        result.append({'name': name, 'capacity': capacity, 'available': max(0, capacity - used), 'cpu_available_m': max(0, quantity(allocatable.get('cpu', '0'), 'cpu') - used_cpu), 'memory_available_bytes': max(0, quantity(allocatable.get('memory', '0'), 'memory') - used_memory), 'taints': node.get('spec', {}).get('taints', []), 'ready': any(c.get('type') == 'Ready' and c.get('status') == 'True' for c in status.get('conditions', [])), 'unschedulable': bool(node.get('spec', {}).get('unschedulable'))})
    return result


def precheck(payload):
    config = payload['config']; run_id = payload['run_id']
    validate_config(config, run_id)
    checks = []
    def add(name, ok, detail):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})
    if config['kind'] == 'rfdetr_2xlarge':
        add('2XLarge许可证', config.get('accept_platform_model_license') is True, '需要管理员明确确认Platform Model License')
    pvc = get_json(['-n', NAMESPACE, 'get', 'pvc', PVC])
    pv_name = pvc.get('spec', {}).get('volumeName')
    pv = get_json(['get', 'pv', pv_name]) if pv_name else {}
    nfs = pv.get('spec', {}).get('nfs', {})
    add('PVC', pvc.get('status', {}).get('phase') == 'Bound' and 'ReadWriteMany' in pvc.get('status', {}).get('accessModes', []) and 'ReadWriteMany' in pv.get('spec', {}).get('accessModes', []) and nfs.get('server') == SERVER and nfs.get('path') == EXPORT, '必须绑定指定 NFS PV，且支持 ReadWriteMany')
    root = mount_root()
    for field in (('model_path',) if config.get('snapshot_id') else ('dataset_path', 'model_path')):
        if field=='model_path' and payload.get('pending_base') and config['kind'] in ('swift_sft','wemm'):continue
        path = safe_path(root, dataset_relative(config[field], config['kind']) if field == 'dataset_path' else model_relative(config[field]))
        add(field, os.path.exists(path), '数据或模型路径必须存在')
    if config.get('snapshot_id'):
        cache = safe_path(root, 'chen/training/dataset-cache')
        parent = cache if os.path.isdir(cache) else os.path.dirname(cache)
        add('snapshot_cache', os.path.isdir(parent) and os.access(parent, os.W_OK), '冻结数据缓存目录必须可写')
    if config['kind'] == 'wemm' and config.get('parameters', {}).get('val_dataset'):
        path = safe_path(root, dataset_relative(config['parameters']['val_dataset'], config['kind']))
        add('val_dataset', os.path.isfile(path), '验证数据文件必须存在')
    available = dict((row['name'], row) for row in nodes())
    for node in config['k8s']['nodes']:
        row = available.get(node, {})
        add(node, row.get('ready') and not row.get('unschedulable') and row.get('available', 0) >= config['k8s']['gpus_per_node'] and row.get('cpu_available_m', 0) >= 16000 and row.get('memory_available_bytes', 0) >= 64 * 1024 ** 3, '节点须就绪且有足够 GPU、CPU 和内存请求余量')
        try:
            node_tolerations(row.get('taints', []))
            add(node + u' 污点', True, '污点可容忍')
        except ValueError as error:
            add(node + u' 污点', False, error_text(error))
    for verb, resource in [('get', 'jobs'), ('create', 'jobs'), ('delete', 'jobs'), ('get', 'pods'), ('get', 'pods/log'), ('create', 'services'), ('delete', 'services'), ('create', 'configmaps'), ('delete', 'configmaps')]:
        answer = kubectl(['auth', 'can-i', verb, resource, '-n', NAMESPACE]).strip()
        add('RBAC ' + verb + ' ' + resource, answer == 'yes', answer)
    return {'ok': all(row['ok'] for row in checks), 'checks': checks}


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        while True:
            chunk = stream.read(1048576)
            if not chunk: break
            digest.update(chunk)
    return digest.hexdigest()


def artifacts(root):
    files = []; truncated = False
    if os.path.isdir(root) and not os.path.islink(root):
        for directory, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(directory, d)))
            for name in sorted(names):
                path = os.path.join(directory, name)
                if os.path.islink(path) or not os.path.isfile(path): continue
                if len(files) >= 500:
                    truncated = True; break
                role = 'weights' if os.path.splitext(name)[1].lower() in ('.pt', '.pth', '.safetensors', '.ckpt', '.bin', '.onnx', '.engine') else 'file'
                files.append({'path': os.path.relpath(path, root).replace(os.sep, '/'), 'bytes': os.path.getsize(path), 'role': role})
            if truncated: break
    return {'files': files, 'truncated': truncated}


def metric_files(root):
    names = set(('results.csv', 'metrics.csv', 'metrics.json', 'metrics.jsonl', 'log.json', 'log.jsonl', 'log.txt', 'logging.jsonl', 'trainer_state.json', 'all_results.json', 'train_results.json', 'eval_results.json'))
    candidates = []; truncated = False; visited = 0
    if os.path.islink(root): raise ValueError('产物目录不能是符号链接')
    for parent, dirs, files in os.walk(root, followlinks=False):
        visited += 1
        if visited > 200:
            truncated = True; break
        dirs[:] = sorted(d for d in dirs if not os.path.islink(os.path.join(parent, d)) and len(os.path.relpath(os.path.join(parent, d), root).split(os.sep)) < 6)
        for name in sorted(files):
            if name not in names and not (parent == root and name == 'training_config.json'): continue
            path = os.path.join(parent, name)
            if not os.path.islink(path) and os.path.isfile(path): candidates.append(path)
    candidates.sort(key=lambda path: (os.path.basename(path) not in ('metrics.csv', 'results.csv', 'trainer_state.json', 'logging.jsonl'), path))
    result = []; total = 0
    for path in candidates:
        relative = os.path.relpath(path, root).replace(os.sep, '/')
        if len(result) >= 12:
            truncated = True; break
        path = safe_path(root, relative, True)
        fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_size > 4 * 1024 * 1024 or total + before.st_size > 8 * 1024 * 1024:
                truncated = True; continue
            with os.fdopen(fd, 'rb') as stream:
                fd = None
                data = stream.read(4 * 1024 * 1024 + 1)
                after = os.fstat(stream.fileno())
            if len(data) != before.st_size or (before.st_size, before.st_mtime, before.st_ino) != (after.st_size, after.st_mtime, after.st_ino):
                truncated = True; continue
            total += len(data)
            result.append({'path': relative, 'data': base64.b64encode(data).decode('ascii'), 'mtime': before.st_mtime})
        finally:
            if fd is not None: os.close(fd)
    return {'files': result, 'truncated': truncated}


def objects(run_id, config):
    selector = 'platform.training.id=' + run_id
    result = {}
    for kind in ('jobs', 'pods', 'services', 'configmaps'):
        items = get_json(['-n', NAMESPACE, 'get', kind, '-l', selector])['items']
        for item in items:
            labels = item['metadata'].get('labels', {})
            if labels.get('platform.training.id') != run_id or labels.get('platform.training.config') != config_hash(config):
                raise ValueError('任务资源所有权不匹配')
        result[kind] = items
    return result


@contextlib.contextmanager
def locked(path):
    import fcntl
    lock_path = os.path.join(path, '.lock')
    if os.path.islink(lock_path): raise ValueError('锁文件不能是符号链接')
    lock = open(lock_path, 'a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN); lock.close()


@contextlib.contextmanager
def try_locked(path):
    import fcntl
    import errno
    lock_path = os.path.join(path, '.lock')
    if os.path.islink(lock_path): raise ValueError('锁文件不能是符号链接')
    lock = open(lock_path, 'a')
    acquired = False
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except IOError as error:
            if error.errno not in (errno.EACCES, errno.EAGAIN): raise
        yield acquired
    finally:
        if acquired: fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def process_identity(pid):
    if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1: return None
    try:
        with open('/proc/sys/kernel/random/boot_id') as stream: boot = stream.read().strip()
        with open('/proc/%s/stat' % pid) as stream: fields = stream.read().rsplit(')', 1)[1].split()
        if fields[0] == 'Z': return None
        start = int(fields[19])
        return (boot, start)
    except (IOError, OSError, ValueError, IndexError):
        return None


def atomic_json(path, value):
    temp = path + '.' + uuid.uuid4().hex
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream: json.dump(value, stream, sort_keys=True)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def staged_config_matches(path, config):
    target = os.path.join(path, 'run_config.json')
    if os.path.islink(target): return False
    try:
        with open(target) as stream: return json.load(stream) == config
    except (IOError, OSError, ValueError): return False


def snapshot_ready(path, payload):
    marker = os.path.join(path, '.dataset-ready')
    if os.path.islink(marker) or not os.path.isfile(marker): return False
    try:
        with open(marker) as stream:
            if stream.read() != payload['snapshot_hash']: return False
    except (IOError, OSError): return False
    folders = ['dataset'] + ([] if payload['config']['kind'] in ('wemm', 'swift_sft') else ['prepared-dataset'])
    return all(os.path.isdir(os.path.join(path, name)) and not os.path.islink(os.path.join(path, name)) for name in folders)


def unpack_status(path, payload):
    owner_path = os.path.join(path, '.unpack-owner')
    if os.path.islink(owner_path): return {'state': 'unknown'}
    try:
        with open(owner_path) as stream: owner = json.load(stream)
    except (IOError, OSError, ValueError): return {'state': 'unknown'}
    if not isinstance(owner, dict) or owner.get('config_hash') != config_hash(payload['config']) or owner.get('snapshot_hash') != payload['snapshot_hash']:
        return {'state': 'unknown'}
    if not owner.get('pid') or not owner.get('boot_id') or not owner.get('start_time'):
        return {'state': 'unknown'}
    if os.path.lexists(os.path.join(path, '.canceled')):
        current = process_identity(owner['pid'])
        return {'state': 'running' if current == (owner['boot_id'], owner['start_time']) else 'unknown'}
    result_path = os.path.join(path, '.unpack-result')
    if os.path.islink(result_path): return {'state': 'unknown'}
    if os.path.isfile(result_path):
        try:
            with open(result_path) as stream: result = json.load(stream)
            if isinstance(result, dict) and all(result.get(k) == owner.get(k) for k in ('token', 'config_hash', 'snapshot_hash', 'pid', 'boot_id', 'start_time')) and result.get('state') == 'failed':
                return {'state': 'failed', 'error': result.get('error', '冻结数据准备失败')}
        except (IOError, OSError, ValueError): pass
        return {'state': 'unknown'}
    current = process_identity(owner['pid'])
    if current == (owner['boot_id'], owner['start_time']): return {'state': 'running'}
    return {'state': 'unknown'}


def unpack_begin(path, payload):
    with try_locked(path) as acquired:
        if not acquired: return unpack_status(path, payload)
        if os.path.exists(os.path.join(path, '.unpack-owner')) or os.path.islink(os.path.join(path, '.unpack-owner')):
            return unpack_status(path, payload)
        if os.path.lexists(os.path.join(path, '.canceled')): return {'state': 'canceled'}
        if any(os.path.lexists(os.path.join(path, name)) for name in ('dataset', 'prepared-dataset', '.dataset-ready', '.unpack-result')):
            return {'state': 'unknown'}
        owner = dict(config_hash=config_hash(payload['config']), snapshot_hash=payload['snapshot_hash'], token=uuid.uuid4().hex)
        owner_path = os.path.join(path, '.unpack-owner')
        write_once(owner_path, json.dumps(owner, sort_keys=True))
        worker = dict(payload, action='unpack_worker', worker_token=owner['token'])
        code = base64.b64encode(PLATFORM_SOURCE).decode('ascii')
        data = base64.b64encode(json.dumps(worker).encode('utf-8')).decode('ascii')
        command = 'import base64;PLATFORM_SOURCE=base64.b64decode(%r);exec(PLATFORM_SOURCE)' % code
        with open(os.devnull, 'rb') as stdin, open(os.devnull, 'ab') as output:
            process = subprocess.Popen([sys.executable, '-c', command, data], stdin=stdin, stdout=output, stderr=output, start_new_session=True)
        identity = process_identity(process.pid)
        if identity is None: return {'state': 'unknown'}
        owner.update(pid=process.pid, boot_id=identity[0], start_time=identity[1])
        atomic_json(owner_path, owner)
        return {'state': 'running'}


def unpack_worker(path, payload):
    with locked(path):
        if not staged_config_matches(path, payload['config']): return {'state': 'unknown'}
        owner_path = os.path.join(path, '.unpack-owner')
        if os.path.islink(owner_path): return {'state': 'unknown'}
        try:
            with open(owner_path) as stream: owner = json.load(stream)
        except (IOError, OSError, ValueError): return {'state': 'unknown'}
        if (not isinstance(owner, dict) or owner.get('token') != payload.get('worker_token') or owner.get('pid') != os.getpid()
                or (owner.get('boot_id'), owner.get('start_time')) != process_identity(os.getpid())
                or owner.get('config_hash') != config_hash(payload['config']) or owner.get('snapshot_hash') != payload['snapshot_hash']):
            return {'state': 'unknown'}
        try:
            if os.path.lexists(os.path.join(path, '.canceled')): return {'state': 'canceled'}
            root = mount_root()
            cache = safe_path(root, 'chen/training/dataset-cache')
            namespace = {}
            exec(payload['snapshot_code'], namespace)
            data = dict(payload, action='unpack', legacy_archives=[])
            namespace['remote_snapshot_ops'](data, path, cache)
            return {'state': 'ready'}
        except Exception as error:
            result = dict(owner, state='failed', error=error_text(error)[-2000:])
            atomic_json(os.path.join(path, '.unpack-result'), result)
            return {'state': 'failed'}


def write_once(path, content):
    content = content.encode('utf-8') if not isinstance(content, bytes) else content
    if os.path.islink(path): raise ValueError('任务文件不能是链接')
    if os.path.exists(path):
        with open(path, 'rb') as stream:
            if stream.read() != content: raise ValueError('已存在任务文件不匹配，禁止覆盖')
    else:
        with open(path, 'wb') as stream: stream.write(content)


def save_logs(path, pods):
    for pod in pods:
        rank = pod['metadata'].get('labels', {}).get('platform.training.rank', '')
        if not re.match(r'^[0-9]+$', rank):
            continue
        phase = pod.get('status', {}).get('phase')
        try:
            text = (u'Pod 等待中：' + (pod_waiting(pod) or 'Pending')) if phase == 'Pending' else kubectl(['-n', NAMESPACE, 'logs', pod['metadata']['name'], '--tail=200'])[-160000:]
        except RuntimeError as error:
            text = u'日志读取失败：' + error_text(error)
        target = os.path.join(path, '.logs-' + rank)
        try:
            if not os.path.exists(target): write_once(target, text)
        except (IOError, OSError, ValueError):
            pass


def delete_owned(state, kinds):
    for kind in kinds:
        for item in state[kind]:
            kubectl(['-n', NAMESPACE, 'delete', kind, item['metadata']['name'], '--wait=true', '--timeout=30s', '--ignore-not-found=true'])


def pod_waiting(pod):
    status = pod.get('status', {})
    for container in status.get('containerStatuses', []):
        waiting = container.get('state', {}).get('waiting', {})
        if waiting.get('reason'): return waiting['reason']
    for condition in status.get('conditions', []):
        if condition.get('status') == 'False' and condition.get('reason'): return condition['reason']
    return status.get('reason', '')


def runtime_instances(config, pods):
    result = []
    for rank, node in enumerate(config['k8s']['nodes']):
        pod = next((p for p in pods if p.get('metadata', {}).get('labels', {}).get('platform.training.rank') == str(rank)), None)
        result.append({'rank': rank, 'node': node, 'phase': pod.get('status', {}).get('phase', 'Unknown') if pod else 'NotCreated', 'waitingReason': pod_waiting(pod) if pod else ''})
    return result


def execute(payload):
    action = payload['action']
    if action == 'nodes': return nodes()
    config = payload['config']; run_id = payload['run_id']
    validate_config(config, run_id)
    if action == 'precheck': return precheck(payload)
    if action in ('model_directory','base_model_file'):
        kind=payload.get('input_kind')
        if kind=='base' or action=='base_model_file':
            directory=safe_path(mount_root(),model_relative(config['model_path']),True)
        else:
            directory=safe_path(run_path(config,run_id),config['output_subdir'],True)
            if payload['path']:directory=safe_path(directory,payload['path'],True)
        if action=='base_model_file':
            file=safe_path(directory,payload['path'],True)
            if not os.path.isfile(file):raise ValueError('模型文件不存在')
            return dict(path=file,bytes=os.path.getsize(file),sha256=file_hash(file))
        entry=payload.get('entry','config.json' if kind=='base' else 'adapter_config.json')
        if entry not in ('config.json','adapter_config.json'):raise ValueError('模型入口无效')
        safe_path(directory,entry,True)
        if any(os.path.islink(os.path.join(parent,name)) for parent,dirs,names in os.walk(directory) for name in dirs+names):
            raise ValueError('模型目录包含符号链接')
        listing=artifacts(directory)
        if listing['truncated']:raise ValueError('模型目录超过500个文件')
        rows=[]
        for item in sorted(listing['files'],key=lambda row:row['path']):
            file=safe_path(directory,item['path'],True)
            rows.append(dict(path=item['path'],bytes=item['bytes'],sha256=file_hash(file)))
        if not any(row['bytes'] and row['path'].endswith(('.safetensors','.bin')) for row in rows):
            raise ValueError('模型目录缺少权重')
        return dict(path=directory,files=rows)
    path = run_path(config, run_id, create=action in ('stage', 'stop'))
    output = safe_path(path, config['output_subdir'])
    if action in ('merge_cache_probe','merge_cache_publish'):
        from pathlib import Path
        namespace={'cache_file_hash':file_hash,'run_lock':locked}
        exec(MODEL_CACHE_SOURCE,namespace)
        local_config=dict(config,output_root=os.path.dirname(path),model_path=os.path.join(path,'base'))
        with locked(path):
            return namespace['merge_cache'](dict(payload,config=local_config,
                model_cache_root=safe_path(mount_root(),'chen/training/model-input-cache')),Path(path))
    if action == 'artifacts': return artifacts(output)
    if action == 'metric_files': return metric_files(output)
    if action == 'artifact':
        file_path = safe_path(output, payload['path'], True)
        if not os.path.isfile(file_path): raise ValueError('产物不是普通文件')
        return {'path': file_path, 'logical_path': config['output_root'] + '/' + run_id + '/' + config['output_subdir'] + '/' + payload['path'], 'bytes': os.path.getsize(file_path), 'sha256': file_hash(file_path)}
    if action == 'stage':
        with locked(path):
            if os.path.exists(os.path.join(path, '.canceled')): raise ValueError('任务已取消')
            write_once(os.path.join(path, 'run_config.json'), json.dumps(config, sort_keys=True, ensure_ascii=True))
            write_once(os.path.join(path, 'train.py'), payload['script'])
        return {'directory': path}
    if action in ('unpack_begin', 'unpack_status', 'unpack_worker'):
        if not config.get('snapshot_id') or not re.match(r'^[0-9a-f]{64}$', payload.get('snapshot_hash', '')):
            raise ValueError('冻结数据摘要无效')
        if not staged_config_matches(path, config): return {'state': 'unknown'}
        if action != 'unpack_worker' and not os.path.lexists(os.path.join(path, '.canceled')) and os.path.lexists(os.path.join(path, '.dataset-ready')):
            return {'state': 'ready' if snapshot_ready(path, payload) else 'unknown'}
        if action == 'unpack_begin': return unpack_begin(path, payload)
        if action == 'unpack_worker': return unpack_worker(path, payload)
        return unpack_status(path, payload)
    if action in ('snapshot_cache', 'unpack'):
        with locked(path):
            if os.path.exists(os.path.join(path, '.canceled')): raise ValueError('任务已取消')
            root = mount_root()
            cache = safe_path(root, 'chen/training/dataset-cache')
            legacy = []
            for item in payload.get('legacy_archives', [])[:5]:
                legacy.append(safe_path(root, training_relative(item)))
            data = dict(payload, legacy_archives=legacy)
            namespace = {}
            exec(payload['snapshot_code'], namespace)
            return namespace['remote_snapshot_ops'](data, path, cache)
    state = objects(run_id, config) if action not in ('start', 'stop') else None
    if action == 'logs':
        selected = payload.get('node')
        rank_nodes = dict((str(i), node) for i, node in enumerate(config['k8s']['nodes']))
        pods = [p for p in state['pods'] if not selected or p.get('spec', {}).get('nodeName', rank_nodes.get(p.get('metadata', {}).get('labels', {}).get('platform.training.rank'))) == selected]
        instances = []
        for pod in pods:
            if pod.get('status', {}).get('phase') == 'Pending':
                text = u'Pod 等待中：' + (pod_waiting(pod) or 'Pending')
            else:
                text = kubectl(['-n', NAMESPACE, 'logs', pod['metadata']['name'], '--tail=200'])[-160000:]
            instances.append({'node': pod.get('spec', {}).get('nodeName', rank_nodes.get(pod.get('metadata', {}).get('labels', {}).get('platform.training.rank'), '')), 'text': text})
        if not instances:
            for rank, node in enumerate(config['k8s']['nodes']):
                if selected and selected != node: continue
                cached = os.path.join(path, '.logs-' + str(rank))
                if os.path.isfile(cached) and not os.path.islink(cached):
                    with open(cached, 'rb') as stream: text = stream.read().decode('utf-8', 'replace')
                    instances.append({'node': node, 'text': text})
        return {'text': '\n'.join(row['text'] for row in instances)[-160000:], 'instances': instances}
    if action == 'status':
        jobs = state['jobs']
        runtime = {'job_count': len(jobs), 'instances': runtime_instances(config, state['pods'])}
        if not jobs and state['pods']: return dict(runtime, status='unknown', error='任务 Pod 仍存在，需停止并确认释放')
        if not jobs: return dict(runtime, status='failed' if os.path.exists(os.path.join(path, '.failed')) else ('canceled' if os.path.exists(os.path.join(path, '.canceled')) else 'missing'))
        failed = any(j.get('status', {}).get('failed', 0) for j in jobs)
        ranks = set()
        for pod in state['pods']:
            containers = pod.get('status', {}).get('containerStatuses', [])
            if len(containers) == 1 and containers[0].get('state', {}).get('terminated', {}).get('exitCode') == 0:
                ranks.add(pod['metadata'].get('labels', {}).get('platform.training.rank'))
        success = len(jobs) == len(config['k8s']['nodes']) and all(j.get('status', {}).get('succeeded', 0) for j in jobs) and ranks == set(str(i) for i in range(len(config['k8s']['nodes'])))
        if failed:
            with locked(path):
                state = objects(run_id, config)
                save_logs(path, state['pods'])
                write_once(os.path.join(path, '.failed'), '')
                delete_owned(state, ('jobs', 'pods'))
                remaining = objects(run_id, config)
                if remaining['jobs'] or remaining['pods']: raise RuntimeError('故障节点停止尚未确认')
            return dict(runtime, status='failed', error='节点训练失败，其他节点已停止', artifacts=artifacts(output))
        if len(jobs) != len(config['k8s']['nodes']): return dict(runtime, status='unknown', error='任务 Job 不完整，请停止并核对集群资源')
        if success:
            result = artifacts(output)
            return dict(runtime, status='completed' if any(f['role'] == 'weights' for f in result['files']) else 'failed', artifacts=result)
        return dict(runtime, status='running')
    if action in ('start', 'stop'):
        if action == 'stop': write_once(os.path.join(path, '.canceled'), '')
        with locked(path):
            state = objects(run_id, config)
            canceled = os.path.join(path, '.canceled')
            if action == 'stop':
                save_logs(path, state['pods'])
                delete_owned(state, ('jobs', 'pods', 'services', 'configmaps'))
                if any(objects(run_id, config).values()): raise RuntimeError('资源仍存在，状态待核对')
                return {'status': 'canceled'}
            if os.path.exists(canceled): return {'status': 'canceled'}
            if os.path.exists(os.path.join(path, '.failed')): return {'status': 'failed'}
            if config.get('snapshot_id'):
                marker = safe_path(path, '.dataset-ready', True)
                with open(marker, 'r') as stream:
                    if stream.read() != payload.get('snapshot_hash'): raise ValueError('冻结数据未准备完成或摘要不匹配')
                safe_path(path, 'dataset', True)
                if config['kind'] not in ('wemm', 'swift_sft'): safe_path(path, 'prepared-dataset', True)
            if state['jobs']:
                if len(state['jobs']) == len(config['k8s']['nodes']): return {'status': 'running'}
                raise RuntimeError('部分任务已创建，请核对集群状态后停止')
            checked = precheck(payload)
            if not checked['ok']: return {'status': 'blocked', 'inspection': checked}
            with open(os.path.join(path, 'run_config.json'), 'rb') as stream:
                if json.loads(stream.read().decode('utf-8')) != config: raise ValueError('任务配置不匹配')
            with open(os.path.join(path, 'train.py'), 'rb') as stream: script = stream.read().decode('utf-8')
            node_info = dict((row['name'], row) for row in nodes())
            tolerations = dict((node, node_tolerations(node_info[node]['taints'])) for node in config['k8s']['nodes'])
            manifests = build_manifest(config, run_id, script, payload.get('assets'), tolerations)
            existing = set((item['kind'], item['metadata']['name']) for rows in state.values() for item in rows)
            for item in manifests:
                if (item['kind'], item['metadata']['name']) not in existing:
                    kubectl(['create', '-f', '-'], json.dumps(item))
            return {'status': 'running'}
    raise ValueError('不支持的训练操作')


def main():
    try:
        payload = json.loads(base64.b64decode(sys.argv[1]).decode('utf-8'))
        print(json.dumps({'success': True, 'result': execute(payload)}, ensure_ascii=True))
    except Exception as error:
        print(json.dumps({'success': False, 'error': error_text(error)[-2000:]}, ensure_ascii=True))
        sys.exit(1)


if __name__ == '__main__':
    main()
