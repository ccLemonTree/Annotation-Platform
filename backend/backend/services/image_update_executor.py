"""Standalone Linux image operations. Snapshots containing env stay on that server."""
import base64
import copy
from contextlib import ExitStack
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
from urllib.parse import quote
from uuid import UUID


def command(args,timeout=60):
    result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=timeout)
    if result.returncode:raise ValueError('镜像操作命令失败：'+args[0]+'（退出码 '+str(result.returncode)+'，请在服务器查看日志）')
    return result.stdout.strip()


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def folder(job,create=True):
    UUID(job);path=Path.home()/'.local/share/data-platform/image-updates'/job
    if any(p.is_symlink() for p in [path]+list(path.parents)):raise ValueError('镜像操作目录不能经过符号链接')
    if create:path.mkdir(mode=0o700,parents=True,exist_ok=True);os.chmod(str(path),0o700)
    return path


def write(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value),encoding='utf-8');os.chmod(str(temp),0o600);os.replace(str(temp),str(path))


def docker_inspect(name):return json.loads(command(['docker','inspect',name]))[0]


def existing_container(name):
    ids=command(['docker','ps','-aq','--no-trunc','--filter','name=^/'+re.escape(name)+'$'])
    return docker_inspect(name) if ids else None


def docker_summary(value):
    state=value['State'];health=state.get('Health',{}).get('Status','not_configured')
    return dict(name=value['Name'].lstrip('/'),uid=value['Id'],image=value['Config']['Image'],image_id=value['Image'],
        ready=state.get('Running',False) and health in ('healthy','not_configured'),health=health,
        owner=(value['Config'].get('Labels') or {}).get('data-platform.image-update'),
        fingerprint=digest({k:value.get(k) for k in ('Config','HostConfig','Mounts')}))


def compose(value):
    labels=value['Config'].get('Labels') or {};project=labels.get('com.docker.compose.project')
    if not project:return None
    cwd=labels.get('com.docker.compose.project.working_dir');files=labels.get('com.docker.compose.project.config_files','').split(',')
    service=labels.get('com.docker.compose.service')
    if not cwd or not service or not files or not Path(cwd).is_absolute():raise ValueError('Compose项目路径或服务标签不完整')
    prefix=['docker-compose'] if shutil.which('docker-compose') else ['docker','compose']
    prefix+=['--project-directory',cwd,'-p',project]
    for file in files:
        path=Path(file) if Path(file).is_absolute() else Path(cwd)/file
        if not path.is_file() or path.is_symlink():raise ValueError('Compose配置不存在或为符号链接')
        prefix+=['-f',str(path)]
    rendered=command(prefix+['config'])
    match=re.search(r'^version:\s*[\'"]?([0-9.]+)',rendered,re.M)
    actual=command(prefix+['config','--hash='+service]).split()[-1]
    if actual!=labels.get('com.docker.compose.config-hash'):raise ValueError('Compose配置与当前容器不一致，请先核对原配置，避免更新镜像时带入其他改动')
    return dict(prefix=prefix,service=service,config_hash=digest(rendered),version=match.group(1) if match else None)


def image_reference(image,expected):
    value=json.loads(command(['docker','image','inspect',image]))[0]
    if value['Id']!=expected:raise ValueError('镜像名称对应的ID已变化，请重新准备镜像')
    if image.startswith('sha256:'):
        names=value.get('RepoTags') or value.get('RepoDigests') or []
        if not names:raise ValueError('旧镜像没有可用名称，请先为旧镜像补充标签')
        image=names[0]
    return image


def compose_prefix(prefix):
    root=Path.home()/'.local/share/data-platform/image-updates';result=[];index=0
    while index<len(prefix):
        if prefix[index]=='-f':
            path=Path(prefix[index+1])
            generated=path.parent.parent==root and re.fullmatch(r'[a-f0-9-]{36}',path.parent.name) and path.name.endswith(('-image.json','-restore.json','-labels.json'))
            if not generated:result.extend(prefix[index:index+2])
            index+=2
        else:result.append(prefix[index]);index+=1
    return result


def compose_image(text,service,value=None):
    lines=text.splitlines(keepends=True);start=None;indent=None
    for index,line in enumerate(lines):
        if re.fullmatch(r'services:\s*(?:#.*)?\r?\n?',line):start=index+1;break
    if start is None:raise ValueError('主Compose需使用services分块格式以安全更新image字段')
    for index in range(start,len(lines)):
        line=lines[index]
        if not line.strip() or line.lstrip().startswith('#'):continue
        spaces=len(line)-len(line.lstrip(' '))
        if spaces==0:break
        if indent is None:indent=spaces
        if spaces!=indent:continue
        if not re.fullmatch(r'\s*(?:'+re.escape(service)+r'|"'+re.escape(service)+r'"|\x27'+re.escape(service)+r'\x27):\s*(?:#.*)?\r?\n?',line):continue
        end=index+1
        while end<len(lines) and (not lines[end].strip() or lines[end].lstrip().startswith('#') or len(lines[end])-len(lines[end].lstrip(' '))>indent):end+=1
        body=[row for row in range(index+1,end) if lines[row].strip() and not lines[row].lstrip().startswith('#')]
        field_indent=min((len(lines[row])-len(lines[row].lstrip(' ')) for row in body),default=0)
        matches=[row for row in body if re.match(r'^ {'+str(field_indent)+r'}image\s*:',lines[row])]
        if len(matches)!=1:break
        row=matches[0];match=re.fullmatch(r'(\s*image\s*:\s*)([^\r\n]*)(\r?\n)?',lines[row]);old=match.group(2)
        if value is not None:lines[row]=match.group(1)+value+(match.group(3) or '')
        return ''.join(lines),old
    raise ValueError('主Compose所选服务必须有独立的image字段；不支持行内或继承的镜像配置')


def replace_compose(path,text):
    metadata=path.stat()
    with tempfile.NamedTemporaryFile(dir=str(path.parent),prefix=path.name+'.platform-',delete=False) as out:
        temp=Path(out.name)
        try:
            out.write(text.encode('utf-8'));out.flush();os.fsync(out.fileno())
            os.chmod(str(temp),metadata.st_mode&0o777)
            if hasattr(os,'chown'):os.chown(str(temp),metadata.st_uid,metadata.st_gid)
        except BaseException:temp.unlink();raise
    try:os.replace(str(temp),str(path))
    finally:
        if temp.exists():temp.unlink()


def compose_apply(info,stage,name,job,image,rollback):
    prefix=compose_prefix(info['prefix']);files=[Path(prefix[i+1]) for i in range(len(prefix)-1) if prefix[i]=='-f']
    if not files:raise ValueError('未找到主Compose配置')
    for path in files:
        if path.is_symlink() or any(p.is_symlink() for p in path.parents):raise ValueError('Compose配置不能经过符号链接')
    state_path=stage/(name+'-compose.json');operation='rollback' if rollback else 'activate'
    # Stable locks serialize shared Compose files across different update records.
    with ExitStack() as stack:
        if os.name=='posix':
            import fcntl
            locks=Path.home()/'.local/share/data-platform/image-updates/.locks';locks.mkdir(mode=0o700,parents=True,exist_ok=True)
            for path in sorted(set(files)):
                lock=stack.enter_context((locks/(digest(str(path))+'.lock')).open('a'));fcntl.flock(lock,fcntl.LOCK_EX)
        labels=stage/(name+'-labels.json')
        run=prefix+['-f',str(labels)]
        state=json.loads(state_path.read_text()) if state_path.exists() else None
        if state:
            if command(run+['config','--hash='+info['service']]).split()[-1]!=state['hash']:raise ValueError('Compose服务配置已被其他操作修改，拒绝覆盖或回滚')
        else:
            changes=[]
            for index,path in enumerate(files):
                text=path.read_bytes().decode('utf-8')
                try:_,old=compose_image(text,info['service'])
                except ValueError:
                    if index==0:raise
                    continue
                backup=stage/('compose-'+digest(str(path))+'.backup')
                if not backup.exists():
                    with backup.open('xb') as out:out.write(path.read_bytes())
                    backup.chmod(0o600)
                comment=re.search(r'\s+#.*$',old)
                changes.append(dict(path=str(path),before=old,after=image+(comment.group() if comment else '')))
            state=dict(changes=changes)
        previous=[];old_labels=labels.read_bytes() if labels.exists() else None
        try:
            for change in state['changes']:
                path=Path(change['path']);text=path.read_bytes().decode('utf-8');previous.append((path,text))
                value=change['before'] if rollback else change['after']
                updated,_=compose_image(text,info['service'],value)
                replace_compose(path,updated)
            data=dict(services={info['service']:dict(labels={'data-platform.image-update':job+':'+operation})})
            if info.get('version'):data['version']=info['version']
            if prefix[0]=='docker':data['services'][info['service']]['pull_policy']='never'
            write(labels,data)
            _,effective=compose_image(command(run+['config']),info['service'])
            if re.sub(r'\s+#.*$','',effective).strip().strip('\"\x27')!=image:raise ValueError('Compose合并后的镜像与目标不一致，拒绝重建服务')
            service_hash=command(run+['config','--hash='+info['service']]).split()[-1]
        except BaseException:
            for path,text in reversed(previous):replace_compose(path,text)
            if old_labels is not None:labels.write_bytes(old_labels)
            elif labels.exists():labels.unlink()
            raise
        state.update(hash=service_hash,operation=operation);write(state_path,state)
        write(stage/(name+'-intent.json'),dict(operation=operation))
        command(run+['up','-d','--no-deps','--no-build','--force-recreate',info['service']],240)


def get_workload(target):return json.loads(command(['kubectl','get',target['workload_kind'],target['workload'],'-n',target['namespace'],'-o','json']))


def template_hash(workload):return digest(workload['spec']['template'])


def inventory(target):
    if target['environment']!='k8s':
        instances=[]
        for name in target['containers']:
            value=docker_inspect(name);row=docker_summary(value);row['compose']=compose(value)
            if value.get('HostConfig',{}).get('AutoRemove'):raise ValueError('自动删除容器无法保留旧实例，请先改为持久服务')
            instances.append(row)
        return dict(instances=instances,desired=len(instances),controller=None,rollout_complete=True)
    workload=get_workload(target);spec=workload['spec'];status=workload.get('status',{});desired=spec.get('replicas',1)
    strategy=spec.get('updateStrategy' if target['workload_kind']=='StatefulSet' else 'strategy',{})
    if strategy.get('type','RollingUpdate')!='RollingUpdate' or strategy.get('rollingUpdate',{}).get('partition',0) or spec.get('paused'):
        raise ValueError('镜像更新要求未暂停的RollingUpdate工作负载且partition为0')
    if not 0<desired<=500:raise ValueError('目标副本数量须为1至500')
    container=next((c for c in spec['template']['spec']['containers'] if c['name']==target['container']),None)
    if not container:raise ValueError('工作负载没有所选容器')
    owners={workload['metadata']['uid']}
    if target['workload_kind']=='Deployment':
        sets=json.loads(command(['kubectl','get','rs','-n',target['namespace'],'-o','json']))['items']
        owners={r['metadata']['uid'] for r in sets if any(o['uid']==workload['metadata']['uid'] for o in r['metadata'].get('ownerReferences',[]))}
    pods=json.loads(command(['kubectl','get','pods','-n',target['namespace'],'-o','json']))['items'];instances=[]
    for pod in pods:
        if pod['metadata'].get('deletionTimestamp') or not any(o['uid'] in owners for o in pod['metadata'].get('ownerReferences',[])):continue
        c=next((c for c in pod['spec']['containers'] if c['name']==target['container']),None)
        state=next((c for c in pod.get('status',{}).get('containerStatuses',[]) if c['name']==target['container']),{})
        ready=any(c.get('type')=='Ready' and c.get('status')=='True' for c in pod.get('status',{}).get('conditions',[]))
        instances.append(dict(name=pod['metadata']['name'],uid=pod['metadata']['uid'],image=c['image'] if c else '',image_id=state.get('imageID',''),ready=ready and state.get('ready',False),health='pod_readiness'))
    return dict(instances=instances,desired=desired,controller=dict(uid=workload['metadata']['uid'],image=container['image'],pull_policy=container.get('imagePullPolicy'),template_hash=template_hash(workload)),
        token=spec['template']['metadata'].get('annotations',{}).get('data-platform/image-update'),
        rollout_complete=status.get('observedGeneration',0)>=workload['metadata']['generation'] and status.get('updatedReplicas',0)==desired and status.get('readyReplicas',0)==desired and status.get('replicas',0)==desired and
            (target['workload_kind']!='StatefulSet' or status.get('currentRevision')==status.get('updateRevision')))


def k8s_apply(target,baseline,image,job,rollback,expected_image=None):
    current=get_workload(target);meta=current['metadata'];template=current['spec']['template']
    token=job+(':rollback' if rollback else ':activate');annotation=template['metadata'].get('annotations',{}).get('data-platform/image-update')
    index=next(i for i,c in enumerate(template['spec']['containers']) if c['name']==target['container'])
    if meta['uid']!=baseline['uid']:raise ValueError('工作负载已被重建，拒绝覆盖')
    if annotation==token and template['spec']['containers'][index]['image']==image:
        if template['spec']['containers'][index].get('imagePullPolicy')!='Never':raise ValueError('工作负载的本地缓存策略已被修改，请人工核对')
        return dict(ok=True)
    if rollback:
        if annotation!=job+':activate' or template['spec']['containers'][index]['image']!=expected_image:raise ValueError('当前镜像由其他操作管理或已发生变化，拒绝恢复旧记录')
    elif template_hash(current)!=baseline['template_hash']:raise ValueError('工作负载配置已变化，请重新检查后新建任务')
    patch=[dict(op='test',path='/metadata/uid',value=meta['uid']),dict(op='test',path='/metadata/resourceVersion',value=meta['resourceVersion']),
        dict(op='replace',path='/spec/template/spec/containers/'+str(index)+'/image',value=image),
        dict(op='add',path='/spec/template/spec/containers/'+str(index)+'/imagePullPolicy',value='Never')]
    if 'annotations' not in template['metadata']:patch.append(dict(op='add',path='/spec/template/metadata/annotations',value={}))
    patch.append(dict(op='add',path='/spec/template/metadata/annotations/data-platform~1image-update',value=token))
    write(folder(job)/'k8s-intent.json',dict(operation='rollback' if rollback else 'activate'))
    command(['kubectl','patch',target['workload_kind'],target['workload'],'-n',target['namespace'],'--type=json','-p',json.dumps(patch)])
    return dict(ok=True)


def clone_config(current,image,job):
    host=copy.deepcopy(current['HostConfig']);mode=host.get('NetworkMode','bridge')
    if mode.startswith('container:') or host.get('Links') or host.get('AutoRemove') or host.get('VolumesFrom') or any(str(host.get(k,'')).startswith('container:') for k in ('IpcMode','PidMode')):
        raise ValueError('容器共享其他容器资源，需通过原编排配置更新')
    config=copy.deepcopy(current['Config']);config['Image']=image;config['Labels']={**(config.get('Labels') or {}),'data-platform.image-update':job}
    mounts=host.get('Mounts') or [];bound={s.split(':')[1] for s in host.get('Binds') or [] if ':' in s}
    bound.update(m.get('Target') for m in mounts)
    for mount in current.get('Mounts',[]):
        if mount['Type']=='volume':
            specified=next((m for m in mounts if m.get('Target')==mount['Destination']),None)
            if specified is not None:specified['Source']=mount['Name']
            elif mount['Destination'] not in bound:
                host['Binds']=[*(host.get('Binds') or []),mount['Name']+':'+mount['Destination']+(':rw' if mount.get('RW') else ':ro')]
    networks={}
    if mode not in ('host','none'):
        for name,net in current.get('NetworkSettings',{}).get('Networks',{}).items():
            networks[name]={key:net[key] for key in ('IPAMConfig','Aliases','Links','DriverOpts') if net.get(key) is not None}
    return dict(config,HostConfig=host,NetworkingConfig=dict(EndpointsConfig=networks))


class UnixHTTP(http.client.HTTPConnection):
    def __init__(self,path):super().__init__('localhost',timeout=120);self.path=path
    def connect(self):
        self.sock=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM);self.sock.settimeout(self.timeout);self.sock.connect(self.path)


def engine(method,path,body=None):
    endpoint=json.loads(command(['docker','context','inspect','--format','{{json .Endpoints.docker.Host}}']))
    if not endpoint.startswith('unix://'):raise ValueError('原生Docker更新要求本机Unix socket，远程Docker context请使用Compose')
    version=command(['docker','version','--format','{{.Server.APIVersion}}'])
    connection=UnixHTTP(endpoint[len('unix://'):])
    try:
        connection.request(method,'/v'+version+path,body=json.dumps(body).encode() if body is not None else None,headers={'Content-Type':'application/json'})
        response=connection.getresponse();data=response.read()
        if response.status>=300:raise ValueError('Docker Engine操作失败（HTTP '+str(response.status)+'），旧实例和配置已保留')
        return json.loads(data) if data else {}
    finally:connection.close()


def pull_alive(receipt):
    try:
        fields=(Path('/proc')/str(receipt['pid'])/'stat').read_text().rsplit(')',1)[1].split()
        return fields[0]!='Z' and fields[19]==receipt['started']
    except (OSError,KeyError):return False


def prepare(target,job,image,baseline,retry=False):
    stage=folder(job);receipt_path=stage/'receipt.json'
    if receipt_path.exists():
        receipt=json.loads(receipt_path.read_text())
        if receipt['image']!=image:raise ValueError('镜像准备记录与本次版本不一致')
        if receipt.get('prepared'):return receipt
        result=stage/'pull-result.json'
        failed=result.exists() and json.loads(result.read_text())['code']!=0
        stopped=not result.exists() and not pull_alive(receipt)
        if failed or stopped:
            if not retry:raise ValueError('镜像拉取失败或中断，可重试准备；请检查仓库权限和网络，私有日志保存在服务器操作目录')
            if any(stage.glob('*-intent.json')):raise ValueError('已操作服务，不能重新准备覆盖原快照')
            if result.exists():result.unlink()
            receipt_path.unlink()
            return prepare(target,job,image,baseline)
        if not result.exists():return dict(pending=True)
        value=json.loads(command(['docker','image','inspect',image]))[0]
        receipt.update(prepared=True,image_id=value['Id'],pending=False);write(receipt_path,receipt);return receipt
    if target['environment']=='k8s':
        raise ValueError('K8s镜像必须先通过本环境Ansible剧本分发到全部节点')
    for instance in baseline['instances']:
        current=docker_inspect(instance['name'])
        if docker_summary(current)['fingerprint']!=instance['fingerprint'] or current['Id']!=instance['uid']:raise ValueError('容器配置已变化，请新建镜像更新记录')
        write(stage/(instance['name']+'.json'),current)
    write(receipt_path,dict(image=image,pending=True,prepared=False))
    code="import subprocess,json,pathlib; p=pathlib.Path("+repr(str(stage))+ ");\nf=(p/'pull.log').open('wb');r=subprocess.run(['docker','pull',"+repr(image)+"],stdout=f,stderr=f);f.close();q=p/'pull-result.tmp';q.write_text(json.dumps({'code':r.returncode}));q.replace(p/'pull-result.json')"
    process=subprocess.Popen([sys.executable,'-c',code],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True)
    try:started=(Path('/proc')/str(process.pid)/'stat').read_text().rsplit(')',1)[1].split()[19]
    except OSError:started=''
    write(receipt_path,dict(image=image,pending=True,prepared=False,pid=process.pid,started=started))
    return dict(pending=True)


def docker_apply(target,instance,job,receipt,rollback):
    stage=folder(job);name=instance['name'];saved=json.loads((stage/(name+'.json')).read_text());current=existing_container(name)
    owner=(current['Config'].get('Labels') or {}).get('data-platform.image-update') if current else None
    expected=saved['Image'] if rollback else receipt['image_id'];token=job+(':rollback' if rollback else ':activate')
    reference=image_reference(receipt['image'],expected) if not rollback else None
    if owner==token and current['Image']==expected and (rollback or current['Config']['Image']==reference):
        if not current['State']['Running']:command(['docker','start',current['Id']],120)
        return dict(ok=True,uid=current['Id'])
    info=instance.get('compose');backup=stage/(name+'-backup.json');intent=stage/(name+'-intent.json')
    if rollback:
        if not intent.exists() and not backup.exists():raise ValueError('此服务没有开始切换镜像，无需恢复')
        if current and current['Id']!=saved['Id'] and (owner!=job+':activate' or current['Image']!=receipt['image_id']):raise ValueError('当前Docker实例不属于此更新，拒绝恢复旧记录')
    elif not current or current['Id']!=instance['uid'] or docker_summary(current)['fingerprint']!=instance['fingerprint']:raise ValueError('Docker实例或配置已变化，拒绝覆盖；如果更新被中断，请先恢复旧实例')
    if info:
        state=stage/(name+'-compose.json')
        if not state.exists():
            if not rollback:
                actual=compose(current)
                if actual['prefix']!=info['prefix'] or actual['service']!=info['service']:raise ValueError('Compose配置来源已变化')
            elif digest(command(info['prefix']+['config']))!=info['config_hash']:raise ValueError('原Compose配置已变化，拒绝将其他配置变更带入恢复')
        reference=reference or image_reference(saved['Config']['Image'],expected)
        compose_apply(info,stage,name,job,reference,rollback)
    elif rollback:
        old=json.loads(backup.read_text());original=docker_inspect(old['uid'])
        if original['Id']!=saved['Id'] or original['Image']!=saved['Image']:raise ValueError('保留的旧实例身份或镜像已变化')
        if current and current['Id']!=saved['Id']:
            command(['docker','stop','--time','30',current['Id']],90)
            command(['docker','update','--restart=no',current['Id']])
            engine('POST','/containers/'+quote(current['Id'])+'/rename?name='+quote(name+'-platform-withdrawn-'+job[:8]))
            for network in current.get('NetworkSettings',{}).get('Networks',{}):engine('POST','/networks/'+quote(network)+'/disconnect',dict(Container=current['Id']))
        if not current or current['Id']!=saved['Id']:engine('POST','/containers/'+quote(old['uid'])+'/rename?name='+quote(name))
        for network,endpoint in clone_config(saved,saved['Image'],token)['NetworkingConfig']['EndpointsConfig'].items():
            if network not in original.get('NetworkSettings',{}).get('Networks',{}):engine('POST','/networks/'+quote(network)+'/connect',dict(Container=old['uid'],EndpointConfig=endpoint))
        engine('POST','/containers/'+quote(old['uid'])+'/update',dict(RestartPolicy=saved['HostConfig'].get('RestartPolicy',{})))
        command(['docker','start',old['uid']],120)
    else:
        config=clone_config(saved,reference,token)
        backup_name=name+'-platform-old-'+job[:8]
        write(intent,dict(operation='activate'))
        write(stage/(name+'-backup.json'),dict(uid=current['Id'],name=backup_name))
        command(['docker','update','--restart=no',current['Id']])
        command(['docker','stop','--time','30',current['Id']],90)
        engine('POST','/containers/'+quote(current['Id'])+'/rename?name='+quote(backup_name))
        for network in config['NetworkingConfig']['EndpointsConfig']:engine('POST','/networks/'+quote(network)+'/disconnect',dict(Container=current['Id']))
        created=engine('POST','/containers/create?name='+quote(name),config)
        engine('POST','/containers/'+quote(created['Id'])+'/start')
    return dict(ok=True,uid=saved['Id'] if rollback and not info else docker_inspect(name)['Id'])


def dispatch(payload):
    action=payload['action'];target=payload['target'];job=payload['job_id'];stage=None
    if action=='inventory':return inventory(target)
    if action=='prepare':return prepare(target,job,payload['image'],payload['baseline'],payload.get('retry',False))
    if action=='receipt':
        stage=folder(job,create=False);path=stage/'receipt.json'
        return dict(json.loads(path.read_text()),mutated=any(stage.glob('*-intent.json'))) if path.exists() else {}
    if action=='apply':
        if target['environment']=='k8s':
            receipt=json.loads((folder(job,create=False)/'receipt.json').read_text())
            if not receipt.get('pinned_image'):raise ValueError('缺少全部节点Ansible预分发收据，拒绝直接更新K8s镜像')
            return k8s_apply(target,payload['baseline']['controller'],payload['image'],job,payload.get('rollback',False),receipt['pinned_image'])
        return docker_apply(target,payload['instance'],job,payload['receipt'],payload.get('rollback',False))
    raise ValueError('未知镜像更新动作')


if __name__=='__main__':
    try:print(json.dumps(dict(success=True,result=dispatch(json.loads(base64.b64decode(sys.argv[1])))),ensure_ascii=False))
    except Exception as error:print(json.dumps(dict(success=False,error=str(error)[-1500:]),ensure_ascii=False))
