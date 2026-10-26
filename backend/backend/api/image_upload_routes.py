"""Authenticated resumable transport; all image imports use the existing library policy."""
import asyncio,hashlib,json,math
from datetime import datetime,timedelta
from pathlib import PurePosixPath
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Request,Response
from pydantic import BaseModel,Field,field_validator
from sqlalchemy import select,func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from api.deps import get_current_user
from database import get_db_session
from models import User,ImageUpload,ImageUploadItem,ImageCollectionMember
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
    min_long_edge:int|None=Field(default=None,ge=0,le=32768,strict=True)
    annotation_format:Literal['yolo']|None=None
    trust_annotations:bool=False

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
    return dict(chunk_size=uploads.CHUNK_SIZE,max_file_size=settings.MAX_ARCHIVE_UPLOAD_SIZE,
        max_image_size=settings.MAX_UPLOAD_SIZE,max_expanded_size=settings.MAX_ARCHIVE_EXPANDED_SIZE,
        max_entries=settings.MAX_ARCHIVE_FILES,retention_days=settings.UPLOAD_RETENTION_DAYS)

@router.post('')
async def create(request:UploadCreate,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    if request.trust_annotations and (not request.annotation_format or user.role!='admin'):
        raise HTTPException(403,'直接信任导入标注需要管理员明确确认YOLO导入')
    if request.annotation_format:
        if not request.filename.lower().endswith('.zip'):raise HTTPException(422,'YOLO标注导入请选择ZIP包')
    maximum=settings.MAX_UPLOAD_SIZE if request.filename.lower().endswith(uploads.IMAGES) else settings.MAX_ARCHIVE_UPLOAD_SIZE
    if request.size>maximum:raise HTTPException(413,'文件超过上传大小限制')
    if len(request.chunk_hashes)!=math.ceil(request.size/uploads.CHUNK_SIZE):raise HTTPException(422,'分片数量与文件大小不一致')
    group=await get_collection(db,user.tenant_id,request.collection_name)
    policy,_=await get_config(db,user.tenant_id,group.id)
    minimum=request.min_long_edge if request.min_long_edge is not None else policy.min_long_edge
    identity=[request.filename,request.size,request.chunk_hashes,str(group.id),minimum]
    if request.annotation_format:identity.extend([request.annotation_format,request.trust_annotations])
    key=hashlib.sha256(json.dumps(identity,separators=(',',':')).encode()).hexdigest()
    query=select(ImageUpload).where(ImageUpload.tenant_id==user.tenant_id,ImageUpload.user_id==user.id,ImageUpload.resume_key==key).order_by(ImageUpload.created_at.desc()).limit(1)
    job=await db.scalar(query)
    if job and job.updated_at<datetime.utcnow()-timedelta(days=settings.UPLOAD_RETENTION_DAYS) and job.status not in ('pending','processing'):
        old_id=job.id;await db.delete(job);await db.flush();await asyncio.to_thread(uploads.remove_staging,old_id);job=None
    if job and job.status=='completed':
        count=await db.scalar(select(func.count()).select_from(ImageUploadItem).join(ImageCollectionMember,
            (ImageUploadItem.image_id==ImageCollectionMember.image_id)&(ImageCollectionMember.collection_id==group.id)).where(ImageUploadItem.upload_id==job.id))
        if count!=job.unique_count or request.annotation_format:job=None
    if job is None:
        try:
            async with db.begin_nested():
                job=ImageUpload(tenant_id=user.tenant_id,user_id=user.id,collection_id=group.id,
                    filename=request.filename,size=request.size,chunk_hashes=request.chunk_hashes,resume_key=key,min_long_edge=minimum,
                    annotation_format=request.annotation_format,trust_annotations=request.trust_annotations)
                db.add(job);await db.flush()
        except IntegrityError:
            job=await db.scalar(query)
            if job is None:raise
    job.updated_at=datetime.utcnow();await db.commit()
    return uploads.response(job)

@router.get('')
async def listing(db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    jobs=(await db.scalars(select(ImageUpload).where(ImageUpload.tenant_id==user.tenant_id,ImageUpload.user_id==user.id)
        .order_by(ImageUpload.updated_at.desc()).limit(20))).all()
    return [uploads.response(job) for job in jobs]

@router.get('/{id}')
async def detail(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    return uploads.response(await scoped(db,id,user))

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
