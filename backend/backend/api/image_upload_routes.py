"""Authenticated resumable transport; all image imports use the existing library policy."""
import asyncio,hashlib,math
from datetime import datetime,timedelta
from pathlib import PurePosixPath
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Request,Response,Query
from pydantic import BaseModel,Field,field_validator
from sqlalchemy import case,delete,insert,select,func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from api.deps import get_current_user
from api.asset_routes import scoped_project
from database import get_db_session
from models import User,ImageUpload,ImageUploadItem,ImageCollectionMember
from models.image_upload import ImageUploadBatch,ImageUploadBatchFile
from services import image_uploads as uploads
from services.image_library import get_collection
from services.image_ingest import get_config
from config.settings import settings

router=APIRouter()

class UploadCreate(BaseModel):
    filename:str=Field(min_length=1,max_length=255)
    size:int=Field(gt=0,strict=True)
    chunk_hashes:list[str]=Field(min_length=1,max_length=100000)
    collection_name:str=Field(min_length=1,max_length=200)
    project_id:UUID|None=None
    image_kind:Literal['originals','crops']='originals'
    min_long_edge:int|None=Field(default=None,ge=0,le=32768,strict=True)
    annotation_format:Literal['yolo']|None=None
    trust_annotations:bool=False
    batch_id:UUID|None=None
    file_key:str|None=Field(default=None,pattern=r'^[0-9a-f]{64}$')

    @field_validator('collection_name')
    @classmethod
    def name(cls,value):
        if not value.strip():raise ValueError('请输入图片集名称')
        return value.strip()

    @field_validator('filename')
    @classmethod
    def filename_valid(cls,value):
        if value!=PurePosixPath(value.replace('\\','/')).name or '\x00' in value or not value.lower().endswith(uploads.IMAGES+uploads.ARCHIVES):
            raise ValueError('请选择图片、ZIP或TAR文件')
        return value

    @field_validator('chunk_hashes')
    @classmethod
    def hashes(cls,values):
        if any(len(value)!=64 or any(char not in '0123456789abcdef' for char in value) for value in values):
            raise ValueError('分片SHA-256无效')
        return values

async def scoped(db,id,user,lock=False):
    query=select(ImageUpload).where(ImageUpload.id==id,ImageUpload.tenant_id==user.tenant_id,ImageUpload.user_id==user.id)
    if lock:query=query.with_for_update()
    job=await db.scalar(query)
    if job is None:raise HTTPException(404,'上传任务不存在')
    return job

@router.get('/config')
async def config(user:User=Depends(get_current_user)):
    return dict(chunk_size=uploads.CHUNK_SIZE,max_file_size=settings.MAX_IMAGE_ARCHIVE_UPLOAD_SIZE,
        max_image_size=settings.MAX_UPLOAD_SIZE,max_expanded_size=settings.MAX_IMAGE_ARCHIVE_EXPANDED_SIZE,
        max_entries=settings.MAX_ARCHIVE_FILES,retention_days=settings.UPLOAD_RETENTION_DAYS)

@router.post('')
async def create(request:UploadCreate,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    if request.project_id is not None:
        await scoped_project(db,request.project_id,user.tenant_id)
    elif request.image_kind=='crops':
        raise HTTPException(422,'上传裁剪图需要指定项目')
    if request.trust_annotations and (not request.annotation_format or user.role!='admin'):
        raise HTTPException(403,'直接信任导入标注需要管理员明确确认YOLO导入')
    if request.annotation_format:
        if not request.filename.lower().endswith('.zip'):raise HTTPException(422,'YOLO标注导入请选择ZIP包')
    maximum=settings.MAX_UPLOAD_SIZE if request.filename.lower().endswith(uploads.IMAGES) else settings.MAX_IMAGE_ARCHIVE_UPLOAD_SIZE
    if request.size>maximum:raise HTTPException(413,'文件超过上传大小限制')
    if len(request.chunk_hashes)!=math.ceil(request.size/uploads.CHUNK_SIZE):raise HTTPException(422,'分片数量与文件大小不一致')
    entry=None
    if request.batch_id is not None:
        batch=await owned_batch(db,user,request.batch_id)
        if (batch.project_id!=request.project_id or batch.collection_name!=request.collection_name
            or batch.image_kind!=request.image_kind or batch.min_long_edge!=request.min_long_edge
            or batch.annotation_format!=request.annotation_format or batch.trust_annotations!=request.trust_annotations):
            raise HTTPException(409,'上传设置与批次不一致')
        entry=await batch_file(db,batch.id,request.file_key)
    group=await get_collection(db,user.tenant_id,request.collection_name)
    policy,_=await get_config(db,user.tenant_id,group.id)
    minimum=request.min_long_edge if request.min_long_edge is not None else (0 if request.image_kind=='crops' else policy.min_long_edge)
    key,legacy_key=uploads.resume_keys(request.filename,request.size,request.chunk_hashes,group.id,minimum,
        request.annotation_format,request.trust_annotations,request.project_id,request.image_kind)
    owner=select(ImageUpload).where(ImageUpload.tenant_id==user.tenant_id,ImageUpload.user_id==user.id)
    query=owner.where(ImageUpload.project_id==request.project_id,ImageUpload.resume_key==key).order_by(ImageUpload.created_at.desc()).limit(1)
    job=await db.scalar(query)
    if job is None:
        # Older receipts have no reliable project attribution. An explicit reselection supplies the target.
        legacy=await db.scalar(owner.where(ImageUpload.project_id.is_(None),ImageUpload.resume_key==legacy_key)
            .order_by(ImageUpload.created_at.desc()).limit(1).with_for_update())
        job=await db.scalar(query) if legacy is not None else None
        if job is None:job=legacy
    if job and job.updated_at<datetime.utcnow()-timedelta(days=settings.UPLOAD_RETENTION_DAYS) and job.status not in ('pending','processing'):
        old_id=job.id;await db.delete(job);await db.flush();await asyncio.to_thread(uploads.remove_staging,old_id);job=None
    if job and job.status=='completed':
        count=await db.scalar(select(func.count()).select_from(ImageUploadItem).join(ImageCollectionMember,
            (ImageUploadItem.image_id==ImageCollectionMember.image_id)&(ImageCollectionMember.collection_id==group.id)).where(ImageUploadItem.upload_id==job.id))
        if count!=job.unique_count or request.annotation_format:job=None
    if job is not None and job.resume_key==legacy_key:
        try:
            async with db.begin_nested():
                job.project_id=request.project_id
                job.resume_key=key
                await db.flush()
        except IntegrityError:
            job=await db.scalar(query)
            if job is None:raise
    if job is None:
        try:
            async with db.begin_nested():
                job=ImageUpload(tenant_id=user.tenant_id,user_id=user.id,collection_id=group.id,project_id=request.project_id,
                    filename=request.filename,size=request.size,chunk_hashes=request.chunk_hashes,resume_key=key,min_long_edge=minimum,
                    annotation_format=request.annotation_format,trust_annotations=request.trust_annotations,image_kind=request.image_kind)
                db.add(job);await db.flush()
        except IntegrityError:
            job=await db.scalar(query)
            if job is None:raise
    if entry is not None:
        await db.execute(delete(ImageUploadBatchFile).where(ImageUploadBatchFile.batch_id==entry.batch_id,
            ImageUploadBatchFile.file_key=='legacy:'+key))
        entry.upload_id=job.id
        entry.status=job.status
    job.updated_at=datetime.utcnow();await db.commit()
    return uploads.response(job)

async def upload_history_scope(db,user,project_id):
    scope=[ImageUpload.tenant_id==user.tenant_id,ImageUpload.project_id==project_id]
    if project_id is not None:
        await scoped_project(db,project_id,user.tenant_id)
    else:
        scope.append(ImageUpload.user_id==user.id)
    return scope


@router.get('')
async def listing(project_id:UUID|None=None,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    query=select(ImageUpload).where(*await upload_history_scope(db,user,project_id))
    jobs=(await db.scalars(query.order_by(ImageUpload.updated_at.desc(),ImageUpload.id.desc()).limit(20))).all()
    return [{**uploads.response(job),'can_manage':job.user_id==user.id} for job in jobs]

class UploadBatchCreate(BaseModel):
    project_id:UUID|None=None
    batch_id:UUID|None=None
    collection_name:str=Field(min_length=1,max_length=200)
    image_kind:Literal['originals','crops']='originals'
    min_long_edge:int|None=Field(default=None,ge=0,le=32768,strict=True)
    annotation_format:Literal['yolo']|None=None
    trust_annotations:bool=False
    file_keys:list[str]=Field(min_length=1,max_length=200000)


async def owned_batch(db,user,id,lock=False):
    query=select(ImageUploadBatch).where(ImageUploadBatch.id==id,ImageUploadBatch.tenant_id==user.tenant_id,
        ImageUploadBatch.user_id==user.id)
    batch=await db.scalar(query.with_for_update(key_share=True) if lock else query)
    if batch is None:raise HTTPException(404,'上传批次不存在')
    if batch.project_id:await scoped_project(db,batch.project_id,user.tenant_id)
    return batch


async def batch_file(db,id,key):
    entry=await db.get(ImageUploadBatchFile,(id,key)) if key else None
    if entry is None:raise HTTPException(409,'文件不属于此上传批次')
    return entry


@router.post('/batches',status_code=201)
async def save_batch(request:UploadBatchCreate,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    if request.project_id:await scoped_project(db,request.project_id,user.tenant_id)
    elif request.image_kind=='crops':raise HTTPException(422,'上传裁剪图需要指定项目')
    keys=sorted(set(request.file_keys))
    if len(keys)!=len(request.file_keys) or any(len(key)!=64 or any(char not in '0123456789abcdef' for char in key) for key in keys):
        raise HTTPException(422,'文件清单无效')
    manifest=hashlib.sha256(''.join(keys).encode()).hexdigest()
    if request.batch_id:
        batch=await owned_batch(db,user,request.batch_id,lock=True)
        if (batch.project_id!=request.project_id or batch.collection_name!=request.collection_name.strip()
            or batch.image_kind!=request.image_kind or batch.total!=len(keys) or batch.manifest not in (None,manifest)
            or batch.min_long_edge!=request.min_long_edge or batch.annotation_format!=request.annotation_format
            or batch.trust_annotations!=request.trust_annotations):
            raise HTTPException(409,'请重新选择此批次的原文件')
        if batch.manifest:
            completed=(await db.scalars(select(ImageUploadBatchFile.file_key).where(
                ImageUploadBatchFile.batch_id==batch.id,ImageUploadBatchFile.status=='completed'))).all()
            return {'id':str(batch.id),'completed_keys':completed}
    else:
        batch=ImageUploadBatch(tenant_id=user.tenant_id,user_id=user.id,project_id=request.project_id,
            collection_name=request.collection_name.strip(),image_kind=request.image_kind,total=len(keys),
            min_long_edge=request.min_long_edge,annotation_format=request.annotation_format,trust_annotations=request.trust_annotations)
        db.add(batch)
    batch.manifest=manifest
    await db.flush()
    await db.execute(insert(ImageUploadBatchFile),[dict(batch_id=batch.id,file_key=key,status='uploading') for key in keys])
    await db.commit()
    return {'id':str(batch.id),'completed_keys':[]}


@router.get('/batches')
async def list_batches(project_id:UUID|None=None,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    if project_id:await scoped_project(db,project_id,user.tenant_id)
    query=select(ImageUploadBatch).where(ImageUploadBatch.tenant_id==user.tenant_id,ImageUploadBatch.project_id==project_id)
    if not project_id:query=query.where(ImageUploadBatch.user_id==user.id)
    batches=(await db.scalars(query.order_by(ImageUploadBatch.created_at.desc(),ImageUploadBatch.id.desc()).limit(20))).all()
    state=case((ImageUpload.status.in_(('completed','failed','pending','processing')),ImageUpload.status),
        else_=ImageUploadBatchFile.status).label('status')
    counts={(id,status):count for id,status,count in (await db.execute(select(ImageUploadBatchFile.batch_id,state,func.count())
        .outerjoin(ImageUpload,ImageUpload.id==ImageUploadBatchFile.upload_id)
        .where(ImageUploadBatchFile.batch_id.in_([batch.id for batch in batches])).group_by(ImageUploadBatchFile.batch_id,state))).all()}
    active_single=[batch.id for batch in batches if batch.total==1 and
        (counts.get((batch.id,'pending')) or counts.get((batch.id,'processing')))]
    archive_progress={}
    if active_single:
        rows=await db.execute(select(ImageUploadBatchFile.batch_id,ImageUpload.filename,ImageUpload.cursor,
            ImageUpload.result['archive_total'].as_integer()).join(ImageUpload,ImageUpload.id==ImageUploadBatchFile.upload_id)
            .where(ImageUploadBatchFile.batch_id.in_(active_single),ImageUpload.status.in_(('pending','processing'))))
        archive_progress={id:dict(processed=cursor,total=total) for id,filename,cursor,total in rows
            if filename.lower().endswith(uploads.ARCHIVES)}
    result=[]
    for batch in batches:
        completed=counts.get((batch.id,'completed'),0)
        failed=counts.get((batch.id,'failed'),0)
        result.append(dict(id=str(batch.id),created_at=batch.created_at.isoformat()+'Z',collection_name=batch.collection_name,
            image_kind=batch.image_kind,total=batch.total,completed=completed,failed=failed,remaining=max(0,batch.total-completed-failed),
            can_resume=batch.user_id==user.id,min_long_edge=batch.min_long_edge,annotation_format=batch.annotation_format,
            trust_annotations=batch.trust_annotations,archive_progress=archive_progress.get(batch.id)))
    return result


@router.put('/batches/{id}/files/{key}/failed',status_code=204)
async def mark_batch_file_failed(id:UUID,key:str,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    await owned_batch(db,user,id)
    entry=await batch_file(db,id,key)
    entry.status='failed'
    await db.commit()
    return Response(status_code=204)


@router.get('/{id}')
async def detail(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    return uploads.response(await scoped(db,id,user))


@router.get('/{id}/images')
async def batch_images(id:UUID,page:int=Query(1,ge=1),page_size:int=Query(1000,ge=1,le=1000),
                       db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    job=await scoped(db,id,user)
    if job.status!='completed':raise HTTPException(409,'请等待本批图片入库完成后再添加到项目')
    query=select(ImageUploadItem.image_id).where(ImageUploadItem.upload_id==id)
    total=await db.scalar(select(func.count()).select_from(query.subquery()))
    ids=(await db.scalars(query.order_by(ImageUploadItem.image_id).offset((page-1)*page_size).limit(page_size))).all()
    return dict(image_ids=ids,total=total,page=page,page_size=page_size)

@router.put('/{id}/parts/{index}')
async def put_part(id:UUID,index:int,request:Request,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    job=await scoped(db,id,user)
    if index<0 or index>=len(job.chunk_hashes):raise HTTPException(422,'分片编号无效')
    expected=uploads.part_size(job,index)
    await db.commit()  # Do not hold a transaction while receiving bytes from a slow network.
    data=bytearray()
    async for block in request.stream():
        if len(data)+len(block)>expected:raise HTTPException(413,'分片大小超出限制')
        data.extend(block)
    if len(data)!=expected or hashlib.sha256(data).hexdigest()!=job.chunk_hashes[index]:raise HTTPException(422,'分片大小或校验不匹配')
    job=await scoped(db,id,user,lock=True)
    if job.status in ('pending','processing','completed'):
        return {'part':index,'received':len(data)}
    if job.status not in ('uploading','failed'):raise HTTPException(409,'任务已进入入库阶段')
    await asyncio.to_thread(uploads.write_part,job,index,data)
    job.updated_at=datetime.utcnow();await db.commit()
    return {'part':index,'received':len(data)}

@router.post('/{id}/complete')
async def complete(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    job=await scoped(db,id,user,lock=True)
    if job.status in ('uploading','failed'):
        if len(uploads.uploaded_parts(job))!=len(job.chunk_hashes):raise HTTPException(409,'仍有分片缺失，请继续上传')
        job.status='pending';job.error=None;job.updated_at=datetime.utcnow();await db.commit()
    return uploads.response(job)

@router.delete('/{id}',status_code=204)
async def discard(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    job=await scoped(db,id,user,lock=True)
    if job.status in ('pending','processing'):raise HTTPException(409,'文件正在入库，完成后可清除上传记录')
    await db.delete(job);await db.commit()
    await asyncio.to_thread(uploads.remove_staging,id)
    return Response(status_code=204)
