"""Fixed SSH executor for offline validation, separate from every training container."""
import base64
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
from uuid import UUID
import zipfile


def docker(args,timeout=30,check=True):
    result=subprocess.run(['docker',*args],capture_output=True,text=True,timeout=timeout)
    if check and result.returncode:raise ValueError((result.stderr or result.stdout)[-1500:])
    return result


def sha(path):
    digest=hashlib.sha256()
    with open(path,'rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def weight_identity(config):
    if config['kind'] not in ('caption','embedding'):return sha(config['weight_path'])
    digest=hashlib.sha256()
    for key in ('weight_path','base_model_path'):
        if not config.get(key):continue
        root=Path(config[key])
        if root.is_symlink() or not root.is_dir() or root.resolve()!=root:raise ValueError('模型目录不存在或包含符号链接')
        if not (root/('adapter_config.json' if key=='weight_path' and config.get('base_model_path') else 'config.json')).is_file():raise ValueError('模型目录缺少config.json；LoRA需填写基础模型目录')
        count=0
        for file in sorted(root.rglob('*')):
            if file.is_symlink():raise ValueError('模型目录包含符号链接，请使用独立模型目录')
            if not file.is_file():continue
            count+=1
            if count>50000:raise ValueError('模型目录文件过多，请选择具体模型或checkpoint目录')
            digest.update((key+'/'+file.relative_to(root).as_posix()+'\0'+sha(file)+'\n').encode())
    return digest.hexdigest()


def state(name,run_id,execution):
    if not docker(['ps','-a','--filter','name=^/'+name+'$','--format','{{.ID}}']).stdout.strip():return None
    value=json.loads(docker(['inspect',name]).stdout)[0]
    labels=value['Config'].get('Labels',{})
    if labels.get('platform.validation.run')!=run_id or labels.get('platform.validation.execution')!=execution:
        raise ValueError('容器归属不匹配，禁止操作')
    return value


def execute(payload):
    action=payload['action'];config=payload['config']
    weight=Path(config['weight_path'])
    if action=='probe':
        if not weight.exists() or weight.is_symlink():raise ValueError('权重不存在或是符号链接')
        image=json.loads(docker(['image','inspect',config['image']]).stdout)[0]['Id']
        return dict(weight_sha256=weight_identity(config),image_digest=image)
    run_id=str(UUID(payload['run_id']));execution=str(UUID(payload['execution']))
    root=Path('/ai/chen/validation/runs')/execution
    if root.resolve()!=root:raise ValueError('验证目录不能包含符号链接')
    name='platform-validation-'+execution
    root.mkdir(parents=True,exist_ok=True)
    owner=root/'owner.json';identity={'run_id':run_id,'execution':execution,'sha256':config['weight_sha256']}
    if owner.exists():
        if owner.is_symlink() or json.loads(owner.read_text())!=identity:raise ValueError('验证目录归属不匹配')
    else:
        with owner.open('x') as out:json.dump(identity,out)
    if action=='stage':return dict(directory=str(root))
    if action=='stop':
        import fcntl
        with (root/'.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            (root/'.canceled').touch()
            found=state(name,run_id,execution)
            if found and found['State']['Running']:docker(['stop','--time','10',name],timeout=25)
            found=state(name,run_id,execution)
            if found and found['State']['Running']:raise ValueError('尚未确认验证容器停止')
            import shutil
            inputs=root/'input'
            if inputs.exists():
                if inputs.is_symlink() or inputs.resolve().parent!=root:raise ValueError('临时验证图片目录越界，拒绝清理')
                shutil.rmtree(inputs)
            (root/'input.zip').unlink(missing_ok=True)
        return dict(stopped=True)
    if action=='start':
        import fcntl
        with (root/'.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if (root/'.canceled').exists():raise ValueError('此轮验证已取消')
            found=state(name,run_id,execution)
            if found:return dict(started=True)
            if weight_identity(config)!=config['weight_sha256']:raise ValueError('权重已变化，请新建任务')
            if sha(root/'input.zip')!=payload['archive_sha256']:raise ValueError('验证图片包哈希不一致')
            with zipfile.ZipFile(root/'input.zip') as archive:
                members=archive.infolist()
                if len(members)>5001 or sum(row.file_size for row in members)>40*1024**3:raise ValueError('验证图片包超限')
                names=set()
                for row in members:
                    path=Path(row.filename)
                    if path.is_absolute() or len(path.parts)!=1 or '\\' in row.filename or '..' in path.parts or stat.S_ISLNK(row.external_attr>>16) or row.filename in names:
                        raise ValueError('验证图片包路径非法')
                    names.add(row.filename)
                destination=root/'input';destination.mkdir(exist_ok=False)
                archive.extractall(destination)
            if config['device']!='cpu':
                query=subprocess.run(['nvidia-smi','--query-compute-apps=gpu_uuid','--format=csv,noheader'],capture_output=True,text=True,timeout=10)
                physical=subprocess.run(['nvidia-smi','--query-gpu=uuid,mig.mode.current','--format=csv,noheader'],capture_output=True,text=True,timeout=10)
                if query.returncode or physical.returncode or config['device'] in query.stdout:raise ValueError('GPU占用或状态未知，禁止抢占')
                rows=[line.split(',') for line in physical.stdout.splitlines()]
                if not any(row[0].strip()==config['device'] and row[1].strip() in ('Disabled','N/A','[N/A]','Not Supported','[Not Supported]') for row in rows):raise ValueError('GPU不存在或MIG状态不支持')
            (root/'config.json').write_text(json.dumps(config,ensure_ascii=False))
            (root/'predict.py').write_text(payload['script'])
            multimodal=config['kind'] in ('caption','embedding')
            args=['create','--pull=never','--name',name,'--label','platform.validation.run='+run_id,
                '--label','platform.validation.execution='+execution,'--network','none','--cap-drop','ALL',
                '--security-opt','no-new-privileges','--cpus','4','--memory','96g' if multimodal else '24g','--pids-limit','512',
                '--mount','type=bind,src='+str(root)+',dst=/job',
                '--mount','type=bind,src='+str(weight)+',dst=/weights/model'+('' if multimodal else weight.suffix)+',readonly',
                '--env','HF_HUB_OFFLINE=1','--env','TRANSFORMERS_OFFLINE=1','--env','OMP_NUM_THREADS=4',
                '--workdir','/job','--entrypoint','python3']
            if config['device']!='cpu':args+=['--gpus','device='+config['device']]
            if config.get('base_model_path'):args+=['--mount','type=bind,src='+config['base_model_path']+',dst=/weights/base,readonly']
            args+=[config['image_digest'],'-u','/job/predict.py']
            docker(args);docker(['start',name]);return dict(started=True)
    if action=='results':
        result_limit=128*1024**2 if config.get('kind') in ('caption','embedding') else 900000
        for _ in range(20):
            found=state(name,run_id,execution)
            if found is None:raise ValueError('验证容器不存在，请检查权重和执行记录')
            if not found['State']['Running']:break
            time.sleep(1)
        else:return dict(ready=False)
        import fcntl
        with (root/'.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            if (root/'.canceled').exists():raise ValueError('此轮验证已取消')
            archive=root/'results.zip'
            if archive.is_symlink():raise ValueError('结果包路径非法')
            if not archive.exists():
                manifest=root/'input/manifest.json'
                if manifest.resolve()!=manifest or manifest.stat().st_size>5*1024**2:raise ValueError('验证清单非法或超限')
                if (root/'results').resolve()!=root/'results':raise ValueError('预测目录路径非法')
                samples=json.loads(manifest.read_text())
                ids=[str(UUID(sample['asset_id'])) for sample in samples]
                if not 0<len(ids)<=5000 or len(set(ids))!=len(ids):raise ValueError('验证清单数量非法')
                temp=archive.with_suffix('.tmp')
                if temp.is_symlink():raise ValueError('结果包临时路径非法')
                with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED) as output:
                    output.writestr('owner.json',json.dumps(identity))
                    for asset_id in ids:
                        path=root/'results'/(asset_id+'.json')
                        if path.is_symlink() or (path.exists() and path.stat().st_size>result_limit):raise ValueError('预测文件非法或超限')
                        if path.exists():output.write(path,asset_id+'.json')
                        else:output.writestr(asset_id+'.json',json.dumps(dict(error='验证容器已退出，此图未生成预测结果')))
                os.replace(temp,archive)
            return dict(ready=True,path=str(archive),size=archive.stat().st_size,sha256=sha(archive))
    raise ValueError('不支持的验证操作')


if __name__=='__main__':
    try:print(json.dumps(dict(success=True,result=execute(json.loads(base64.b64decode(sys.argv[1])))),ensure_ascii=False))
    except Exception as error:
        print(json.dumps(dict(success=False,error=str(error)[-1500:]),ensure_ascii=False));sys.exit(1)
