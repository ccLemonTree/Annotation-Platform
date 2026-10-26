"""Fixed Linux process inspection/stop commands, executed through ssh-manager."""
import base64,csv,io,json,os,re,select,signal,subprocess,sys
from pathlib import Path


def command(args,timeout=10):
    result=subprocess.run(args,capture_output=True,text=True,timeout=timeout)
    if result.returncode:raise ValueError('远端命令失败，请核对权限或资源状态')
    return result.stdout


def process_start(pid):
    return Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[19]


def inspect_process(pid,gpu_uuid):
    if type(pid)!=int or pid<=1:raise ValueError('无效进程')
    start=process_start(pid)
    rows=list(csv.reader(io.StringIO(command(['nvidia-smi','--query-compute-apps=gpu_uuid,pid,process_name','--format=csv,noheader,nounits'])),skipinitialspace=True))
    if len(rows)>4096 or any(len(row)!=3 for row in rows):raise ValueError('GPU进程信息不完整')
    found=[row for row in rows if row[1].strip()==str(pid)]
    if not any(row[0].strip()==gpu_uuid for row in found):raise ValueError('进程已退出或GPU占用已变化，请刷新')
    cgroup=Path(f'/proc/{pid}/cgroup').read_text()
    if 'kubepods' in cgroup:raise ValueError('该进程由Kubernetes管理，请停止对应工作负载后释放资源')
    container=None
    match=re.search(r'docker[/-]([a-f0-9]{64})',cgroup)
    uuids=sorted({row[0].strip() for row in found})
    if match:
        info=json.loads(command(['docker','inspect',match[1]]))[0]
        if not info['State']['Running']:raise ValueError('容器状态已变化，请刷新')
        labels=info.get('Config',{}).get('Labels') or {}
        container={'id':info['Id'],'name':info['Name'].lstrip('/'),
            'labels':{key:labels[key] for key in ('platform.training.id','platform.validation.run','platform.validation.execution') if key in labels}}
        members=set()
        for row in rows:
            try:
                if match[1] in Path(f'/proc/{int(row[1])}/cgroup').read_text():members.add(row[0].strip())
            except FileNotFoundError:continue
        uuids=sorted(members|set(uuids))
    elif re.search(r'[a-f0-9]{64}',cgroup):
        raise ValueError('该进程属于其他容器运行时，请从对应任务停止，不能直接结束进程')
    if process_start(pid)!=start:raise ValueError('进程身份已变化，请刷新后重试')
    return dict(pid=pid,gpu_uuid=gpu_uuid,name=found[0][2].strip(),gpu_uuids=uuids,
        start=start,boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),container=container)


def stop_process(target,force):
    if not hasattr(os,'pidfd_open') or not hasattr(signal,'pidfd_send_signal'):
        raise ValueError('服务器不支持安全的PID身份锁定，请通过原任务停止')
    fd=os.pidfd_open(target['pid'])
    try:
        if process_start(target['pid'])!=target['start']:raise ValueError('进程身份已变化，请重新确认')
        signal.pidfd_send_signal(fd,signal.SIGTERM)
        if not select.select([fd],[],[],8)[0]:
            if not force:return {'stopped':False,'message':'已发送正常结束信号，进程仍在退出；请刷新后核对，或重新确认并选择强制结束'}
            signal.pidfd_send_signal(fd,signal.SIGKILL)
            if not select.select([fd],[],[],4)[0]:return {'stopped':False,'message':'进程尚未退出，GPU资源仍可能被占用，请刷新核对'}
        return {'stopped':True,'message':'所选进程已退出，请查看刷新后的GPU占用'}
    finally:os.close(fd)


def execute(payload):
    if payload['action']=='inspect':return inspect_process(payload['pid'],payload['gpu_uuid'])
    if payload['action']!='stop':raise ValueError('不支持的GPU操作')
    target=payload['target'];current=inspect_process(target['pid'],target['gpu_uuid'])
    if current!=target:raise ValueError('进程或容器身份已变化，请刷新后重新确认')
    container=current['container']
    if container:
        if container['labels']:raise ValueError('平台任务必须通过任务停止流程释放')
        command(['docker','stop','--time','10',container['id']],timeout=25)
        state=json.loads(command(['docker','inspect',container['id']]))[0]['State']
        if state['Running']:raise ValueError('容器尚未确认停止，请刷新核对')
        return {'stopped':True,'message':'所选进程所在容器已停止，请查看刷新后的GPU占用'}
    return stop_process(current,payload.get('force',False))


if __name__=='__main__':
    try:print(json.dumps({'success':True,'result':execute(json.loads(base64.b64decode(sys.argv[1])))},ensure_ascii=False))
    except Exception as error:
        message=str(error) if isinstance(error,ValueError) else 'GPU操作未确认完成，请刷新核对远端状态和权限'
        print(json.dumps({'success':False,'error':message},ensure_ascii=False));sys.exit(1)
