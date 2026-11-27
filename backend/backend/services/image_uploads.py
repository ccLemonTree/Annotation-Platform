"""Bounded local staging, safe archive reads and durable per-image import checkpoints."""
import asyncio,hashlib,io,json,logging,os,shutil,stat,tarfile,zipfile
from datetime import datetime,timedelta
from pathlib import Path,PurePosixPath,PureWindowsPath
from uuid import UUID
from fastapi import HTTPException,UploadFile
from sqlalchemy import select,update,func,or_
from models import Asset,ImageUpload,ImageUploadItem,ImageCollection,ImageCollectionMember,User
from models.image_upload import ImageUploadBatchFile
from config.settings import settings

CHUNK_SIZE=8*1024**2
IMAGES=('.jpg','.jpeg','.png','.bmp','.webp')
ARCHIVES=('.zip','.tar','.tar.gz','.tgz')
_worker=None
logger=logging.getLogger(__name__)

def staging(id):
    root=Path(settings.STORAGE_LOCAL_PATH).resolve()/'.incoming'
    path=root/str(UUID(str(id)))
    if root.is_symlink() or root.is_junction() or not path.resolve().is_relative_to(root.resolve()) or path.is_symlink() or path.is_junction():
        raise ValueError('上传暂存路径无效')
    return path

def remove_staging(id):
    path=staging(id)
    if path.exists():shutil.rmtree(path)

def part_size(job,index):
    return min(CHUNK_SIZE,job.size-index*CHUNK_SIZE)

def resume_keys(filename,size,hashes,collection_id,minimum,annotation_format,trust_annotations,project_id,image_kind='originals'):
    identity=[filename,size,hashes,str(collection_id),minimum]
    if annotation_format:identity.extend([annotation_format,trust_annotations])
    if image_kind=='crops':identity.append('crops')
    def digest(values):
        return hashlib.sha256(json.dumps(values,separators=(',',':')).encode()).hexdigest()
    return digest([*identity,str(project_id) if project_id else None]),digest(identity)

def uploaded_parts(job):
    if job.status in ('pending','processing','completed'):return list(range(len(job.chunk_hashes)))
    root=staging(job.id)
    return [i for i in range(len(job.chunk_hashes)) if (root/f'{i}.part').is_file() and (root/f'{i}.part').stat().st_size==part_size(job,i)]

def response(job):
    parts=uploaded_parts(job)
    return dict(id=str(job.id),filename=job.filename,size=job.size,collection_id=str(job.collection_id),min_long_edge=job.min_long_edge,
        project_id=str(job.project_id) if job.project_id else None,
        image_kind=job.image_kind,
        annotation_format=job.annotation_format,
        trust_annotations=job.trust_annotations,
        status=job.status,uploaded_parts=parts,uploaded_bytes=job.size if job.user_id is None else sum(part_size(job,i) for i in parts),
        chunk_size=CHUNK_SIZE,result=job.result,error=job.error,cursor=job.cursor,
        updated_at=job.updated_at.isoformat()+'Z')

def write_part(job,index,content):
    root=staging(job.id);root.mkdir(parents=True,exist_ok=True)
    target=root/f'{index}.part';temporary=root/f'{index}.tmp'
    with temporary.open('wb') as file:file.write(content)
    os.replace(temporary,target)

def assemble(job):
    root=staging(job.id);target=root/'assembled';temporary=root/'assembled.tmp'
    with temporary.open('wb') as output:
        for index,hash in enumerate(job.chunk_hashes):
            digest=hashlib.sha256();size=0
            with (root/f'{index}.part').open('rb') as source:
                while block:=source.read(1024**2):output.write(block);digest.update(block);size+=len(block)
            if size!=part_size(job,index) or digest.hexdigest()!=hash:
                (root/f'{index}.part').unlink(missing_ok=True)
                raise ValueError('分片损坏，请重新选择文件补传')
    os.replace(temporary,target)
    return target

def safe_name(name):
    name=name.replace('\\','/')
    if not name or len(name)>1000 or '\x00' in name or PurePosixPath(name).is_absolute() or PureWindowsPath(name).drive or '..' in PurePosixPath(name).parts or ':' in name:
        raise ValueError('压缩包包含非法路径')
    return name

def members(file,kind):
    return file.infolist() if kind=='zip' else file

def member_info(item,kind):
    if kind=='zip':
        mode=item.external_attr>>16
        if stat.S_IFMT(mode) not in (0,stat.S_IFREG,stat.S_IFDIR) or item.flag_bits&1:
            raise ValueError('不支持压缩包链接、特殊文件或加密内容')
        return safe_name(item.filename),item.file_size,item.is_dir()
    if not (item.isfile() or item.isdir()):raise ValueError('不支持TAR链接或特殊文件')
    return safe_name(item.name),item.size,item.isdir()

def open_archive(path,kind):
    if hasattr(path,'read'):
        path.seek(0)
        return zipfile.ZipFile(path) if kind=='zip' else tarfile.open(fileobj=path,mode='r:*')
    return zipfile.ZipFile(path) if kind=='zip' else tarfile.open(path,mode='r:*')

def check_archive(path,kind):
    count=total=0
    with open_archive(path,kind) as file:
        for item in members(file,kind):
            _,size,_=member_info(item,kind)
            if size<0:raise ValueError('压缩包文件大小无效')
            count+=1;total+=size
            if count>settings.MAX_ARCHIVE_FILES:
                raise ValueError(f'压缩包条目数量超过限制：已检查{count}项，上限{settings.MAX_ARCHIVE_FILES}项（含图片、标注和目录）')
            if total>settings.MAX_IMAGE_ARCHIVE_EXPANDED_SIZE:
                raise ValueError(f'压缩包展开大小超过限制：已检查{total}字节，上限{settings.MAX_IMAGE_ARCHIVE_EXPANDED_SIZE}字节')
    return count

def import_items(path,filename,cursor,checked=False,*,on_total=None,annotation_format=None):
    kind='zip' if filename.lower().endswith('.zip') else 'tar' if filename.lower().endswith(ARCHIVES[1:]) else None
    if kind is None:
        if cursor==0:
            with path.open('rb') as file:yield 0,filename,file.read(settings.MAX_UPLOAD_SIZE+1),None
        return
    if not checked:
        total=check_archive(path,kind)
        if on_total:on_total(total)
    with open_archive(path,kind) as file:
        for index,item in enumerate(members(file,kind)):
            name,size,directory=member_info(item,kind)
            if index<cursor:continue
            if not directory and (annotation_format=='json' and name.lower().endswith('.json')
                    or annotation_format=='yolo' and name.lower().endswith(('.txt','.yaml','.yml'))):
                continue
            if directory or not name.lower().endswith(IMAGES):yield index,name,None,'ignored';continue
            if size>settings.MAX_UPLOAD_SIZE:yield index,name,None,'invalid';continue
            try:
                source=file.open(item) if kind=='zip' else file.extractfile(item)
                with source:content=source.read(settings.MAX_UPLOAD_SIZE+1)
            except (zipfile.BadZipFile,EOFError,OSError):
                yield index,name,None,'invalid',None,name+'：图片解压读取失败';continue
            yield index,name,content,None

def next_item(iterator):
    return next(iterator,None)


def read_annotation_json(path, filename):
    kind = 'zip' if filename.lower().endswith('.zip') else 'tar'
    with open_archive(path, kind) as package:
        matches, named = [], []
        for entry in members(package, kind):
            name, size, directory = member_info(entry, kind)
            if not directory and name.lower().endswith('.json'):
                matches.append((size, entry))
                if PurePosixPath(name).name.lower() == 'annotations.json':
                    named.append((size, entry))
        choices = named or matches
        if len(choices) != 1:
            raise ValueError('压缩包须包含一个 annotations.json 或唯一的 JSON 标注文件')
        size, entry = choices[0]
        if size > settings.MAX_UPLOAD_SIZE:
            raise ValueError('压缩包内标注文件超过上传大小限制')
        reader = package.open(entry) if kind == 'zip' else package.extractfile(entry)
        with reader:
            return reader.read(settings.MAX_UPLOAD_SIZE + 1)


async def external_archive_annotations(db, job, path, records):
    from api.asset_routes import scoped_project
    from services.annotation_json_import import apply_annotations
    from services.yolo_import import import_label_files

    project = await scoped_project(db, job.project_id, job.tenant_id)
    if job.annotation_format == 'json':
        response = await apply_annotations(db, project, records, None, commit=False,
            crop_from_boxes=bool(job.result.get('crop_from_boxes')))
    else:
        with path.open('rb') as file:
            response = await import_label_files(db, project, None, None,
                UploadFile(file, filename=job.filename), None, job.id, allow_tar=True)
    counts = dict(imported=0, unchanged=0, conflict=0, missing=0, invalid=0, errors=[])
    if 'crop_count' in response:counts['crop_count']=response['crop_count']
    for row in response['items']:
        counts[row['status']] += 1
        if row['status'] not in ('imported','unchanged') and len(counts['errors']) < 20:
            counts['errors'].append({key: row[key] for key in ('filename','status','message') if key in row})
    return counts


async def process_one(sessions):
    async with sessions() as db:
        job=await db.scalar(select(ImageUpload).where(ImageUpload.status=='pending').order_by(ImageUpload.created_at).with_for_update(skip_locked=True).limit(1))
        if job is None:return False
        job.status='processing';job.attempt+=1
        if job.project_id and not job.cursor:
            job.result={**job.result,'project_association_complete':True,'added_count':0,'project_duplicates':0,'project_conflicts':0,'queued_count':0}
        id,attempt=job.id,job.attempt
        await db.commit()
    iterator=None
    archive_total=None

    def set_archive_total(total):
        nonlocal archive_total
        archive_total=total

    try:
        path=staging(id)/'assembled' if job.user_id is None else await asyncio.to_thread(assemble,job)
        external_labels=job.annotation_format if job.user_id is None else None
        records, annotation_kinds = [], {}
        if external_labels=='json':
            from services.annotation_json_import import annotation_image_kinds,parse_annotation_records
            kind='zip' if job.filename.lower().endswith('.zip') else 'tar'
            archive_total=await asyncio.to_thread(check_archive,path,kind)
            raw=await asyncio.to_thread(read_annotation_json,path,job.filename)
            records=parse_annotation_records(raw)
            annotation_kinds=annotation_image_kinds(records)
        if job.annotation_format and job.user_id is not None:
            from services import yolo_import
            iterator=yolo_import.import_items(path,job.filename,job.cursor,on_total=set_archive_total)
        else:iterator=import_items(path,job.filename,job.cursor,checked=archive_total is not None,
            on_total=set_archive_total,annotation_format=external_labels)
        while True:
            read=asyncio.create_task(asyncio.to_thread(next_item,iterator))
            try:item=await asyncio.shield(read)
            except asyncio.CancelledError:
                await read
                raise
            if item is None:break
            index,name,content,skip=item[:4]
            async with sessions() as db:
                current=await db.scalar(select(ImageUpload).where(ImageUpload.id==id).with_for_update())
                if current is None or current.status!='processing' or current.attempt!=attempt:return True
                external=current.user_id is None
                if (external or job.project_id or current.result.get('project_association_complete')) and current.project_id is None:
                    raise ValueError('目标项目已删除')
                result={**current.result,'errors':list(current.result.get('errors',[]))}
                if archive_total is not None:result['archive_total']=archive_total
                if skip:
                    result[skip]+=1
                    if len(item)>5 and len(result['errors'])<20:result['errors'].append(item[5][:500])
                else:
                    from api.asset_routes import read_upload
                    from services.image_ingest import ResolutionFiltered
                    from services.image_library import register_image,assign_collection
                    filename=PurePosixPath(name).name
                    image_kind=annotation_kinds.get(filename.lower(),current.image_kind)
                    minimum=0 if external_labels=='json' and image_kind=='crops' else current.min_long_edge
                    try:values=await read_upload(UploadFile(filename=filename,file=io.BytesIO(content)),minimum)
                    except ResolutionFiltered:result['filtered']+=1
                    except HTTPException:
                        result['invalid']+=1
                        if len(result['errors'])<20:result['errors'].append(name[:255]+'：图片格式、大小或内容无效')
                    else:
                        if current.project_id:
                            from api.asset_routes import scoped_project
                            from services.upload_follow import lock_follow_job
                            actor=await db.get(User,current.user_id) if current.user_id is not None else None
                            if current.user_id is not None:
                                if not actor or not actor.is_active or actor.tenant_id!=current.tenant_id:
                                    raise ValueError('上传用户已停用或租户变化')
                                db.info['current_user']={'id':actor.id,'role':actor.role,'tenant_id':actor.tenant_id}
                            project=await scoped_project(db,current.project_id,current.tenant_id)
                            follow=None if external_labels else await lock_follow_job(db,current.project_id)
                        image=await register_image(db,current.tenant_id,values,str(id))
                        member=await db.get(ImageCollectionMember,(current.collection_id,image.id))
                        result['duplicates' if member else 'imported']+=1
                        group=await db.get(ImageCollection,current.collection_id)
                        await assign_collection(db,current.tenant_id,[image.id],group.name)
                        if current.annotation_format and not external:
                            from services.yolo_import import save
                            await save(db,current,image,item[4],result)
                        if await db.get(ImageUploadItem,(id,image.id)) is None:
                            if current.project_id:
                                from services.image_library import attach_images
                                from services.upload_follow import enqueue_uploads,find_streaming_follow_job
                                try:
                                    assets,created=await attach_images(db,current.project_id,current.tenant_id,[image.id],image_kind=image_kind)
                                except HTTPException as exc:
                                    if exc.status_code!=409:raise
                                    result['project_conflicts']=result.get('project_conflicts',0)+1
                                    if len(result['errors'])<20:result['errors'].append(name[:255]+'：'+str(exc.detail))
                                else:
                                    if current.annotation_format and not external and project.task_type=='detection':
                                        from services.yolo_import import copy_to_project
                                        imported=await copy_to_project(db,assets,actor)
                                        for key,value in imported.items():result['project_'+key]=result.get('project_'+key,0)+value
                                    result['added_count']=result.get('added_count',0)+len(created)
                                    result['project_duplicates']=result.get('project_duplicates',0)+int(not created)
                                    if external_labels:
                                        for asset in created:
                                            asset.device_info={**(asset.device_info or {}),'archive_upload_id':str(id)}
                                    else:
                                        crop_entry_locked=follow is not None
                                        if follow is None and created:follow=await find_streaming_follow_job(db,current.project_id)
                                        queued,warning=await enqueue_uploads(db,follow,created,crop_entry_locked=crop_entry_locked)
                                        result['queued_count']=result.get('queued_count',0)+queued
                                        if warning:result['queue_warning']=warning
                            db.add(ImageUploadItem(upload_id=id,image_id=image.id))
                current.result=result;current.cursor=index+1;current.updated_at=datetime.utcnow()
                await db.commit()
        async with sessions() as db:
            active=select(ImageUpload).where(ImageUpload.id==id,ImageUpload.status=='processing',ImageUpload.attempt==attempt)
            current=await db.scalar(active.with_for_update().execution_options(populate_existing=True))
            if current and external_labels and 'annotations' not in current.result:
                summary=await external_archive_annotations(db,current,path,records)
                current.result={**current.result,'annotations':summary,'archive_total':archive_total}
                current.cursor=archive_total
                await db.commit()
                current=await db.scalar(active.with_for_update().execution_options(populate_existing=True))
            if current and current.status=='processing' and current.attempt==attempt:
                if external_labels and current.result['added_count']:
                    from services.upload_follow import enqueue_uploads,lock_follow_job
                    follow=await lock_follow_job(db,current.project_id)
                    if follow is not None:
                        result=dict(current.result)
                        query=select(Asset).join(ImageUploadItem,ImageUploadItem.image_id==Asset.image_id).where(
                            Asset.project_id==current.project_id,ImageUploadItem.upload_id==id,
                            or_(Asset.device_info['archive_upload_id'].as_string()==str(id),Asset.batch_id==id)).order_by(Asset.id)
                        stream=await db.stream_scalars(query.execution_options(yield_per=500))
                        async for assets in stream.partitions():
                            queued,warning=await enqueue_uploads(db,follow,assets)
                            result['queued_count']+=queued
                            if warning:result['queue_warning']=warning
                        current.result=result
                current.unique_count=await db.scalar(select(func.count()).select_from(ImageUploadItem).where(ImageUploadItem.upload_id==id))
                current.status='completed';current.error=None
                await db.execute(update(ImageUploadBatchFile).where(ImageUploadBatchFile.upload_id==id).values(status='completed'))
                await db.commit()
        await asyncio.to_thread(remove_staging,id)
    except asyncio.CancelledError:raise
    except Exception as exc:
        message=str(exc.detail) if isinstance(exc,HTTPException) else str(exc) if isinstance(exc,ValueError) else '文件校验或入库失败，请重试；损坏的压缩包需重新打包'
        async with sessions() as db:
            changed=await db.execute(update(ImageUpload).where(ImageUpload.id==id,ImageUpload.status=='processing',ImageUpload.attempt==attempt).values(status='failed',error=message[:500]))
            if changed.rowcount:
                await db.execute(update(ImageUploadBatchFile).where(ImageUploadBatchFile.upload_id==id).values(status='failed'))
            await db.commit()
        if job.user_id is None:
            if iterator:
                iterator.close()
                iterator=None
            await asyncio.to_thread(remove_staging,id)
        logger.warning('Upload import failed: %s',type(exc).__name__)
    finally:
        if iterator:iterator.close()
    return True

async def expire(sessions):
    async with sessions() as db:
        jobs=(await db.scalars(select(ImageUpload).where(ImageUpload.updated_at<datetime.utcnow()-timedelta(days=settings.UPLOAD_RETENTION_DAYS),ImageUpload.status.in_(['uploading','failed','completed'])).with_for_update(skip_locked=True))).all()
        ids=[job.id for job in jobs]
        for job in jobs:await db.delete(job)
        await db.commit()
    for id in ids:await asyncio.to_thread(remove_staging,id)

async def run_worker(sessions):
    last_cleanup=0
    while True:
        try:
            now=asyncio.get_running_loop().time()
            if now-last_cleanup>3600:await expire(sessions);last_cleanup=now
            if not await process_one(sessions):await asyncio.sleep(1)
        except asyncio.CancelledError:raise
        except Exception as exc:logger.error('Upload worker failed: %s',type(exc).__name__);await asyncio.sleep(1)

async def start_worker():
    global _worker
    from database import AsyncSessionLocal
    # shortcut: one backend process; add leases before running multiple backend instances.
    async with AsyncSessionLocal() as db:
        await db.execute(update(ImageUpload).where(ImageUpload.status=='processing').values(status='pending'))
        await db.commit()
    _worker=asyncio.create_task(run_worker(AsyncSessionLocal))

async def stop_worker():
    if _worker:
        _worker.cancel()
        try:await _worker
        except asyncio.CancelledError:pass
