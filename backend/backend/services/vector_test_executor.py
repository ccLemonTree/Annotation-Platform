"""Remote vLLM operations. The bridge includes the model bundle helpers."""
import base64
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler
from uuid import UUID


IMAGE = 'registry.cn-hangzhou.aliyuncs.com/lemontree_images/vllm-openai:v0.27.0-cu129-ubuntu2404'
LABEL = 'platform.vector-test.id'
MODEL = '/models/wemm_embedding_2b'


def docker(*args, timeout=30, env=None):
    return subprocess.run(['docker', *args], capture_output=True, text=True, timeout=timeout, env=env)


def owned(name, identifier):
    found = docker('ps', '-a', '--filter', 'name=^/'+name+'$', '--format', '{{.ID}}')
    if found.returncode:
        raise ValueError('无法读取 Docker 容器列表')
    if not found.stdout.strip():
        return None
    result = docker('inspect', '--format', '{{ index .Config.Labels "'+LABEL+'" }} {{.State.Status}}', name)
    if result.returncode or not result.stdout.startswith(identifier+' '):
        raise ValueError('同名容器不属于此测试记录')
    return result.stdout.split()[1]


def launch_image_pull(stage):
    with (stage/'image-pull.log').open('wb') as log:
        process = subprocess.Popen(
            ['sh', '-c', 'docker pull "$1"; printf "%s\n" "$?" > "$2"', 'pull-vllm',
             IMAGE, str(stage/'image-pull.exit')],
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    return process.pid


def pull_running(stage, pid):
    try:
        args = Path('/proc')/str(pid)/'cmdline'
        return os.fsencode(stage/'image-pull.exit') in args.read_bytes().split(b'\0')
    except FileNotFoundError:
        return False


def ensure_image(stage):
    if docker('image', 'inspect', IMAGE).returncode == 0:
        return dict(ready=True)
    stage.mkdir(mode=0o700, parents=True, exist_ok=True)
    previous = stage/'image-pull.pid'
    if previous.exists():
        if (stage/'image-pull.exit').exists() or not pull_running(stage, int(previous.read_text())):
            raise ValueError('vLLM 镜像拉取失败或已中断，请查看日志后重新部署')
    else:
        previous.write_text(str(launch_image_pull(stage)))
    return dict(ready=False)


def start(name, identifier, spec):
    if owned(name, identifier):
        return {}
    destination = real(spec['model_path'])
    if not (destination/'chat_template.jinja').is_file():
        raise ValueError('模型目录缺少 chat_template.jinja')
    receipt = inspect_source(dict(root=str(destination.parent), artifact_path=destination.name, runtime='vllm'))
    if spec.get('fingerprint') and spec['fingerprint'] != fingerprint(receipt):
        raise ValueError('模型文件在传输后发生变化')
    if docker('image', 'inspect', IMAGE).returncode:
        raise ValueError('指定的 vLLM 镜像已从服务器移除，请重新部署')
    gpu = spec['gpu']
    probe = subprocess.run(['nvidia-smi', '-i', gpu, '--query-gpu=memory.used', '--format=csv,noheader,nounits'],
                           capture_output=True, text=True, timeout=10)
    if probe.returncode or probe.stdout.strip() != '0':
        raise ValueError('指定 GPU 不可用或已经占用')
    env = dict(os.environ, VLLM_API_KEY=spec['token'])
    result = docker('run', '-d', '--name', name, '--label', LABEL+'='+identifier, '--restart', 'unless-stopped',
                    '--gpus', 'device='+gpu, '-p', '127.0.0.1:'+str(spec['port'])+':8000', '-e', 'VLLM_API_KEY',
                    '--mount', 'type=bind,src='+str(destination)+',dst='+MODEL+',readonly',
                    IMAGE, MODEL, '--served-model-name', MODEL, '--runner', 'pooling',
                    '--chat-template', MODEL+'/chat_template.jinja', '--dtype', 'half', '--max-model-len', '8192',
                    '--limit-mm-per-prompt', '{"image":{"count":1,"width":1024,"height":1024},"video":0}',
                    '--enforce-eager', '--skip-mm-profiling', '--host', '0.0.0.0', '--port', '8000', env=env)
    if result.returncode:
        raise ValueError('启动 vLLM 失败，请核对映射端口、GPU 和 Docker 运行环境')
    return {}


def fingerprint(receipt):
    import hashlib
    return hashlib.sha256(json.dumps(sorted((f['path'], f['sha256']) for f in receipt['files']
                                           if f['path'] != '.platform-deployment.json'),
                                     separators=(',', ':')).encode()).hexdigest()


def embed(spec, requests):
    opener = build_opener(ProxyHandler({}))
    url = 'http://127.0.0.1:'+str(spec['port'])+'/v1/embeddings'
    results = []
    for messages in requests:
        data = json.dumps(dict(model=MODEL, messages=messages, encoding_format='float')).encode()
        try:
            request = Request(url, data=data, headers={
                'Authorization': 'Bearer '+spec['token'], 'Content-Type': 'application/json'})
            with opener.open(request, timeout=25) as response:
                values = json.loads(response.read(1024**2))
            results.append(dict(vector=values['data'][0]['embedding']))
        except HTTPError as exc:
            results.append(dict(error='vLLM HTTP '+str(exc.code)))
        except (URLError, TimeoutError, ValueError, TypeError, KeyError, IndexError):
            results.append(dict(error='测试模型未就绪、响应格式无效或请求超时'))
    return results


def remove_query(folder):
    path = folder/'input.json'
    if path.exists():
        path.unlink()
    if folder.exists():
        folder.rmdir()


def log_tail(text):
    limit = 64000
    if len(text) <= limit:
        return text
    tail = text[-limit:]
    _, boundary, complete = tail.partition('\n')
    return complete if boundary and complete else tail


def execute(spec):
    identifier = str(UUID(spec['id']))
    action = spec['action']
    name = 'platform-vector-test-'+identifier
    target = dict(repository='/opt/platform-vector-tests', runtime='vllm', model_name=identifier)
    stage = workspace(target, identifier)
    if action == 'image':
        return ensure_image(stage)
    if action == 'pack':
        return pack_source(spec['source'], identifier)
    if action == 'prepare':
        stage.mkdir(parents=True, exist_ok=True)
        return dict(path=str(stage/'input.zip'))
    if action == 'receive':
        receipt = receive(target, identifier, spec['sha256'])
        (stage/'input.zip').unlink()
        return dict(path=str(stage/'release'), fingerprint=fingerprint(receipt))
    if action == 'start':
        return start(name, identifier, spec)
    if spec.get('request_id'):
        folder = real(stage/('query-'+str(UUID(spec['request_id']))))
        if action == 'prepare_query':
            folder.mkdir(mode=0o700, parents=True)
            return dict(path=str(folder/'input.json'))
        if action == 'cleanup_query':
            remove_query(folder)
            return {}
    if action == 'embed':
        if not spec.get('request_id'):
            return embed(spec, spec['requests'])
        try:
            path = real(folder/'input.json')
            if not path.is_file() or path.stat().st_size > 16*1024**2 or sha(path) != spec['sha256']:
                raise ValueError('查询图片数据校验失败')
            return embed(spec, json.loads(path.read_text(encoding='utf-8')))
        finally:
            remove_query(folder)
    state = owned(name, identifier)
    if action == 'stop':
        if state and docker('rm', '-f', name).returncode:
            raise ValueError('无法停止测试容器，请核对远端状态')
        return {}
    if action == 'status':
        return dict(state=state)
    if action == 'logs':
        if not state:
            log = stage/'image-pull.log'
            if log.is_file():
                with log.open('rb') as stream:
                    offset = max(0, log.stat().st_size-64001)
                    stream.seek(offset)
                    text = stream.read(64001).decode('utf-8', errors='replace')
                if offset and '\n' in text:
                    text = text.partition('\n')[2]
                return dict(logs=log_tail('\n'.join(text.splitlines()[-200:])))
            return dict(logs='测试容器尚未创建或已停止')
        result = subprocess.run(['docker', 'logs', '--tail', '200', name],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30)
        return dict(logs=log_tail(result.stdout.decode('utf-8', errors='replace').replace(spec['token'], '[redacted]')))
    raise ValueError('未知测试服务操作')


if __name__ == '__main__':
    try:
        value = execute(json.loads(base64.b64decode(sys.argv[1])))
        print(json.dumps(dict(success=True, result=value), ensure_ascii=False))
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as error:
        # Docker parameters and credentials are never returned by command exceptions.
        message = str(error) if isinstance(error, ValueError) else '远端测试操作失败，请检查服务器和容器状态'
        print(json.dumps(dict(success=False, error=message), ensure_ascii=False))
