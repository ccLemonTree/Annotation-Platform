"""Standalone upload-to-overlay previews; no catalog or annotation writes."""
from uuid import UUID
import asyncio,shutil
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from fastapi import APIRouter,Depends,HTTPException,Response,Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartException
from starlette.exceptions import HTTPException as StarletteHTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from api.deps import get_current_user
from api.validation_routes import page_result
from database import get_db_session
from models import User
from models.validation import ValidationRun,ValidationItem,ValidationSet
from schemas.common import PaginationParams
from services import validation,model_preview
from api.asset_routes import scoped_project
from services import model_preview_video
from services.storage import get_storage
from config.settings import settings

router=APIRouter()


@router.post('',status_code=201)
async def create(request:Request,
    db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    received=0
    async def receive():
        nonlocal received
        message=await request.receive()
        if message['type']=='http.disconnect':raise MultiPartException('上传请求已断开')
        received+=len(message.get('body',b''))
        if received>settings.MAX_ARCHIVE_UPLOAD_SIZE+16*1024**2:raise MultiPartException('上传请求超过大小限制')
        return message
    bounded=Request(request.scope,receive=receive)
    try:
        async with bounded.form(max_files=model_preview.MAX_IMAGES,max_fields=1,max_part_size=256000) as form:
            raw=form.get('request');files=form.getlist('files')
            if not isinstance(raw,str) or any(not isinstance(file,UploadFile) for file in files):
                raise HTTPException(422,'请选择图片、压缩包或文件夹，并填写模型参数')
            try:config=model_preview.PreviewInput.model_validate_json(raw)
            except ValidationError:raise HTTPException(422,'预览参数无效，请检查模型用途、来源、提示词、检索查询或权重配置')
            return validation.run_dict(await model_preview.create(db,user,config,files))
    except StarletteHTTPException as error:
        if error.detail=='上传请求超过大小限制':raise HTTPException(413,error.detail)
        raise


@router.get('/config')
async def config(user:User=Depends(get_current_user)):
    return dict(max_images=model_preview.MAX_IMAGES,max_upload_size=settings.MAX_ARCHIVE_UPLOAD_SIZE,
        max_expanded_size=settings.MAX_ARCHIVE_EXPANDED_SIZE,max_image_size=settings.MAX_UPLOAD_SIZE,
        max_video_size=model_preview_video.MAX_VIDEO_SIZE)


@router.get('')
async def listing(project_id:UUID|None=None,task_type:Literal['detection','caption','embedding']='detection',pagination:PaginationParams=Depends(),db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    query=select(ValidationRun).where(ValidationRun.tenant_id==user.tenant_id,ValidationRun.user_id==user.id,
        ValidationRun.model_snapshot['preview'].as_boolean().is_(True))
    if project_id:
        await scoped_project(db,project_id,user.tenant_id)
        query=query.join(ValidationSet,ValidationSet.id==ValidationRun.set_id).where(ValidationSet.project_id==project_id)
    query=query.where(validation.multimodal.kind_filter(ValidationRun.model_snapshot,task_type))
    return await page_result(db,query,ValidationRun,pagination,validation.run_dict)


@router.get('/{id}')
async def detail(id:UUID,pagination:PaginationParams=Depends(),db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    run=await model_preview.scoped(db,id,user)
    order=ValidationItem.original_name if run.model_snapshot.get('video') else ValidationItem.created_at
    items=(await db.scalars(select(ValidationItem).where(ValidationItem.run_id==id).order_by(order,ValidationItem.id)
        .offset(pagination.skip).limit(pagination.limit))).all()
    return {**validation.run_dict(run),'items':[validation.item_dict(item) for item in items],
        'page':pagination.page,'page_size':pagination.page_size}


@router.post('/{id}/{action}')
async def change(id:UUID,action:str,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    run=await model_preview.scoped(db,id,user,lock=True)
    if action not in ('cancel','retry'):raise HTTPException(404,'不支持的预览操作')
    operation=validation.cancel_run if action=='cancel' else validation.retry_run
    return validation.run_dict(await operation(db,run))


@router.delete('/{id}',status_code=204)
async def remove(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    await model_preview.remove(db,await model_preview.scoped(db,id,user,lock=True))


@router.get('/{id}/items/{item_id}/image')
async def image(id:UUID,item_id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    run=await model_preview.scoped(db,id,user)
    item=await db.scalar(select(ValidationItem).where(ValidationItem.id==item_id,ValidationItem.run_id==id))
    if item is None:raise HTTPException(404,'预览图片不存在')
    dataset=await db.get(ValidationSet,run.set_id)
    sample=next(sample for sample in dataset.samples if sample['asset_id']==str(item.asset_id))
    content,media_type=await validation.read_sample(sample)
    return Response(content,media_type=media_type,headers={'Cache-Control':'private, no-store'})


@router.get('/{id}/video')
async def video(id:UUID,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    run=await model_preview.scoped(db,id,user)
    if not run.model_snapshot.get('video'):raise HTTPException(404,'此预览没有视频')
    if run.status!='completed':raise HTTPException(409,'请等待所有采样画面分析完成')
    dataset=await db.get(ValidationSet,run.set_id)
    items=(await db.scalars(select(ValidationItem).where(ValidationItem.run_id==id)
        .order_by(ValidationItem.original_name,ValidationItem.id))).all()
    temporary=TemporaryDirectory()
    try:
        source=await get_storage().get_file(dataset.source['video_path'])
        try:
            with (Path(temporary.name)/'source').open('wb') as target:
                await asyncio.to_thread(shutil.copyfileobj,source,target)
        finally:source.close()
        pending=asyncio.create_task(asyncio.to_thread(model_preview_video.render,Path(temporary.name)/'source',items,
            run.model_snapshot['video']['interval'],temporary.name))
        try:output=await asyncio.shield(pending)
        except asyncio.CancelledError:await pending;raise
        return FileResponse(output,media_type='video/mp4',filename=Path(run.name).stem+'-检测结果.mp4',
            headers={'Cache-Control':'private, no-store'},background=BackgroundTask(temporary.cleanup))
    except BaseException:
        temporary.cleanup()
        raise
