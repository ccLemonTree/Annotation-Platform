"""Bounded, read-only logs for a backend-selected deployment target."""
import base64
import json
from pathlib import Path
import re
import subprocess
import sys
import threading
from uuid import UUID

LIMIT=65536


def redact(text):
    text=re.sub(r'(?i)(\b(?:bearer|basic)\s+)[A-Za-z0-9._~+/=-]+',r'\1[REDACTED]',text)
    keys=r'(?:password|passwd|secret|access_token|token|api_key|authorization)'
    text=re.sub(r'(?i)('+keys+r'''["']?\s*[:=]\s*)("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')''',r'\1[REDACTED]',text)
    text=re.sub(r'(?i)('+keys+r'["\x27]?\s*[:=]\s*)[^\s,"\x27]+',r'\1[REDACTED]',text)
    text=re.sub(r'(?i)(https?://)[^\s/@:]+:[^\s/@]+@',r'\1[REDACTED]@',text)
    return re.sub(r'eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}','[REDACTED]',text)


def bounded(text):
    data=redact(text).encode('utf-8')
    return dict(content=data[-LIMIT:].decode('utf-8',errors='ignore'),truncated=len(data)>LIMIT)


def command(args,limit=4*1024*1024):
    process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    data=bytearray()
    def read():
        while True:
            chunk=process.stdout.read(4096)
            if not chunk:return
            data.extend(chunk)
            if len(data)>limit:del data[:-limit]
    reader=threading.Thread(target=read,daemon=True);reader.start()
    try:
        reader.join(10)
        if reader.is_alive():raise ValueError('日志读取超时，请重试或使用SSH调试')
        if process.wait(timeout=2):raise ValueError('日志命令读取失败，请使用SSH调试核对服务状态')
        return bytes(data).decode('utf-8',errors='replace')
    finally:
        if process.poll() is None:process.kill()
        process.wait();reader.join(2);process.stdout.close()


def safe(path):
    if any(p.is_symlink() for p in [path,*path.parents]):raise ValueError('日志路径不能经过符号链接')
    return path


def tail(path):
    safe(path)
    if not path.is_file():return bounded('')
    with path.open('rb') as file:
        file.seek(0,2);size=file.tell();file.seek(max(0,size-LIMIT))
        data=file.read(LIMIT)
    result=bounded(data.decode('utf-8',errors='replace'))
    result['truncated']=result['truncated'] or size>LIMIT
    return result


def updates(payload):
    UUID(payload['job_id'])
    root=Path.home()/'.local/share/data-platform/image-updates'
    if payload['target']['environment']!='k8s':return tail(root/payload['job_id']/'pull.log')
    key=payload.get('cache_key')
    if key is None:return bounded('')
    if not re.fullmatch(r'[a-f0-9]{64}',key):raise ValueError('无效预分发日志标识')
    stage=safe(root/'preloads'/key);attempts=safe(stage/'attempts')
    folders=[safe(p) for p in attempts.iterdir() if p.is_dir()] if attempts.is_dir() else []
    return tail(max(folders,key=lambda p:p.stat().st_mtime)/'ansible.log') if folders else tail(stage/'runner.log')


def service(payload):
    target=payload['target'];instances=[]
    if target['environment']=='k8s':
        ns=target['namespace'];kind=target['workload_kind']
        if kind not in ('Deployment','StatefulSet'):raise ValueError('无效工作负载类型')
        workload=json.loads(command(['kubectl','get',kind,target['workload'],'-n',ns,'-o','json']))
        owners={workload['metadata']['uid']}
        if kind=='Deployment':
            sets=json.loads(command(['kubectl','get','rs','-n',ns,'-o','json']))['items']
            owners={r['metadata']['uid'] for r in sets if any(o['uid'] in owners for o in r['metadata'].get('ownerReferences',[]))}
        pods=json.loads(command(['kubectl','get','pods','-n',ns,'-o','json']))['items']
        for pod in pods:
            meta=pod['metadata']
            if meta.get('deletionTimestamp') or not any(o['uid'] in owners for o in meta.get('ownerReferences',[])):continue
            if any(c['name']==target['container'] for c in pod['spec']['containers']):instances.append(dict(name=meta['name'],uid=meta['uid']))
    else:
        for name in target['containers']:
            uid=json.loads(command(['docker','inspect','--format','{{json .Id}}',name]))
            instances.append(dict(name=name,uid=uid))
    instances.sort(key=lambda row:row['name'])
    selected=payload.get('instance') or (instances[0]['name'] if instances else None)
    row=next((r for r in instances if r['name']==selected),None)
    if selected and not row:raise ValueError('实例已移除或不属于此目标，请重新选择')
    if not row:return dict(content='暂无可读取日志的实例',truncated=False,instances=[],selected_instance=None)
    if target['environment']=='k8s':
        args=['kubectl','logs',row['name'],'-n',ns,'-c',target['container'],'--tail=200','--timestamps=true','--limit-bytes=65536']
    else:args=['docker','logs','--tail=200','--timestamps',row['uid']]
    text=command(args,limit=LIMIT)
    result=bounded(text);result['truncated']=result['truncated'] or len(text.encode('utf-8'))>=LIMIT
    return dict(result,instances=instances,selected_instance=selected)


def dispatch(payload):
    if payload['source']=='updates':return updates(payload)
    if payload['source']=='service':return service(payload)
    raise ValueError('未知日志来源')


if __name__=='__main__':
    try:print(json.dumps(dict(success=True,result=dispatch(json.loads(base64.b64decode(sys.argv[1])))),ensure_ascii=False))
    except Exception:print(json.dumps(dict(success=False,error='日志暂不可读；请核对SSH连接、目标实例及日志目录权限'),ensure_ascii=False))
