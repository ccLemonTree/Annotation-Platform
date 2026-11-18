"""Store immutable model files; never deserialize uploaded executable checkpoints."""
import asyncio
import hashlib
import json
import logging
from pathlib import Path,PurePosixPath
import re
import shutil
import tempfile
from uuid import uuid4
import zipfile
from fastapi import HTTPException
from sqlalchemy import select,inspect,case,func,or_
from sqlalchemy.exc import IntegrityError
from starlette.responses import FileResponse
from config.settings import settings
from database import AsyncSessionLocal
from models.model_repository import ModelRepositoryItem
from schemas.model_repository import RepositoryMetadata,repository_path
from services import training_runs
from services.model_deployment_executor import relative,sha

_tasks={}
_auto_tasks={}
_slots=asyncio.Semaphore(1)
CONFIG_LIMIT=1024**2


def summary(row):return training_runs.summary(row)


def group_name():
    name=ModelRepositoryItem.name
    fallback=case(*[(func.lower(name).endswith(suffix.lower(),autoescape=True),func.rtrim(func.substr(name,1,func.length(name)-len(suffix))))
        for suffix in (' · TENSORRT',' · ONNX',' · PT')],else_=name)
    return case((ModelRepositoryItem.group_name!='',ModelRepositoryItem.group_name),else_=fallback)


def query(tenant,q='',format=None,gpu_model='',group=None):
    result=select(ModelRepositoryItem).where(ModelRepositoryItem.tenant_id==tenant)
    if q:
        pattern='%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        result=result.where(or_(ModelRepositoryItem.name.ilike(pattern,escape='\\'),ModelRepositoryItem.group_name.ilike(pattern,escape='\\')))
    if format:result=result.where(ModelRepositoryItem.format==format)
    if gpu_model:result=result.where(ModelRepositoryItem.gpu_model==gpu_model)
    if group is not None:result=result.where(group_name()==group)
    return result


async def groups(db,tenant,pagination,q,format,gpu_model):
    rows=query(tenant,q,format,gpu_model).add_columns(group_name().label('effective_group')).subquery()
    grouped=select(rows.c.effective_group.label('name'),func.count().label('version_count'),func.max(rows.c.created_at).label('latest_at'),
        func.sum(case((rows.c.status=='importing',1),else_=0)).label('importing_count')).group_by(rows.c.effective_group)
    total=await db.scalar(select(func.count()).select_from(grouped.subquery()))
    items=[dict(r) for r in (await db.execute(grouped.order_by(func.max(rows.c.created_at).desc(),rows.c.effective_group)
        .offset(pagination.offset).limit(pagination.limit))).mappings()]
    variants=(await db.execute(select(rows.c.effective_group,rows.c.format,rows.c.gpu_model).where(rows.c.effective_group.in_([r['name'] for r in items])).distinct())).all() if items else []
    for item in items:
        item['formats']=sorted({r.format for r in variants if r.effective_group==item['name']})
        item['gpu_models']=sorted({r.gpu_model for r in variants if r.effective_group==item['name'] and r.gpu_model})
    gpus=(await db.scalars(select(ModelRepositoryItem.gpu_model).where(ModelRepositoryItem.tenant_id==tenant,
        ModelRepositoryItem.format=='tensorrt',ModelRepositoryItem.gpu_model!='').distinct().order_by(ModelRepositoryItem.gpu_model))).all()
    return dict(items=items,total=total,version_total=await db.scalar(select(func.count()).select_from(rows)),page=pagination.page,page_size=pagination.page_size,gpu_models=gpus)


def config_text(content):
    if not content or len(content)>CONFIG_LIMIT or b'\0' in content:raise HTTPException(422,'config.pbtxt必须为非空UTF-8文本，且不超过1 MiB')
    try:return content.decode('utf-8-sig')
    except UnicodeDecodeError:raise HTTPException(422,'config.pbtxt必须为UTF-8文本') from None


async def config(row):
    if row.status!='ready':raise HTTPException(409,'模型尚未入库完成')
    if row.format not in ('tensorrt','python'):raise HTTPException(422,'仅TensorRT和Python backend版本支持config.pbtxt')
    config_path=str(PurePosixPath(row.entry_file).parent.parent/'config.pbtxt') if row.format=='python' else 'config.pbtxt'
    info=next((r for r in row.files if r['path']==config_path),None)
    if info is None:raise HTTPException(404,'此版本未附带config.pbtxt')
    path=folder(row)/'files'/config_path
    def read():
        if any(p.is_symlink() for p in [path,*path.parents]) or not path.is_file() or path.stat().st_size!=info['bytes'] or info['bytes']>CONFIG_LIMIT:
            raise HTTPException(409,'config.pbtxt文件校验失败')
        content=path.read_bytes()
        if hashlib.sha256(content).hexdigest()!=info['sha256']:raise HTTPException(409,'config.pbtxt文件校验失败')
        return dict(path=config_path,content=config_text(content),sha256=info['sha256'])
    return await asyncio.to_thread(read)


async def scoped(db,id,tenant,lock=False):
    query=select(ModelRepositoryItem).where(ModelRepositoryItem.id==id,ModelRepositoryItem.tenant_id==tenant)
    if lock:query=query.with_for_update()
    row=await db.scalar(query.execution_options(populate_existing=True))
    if row is None:raise HTTPException(404,'模型仓库记录不存在')
    return row


def folder(row):
    path=Path(settings.MODEL_ARCHIVE_PATH)/'repository'/str(row.tenant_id)/str(row.id)
    if any(p.is_symlink() for p in [path,*path.parents]):raise HTTPException(409,'模型存储路径包含符号链接')
    return path


def check_space(path,size):
    if shutil.disk_usage(path).free<size+64*1024**2:raise HTTPException(409,'模型仓库磁盘空间不足')


def validate_files(rows,entry,allow_empty=False):
    if not rows or len(rows)>500:raise HTTPException(422,'请选择1至500个模型文件')
    names=[];total=0
    for row in rows:
        try:name=repository_path(row['path'])
        except (ValueError,KeyError):raise HTTPException(422,'模型文件相对路径无效') from None
        if len(name)>500 or not isinstance(row.get('bytes'),int) or row['bytes']<0 or row['bytes']==0 and (not allow_empty or name==entry):raise HTTPException(422,'模型文件不能为空或路径过长')
        key=name.casefold()
        if key in names or any(key.startswith(p+'/') or p.startswith(key+'/') for p in names):raise HTTPException(422,'模型文件路径重复或与目录冲突')
        names.append(key);total+=row['bytes']
        if not re.fullmatch('[a-f0-9]{64}',row.get('sha256','')):raise HTTPException(422,'模型文件SHA256无效')
    if entry not in {row['path'] for row in rows}:raise HTTPException(422,'主文件未包含在上传或导出清单中')
    if total>settings.MAX_ARCHIVE_UPLOAD_SIZE:raise HTTPException(413,'模型文件总大小超过仓库上传限制')
    return total


async def persist(db,row):
    db.add(row)
    try:await db.commit()
    except IntegrityError:
        await db.rollback();raise HTTPException(409,'同一模型版本、格式、GPU型号和batch规格已存在，请使用新版本或不同batch') from None
    await db.refresh(row);return row


async def available(db,user,metadata):
    if await db.scalar(select(ModelRepositoryItem.id).where(ModelRepositoryItem.tenant_id==user.tenant_id,
            *[getattr(ModelRepositoryItem,key)==getattr(metadata,key) for key in ('name','version','format','gpu_model','batch_profile')]).limit(1)):
        raise HTTPException(409,'同一模型版本、格式、GPU型号和batch规格已存在，请使用新版本或不同batch')


async def update(db,row,request):
    if row.status=='importing':raise HTTPException(409,'模型正在入库，请完成后再修改信息')
    values=request.model_dump()
    build_fields={'gpu_model','tensorrt_version'} & request.model_fields_set
    if build_fields:
        if row.format!='tensorrt' or row.source.get('kind')!='upload' or row.run_id:
            raise HTTPException(422,'仅直接上传的TensorRT模型可修正GPU型号和TensorRT版本，训练产物构建信息只读')
        if 'gpu_model' in build_fields and not request.gpu_model:raise HTTPException(422,'TensorRT模型必须指定实际构建GPU型号')
    for key in {'gpu_model','tensorrt_version'}-build_fields:values.pop(key)
    if 'group_name' not in request.model_fields_set:values.pop('group_name')
    for key,value in values.items():setattr(row,key,value)
    return await persist(db,row)


async def upload(db,user,metadata,uploads):
    if metadata.format=='huggingface':raise HTTPException(422,'完整模型请通过合并任务入库')
    if metadata.format=='tensorrt' and not metadata.gpu_model:raise HTTPException(422,'TensorRT上传必须指定GPU型号')
    if not uploads or len(uploads)>500:raise HTTPException(422,'请选择1至500个模型文件')
    if metadata.format=='pt' and len(uploads)!=1:raise HTTPException(422,'PT模型只接受一个主文件')
    if metadata.format=='tensorrt' and (len(uploads)>2 or sorted(f.filename for f in uploads)!=sorted([metadata.entry_file]+(['config.pbtxt'] if len(uploads)==2 else []))):
        raise HTTPException(422,'TensorRT只接受一个主文件和可选的config.pbtxt')
    python_root=PurePosixPath(metadata.entry_file).parent.parent if metadata.format=='python' else None
    config_path=str(python_root/'config.pbtxt') if python_root is not None else 'config.pbtxt'
    await available(db,user,metadata)
    if not db.new and not db.dirty and not db.deleted and db.in_transaction():
        await db.commit()
    row=ModelRepositoryItem(id=uuid4(),tenant_id=user.tenant_id,user_id=user.id,**metadata.model_dump(),
        source={'kind':'upload'},status='ready',files=[],file_size=0)
    destination=folder(row);partial=destination/'partial';partial.mkdir(parents=True)
    try:
        names=[];files=[];total=0
        for file in uploads:
            try:name=repository_path(file.filename)
            except (ValueError,TypeError):raise HTTPException(422,'模型文件相对路径无效') from None
            if python_root is not None:
                try:PurePosixPath(name).relative_to(python_root)
                except ValueError:raise HTTPException(422,'Python backend文件必须属于同一个模型目录') from None
            key=name.casefold()
            if len(name)>500 or key in names or any(key.startswith(p+'/') or p.startswith(key+'/') for p in names):raise HTTPException(422,'模型文件路径重复或与目录冲突')
            names.append(key);target=partial/name;target.parent.mkdir(parents=True,exist_ok=True)
            digest=hashlib.sha256();size=0
            with target.open('xb') as out:
                while chunk:=await file.read(1024**2):
                    total+=len(chunk);size+=len(chunk)
                    if metadata.format in ('tensorrt','python') and name==config_path and size>CONFIG_LIMIT:raise HTTPException(422,'config.pbtxt不能超过1 MiB')
                    if total>settings.MAX_ARCHIVE_UPLOAD_SIZE:raise HTTPException(413,'模型文件总大小超过仓库上传限制')
                    check_space(partial,len(chunk));await asyncio.to_thread(out.write,chunk);digest.update(chunk)
            if metadata.format in ('tensorrt','python') and name==config_path:config_text(target.read_bytes())
            files.append(dict(path=name,bytes=size,sha256=digest.hexdigest()))
        if python_root is not None and config_path not in {item['path'] for item in files}:raise HTTPException(422,'Python backend目录缺少config.pbtxt')
        row.file_size=validate_files(files,metadata.entry_file,allow_empty=python_root is not None);row.files=files
        partial.replace(destination/'files')
        commit=asyncio.create_task(persist(db,row))
        try:await asyncio.shield(commit)
        except asyncio.CancelledError:
            await commit;raise
        return row
    except BaseException:
        if row not in db:await asyncio.to_thread(shutil.rmtree,destination,True)
        else:
            from sqlalchemy import inspect as state
            if not state(row).persistent:await asyncio.to_thread(shutil.rmtree,destination,True)
        raise
    finally:
        for file in uploads:await file.close()


async def source_run(db,id,tenant):
    run=await training_runs.scoped(db,id,tenant)
    if run.status!='completed' or run.kind not in ('yolo26','rfdetr_medium','rfdetr_2xlarge','model_export','model_merge','swift_sft','wemm'):
        raise HTTPException(409,'请选择已完成的小模型训练或标准导出任务')
    return run


async def metadata(db,run,path,verify=False):
    if run.kind in ('swift_sft','wemm') and PurePosixPath(path).name in ('config.json','adapter_config.json'):
        directory=PurePosixPath(path).parent
        base=path=='base/config.json'
        if not base and directory.parts and directory.parts[0]=='base' and run.source.get('repository_model_id'):
            raise HTTPException(422,'请选择训练输出目录内的模型')
        entry=PurePosixPath(path).name
        relative_dir='' if directory==PurePosixPath('.') else str(directory)
        receipt=await training_runs.remote(db,run,'model_directory',input_kind='base' if base else 'adapter',path=relative_dir,
            entry=entry)
        result=dict(format='huggingface',entry_file=path,gpu_model='',tensorrt_version='',files=[
            {**item,'path':str(directory/item['path'])} for item in receipt['files']])
        if entry=='config.json' and any(PurePosixPath(item['path']).name=='adapter_config.json' for item in result['files']):
            raise HTTPException(422,'此目录包含 LoRA，请选择对应的 adapter_config.json')
    elif run.config.get('execution_backend')=='k8s' and PurePosixPath(path).suffix.lower() in ('.pt','.pth'):
        receipt=await training_runs.artifact_metadata(db,run,path)
        result=dict(format='pt',gpu_model='',tensorrt_version='',entry_file=path,
            files=[dict(path=path,bytes=receipt['bytes'],sha256=receipt['sha256'])])
    else:
        result=await training_runs.remote(db,run,'repository_metadata',path=path,verify=verify)
    try:
        data=RepositoryMetadata(name='source',version='source',format=result['format'],entry_file=result['entry_file'],
            gpu_model=result.get('gpu_model',''),tensorrt_version=result.get('tensorrt_version',''),batch_profile=result.get('batch_profile',''))
        validate_files(result['files'],data.entry_file,allow_empty=data.format=='huggingface')
    except (KeyError,TypeError,ValueError):raise HTTPException(502,'训练产物清单无效或缺少GPU信息') from None
    if data.entry_file!=path or data.format=='tensorrt' and (not data.gpu_model or not data.tensorrt_version):raise HTTPException(422,'训练导出缺少可信GPU型号或TensorRT版本')
    return result


async def create_import(db,user,request):
    run=await source_run(db,request.run_id,user.tenant_id)
    if request.format=='huggingface' and run.kind not in ('swift_sft','wemm','model_merge'):
        raise HTTPException(422,'来源任务不支持模型目录归档')
    if request.format=='huggingface' and PurePosixPath(request.artifact_path).name=='adapter_config.json' and run.kind not in ('swift_sft','wemm'):
        raise HTTPException(422,'LoRA 来源须为已完成的微调任务')
    data=await metadata(db,run,request.artifact_path)
    if request.format!=data['format'] or request.gpu_model and request.gpu_model!=data['gpu_model']:raise HTTPException(422,'所选格式或GPU型号与训练导出清单不一致')
    name,group=request.name,request.group_name
    if request.format=='huggingface' and request.artifact_path=='base/config.json':
        model=PurePosixPath(run.config.get('model_path') or '').name
        model=re.sub(r'[\x00-\x1f\x7f]',' ',model).strip() or '基础模型'
        name=model[:100]
        group=f"基础模型 · {model[:93]}"
    values=RepositoryMetadata(name=name,group_name=group,version=request.version,format=data['format'],gpu_model=data['gpu_model'],
        tensorrt_version=data['tensorrt_version'],entry_file=data['entry_file'],batch_profile=data.get('batch_profile',''))
    await available(db,user,values)
    row=ModelRepositoryItem(id=uuid4(),tenant_id=user.tenant_id,user_id=user.id,run_id=run.id,**values.model_dump(),
        source=dict(kind='training',artifact_path=request.artifact_path,
            **({'onnx_input':data['onnx_input']} if data.get('onnx_input') else {})),files=data['files'],
        file_size=validate_files(data['files'],data['entry_file'],allow_empty=data['format']=='huggingface'),status='importing')
    return await submit(db,row)


def schedule_automatic(run,path,user_id=None):
    format={'.pt':'pt','.pth':'pt','.onnx':'onnx','.engine':'tensorrt','.plan':'tensorrt'}.get(PurePosixPath(path).suffix.lower())
    if run.kind in ('model_merge','swift_sft','wemm') and PurePosixPath(path).name in ('config.json','adapter_config.json'):
        format='huggingface'
    key=(run.id,path)
    if key in _auto_tasks:return _auto_tasks[key]
    if (not format or run.status!='completed'
            or run.kind not in ('yolo26','rfdetr_medium','rfdetr_2xlarge','model_export','model_merge','swift_sft','wemm')):
        return
    task=asyncio.create_task(automatic_import(run.id,run.tenant_id,user_id or run.user_id,path,format))
    _auto_tasks[key]=task
    task.add_done_callback(lambda _: _auto_tasks.pop(key,None))
    return task


async def automatic_import(run_id,tenant,user_id,path,format):
    from models import User
    from schemas.model_repository import RepositoryImport
    try:
        async with AsyncSessionLocal() as db:
            conn=await db.connection()
            if not await conn.run_sync(lambda c:inspect(c).has_table('model_repository_items')):return
            user=await db.get(User,user_id)
            if not user or not user.is_active or user.role!='admin' or user.tenant_id!=tenant:return
            run=await source_run(db,run_id,tenant)
            existing=await db.scalar(select(ModelRepositoryItem.id).where(ModelRepositoryItem.tenant_id==tenant,
                ModelRepositoryItem.run_id==run_id,ModelRepositoryItem.entry_file==path).order_by(
                    (ModelRepositoryItem.status=='ready').desc(),ModelRepositoryItem.created_at.desc()).limit(1))
            if existing:return existing
            name=re.sub(r'[\x00-\x1f\x7f]',' ',run.name).strip() or '训练模型'
            group=name.rsplit(' · ',1)[0] if run.kind in ('model_export','model_merge') else name
            if run.kind in ('swift_sft','wemm') and format=='huggingface' and path!='base/config.json':
                name=name[:75]+' · '+str(PurePosixPath(path).parent.name or '训练模型')[:20]
            version=f"{run_id}-{hashlib.sha256(path.encode()).hexdigest()[:12]}"
            row=await create_import(db,user,RepositoryImport(name=name[:100],group_name=group[:100],version=version,
                format=format,run_id=run_id,artifact_path=path))
            return row.id
    except Exception as error:
        logging.getLogger(__name__).warning('Automatic model import failed for run %s (%s)',run_id,type(error).__name__)


def schedule(row):
    _tasks[str(row.id)]=asyncio.create_task(worker(row.id,row.tenant_id))


async def submit(db,row):
    async def save():
        await persist(db,row);schedule(row);return row
    task=asyncio.create_task(save())
    try:return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task;raise


async def copy_import(db,row):
    from services.model_deployments import authorized
    await authorized(db,row)
    run=await source_run(db,row.run_id,row.tenant_id)
    data=await metadata(db,run,row.entry_file,verify=True)
    if any(data[k]!=getattr(row,k) for k in ('format','gpu_model','tensorrt_version','files')) or data.get('batch_profile','')!=row.batch_profile:raise HTTPException(409,'训练产物在入库后发生变化，请创建新版本')
    destination=folder(row);partial=destination/'partial'
    # Retry only replaces this record's uncommitted staging files.
    if (destination/'files').exists():raise HTTPException(409,'仓库已存在完整文件，请核对后使用新版本')
    partial.mkdir(parents=True,exist_ok=True)
    try:
        check_space(partial,row.file_size)
        for item in row.files:
            receipt=await training_runs.remote(db,run,'base_model_file',path=item['path'].removeprefix('base/')) if (
                run.kind in ('swift_sft','wemm') and row.entry_file=='base/config.json') else await training_runs.artifact_metadata(db,run,item['path'])
            if any(receipt.get(k)!=item[k] for k in ('bytes','sha256')):raise HTTPException(409,'训练文件元数据已变化，禁止入库')
            target=partial/item['path'];target.parent.mkdir(parents=True,exist_ok=True)
            await training_runs.fetch_artifact(db,run,receipt,target)
        await authorized(db,row)
        row=await scoped(db,row.id,row.tenant_id,lock=True)
        partial.replace(destination/'files');row.status='ready';row.error=None
        commit=asyncio.create_task(db.commit())
        try:await asyncio.shield(commit)
        except asyncio.CancelledError:await commit
    except BaseException:
        await asyncio.to_thread(shutil.rmtree,destination,True);raise


async def worker(id,tenant):
    try:
        async with _slots:
            async with AsyncSessionLocal() as db:
                row=await scoped(db,id,tenant)
                try:await copy_import(db,row)
                except BaseException as error:
                    await db.rollback();row=await scoped(db,id,tenant)
                    row.status='interrupted' if isinstance(error,asyncio.CancelledError) else 'failed'
                    row.error=str(error.detail) if isinstance(error,HTTPException) else '入库中断，可重试' if isinstance(error,asyncio.CancelledError) else '模型复制失败，请检查来源文件后重试'
                    await db.commit()
                    if isinstance(error,asyncio.CancelledError):raise
    finally:_tasks.pop(str(id),None)


async def retry(db,user,row):
    if row.source.get('kind')!='training' or row.status not in ('failed','interrupted') or str(row.id) in _tasks:raise HTTPException(409,'此记录不能重新入库')
    row.status='importing';row.error=None;row.user_id=user.id
    return await submit(db,row)


async def download(row):
    if row.status!='ready':raise HTTPException(409,'模型尚未入库完成')
    root=folder(row)/'files'
    def verify():
        for item in row.files:
            path=root/relative(item['path'])
            if any(p.is_symlink() for p in [path,*path.parents]) or not path.is_file() or path.stat().st_size!=item['bytes'] or sha(path)!=item['sha256']:raise HTTPException(409,'仓库文件校验失败')
    await asyncio.to_thread(verify)
    if len(row.files)==1:return FileResponse(root/row.entry_file,filename=PurePosixPath(row.entry_file).name,media_type='application/octet-stream')
    check_space(root,row.file_size)
    temp=tempfile.TemporaryDirectory(prefix='download-',dir=folder(row));archive=Path(temp.name)/'model.zip'
    def pack():
        with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as out:
            for item in row.files:out.write(root/item['path'],item['path'])
    packing=asyncio.create_task(asyncio.to_thread(pack))
    try:
        try:await asyncio.shield(packing)
        except asyncio.CancelledError:
            await packing;raise
    except BaseException:
        temp.cleanup();raise
    return download_response(temp,archive,'model-'+str(row.id)+'.zip')


def download_response(temp,archive,filename):
    class DownloadResponse(FileResponse):
        async def __call__(self,*args,**kwargs):
            try:await super().__call__(*args,**kwargs)
            finally:await asyncio.to_thread(temp.cleanup)
    return DownloadResponse(archive,filename=filename,media_type='application/zip')


async def resolve_batch_rows(db,tenant,request):
    if request.ids is not None:
        rows=(await db.scalars(select(ModelRepositoryItem).where(ModelRepositoryItem.tenant_id==tenant,ModelRepositoryItem.id.in_(request.ids)))).all()
        if len(rows)!=len(request.ids):raise HTTPException(404,'部分模型版本不存在或不属于当前租户')
        by_id={row.id:row for row in rows};rows=[by_id[id] for id in request.ids]
    else:
        filters=request.filters
        rows=(await db.scalars(query(tenant,filters.q,filters.format,filters.gpu_model,filters.group_name or None)
            .where(ModelRepositoryItem.status=='ready').order_by(ModelRepositoryItem.created_at.desc(),ModelRepositoryItem.id.desc()).limit(101))).all()
        if len(rows)>100:raise HTTPException(422,'匹配超过100个可下载版本，请缩小筛选范围')
    if not rows:raise HTTPException(404,'没有符合条件的可下载模型')
    if any(row.status!='ready' for row in rows):raise HTTPException(409,'所选模型尚未入库完成，请重新选择可下载版本')
    if any(not row.files or sum(item['bytes'] for item in row.files)!=row.file_size for row in rows):raise HTTPException(409,'模型文件清单无效')
    return rows


async def download_batch(db,tenant,request):
    rows=await resolve_batch_rows(db,tenant,request)
    models=[dict(id=str(row.id),name=row.name,group_name=row.group_name,version=row.version,format=row.format,
        gpu_model=row.gpu_model,tensorrt_version=row.tensorrt_version,batch_profile=row.batch_profile,
        directory='models/'+str(row.id),entry_file=row.entry_file,file_size=row.file_size,files=list(row.files)) for row in rows]
    sources=[(folder(row),list(row.files),row.file_size) for row in rows]
    total_size=sum(size for _,_,size in sources)
    root=sources[0][0]
    await db.rollback()
    manifest=json.dumps(dict(models=models),ensure_ascii=False,indent=2).encode('utf-8')
    if not root.is_dir():raise HTTPException(409,'仓库模型目录缺失')
    check_space(root,total_size+len(manifest)+1024**2)
    temp=tempfile.TemporaryDirectory(prefix='download-batch-',dir=root);archive=Path(temp.name)/'models.zip'
    def pack():
        try:
            with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_STORED,allowZip64=True) as out:
                for (row_folder,row_files,_),model in zip(sources,models):
                    base=row_folder/'files'
                    for item in row_files:
                        name=relative(item['path']);path=base/name
                        if any(p.is_symlink() for p in [path,*path.parents]) or not path.is_file() or path.stat().st_size!=item['bytes']:raise HTTPException(409,'仓库文件校验失败')
                        digest=hashlib.sha256();size=0
                        with path.open('rb') as source,out.open(model['directory']+'/'+name,'w',force_zip64=True) as target:
                            while chunk:=source.read(1024**2):
                                size+=len(chunk)
                                if size>item['bytes']:raise HTTPException(409,'仓库文件校验失败')
                                digest.update(chunk);target.write(chunk)
                        if size!=item['bytes'] or digest.hexdigest()!=item['sha256']:raise HTTPException(409,'仓库文件校验失败')
                out.writestr('manifest.json',manifest)
        except OSError:raise HTTPException(409,'仓库文件读取或打包失败，请检查文件和存储空间') from None
    packing=asyncio.create_task(asyncio.to_thread(pack))
    try:
        try:await asyncio.shield(packing)
        except asyncio.CancelledError:
            await packing;raise
    except BaseException:
        temp.cleanup();raise
    return download_response(temp,archive,'models-batch.zip')


async def start_worker():
    async with AsyncSessionLocal() as db:
        connection=await db.connection()
        if not await connection.run_sync(lambda c:inspect(c).has_table('model_repository_items')):return
        rows=(await db.scalars(select(ModelRepositoryItem).where(ModelRepositoryItem.status=='importing'))).all()
        for row in rows:
            await asyncio.to_thread(shutil.rmtree,folder(row),True)
            row.status='interrupted';row.error='应用重启中断入库，可手动重试'
        await db.commit()


async def stop_worker():
    tasks=[*_auto_tasks.values(),*_tasks.values()]
    for task in tasks:task.cancel()
    if tasks:await asyncio.gather(*tasks,return_exceptions=True)
