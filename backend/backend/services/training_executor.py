"""Fixed remote executor. Standard library only; never runs user shell text."""
import base64
import contextlib
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import stat
import subprocess
import sys
import zipfile


def docker(args,check=True,timeout=25):
    result=subprocess.run(['docker',*args],capture_output=True,text=True,timeout=timeout)
    if check and result.returncode:raise RuntimeError((result.stderr or result.stdout or 'Docker失败')[-2000:])
    return result


def container_name(run_id):
    from uuid import UUID
    return 'platform-train-'+str(UUID(run_id))


def container_state(run_id):
    name=container_name(run_id)
    found=docker(['ps','-a','--filter','name=^/'+name+'$','--format','{{.ID}}']).stdout.strip()
    if not found:return None
    info=json.loads(docker(['inspect',name]).stdout)[0]
    if info.get('Config',{}).get('Labels',{}).get('platform.training.id')!=run_id:
        raise RuntimeError('同名容器不是本平台任务，禁止操作')
    return info


def file_hash(path):
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def safe_child(root,name):
    if not isinstance(name,str) or '\\' in name or PurePosixPath(name).is_absolute() or '..' in name.split('/'):
        raise ValueError('冻结包路径非法')
    path=root.joinpath(name)
    if not path.resolve().is_relative_to(root.resolve()):raise ValueError('冻结包路径越界')
    return path


def unpack_snapshot(archive,destination,kind):
    destination=Path(destination)
    if destination.exists():raise ValueError('数据目录已经存在，禁止覆盖')
    with zipfile.ZipFile(archive) as source:
        members=source.infolist()
        if len(members)>40000 or sum(row.file_size for row in members)>40*1024**3:raise ValueError('冻结包超出40000项/40GiB限制')
        seen=set()
        for row in members:
            safe_child(destination,row.filename)
            if stat.S_ISLNK(row.external_attr>>16) or row.filename in seen:raise ValueError('冻结包不允许链接或重复路径')
            seen.add(row.filename)
        destination.mkdir()
        for row in members:
            path=safe_child(destination,row.filename)
            if row.is_dir():path.mkdir(parents=True,exist_ok=True)
            else:
                path.parent.mkdir(parents=True,exist_ok=True)
                with source.open(row) as inp,path.open('xb') as out:shutil.copyfileobj(inp,out,1024*1024)
    manifest=json.loads((destination/'manifest.json').read_text())
    if kind in ('swift_sft','wemm'):
        training=manifest.get('training',{})
        if training.get('samples',0)<1 or training.get('format') not in ('swift_sft','infonce','contrastive','online_contrastive','cosine_similarity'):
            raise ValueError('冻结包缺少训练格式或有效样本')
        for asset in manifest['assets']:
            if file_hash(safe_child(destination,asset['image']))!=asset['content_hash']:raise ValueError('冻结包图片哈希不匹配')
        dataset=destination/'train.jsonl'
        rows=[json.loads(line) for line in dataset.read_text(encoding='utf-8').splitlines() if line.strip()]
        if len(rows)!=training['samples']:raise ValueError('冻结包训练样本数不匹配')
        for row in rows:
            for key in ('images','positive_images','negative_images'):
                groups=[row.get(key,[])] if key=='images' else row.get(key,[])
                for group in groups:
                    for index,name in enumerate(group):
                        path=safe_child(destination,name)
                        if not path.is_file():raise ValueError('冻结包引用图片不存在')
                        group[index]='/output/dataset/'+path.relative_to(destination).as_posix()
        dataset.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in rows),encoding='utf-8')
        return
    for asset in manifest['assets']:
        if file_hash(safe_child(destination,asset['image_path']))!=asset['content_hash']:raise ValueError('冻结包图片哈希不匹配')
    splits={a['split'] for a in manifest['assets']}
    if not {'train','val'}<=splits or splits-{'train','val','test'}:raise ValueError('冻结包缺少明确train/val划分')
    if kind=='yolo26':
        (destination/'data.yaml').write_text(json.dumps(dict(path='/output/dataset',names=manifest['labels'],
            train='images/train',val='images/val',test='images/test' if 'test' in splits else []),ensure_ascii=False))
        return
    root=destination/'rfdetr';root.mkdir()
    for split in sorted(splits):
        target=root/('valid' if split=='val' else split);target.mkdir()
        assets=[a for a in manifest['assets'] if a['split']==split]
        if manifest['format']=='yolo':
            (target/'images').mkdir();(target/'labels').mkdir()
            for asset in assets:
                original=safe_child(destination,asset['image_path'])
                os.link(original,target/'images'/original.name)
                shutil.copyfile(destination/'labels'/split/(original.stem+'.txt'),target/'labels'/(original.stem+'.txt'))
        else:
            document=json.loads((destination/'annotations'/('instances_'+split+'.json')).read_text())
            categories={item['id']:index for index,item in enumerate(document['categories'])}
            for category in document['categories']:category['id']=categories[category['id']]
            for annotation in document['annotations']:annotation['category_id']=categories[annotation['category_id']]
            for image in document['images']:
                original=safe_child(destination,image['file_name']);os.link(original,target/original.name)
                image['file_name']=original.name
            (target/'_annotations.coco.json').write_text(json.dumps(document,ensure_ascii=False))
    if manifest['format']=='yolo':
        (root/'data.yaml').write_text(json.dumps(dict(names=manifest['labels'],nc=len(manifest['labels']),
            train='train/images',val='valid/images',test='test/images' if 'test' in splits else []),ensure_ascii=False))


def dataset_metadata(config,code):
    source=Path(config['dataset_path'])
    folder=source if source.is_dir() else source.parent
    mounts={str(folder):dict(source=str(folder),target=str(folder),read_only=True)}
    for mount in config.get('mounts',[]):mounts[mount['target']]=dict(mount,read_only=True)
    args=['run','--rm','--pull=never','--network','none','--entrypoint','python3']
    for mount in mounts.values():args+=['--mount','type=bind,src='+mount['source']+',dst='+mount['target']+',readonly']
    program=code+'\nimport sys\nprint(json.dumps(inspect_server_detection(sys.argv[1],sys.argv[2]),ensure_ascii=False))\n'
    args+=[config['image'],'-c',program,str(source),config['kind']]
    info=json.loads(docker(args,timeout=90).stdout)
    if config['kind']=='yolo26' and info['format']!='yolo':raise ValueError('YOLO需要YOLO格式数据')
    selected=config.get('selected_labels')
    if selected is not None and set(selected)-set(info['labels']):raise ValueError('选择的训练标签在数据源中不存在')
    return info


def writable_output(root):
    ancestor=root
    while not ancestor.exists() and ancestor.parent!=ancestor:ancestor=ancestor.parent
    return ancestor.is_dir() and os.access(ancestor,os.W_OK)


def lightly_metadata(config,code):
    weights=Path(config['model_path'])
    args=['run','--rm','--pull=never','--network','none','--entrypoint','python3',
        '--mount','type=bind,src='+str(weights.parent)+',dst='+str(weights.parent)+',readonly',
        config['image'],'-c',code+'\nimport sys\nprint(json.dumps(inspect_lightly_model(sys.argv[1])))\n',str(weights)]
    return json.loads(docker(args,timeout=120).stdout)


def precheck(payload):
    config=payload['config'];checks=[]
    def add(name,ok,detail):checks.append(dict(name=name,ok=bool(ok),detail=detail))
    image=docker(['image','inspect',config['image']],check=False)
    add('镜像',image.returncode==0,'已存在，不自动拉取' if image.returncode==0 else '镜像尚未就绪，请等待拉取完成')
    digest=None
    if image.returncode==0:digest=json.loads(image.stdout)[0]['Id']
    gpu=subprocess.run(['nvidia-smi','--query-gpu=index,uuid,name,memory.free,mig.mode.current','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=10)
    if gpu.returncode:raise RuntimeError('GPU状态读取失败')
    gpus=[]
    for line in gpu.stdout.strip().splitlines():
        index,uuid,name,free,mig=[part.strip() for part in line.split(',')]
        gpus.append(dict(index=int(index),uuid=uuid,name=name,free_mib=float(free),mig_mode=mig))
    processes=subprocess.run(['nvidia-smi','--query-compute-apps=gpu_uuid,pid','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=10)
    if processes.returncode:raise RuntimeError('GPU进程状态未知，不能分配')
    busy={line.split(',')[0].strip() for line in processes.stdout.splitlines() if line.strip()}
    for uuid in config['gpu_uuids']:
        physical=next((g for g in gpus if g['uuid']==uuid),None)
        available=physical and physical['mig_mode'] in ('Disabled','N/A','[N/A]','Not Supported','[Not Supported]') and uuid not in busy
        add('GPU '+uuid,available,'存在且无计算进程' if available else 'GPU不存在、已有计算任务或MIG启用/未知，禁止分配或抢占')
    root=Path(config['output_root'])
    add('输出根目录',writable_output(root),'已有目录需可写；不存在时在可写父目录下自动创建：'+str(root))
    pending=payload.get('pending_onnx',False) and config['kind']=='model_export' and config['output']['format']=='tensorrt'
    if pending:
        if image.returncode==0:
            code='from pathlib import Path; assert Path("/usr/src/tensorrt/bin/trtexec").is_file(), "缺少trtexec"'
            result=docker(['run','--rm','--pull=never','--network','none','--entrypoint','python3',digest,'-c',code],check=False,timeout=120)
            add('TensorRT构建依赖',result.returncode==0,'trtexec已就绪；目标服务器无需Python onnx包' if result.returncode==0 else (result.stderr or result.stdout)[-2000:])
        add('ONNX输入',True,'训练服务器完成ONNX导出后自动传输并校验，不要求目标服务器已有检查点')
    elif config['kind'] in ('model_export','model_merge'):
        for key in ('model_path','adapter_path'):
            value=config.get(key)
            if value:
                path=Path(value)
                add('来源 '+key,path.exists() and path.resolve()==path,'来源须存在且不能通过符号链接越界：'+value)
        if image.returncode==0 and payload.get('output_code') and all(c['ok'] for c in checks if c['name'].startswith('来源 ')):
            args=['run','--rm','--pull=never','--network','none','--entrypoint','python3']
            for key in ('model_path','adapter_path'):
                value=config.get(key)
                if value:
                    path=Path(value);path=path if path.is_dir() else path.parent
                    args+=['--mount','type=bind,src='+str(path)+',dst='+str(path)+',readonly']
            program=payload['output_code']+'\nprint(json.dumps(check(json.loads(sys.argv[1]))))\n'
            try:
                result=docker([*args,digest,'-c',program,json.dumps(config)],timeout=120)
                add('导出依赖与来源',True,json.loads(result.stdout.strip().splitlines()[-1]))
            except (ValueError,RuntimeError,subprocess.TimeoutExpired) as error:add('导出依赖与来源',False,str(error))
    elif config.get('snapshot_id'):
        add('冻结包',True,'启动时传输固定SHA256包并校验全部图片，不重新导出')
    else:
        path=Path(config['dataset_path'])
        if config['kind'] in ('swift_sft','wemm'):add('训练数据',path.is_file(),'Swift填写JSONL：'+str(path))
        elif payload.get('dataset_code') and image.returncode==0:
            try:
                info=dataset_metadata(config,payload['dataset_code'])
                add('训练数据与类别',True,json.dumps(info,ensure_ascii=False))
            except (ValueError,RuntimeError) as error:add('训练数据与类别',False,str(error))
        else:
            ok=path.is_file() if config['kind']=='yolo26' else path.is_dir() and (
                (path/'train/_annotations.coco.json').is_file() or (path/'data.yaml').is_file() and (path/'train/images').is_dir())
            add('训练数据',ok,'镜像就绪后读取数据划分和标签：'+str(path))
    if config['model_path'] and not pending:
        weights=Path(config['model_path'])
        add('基础权重',weights.is_dir() if config['kind'] in ('swift_sft','wemm','model_merge') else weights.is_file(),
            'Swift需要模型目录，检测模型需要权重文件：'+config['model_path'])
    if config['kind']=='dinov3_vits16_ltdetr' and image.returncode==0:
        try:
            if not payload.get('dataset_code'):raise ValueError('缺少DINOv3预检查适配代码')
            info=lightly_metadata({**config,'image':digest},payload['dataset_code'])
            add('Lightly依赖与检测权重',True,json.dumps(info,ensure_ascii=False))
        except (ValueError,RuntimeError,subprocess.TimeoutExpired) as error:
            add('Lightly依赖与检测权重',False,str(error))
    if config['kind']=='rfdetr_2xlarge':
        add('2XLarge许可证',config['accept_platform_model_license'],'需要管理员明确确认Platform Model License')
        if not config['model_path']:add('2XLarge默认权重',False,'请填写服务器已有2XLarge权重；平台不下载默认权重')
    val=config['parameters'].get('val_dataset')
    if val:add('验证数据',Path(val).is_file(),'验证JSONL须存在：'+val)
    for mount in config['mounts']:add('挂载 '+mount['source'],Path(mount['source']).exists(),'挂载源必须已存在')
    return dict(ok=all(c['ok'] for c in checks),checks=checks,gpus=gpus,image_digest=digest)


def run_directory(payload):
    run_id=payload['run_id'];container_name(run_id)
    root=Path(payload['config']['output_root'])
    if not writable_output(root):raise ValueError('输出目录或其父目录不可写')
    root.mkdir(parents=True,exist_ok=True)
    path=root/run_id
    config_hash=hashlib.sha256(json.dumps(payload['config'],sort_keys=True).encode()).hexdigest()
    path.mkdir(exist_ok=True)
    if path.is_symlink():raise ValueError('任务目录不能是符号链接')
    marker=path/'owner.json'
    value=dict(run_id=run_id,config_hash=config_hash)
    try:
        with marker.open('x') as out:json.dump(value,out)
    except FileExistsError:
        if json.loads(marker.read_text())!=value:raise ValueError('任务目录已被其他配置占用，禁止覆盖')
    if marker.is_symlink():raise ValueError('任务标记不能是链接')
    return path,config_hash


@contextlib.contextmanager
def run_lock(path):
    import fcntl
    with (path/'.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        yield


def artifacts(path):
    files=[];truncated=False
    if path.exists():
        for root,dirs,names in os.walk(path,followlinks=False):
            dirs[:]=[d for d in dirs if not (Path(root)/d).is_symlink()]
            for name in names:
                file=Path(root)/name
                if file.is_symlink():continue
                if len(files)>=500:truncated=True;break
                files.append(dict(path=str(file.relative_to(path)),bytes=file.stat().st_size,
                    role='weights' if file.suffix in ('.pt','.pth','.safetensors','.ckpt','.bin','.onnx','.engine') else 'file'))
            if truncated:break
    return dict(files=files,truncated=truncated)


def artifact_file(root,name):
    if not isinstance(name,str) or not name or len(name)>1000 or '\\' in name or PurePosixPath(name).is_absolute() or any(p in ('','.', '..') for p in name.split('/')):
        raise ValueError('产物路径非法')
    path=root/name
    if root.resolve()!=root or path.resolve()!=path or not path.is_file():raise ValueError('产物不存在或包含符号链接')
    return path


def pack_onnx(root,archive):
    manifest=json.loads(artifact_file(root,'result.json').read_text())
    files=manifest['files']
    onnx=[row['path'] for row in files if row['path'].endswith('.onnx')]
    if len(onnx)!=1 or PurePosixPath(onnx[0]).parent!=PurePosixPath('.'):
        raise ValueError('来源须包含唯一的根目录ONNX文件')
    if not files or len(files)>500 or sum(row['bytes'] for row in files)>40*1024**3:
        raise ValueError('ONNX产物超过500项/40GiB传输上限')
    entries=[]
    with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED) as out:
        for row in files:
            file=artifact_file(root,row['path'])
            if file.stat().st_size!=row['bytes'] or file_hash(file)!=row['sha256']:
                raise ValueError('ONNX产物校验失败，禁止传输')
            name='model.onnx' if row['path']==onnx[0] else row['path']
            if name=='bundle.json' or (name=='model.onnx' and row['path']!=onnx[0]):raise ValueError('ONNX传输文件名冲突')
            entries.append(dict(path=name,bytes=row['bytes'],sha256=row['sha256']))
            out.write(file,name)
        out.writestr('bundle.json',json.dumps(dict(files=entries,onnx=manifest['onnx'])))


def unpack_onnx(archive,destination):
    # Reuse the owned task input after a backend restart only if every byte still matches.
    with zipfile.ZipFile(archive) as source:
        members=source.infolist()
        if len(members)>501 or sum(row.file_size for row in members)>40*1024**3:raise ValueError('ONNX包超出传输上限')
        names=[row.filename for row in members]
        if len(set(names))!=len(names):raise ValueError('ONNX包有重复路径')
        if source.getinfo('bundle.json').file_size>1024**2:raise ValueError('ONNX包清单过大')
        for row in members:
            safe_child(destination,row.filename)
            if row.is_dir() or stat.S_ISLNK(row.external_attr>>16):raise ValueError('ONNX包不允许目录项或符号链接')
        manifest=json.loads(source.read('bundle.json'))
        expected={row['path']:row for row in manifest['files']}
        if 'model.onnx' not in expected or set(names)!=set(expected)|{'bundle.json'}:raise ValueError('ONNX清单不完整')
        for name,row in expected.items():
            info=source.getinfo(name)
            if info.file_size!=row['bytes']:raise ValueError('ONNX文件大小校验失败')
            digest=hashlib.sha256()
            with source.open(name) as inp:
                for chunk in iter(lambda:inp.read(1024**2),b''):digest.update(chunk)
            if digest.hexdigest()!=row['sha256']:raise ValueError('ONNX文件哈希校验失败')
        destination.mkdir(parents=True,exist_ok=True)
        if destination.resolve()!=destination:raise ValueError('ONNX输入目录不能包含符号链接')
        for name,row in expected.items():
            target=safe_child(destination,name)
            if target.exists():
                if not target.is_file() or target.is_symlink() or file_hash(target)!=row['sha256']:raise ValueError('已有ONNX输入不匹配，禁止覆盖')
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with source.open(name) as inp,target.open('xb') as out:shutil.copyfileobj(inp,out,1024**2)
        marker=destination/'bundle.json';content=json.dumps(manifest)
        if marker.exists():
            if marker.is_symlink() or marker.read_text()!=content:raise ValueError('已有ONNX清单不匹配')
        else:marker.write_text(content)


def execute(payload):
    action=payload['action'];config=payload['config'];run_id=payload['run_id']
    if action=='dataset':return dataset_metadata(config,payload['dataset_code'])
    if action=='precheck':return precheck(payload)
    if action=='status':
        info=container_state(run_id)
        if info is None:return dict(status='missing')
        state=info['State'];status=state['Status']
        if status in ('running','restarting','paused'):return dict(status='running',container_id=info['Id'])
        if status=='created':return dict(status='created',container_id=info['Id'])
        if status in ('exited','dead'):
            output=Path(config['output_root'])/run_id
            result=artifacts(output/config['output_subdir'])
            success=state['ExitCode']==0 and any(f['role']=='weights' for f in result['files'])
            if config['kind'] in ('model_export','model_merge'):
                manifest=output/config['output_subdir']/'result.json'
                success=success and manifest.is_file() and not manifest.is_symlink()
            canceled=(output/'.canceled').exists()
            return dict(status='canceled' if canceled else ('completed' if success else 'failed'),exit_code=state['ExitCode'],
                oom_killed=state.get('OOMKilled',False),artifacts=result,
                error='' if success or canceled else ('导出/合并失败或缺少完整产物清单，请查看日志' if config['kind'] in ('model_export','model_merge') else '训练失败或未找到权重产物，请查看日志'))
        return dict(status='unknown',error='Docker状态需要核对：'+status)
    if action=='logs':
        if container_state(run_id) is None:return dict(text='容器尚未创建。')
        response=subprocess.run(['docker','logs','--tail','200',container_name(run_id)],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=15)
        if response.returncode:raise RuntimeError('训练日志读取失败')
        return dict(text=response.stdout.decode('utf-8',errors='replace')[-160000:])
    if action=='artifacts':return artifacts(Path(config['output_root'])/run_id/config['output_subdir'])
    if action=='artifact':
        file=artifact_file(Path(config['output_root'])/run_id/config['output_subdir'],payload['path'])
        return dict(path=str(file),bytes=file.stat().st_size,sha256=file_hash(file))
    path,config_hash=run_directory(payload)
    with run_lock(path):
        if action=='pack_onnx':
            archive=path/'onnx.zip'
            if archive.is_symlink():raise ValueError('ONNX包不能是符号链接')
            pack_onnx(path/config['output_subdir'],archive)
            return dict(path=str(archive),bytes=archive.stat().st_size,sha256=file_hash(archive))
        if action=='receive_onnx':
            archive=path/'input.zip'
            if archive.is_symlink() or file_hash(archive)!=payload['sha256']:raise ValueError('ONNX传输包校验失败')
            unpack_onnx(archive,path/'onnx')
            return dict(ok=True)
        if action=='stage':
            for name,content in [('run_config.json',json.dumps(config,ensure_ascii=False,indent=2)),('train.py',payload['script'])]:
                target=path/name
                if target.exists():
                    if target.is_symlink() or target.read_text()!=content:raise ValueError('已存在任务文件不匹配，禁止覆盖')
                else:target.write_text(content)
            return dict(directory=str(path))
        if action=='unpack':
            archive=path/'input.zip'
            if file_hash(archive)!=payload['snapshot_hash']:raise ValueError('传输冻结包SHA256不匹配')
            dataset=path/'dataset'
            if not (path/'.dataset-ready').exists():
                unpack_snapshot(archive,dataset,config['kind'])
                (path/'.dataset-ready').write_text(payload['snapshot_hash'])
            elif (path/'.dataset-ready').read_text()!=payload['snapshot_hash']:raise ValueError('冻结包版本不一致')
            return dict(ok=True)
        if action=='stop':
            (path/'.canceled').touch()
            info=container_state(run_id)
            if info and info['State']['Status'] in ('running','restarting','paused'):
                docker(['stop','--time','15',container_name(run_id)],timeout=22)
                if container_state(run_id)['State']['Running']:raise RuntimeError('容器仍在运行，保留GPU占用')
            return dict(status='canceled')
        if action!='start':raise ValueError('不支持的训练操作')
        if (path/'.canceled').exists():return dict(status='canceled')
        info=container_state(run_id)
        if info:
            if info['State']['Status']=='created':docker(['start',container_name(run_id)])
            return dict(status='running' if info['State']['Status'] in ('created','running','restarting') else 'unknown')
        checked=precheck(payload)
        if not checked['ok']:return dict(status='blocked',inspection=checked)
        if config.get('snapshot_id') and not (path/'.dataset-ready').exists():raise ValueError('冻结数据尚未验证')
        mounts={'/output':dict(source=str(path),target='/output',read_only=False)}
        for name in ('dataset_path','model_path','adapter_path'):
            value=config.get(name)
            if value and (not config.get('snapshot_id') or name in ('model_path','adapter_path')):
                source=Path(value);source=source if source.is_dir() else source.parent
                mounts[str(source)]=dict(source=str(source),target=str(source),read_only=True)
        val=config['parameters'].get('val_dataset')
        if val:mounts[str(Path(val).parent)]=dict(source=str(Path(val).parent),target=str(Path(val).parent),read_only=True)
        for mount in config['mounts']:mounts[mount['target']]=mount
        args=['create','--pull=never','--name',container_name(run_id),'--label','platform.training.id='+run_id,
            '--label','platform.training.config='+config_hash,'--gpus','"device='+','.join(config['gpu_uuids'])+'"',
            '--shm-size',str(config['shm_size_gb'])+'g','--network','none','--workdir','/output','--entrypoint','python3']
        for mount in mounts.values():
            args+=['--mount','type=bind,src='+mount['source']+',dst='+mount['target']+(',readonly' if mount['read_only'] else '')]
        for key,value in config['environment'].items():args+=['--env',key+'='+value]
        args+=[checked['image_digest'],'-u','/output/train.py']
        docker(args);docker(['start',container_name(run_id)])
        return dict(status='running',image_digest=checked['image_digest'])


if __name__=='__main__':
    try:
        request=json.loads(base64.b64decode(sys.argv[1]))
        print(json.dumps(dict(success=True,result=execute(request)),ensure_ascii=False))
    except Exception as error:
        print(json.dumps(dict(success=False,error=str(error)[-2000:]),ensure_ascii=False))
        sys.exit(1)
