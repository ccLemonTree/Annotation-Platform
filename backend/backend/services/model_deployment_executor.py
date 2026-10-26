"""Fixed SSH actions. Standard library only, including on Python 3.6 hosts."""
import base64
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
from uuid import UUID
import zipfile


def sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024**2),b''):digest.update(chunk)
    return digest.hexdigest()


def relative(value):
    if not isinstance(value,str) or not value or len(value)>1000 or '\\' in value or ':' in value or any(ord(c)<32 for c in value) or PurePosixPath(value).is_absolute() or any(p in ('','.','..') for p in value.split('/')):
        raise ValueError('模型包相对路径无效')
    return value


def real(path):
    path=Path(path)
    if not path.is_absolute() or path==Path(path.anchor) or any(p.is_symlink() for p in [path]+list(path.parents)):
        raise ValueError('目录必须为具体绝对路径且不能经过符号链接')
    return path.resolve()


def files(folder):
    result=[]
    for p in sorted(folder.rglob('*')):
        if p.is_symlink() or not (p.is_file() or p.is_dir()):raise ValueError('模型目录包含链接或非普通文件')
        if p.is_file():result.append(p)
        if len(result)>20000:raise ValueError('模型文件超过20000个')
    return result


def inspect_source(spec):
    root=real(spec['root']);path=root/relative(spec['artifact_path'])
    if any(p.is_symlink() for p in [path]+list(path.parents)):raise ValueError('来源包含符号链接')
    if spec['runtime']=='triton_plan':
        manifest=json.loads((root/'result.json').read_text(encoding='utf-8'))
        row=next((r for r in manifest['files'] if r['path']==spec['artifact_path']),None)
        if manifest['format']!='tensorrt' or path.suffix not in ('.engine','.plan') or row is None:
            raise ValueError('请选择完成TensorRT导出的engine/plan产物')
        if not path.is_file() or path.stat().st_size!=row['bytes'] or sha(path)!=row['sha256']:
            raise ValueError('导出模型校验失败，文件已变化')
        if not manifest.get('gpu_name') or not (manifest.get('versions',{}).get('tensorrt_runtime') or manifest.get('versions',{}).get('tensorrt')):
            raise ValueError('导出缺少GPU或TensorRT版本信息')
        entries=[dict(path='1/model.plan',source=str(path),bytes=row['bytes'],sha256=row['sha256'])]
    else:
        if not path.is_dir() or not (path/'config.json').is_file():raise ValueError('请选择含config.json的完整模型目录')
        paths=files(path)
        if (path/'adapter_config.json').exists():raise ValueError('不能下发adapter，请先合并为完整模型')
        if not any(p.suffix=='.safetensors' or p.name.startswith('pytorch_model') and p.suffix=='.bin' for p in paths):
            raise ValueError('完整模型缺少权重')
        if not any(p.name in ('tokenizer.json','tokenizer.model','vocab.json','vocab.txt') for p in paths):
            raise ValueError('完整模型缺少tokenizer文件')
        for index in path.glob('*.index.json'):
            weight_map=json.loads(index.read_text(encoding='utf-8')).get('weight_map',{})
            if not weight_map or any(not (path/relative(name)).is_file() for name in weight_map.values()):raise ValueError('模型缺少权重分片')
        prefix='1/model/' if spec['runtime']=='triton_vllm' else ''
        entries=[dict(path=prefix+p.relative_to(path).as_posix(),source=str(p),bytes=p.stat().st_size,sha256=sha(p)) for p in paths]
        manifest={}
    total=sum(row['bytes'] for row in entries)
    if not total or total>512*1024**3:raise ValueError('模型大小无效或超过512GiB')
    return dict(files=entries,bytes=total,gpu_name=manifest.get('gpu_name'),versions=manifest.get('versions',{}))


def pack_source(spec,job):
    UUID(job);receipt=inspect_source(spec)
    folder=real(real(spec['root']).parent/'.model-deployments'/job);folder.mkdir(parents=True,exist_ok=True)
    archive=folder/'bundle.zip'
    with zipfile.ZipFile(str(archive),'w',compression=zipfile.ZIP_STORED,allowZip64=True) as out:
        for row in receipt['files']:
            p=Path(row['source']);out.write(str(p),row['path'])
            if p.stat().st_size!=row['bytes'] or sha(p)!=row['sha256']:raise ValueError('打包期间来源模型发生变化')
        bundle={**receipt,'files':[{k:v for k,v in r.items() if k!='source'} for r in receipt['files']]}
        out.writestr('bundle.json',json.dumps(bundle,ensure_ascii=False))
    return dict(path=str(archive),bytes=archive.stat().st_size,sha256=sha(archive))


def workspace(target,job):
    UUID(job);repo=real(target['repository'])
    return real(repo.parent/('.'+repo.name+'-platform-deployments')/job)


def destination(target):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}',target['model_name']):raise ValueError('模型名无效')
    return real(target['repository'])/target['model_name']


def target_state(target):
    path=destination(target)
    if path.is_symlink():raise ValueError('目标模型不能为符号链接')
    rows=[]
    if path.exists():
        if not path.is_dir():raise ValueError('目标模型路径不是目录')
        for p in files(path):
            s=p.stat();rows.append([p.relative_to(path).as_posix(),s.st_size,s.st_mtime_ns])
    return dict(exists=path.exists(),fingerprint=hashlib.sha256(json.dumps(rows).encode()).hexdigest(),
        path=str(path),free_bytes=shutil.disk_usage(str(path.parent)).free,
        staging_same_device=path.parent.stat().st_dev==path.parent.parent.stat().st_dev)


def write_json(path,value):
    temp=path.with_suffix('.tmp');temp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8');os.replace(str(temp),str(path))


@contextmanager
def locked(target):
    folder=workspace(target,str(UUID(int=0))).parent;folder.mkdir(parents=True,exist_ok=True)
    with (folder/(target['model_name']+'.lock')).open('a+b') as stream:
        if os.name=='nt':
            import msvcrt
            stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            if os.name=='nt':stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(stream,fcntl.LOCK_UN)


def receive(target,job,digest):
    stage=workspace(target,job);archive=stage/'input.zip';release=stage/'release'
    if sha(archive)!=digest:raise ValueError('传输包SHA256校验失败')
    with locked(target),zipfile.ZipFile(str(archive)) as inp:
        infos=inp.infolist()
        if len(infos)>20001 or len({i.filename for i in infos})!=len(infos):raise ValueError('模型包文件数无效或重复')
        for i in infos:
            relative(i.filename)
            if i.is_dir() or stat.S_ISLNK(i.external_attr>>16):raise ValueError('模型包不能包含链接或目录条目')
        if 'bundle.json' not in inp.namelist() or inp.getinfo('bundle.json').file_size>8*1024**2:raise ValueError('模型包缺少有效清单')
        bundle=json.loads(inp.read('bundle.json'));rows=bundle['files']
        if len({r['path'] for r in rows})!=len(rows) or set(inp.namelist())!={'bundle.json'}|{r['path'] for r in rows}:
            raise ValueError('模型包与清单不符')
        total=sum(r['bytes'] for r in rows)
        if total<=0 or total>512*1024**3 or shutil.disk_usage(str(stage)).free<total+64*1024**2:raise ValueError('目标剩余空间不足或模型包过大')
        if release.exists():
            # Only this owned staging directory is replaceable; live files are untouched.
            if release.is_symlink():raise ValueError('暂存目录不能为链接')
            shutil.rmtree(str(release))
        release.mkdir()
        for row in rows:
            name=relative(row['path']);info=inp.getinfo(name)
            if info.file_size!=row['bytes']:raise ValueError('模型文件大小与清单不符')
            out=release/name;out.parent.mkdir(parents=True,exist_ok=True)
            with inp.open(name) as source,out.open('wb') as sink:shutil.copyfileobj(source,sink,1024**2)
            if sha(out)!=row['sha256']:raise ValueError('模型文件校验失败')
        if target['runtime']!='vllm':
            old=destination(target);config=target.get('triton_config') or ((old/'config.pbtxt').read_text(encoding='utf-8') if (old/'config.pbtxt').is_file() else '')
            expected='platform: "tensorrt_plan"' if target['runtime']=='triton_plan' else 'backend: "vllm"'
            if not config:config='name: "'+target['model_name']+'"\n'+expected+'\nmax_batch_size: 0\ninstance_group: [{count:1 kind:KIND_GPU gpus:[0]}]\n'
            clean=re.sub(r'#.*','',config)
            backend='tensorrt_plan' if target['runtime']=='triton_plan' else 'vllm'
            if not re.search(r'(?:platform|backend)\s*:\s*[\'\"]'+backend+r'[\'\"]',clean):raise ValueError('Triton配置backend与部署类型不符')
            declared=re.search(r'\bname\s*:\s*[\'\"]([^\'\"]+)',clean)
            if declared and declared.group(1)!=target['model_name']:raise ValueError('Triton配置模型名不一致')
            (release/'config.pbtxt').write_text(config,encoding='utf-8')
            if target['runtime']=='triton_vllm':
                old_config=old/'1/model.json'
                options=target.get('runtime_config') or (json.loads(old_config.read_text()) if old_config.is_file() else {})
                options={**options,'model':target['internal_model_path']}
                write_json(release/'1/model.json',options)
        write_json(release/'.platform-deployment.json',dict(id=job))
        content=[dict(path=p.relative_to(release).as_posix(),bytes=p.stat().st_size,sha256=sha(p)) for p in files(release)]
        receipt=dict(sha256=digest,files=content,bytes=total,state='staged')
        write_json(stage/'receipt.json',receipt)
    return receipt


def verify_release(folder,receipt):
    folder=real(folder)
    if {p.relative_to(folder).as_posix() for p in files(folder)}!={r['path'] for r in receipt['files']}:raise ValueError('暂存模型清单已变化')
    for row in receipt['files']:
        p=folder/relative(row['path'])
        if p.stat().st_size!=row['bytes'] or sha(p)!=row['sha256']:raise ValueError('暂存模型校验失败')


def promote(target,job,baseline):
    stage=workspace(target,job);live=destination(target)
    with locked(target):
        receipt=json.loads((stage/'receipt.json').read_text())
        marker=live/'.platform-deployment.json'
        if marker.is_file() and json.loads(marker.read_text()).get('id')==job:
            verify_release(live,receipt);return dict(state='promoted',had_previous=(stage/'previous').exists())
        current=target_state(target)
        if (current['exists'],current['fingerprint'])!=(baseline['exists'],baseline['fingerprint']):raise ValueError('在线模型已变化，请重新创建下发任务')
        verify_release(stage/'release',receipt)
        if (stage/'previous').exists():raise ValueError('此任务已保留旧版本，不能覆盖备份')
        receipt.update(state='promoting',had_previous=live.exists(),previous_state=baseline);write_json(stage/'receipt.json',receipt)
        if live.exists():os.replace(str(live),str(stage/'previous'))
        try:os.replace(str(stage/'release'),str(live))
        except BaseException:
            if (stage/'previous').exists():os.replace(str(stage/'previous'),str(live))
            raise
        receipt['state']='promoted';write_json(stage/'receipt.json',receipt)
        return dict(state='promoted',had_previous=receipt['had_previous'])


def restore(target,job):
    stage=workspace(target,job);live=destination(target)
    with locked(target):
        receipt=json.loads((stage/'receipt.json').read_text())
        previous=receipt.get('previous_state')
        if receipt['state']=='restored':
            current=target_state(target)
            if previous and (current['exists'],current['fingerprint'])!=(previous['exists'],previous['fingerprint']):raise ValueError('恢复后的在线模型已变化，拒绝重复恢复')
            return dict(state='restored',had_previous=receipt['had_previous'])
        if receipt['state'] in ('promoting','restoring') and not live.exists():
            if (stage/'previous').exists():os.replace(str(stage/'previous'),str(live))
            elif receipt.get('had_previous'):raise ValueError('旧模型目录缺失，保留现场等待核对')
            receipt['state']='restored';write_json(stage/'receipt.json',receipt)
            return dict(state='restored',had_previous=receipt.get('had_previous',False))
        if receipt['state']=='restoring' and previous:
            current=target_state(target)
            if (current['exists'],current['fingerprint'])==(previous['exists'],previous['fingerprint']):
                receipt['state']='restored';write_json(stage/'receipt.json',receipt)
                return dict(state='restored',had_previous=receipt['had_previous'])
        marker=live/'.platform-deployment.json'
        if not marker.is_file() or json.loads(marker.read_text()).get('id')!=job:raise ValueError('在线模型属于其他发布，拒绝覆盖')
        if (stage/'withdrawn').exists():raise ValueError('恢复状态待核对，旧目录已保留')
        receipt['state']='restoring';write_json(stage/'receipt.json',receipt)
        os.replace(str(live),str(stage/'withdrawn'))
        try:
            if (stage/'previous').exists():os.replace(str(stage/'previous'),str(live))
        except BaseException:
            os.replace(str(stage/'withdrawn'),str(live));raise
        receipt['state']='restored';write_json(stage/'receipt.json',receipt)
        return dict(state='restored',had_previous=receipt.get('had_previous',False))


def command(args,timeout=60):
    result=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,universal_newlines=True,timeout=timeout)
    if result.returncode:raise ValueError('目标命令失败：'+result.stderr[-1200:])
    return result.stdout.strip()


def option(args,key,default=None):
    for i,value in enumerate(args):
        if value.startswith(key+'='):return value[len(key)+1:]
        if value==key and i+1<len(args):return args[i+1]
    return default


def execution(target,instance):
    if target['environment']=='k8s':
        pod=json.loads(command(['kubectl','get','pod',instance['name'],'-n',target['namespace'],'-o','json']))
        if pod['metadata']['uid']!=instance['uid']:raise ValueError('Pod已变化，请重新核对实例')
        state=next((s for s in pod['status'].get('containerStatuses',[]) if s['name']==target['container']),{})
        if state.get('imageID')!=instance['image_id']:raise ValueError('Pod推理镜像已变化，请重新核对')
        return ['kubectl','exec','-n',target['namespace'],instance['name'],'-c',target['container'],'--']
    current=json.loads(command(['docker','inspect',instance['name']]))[0]
    if current['Id']!=instance['uid'] or not current['State']['Running']:raise ValueError('Docker容器已变化或未运行')
    return ['docker','exec',instance['name']]


def model_mount(target,args,mounts):
    if target['runtime']=='vllm':
        model=option(args,'--model') or next((a for a in args if a.startswith('/models/')),None)
    else:model=option(args,'--model-repository')
    if not model:raise ValueError('无法从启动参数识别模型路径，请使用显式模型路径')
    if target['runtime']!='vllm' and option(args,'--model-control-mode')!='explicit':raise ValueError('Triton必须使用explicit模型控制模式')
    if target['runtime']=='triton_vllm' and not any(option(args,'--load-model')==name or '--load-model='+name in args for name in ('*',target['model_name'])):
        raise ValueError('大模型重启生效要求启动参数--load-model包含目标模型或*')
    expected=str(destination(target)) if target['runtime']=='vllm' else target['repository']
    matching=[]
    for mount in mounts:
        root=mount['destination'].rstrip('/')
        if model==root or model.startswith(root+'/'):
            mapped=mount['source'].rstrip('/')+model[len(root):]
            candidates=[mapped]
            if mount.get('nfs4'):candidates.extend(root.rstrip('/')+mapped for root in target.get('nfs4_roots',[]))
            if expected in candidates:matching.append(mount)
    if len(matching)!=1:raise ValueError('推理模型挂载与文件服务器目标路径不一致')
    port=int(option(args,'--port' if target['runtime']=='vllm' else '--http-port','8000'))
    if not 0<port<65536:raise ValueError('推理HTTP端口无效')
    return dict(port=port,served_model=option(args,'--served-model-name',model),
        internal_model_path=model.rstrip('/')+'/'+target['model_name']+'/1/model' if target['runtime']=='triton_vllm' else model)


def inventory(target):
    instances=[];controller=None;volumes_cache={}
    if target['environment']=='k8s':
        ns=target['namespace'];kind=target['workload_kind'];name=target['workload']
        controller=json.loads(command(['kubectl','get',kind,name,'-n',ns,'-o','json']))
        desired=controller['spec'].get('replicas',1)
        owner_ids={controller['metadata']['uid']}
        if kind=='Deployment':
            sets=json.loads(command(['kubectl','get','rs','-n',ns,'-o','json']))['items']
            owner_ids={r['metadata']['uid'] for r in sets if any(o['uid']==controller['metadata']['uid'] for o in r['metadata'].get('ownerReferences',[]))}
        pods=json.loads(command(['kubectl','get','pods','-n',ns,'-o','json']))['items']
        for pod in pods:
            if pod['metadata'].get('deletionTimestamp') or not any(o['uid'] in owner_ids for o in pod['metadata'].get('ownerReferences',[])):continue
            c=next((c for c in pod['spec']['containers'] if c['name']==target['container']),None)
            if c is None:raise ValueError('工作负载缺少指定推理容器')
            mounts=[]
            for m in c.get('volumeMounts',[]):
                volume=next(v for v in pod['spec']['volumes'] if v['name']==m['name'])
                nfs=volume.get('nfs')
                if 'persistentVolumeClaim' in volume:
                    claim=volume['persistentVolumeClaim']['claimName']
                    if claim not in volumes_cache:
                        pvc=json.loads(command(['kubectl','get','pvc',claim,'-n',ns,'-o','json']))
                        pv=json.loads(command(['kubectl','get','pv',pvc['spec']['volumeName'],'-o','json']));volumes_cache[claim]=pv['spec'].get('nfs')
                    nfs=volumes_cache[claim]
                if nfs and nfs['server'] in target.get('file_addresses',[]):
                    if m.get('subPathExpr'):raise ValueError('暂不支持动态subPathExpr模型挂载')
                    nfs4=False
                    if target.get('nfs4_roots'):
                        mount_table=command(['kubectl','exec','-n',ns,pod['metadata']['name'],'-c',target['container'],'--','cat','/proc/mounts'])
                        for line in mount_table.splitlines():
                            fields=line.split()
                            if len(fields)>3 and fields[1]==m['mountPath'] and (fields[2]=='nfs4' or re.search(r'(?:^|,)vers=4(?:\.|,|$)',fields[3])):nfs4=True
                    mounts.append(dict(source=nfs['path'].rstrip('/')+('/'+m['subPath'] if m.get('subPath') else ''),destination=m['mountPath'],nfs4=nfs4))
            args=c.get('command',[])+c.get('args',[])
            details=model_mount(target,args,mounts)
            state=next((s for s in pod['status'].get('containerStatuses',[]) if s['name']==target['container']),{})
            instances.append(dict(name=pod['metadata']['name'],uid=pod['metadata']['uid'],image=c['image'],image_id=state.get('imageID',''),
                ready=pod['status'].get('phase')=='Running' and state.get('ready',False),**details))
    else:
        desired=len(target['containers'])
        for c in json.loads(command(['docker','inspect']+target['containers'])):
            args=(c['Config'].get('Entrypoint') or [])+(c['Config'].get('Cmd') or [])
            mounts=[dict(source=m['Source'],destination=m['Destination']) for m in c['Mounts'] if m['Type']=='bind']
            details=model_mount(target,args,mounts)
            instances.append(dict(name=c['Name'].lstrip('/'),uid=c['Id'],image=c['Config']['Image'],image_id=c['Image'],ready=c['State']['Running'],**details))
    if not 0<desired<=500:raise ValueError('目标副本数为0或超过500，拒绝启动已停止服务')
    internal={i['internal_model_path'] for i in instances}
    if len(internal)>1:raise ValueError('多个实例的模型挂载路径不一致')
    identity=dict(uid=controller['metadata']['uid'],kind=target['workload_kind'],name=target['workload']) if controller else None
    result=dict(instances=instances,desired=desired,controller=identity,internal_model_path=next(iter(internal),None))
    if controller:
        spec=controller['spec'];status=controller.get('status',{})
        if target['runtime']!='triton_plan':
            if target['workload_kind']=='StatefulSet' and (spec.get('updateStrategy',{}).get('type','RollingUpdate')!='RollingUpdate' or spec.get('updateStrategy',{}).get('rollingUpdate',{}).get('partition',0)):
                raise ValueError('vLLM自动生效要求完整RollingUpdate策略，不能使用OnDelete或分区更新')
        result['rollout_token']=spec['template']['metadata'].get('annotations',{}).get('data-platform/model-deployment')
        result['rollout_complete']=(status.get('observedGeneration',0)>=controller['metadata']['generation'] and
            status.get('updatedReplicas',0)==desired and status.get('readyReplicas',0)==desired and
            (target['workload_kind']!='StatefulSet' or status.get('currentRevision')==status.get('updateRevision')))
    return result


def compatibility(target,instance):
    code="import ctypes,ctypes.util,json,subprocess;v=ctypes.CDLL(ctypes.util.find_library('nvinfer')).getInferLibVersion();d=10000 if v>=10000 else 1000;print(json.dumps({'tensorrt':'{}.{}.{}'.format(v//d,(v%d)//100,v%100),'gpus':subprocess.check_output(['nvidia-smi','--query-gpu=name','--format=csv,noheader']).decode().strip().splitlines()}))"
    return json.loads(command(execution(target,instance)+['python3','-c',code],90))


def probe(target,instance,action='ready',absent=False):
    name=target['model_name']
    path='/v1/models' if target['runtime']=='vllm' else '/v2/models/'+name+'/ready'
    if action in ('load','unload'):path='/v2/repository/models/'+name+'/'+action
    missing=absent and action=='ready'
    if missing:path='/v2/repository/index'
    request=dict(url='http://127.0.0.1:'+str(instance['port'])+path,method='POST' if missing or action in ('load','unload') else 'GET',body={'ready':True} if missing else {})
    code="import json,urllib.request,urllib.error;d=json.loads("+repr(json.dumps(request))+");\ntry:\n r=urllib.request.urlopen(urllib.request.Request(d['url'],data=json.dumps(d['body']).encode() if d['method']=='POST' else None,headers={'Content-Type':'application/json'},method=d['method']),timeout=180);print(json.dumps({'ok':True,'body':r.read(1048576).decode()}))\nexcept urllib.error.HTTPError as e:print(json.dumps({'ok':False,'code':e.code,'body':e.read(2000).decode()}))"
    result=json.loads(command(execution(target,instance)+['python3','-c',code],200))
    if result['ok'] and missing:result['ok']=not any(model.get('name')==name for model in json.loads(result['body']))
    if result['ok'] and target['runtime']=='vllm':
        models=json.loads(result['body']).get('data',[]);result['ok']=instance['served_model'] in {m.get('id') for m in models}
    return dict(name=instance['name'],uid=instance['uid'],ok=result['ok'],detail=result.get('body','')[:1000])


def restart(target,instance=None,controller=None,token=''):
    if target['environment']=='k8s':
        current=json.loads(command(['kubectl','get',target['workload_kind'],target['workload'],'-n',target['namespace'],'-o','json']))
        if current['metadata']['uid']!=controller['uid']:raise ValueError('工作负载已被重新创建')
        patch=json.dumps({'spec':{'template':{'metadata':{'annotations':{'data-platform/model-deployment':token}}}}})
        command(['kubectl','patch',target['workload_kind'],target['workload'],'-n',target['namespace'],'--type=merge','-p',patch])
    else:
        execution(target,instance);command(['docker','restart','--time','30',instance['name']],120)
    return dict(ok=True)


def dispatch(payload):
    action=payload['action'];target=payload.get('target',{});job=payload.get('job_id')
    if action=='source':
        result=inspect_source(payload['source']);result['file_count']=len(result.pop('files'));return result
    if action=='audit':
        result=target_state(target);marker=destination(target)/'.platform-deployment.json'
        result['deployment_id']=None
        if marker.is_file():
            current=json.loads(marker.read_text());UUID(current['id'])
            receipt=json.loads((workspace(target,current['id'])/'receipt.json').read_text())
            verify_release(destination(target),receipt);result['deployment_id']=current['id']
        return result
    if action=='pack':return pack_source(payload['source'],job)
    if action=='inventory':return inventory(target)
    if action=='compatibility':return compatibility(target,payload['instance'])
    if action=='probe':return probe(target,payload['instance'],payload.get('operation','ready'),payload.get('absent',False))
    if action=='restart':return restart(target,payload.get('instance'),payload.get('controller'),payload.get('token',''))
    if action=='state':
        result=target_state(target)
        result['addresses']=command(['hostname','-I']).split()
        roots=set()
        if shutil.which('exportfs'):
            exported=command(['exportfs','-v']);current=None
            for line in exported.splitlines():
                if line.startswith('/'):current=line.split()[0]
                if current and re.search(r'\bfsid=(?:0|root)(?:,|\))',line):roots.add(current)
        result['nfs4_roots']=sorted(roots)
        if job:
            stage=workspace(target,job);receipt=stage/'receipt.json'
            result['receipt']=json.loads(receipt.read_text()) if receipt.is_file() else None
            if result['receipt']:
                result['receipt']['file_count']=len(result['receipt'].pop('files',[]))
        return result
    if action=='prepare':
        folder=workspace(target,job);folder.mkdir(parents=True,exist_ok=True);return dict(path=str(folder/'input.zip'))
    if action=='receive':
        result=receive(target,job,payload['sha256']);result['file_count']=len(result.pop('files'));return result
    if action=='promote':return promote(target,job,payload['baseline'])
    if action=='restore':return restore(target,job)
    raise ValueError('未知模型下发动作')


if __name__=='__main__':
    try:print(json.dumps(dict(success=True,result=dispatch(json.loads(base64.b64decode(sys.argv[1])))),ensure_ascii=False))
    except Exception as error:print(json.dumps(dict(success=False,error=str(error)[-2000:]),ensure_ascii=False))
