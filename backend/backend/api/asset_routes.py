"""Asset CRUD and image uploads through the shared storage backend."""
import hashlib
import warnings
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from uuid import UUID
from typing import Literal
from services.annotation_scope import eligible_assets,require_annotation_asset

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status, Response
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from api.deps import get_current_user, get_tenant_id
from config.settings import settings
from database import get_db_session as get_db
from models import Asset, Project, User, Annotation
from services.image_library import register_image, attach_images, get_collection, assign_collection
from schemas.asset import AssetResponse, AssetUploadResponse
from schemas.common import PaginatedResponse, PaginationParams
from services.storage import get_storage, LocalStorage

router = APIRouter()
ASSET_ORDER = (Asset.created_at.desc(), Asset.id.desc())
IMAGE_TYPES = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"),
               "BMP": ("bmp", "image/bmp"), "WEBP": ("webp", "image/webp")}

def calculate_file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

async def scoped_project(db, project_id, tenant_id):
    result = await db.execute(select(Project).where(Project.id == project_id, Project.tenant_id == tenant_id))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project

async def scoped_asset(db, asset_id, tenant_id, for_update=False):
    query = select(Asset).join(Project).where(Asset.id == asset_id, Project.tenant_id == tenant_id)
    if for_update:
        query = query.with_for_update(of=Asset)
    result = await db.execute(query)
    asset = result.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail="资产不存在")
    return asset

def upload_response(asset):
    return AssetUploadResponse(asset_id=asset.id, original_name=asset.original_name,
                               blob_path=asset.blob_path, file_size=asset.file_size)

async def validate_image_upload(file):
    name = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not name or len(name) > 255 or Path(name).suffix.lower() not in (".jpg", ".jpeg", ".png", ".bmp", ".webp"):
        raise HTTPException(status_code=400, detail="请选择有效的jpg/png/bmp/webp图片")
    content = await file.read(settings.MAX_UPLOAD_SIZE + 1)
    if len(content) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(status_code=413, detail="图片超过上传大小限制")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(content)) as image:
                width, height, image_format = image.width, image.height, image.format
                image.verify()
            with Image.open(BytesIO(content)) as image:
                image.load()
        if image_format not in IMAGE_TYPES:
            raise ValueError("Unsupported image format")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(status_code=400, detail="图片无效、损坏或分辨率过大")

    digest = calculate_file_hash(content)
    return content, dict(original_name=name, content_hash=digest, file_size=len(content),
        width=width, height=height, format=image_format, source_type='upload')

async def read_upload(file, min_long_edge=0):
    content, values = await validate_image_upload(file)
    from services.image_ingest import check_resolution
    check_resolution(values['width'],values['height'],min_long_edge)
    digest, image_format = values['content_hash'], values['format']
    key = f"images/{digest[:2]}/{digest}.{IMAGE_TYPES[image_format][0]}"
    storage = get_storage()
    if not await storage.file_exists(key):
        await storage.save_file(BytesIO(content), key)
    return dict(**values, blob_path=key)

async def register_upload(file, project_id, db, skip_filtered=False):
    project = await db.get(Project, project_id)
    from services.image_ingest import get_config,ResolutionFiltered
    from datetime import datetime,timezone,timedelta
    group=await get_collection(db,project.tenant_id,datetime.now(timezone(timedelta(hours=8))).strftime('%m%d'))
    config,_=await get_config(db,project.tenant_id,group.id)
    try:
        values=await read_upload(file,config.min_long_edge)
    except ResolutionFiltered as exc:
        if skip_filtered:return None
        raise HTTPException(422,str(exc))
    image = await register_image(db, project.tenant_id, values)
    await assign_collection(db,project.tenant_id,[image.id],group.name)
    assets, _ = await attach_images(db, project_id, project.tenant_id, [image.id])
    return assets[0]

@router.post("/upload", response_model=AssetUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_asset(project_id: UUID = Query(...), file: UploadFile = File(...),
                       tenant_id: int = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
                       current_user: User = Depends(get_current_user)):
    await scoped_project(db, project_id, tenant_id)
    asset = await register_upload(file, project_id, db)
    await db.commit()
    return upload_response(asset)

@router.post("/batch-upload", response_model=list[AssetUploadResponse], status_code=status.HTTP_201_CREATED)
async def batch_upload_assets(response:Response,project_id: UUID = Query(...), files: list[UploadFile] = File(...),
                             tenant_id: int = Depends(get_tenant_id), db: AsyncSession = Depends(get_db),
                             current_user: User = Depends(get_current_user)):
    await scoped_project(db, project_id, tenant_id)
    if not files:
        raise HTTPException(status_code=400, detail="请选择图片")
    assets=[];skipped=0
    for file in files:
        asset=await register_upload(file,project_id,db,skip_filtered=True)
        if asset is None:skipped+=1
        else:assets.append(upload_response(asset))
    response.headers['X-Images-Skipped']=str(skipped)
    await db.commit()
    return assets

def asset_conditions(db,project,scope,image_kind,annotation_status=None,label=None,q=None,review_status=None):
    conditions = [Asset.project_id == project.id]
    if scope=='annotation':conditions.append(eligible_assets(project))
    if image_kind!='all':conditions.append(Asset.source_type=='model_crop' if image_kind=='crops' else Asset.source_type!='model_crop')
    if q and q.strip():conditions.append(Asset.original_name.icontains(q.strip(),autoescape=True))
    if annotation_status:
        if annotation_status not in ("pending", "annotating", "completed", "reviewed"):
            raise HTTPException(status_code=400, detail="无效标注状态")
        conditions.append(Asset.annotation_status == annotation_status)
    if label:
        if db.bind.dialect.name=='sqlite':
            boxes=func.json_each(Annotation.annotation_data,'$.boxes').table_valued('value')
            matches=select(1).select_from(boxes).where(func.json_extract(boxes.c.value,'$.label')==label).exists()
        else:
            matches=Annotation.annotation_data['boxes'].contains([{'label':label}])
        conditions.append(select(1).where(Annotation.asset_id==Asset.id,Annotation.is_latest.is_(True),matches).exists())
    if review_status:
        conditions.append(select(1).where(Annotation.asset_id==Asset.id,Annotation.is_latest.is_(True),Annotation.review_status==review_status).exists())
    return conditions

@router.get("", response_model=PaginatedResponse[AssetResponse])
async def list_assets(response:Response,project_id: UUID = Query(...), annotation_status: str | None = Query(None),
                      label: str | None = Query(None,min_length=1,max_length=100),
                      q: str | None = Query(None,max_length=255),
                      review_status:Literal['pending','approved','rejected']|None=None,
                      scope:Literal['all','annotation']='all',image_kind:Literal['all','originals','crops']='all',
                      pagination: PaginationParams = Depends(), tenant_id: int = Depends(get_tenant_id),
                      db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    project=await scoped_project(db, project_id, tenant_id)
    conditions=asset_conditions(db,project,scope,image_kind,annotation_status,label,q,review_status)
    if label:response.headers['X-Annotation-Label-Filter']='1'
    if q:response.headers['X-Asset-Filename-Filter']='1'
    if review_status:response.headers['X-Annotation-Review-Filter']='1'
    total = await db.scalar(select(func.count(Asset.id)).where(*conditions))
    result = await db.execute(select(Asset).where(*conditions).order_by(*ASSET_ORDER)
                              .offset(pagination.skip).limit(pagination.limit))
    return PaginatedResponse(items=result.scalars().all(), total=total, page=pagination.page, page_size=pagination.page_size)

@router.get('/selection')
async def select_all_assets(response:Response,project_id:UUID,scope:Literal['all','annotation']='all',image_kind:Literal['all','originals','crops']='all',
                            q:str|None=Query(None,max_length=255),
                            review_status:Literal['pending','approved','rejected']|None=None,
                            annotation_status:str|None=None,label:str|None=Query(None,min_length=1,max_length=100),tenant_id:int=Depends(get_tenant_id),
                            db:AsyncSession=Depends(get_db),current_user:User=Depends(get_current_user)):
    project=await scoped_project(db,project_id,tenant_id)
    if q:response.headers['X-Asset-Filename-Filter']='1'
    if review_status:response.headers['X-Annotation-Review-Filter']='1'
    ids=(await db.scalars(select(Asset.id).where(*asset_conditions(db,project,scope,image_kind,annotation_status,label,q,review_status)).order_by(*ASSET_ORDER))).all()
    return {'asset_ids':ids,'total':len(ids)}

@router.get("/{asset_id}", response_model=AssetResponse)
async def get_asset(asset_id: UUID, tenant_id: int = Depends(get_tenant_id),
                    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    return await scoped_asset(db, asset_id, tenant_id)

@router.get("/{asset_id}/neighbors")
async def asset_neighbors(asset_id: UUID, response:Response,label:str|None=Query(None,min_length=1,max_length=100),
                          review_status:Literal['pending','approved','rejected']|None=None,tenant_id: int = Depends(get_tenant_id),
                          db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    asset = await scoped_asset(db, asset_id, tenant_id)
    project=await scoped_project(db,asset.project_id,tenant_id)
    require_annotation_asset(project,asset)
    conditions=asset_conditions(db,project,'annotation','all',label=label,review_status=review_status)
    if review_status:response.headers['X-Annotation-Review-Filter']='1'
    ranked = select(Asset.id,
        func.row_number().over(order_by=ASSET_ORDER).label('position'),
        func.count().over().label('total'),
        func.lag(Asset.id, type_=Asset.id.type).over(order_by=ASSET_ORDER).label('previous_id'),
        func.lead(Asset.id, type_=Asset.id.type).over(order_by=ASSET_ORDER).label('next_id'),
    ).where(*conditions).subquery()
    row = (await db.execute(select(ranked.c.position, ranked.c.total, ranked.c.previous_id, ranked.c.next_id)
        .where(ranked.c.id == asset_id))).mappings().one_or_none()
    if row is None:
        if review_status:
            anchor_created_at=select(Asset.created_at).where(Asset.id==asset.id).correlate(None).scalar_subquery()
            ahead=(Asset.created_at>anchor_created_at)|((Asset.created_at==anchor_created_at)&(Asset.id>asset.id))
            behind=(Asset.created_at<anchor_created_at)|((Asset.created_at==anchor_created_at)&(Asset.id<asset.id))
            total=await db.scalar(select(func.count()).select_from(Asset).where(*conditions))
            previous=await db.scalar(select(Asset.id).where(*conditions,ahead).order_by(Asset.created_at.asc(),Asset.id.asc()).limit(1))
            following=await db.scalar(select(Asset.id).where(*conditions,behind).order_by(*ASSET_ORDER).limit(1))
            return dict(position=0,total=total,previous_id=previous,next_id=following,matched=False)
        raise HTTPException(404, '资产不存在')
    return {**dict(row),**({'matched':True} if review_status else {})}

@router.get("/{asset_id}/content")
async def asset_content(asset_id: UUID, tenant_id: int = Depends(get_tenant_id),
                        db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    return await image_content(await scoped_asset(db, asset_id, tenant_id))

async def image_content(asset):
    storage = get_storage()
    media_type = IMAGE_TYPES.get(asset.format, ("", "application/octet-stream"))[1]
    try:
        data = await storage.get_file(asset.blob_path)
    except (FileNotFoundError, ValueError):
        # Existing uploads may still use paths under the old, configured upload root.
        legacy = Path(asset.blob_path).resolve()
        if isinstance(storage, LocalStorage) and legacy.is_relative_to(Path(settings.UPLOAD_DIR).resolve()) and legacy.is_file():
            return FileResponse(legacy, media_type=media_type)
        raise HTTPException(status_code=404, detail="图片文件不存在")
    return StreamingResponse(iter(lambda: data.read(65536), b""), media_type=media_type,
                             headers={"Content-Disposition": "inline; filename*=UTF-8''" + quote(asset.original_name, safe="")},
                             background=BackgroundTask(data.close))

@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(asset_id: UUID, tenant_id: int = Depends(get_tenant_id),
                       db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    asset = await scoped_asset(db, asset_id, tenant_id, for_update=True)
    # shortcut: retain immutable files until SmartEye download references and garbage collection are integrated.
    await db.delete(asset)
    await db.execute(update(Project).where(Project.id == asset.project_id).values(
        total_assets=Project.total_assets - 1,
        annotated_count=Project.annotated_count - int(asset.annotation_status in ('completed', 'reviewed')),
        reviewed_count=Project.reviewed_count - int(asset.annotation_status == 'reviewed')))
    await db.commit()

