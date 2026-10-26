"""Checkpoint snapshots and one offline container per validation attempt via ssh-manager."""
import asyncio
import hashlib
from pathlib import Path, PurePosixPath
import json
import shutil
import tempfile
from time import monotonic
from types import SimpleNamespace
from uuid import UUID, uuid4
import zipfile
from fastapi import HTTPException
from sqlalchemy import select
from models.validation import ValidationRun, ValidationItem, ValidationSet
from models.training_run import TrainingRun
from services import training_runs, gpu_servers
from services.gpu_reservations import assert_free

ROOT=Path(__file__).resolve().parents[1]


def result_directory(run):
    base=(ROOT/'.backups/validation-results').resolve()
    path=base/str(UUID(run.model_snapshot['execution']['id']))
    if path.resolve()!=path:raise HTTPException(409,'本地结果缓存路径非法')
    return path


async def active(db,run,*,lock=False):
    from services.validation import validate_context as validate
    current=await db.get(ValidationRun,run.id,populate_existing=True,with_for_update=lock)
    if current is None or current.status not in ('pending','running') or current.model_snapshot['execution']['id']!=run.model_snapshot['execution']['id']:
        raise HTTPException(409,'验证已取消或轮次变化')
    await validate(db,current)
    return current


async def remote(db,run,action,**values):
    config=run.model_snapshot['checkpoint'];execution=run.model_snapshot['execution']
    proxy=SimpleNamespace(server=config['server'],tenant_id=run.tenant_id)
    connection=await training_runs.connection(db,proxy)
    payload=dict(action=action,run_id=str(run.id),execution=execution['id'],config={**config,'policy':run.config},**values)
    return await asyncio.to_thread(training_runs.bridge,'platform_training' if connection else proxy.server['name'],
        dict(connection=connection,executor='validation',payload=payload))


async def snapshot(db,user,request,*,preview=False):
    config=request.model_dump(mode='json')
    if request.training_run_id:
        training=await training_runs.scoped(db,request.training_run_id,user.tenant_id)
        if training.status=='draft' or (not preview and training.status in training_runs.ACTIVE):raise HTTPException(409,'请等待训练结束后选择稳定的权重产物')
        root=PurePosixPath(training.output_dir)/training.config['output_subdir']
        if not PurePosixPath(request.weight_path).is_relative_to(root):raise HTTPException(400,'权重不属于所选训练任务输出目录')
        expected=('embedding' if training.config.get('parameters',{}).get('task_type')=='embedding' or training.kind=='wemm' else 'caption') if training.kind in ('swift_sft','wemm') else training.kind
        if request.kind!=expected or request.server_id!=training.server['id']:raise HTTPException(400,'训练任务、服务器或模型类型不匹配')
        config['image']=training.config['image']
    server=await training_runs.server_snapshot(db,request.server_id,user.tenant_id)
    if server['id']!='default':await gpu_servers.scoped_server(db,UUID(server['id']),user.tenant_id,lock=True)
    config['server']=server
    proxy=SimpleNamespace(id=uuid4(),tenant_id=user.tenant_id,config={},model_snapshot={'checkpoint':config,'execution':{'id':str(uuid4())}})
    metadata=await remote(db,proxy,'probe')
    if len(metadata.get('weight_sha256',''))!=64 or not metadata.get('image_digest','').startswith('sha256:'):raise HTTPException(502,'权重或镜像校验信息无效')
    await assert_free(db,[config['device']])
    return dict(source='checkpoint',id=metadata['weight_sha256'],revision=1,name=PurePosixPath(config['weight_path']).name,
        checkpoint={**config,**metadata},execution={'id':str(uuid4()),'active':True,'prepared':False})


async def validate_context(db,run,lock=False):
    config=run.model_snapshot['checkpoint'];server=config['server']
    if server['id']=='default':
        from config.settings import settings
        if server['name']!=settings.GPU_SERVER_NAME:raise HTTPException(409,'默认服务器连接已变化')
    else:
        current=await gpu_servers.scoped_server(db,UUID(server['id']),run.tenant_id,lock=lock)
        if not current.enabled or current.revision!=server['revision']:raise HTTPException(409,'验证服务器连接已变化')


async def stop(db,run):
    if not run.model_snapshot['execution']['active']:return
    await remote(db,run,'stop')
    run.model_snapshot={**run.model_snapshot,'execution':{**run.model_snapshot['execution'],'active':False}}
    directory=result_directory(run)
    if directory.exists():await asyncio.to_thread(shutil.rmtree,directory)


async def prepare(sessions,run):
    from services.validation import read_sample
    async with sessions() as db:
        await active(db,run)
        dataset=await db.get(ValidationSet,run.set_id)
        pending=set((await db.scalars(select(ValidationItem.asset_id).where(ValidationItem.run_id==run.id,
            ValidationItem.status.in_(('pending','running'))))).all())
        samples=[sample for sample in dataset.samples if UUID(sample['asset_id']) in pending]
    folder=ROOT/'.backups/validation-transfer';folder.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='validation-',dir=folder) as temp:
        archive=Path(temp)/'input.zip';manifest=[];size=0
        next_check=monotonic()+1
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED) as output:
            for sample in samples:
                if monotonic()>=next_check:
                    async with sessions() as db:await active(db,run)
                    next_check=monotonic()+1
                content,_=await read_sample(sample);size+=len(content)
                if size>40*1024**3:raise HTTPException(400,'验证图片超过40GiB，请缩小验证集')
                name=sample['asset_id']+'.img';await asyncio.to_thread(output.writestr,name,content)
                manifest.append(dict(asset_id=sample['asset_id'],file=name,sha256=sample['content_hash'],width=sample['width'],height=sample['height']))
                if sample.get('query_texts'):manifest[-1]['query_texts']=sample['query_texts']
            output.writestr('manifest.json',json.dumps(manifest))
        digest=await asyncio.to_thread(file_sha256,archive)
        async with sessions() as db:
            current=await active(db,run);staged=await remote(db,current,'stage')
            connection=await training_runs.connection(db,SimpleNamespace(server=current.model_snapshot['checkpoint']['server'],tenant_id=current.tenant_id))
            server=current.model_snapshot['checkpoint']['server']['name']
        try:
            await asyncio.to_thread(training_runs.bridge,'platform_training' if connection else server,
                dict(connection=connection,upload=dict(localPath=str(archive),remotePath=staged['directory']+'/input.zip')))
        except HTTPException as error:
            raise HTTPException(error.status_code,'验证图片包上传失败，尚未启动推理：'+str(error.detail)) from error
        async with sessions() as db:
            current=await active(db,run)
            script='multimodal_predict.py' if run.model_snapshot['checkpoint']['kind'] in ('caption','embedding') else 'checkpoint_predict.py'
            await remote(db,current,'start',archive_sha256=digest,script=(ROOT/'services'/script).read_text('utf-8'))
            current=await active(db,run)
            current.model_snapshot={**current.model_snapshot,'execution':{**current.model_snapshot['execution'],'prepared':True}}
            await db.commit();run.model_snapshot=current.model_snapshot


async def collect_results(sessions,run):
    directory=result_directory(run)
    result_limit=128*1024**2 if run.model_snapshot['checkpoint'].get('kind') in ('caption','embedding') else 900000
    while True:
        async with sessions() as db:
            current=await active(db,run)
            package=await remote(db,current,'results')
        if package.get('ready'):break
        await asyncio.sleep(1)
    expected='/ai/chen/validation/runs/'+str(UUID(run.model_snapshot['execution']['id']))+'/results.zip'
    if package.get('path')!=expected or not isinstance(package.get('size'),int) or not 0<package['size']<=5000*900000+5*1024**2:
        raise HTTPException(503,'远端结果包信息无效，请检查后重试')
    directory.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='transfer-',dir=directory.parent) as temp:
        temp=Path(temp);archive=temp/'results.zip';contents=temp/'results';contents.mkdir()
        async with sessions() as db:
            current=await active(db,run)
            connection=await training_runs.connection(db,SimpleNamespace(server=current.model_snapshot['checkpoint']['server'],tenant_id=current.tenant_id))
            server=current.model_snapshot['checkpoint']['server']['name']
            known={str(id) for id in (await db.scalars(select(ValidationItem.asset_id).where(ValidationItem.run_id==run.id))).all()}
            pending={str(id) for id in (await db.scalars(select(ValidationItem.asset_id).where(ValidationItem.run_id==run.id,ValidationItem.status.in_(('pending','running'))))).all()}
        await asyncio.to_thread(training_runs.bridge,'platform_training' if connection else server,
            dict(connection=connection,download=dict(localPath=str(archive),remotePath=expected)))
        def unpack():
            if archive.stat().st_size!=package['size'] or file_sha256(archive)!=package.get('sha256'):raise ValueError('结果包传输校验失败')
            with zipfile.ZipFile(archive) as source:
                members=source.infolist();names=[item.filename for item in members]
                if sum(item.file_size for item in members)>5000*900000+5*1024**2:raise ValueError('结果包展开大小超限')
                if not 1<len(names)<=5001 or len(set(names))!=len(names) or 'owner.json' not in names:raise ValueError('结果包清单非法')
                ids=set()
                for item in members:
                    if item.file_size>result_limit:raise ValueError('单图结果超限')
                    if item.filename=='owner.json':continue
                    id=str(UUID(item.filename.removesuffix('.json')))
                    if item.filename!=id+'.json':raise ValueError('结果文件名非法')
                    ids.add(id)
                if not pending<=ids<=known:raise ValueError('结果包缺少图片或包含其他任务图片')
                owner=dict(run_id=str(run.id),execution=run.model_snapshot['execution']['id'],sha256=run.model_snapshot['checkpoint']['weight_sha256'])
                if json.loads(source.read('owner.json'))!=owner:raise ValueError('结果包轮次或权重不匹配')
                for id in ids:(contents/(id+'.json')).write_bytes(source.read(id+'.json'))
        try:await asyncio.to_thread(unpack)
        except (ValueError,OSError,zipfile.BadZipFile,KeyError) as error:raise HTTPException(503,'结果包校验失败，请检查远端结果后重试') from error
        async with sessions() as db:
            await active(db,run,lock=True)
            contents.rename(directory)
            await db.commit()


def file_sha256(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


async def infer(sessions,run,sample):
    if not run.model_snapshot['execution']['prepared']:await prepare(sessions,run)
    directory=result_directory(run)
    if not directory.exists():await collect_results(sessions,run)
    async with sessions() as db:
        await active(db,run)
    path=directory/(str(UUID(sample['asset_id']))+'.json')
    result=json.loads(await asyncio.to_thread(path.read_text,encoding='utf-8'))
    if result.get('error'):raise HTTPException(502,result['error'])
    return result
