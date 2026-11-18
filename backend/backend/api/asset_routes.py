"""Asset CRUD and image uploads through the shared storage backend."""
import hashlib
import warnings
from io import BytesIO
from pathlib import Path
from urllib.parse import quote
from uuid import UUID
from typing import Annotated, Literal
from services.annotation_scope import eligible_assets,require_annotation_asset
from services.project_access import project_condition
from services.scene_categories import scene_result_status

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status, Response
from fastapi.responses import FileResponse, StreamingResponse
from PIL import Image, UnidentifiedImageError
from pydantic import StringConstraints
from sqlalchemy import and_, select, update, func, case, cast, or_, String
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool

from api.deps import get_current_user, get_tenant_id
from config.settings import settings
from database import get_db_session as get_db
from models import Asset, Project, User, Annotation, LibraryImage
from models.image_collection import ImageCollection,ImageCollectionMember
from services.image_library import register_image, attach_images, get_collection, assign_collection,annotation_label_condition,image_scene_condition,id_in_condition
from schemas.asset import AssetResponse, AssetUploadResponse
from schemas.common import PaginatedResponse, PaginationParams
from schemas.object_attributes import AttributeFilters
from services.storage import get_storage, LocalStorage

router = APIRouter()
ASSET_ORDER = (Asset.created_at.desc(), Asset.id.desc())
IMAGE_TYPES = {"JPEG": ("jpg", "image/jpeg"), "PNG": ("png", "image/png"),
               "BMP": ("bmp", "image/bmp"), "WEBP": ("webp", "image/webp")}

def calculate_file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()

async def scoped_project(db, project_id, tenant_id, for_update=False):
    query = select(Project).where(Project.id == project_id, Project.tenant_id == tenant_id,project_condition(db))
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    result = await db.execute(query)
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")
    return project

async def scoped_asset(db, asset_id, tenant_id, for_update=False):
    query = select(Asset).join(Project).where(Asset.id == asset_id, Project.tenant_id == tenant_id,project_condition(db))
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
    group=await get_collection(db,project.tenant_id,project.project_name)
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

def collection_key(value):
    return func.replace(cast(value,String),'-','')


async def asset_conditions(db,project,scope,image_kind,annotation_status=None,label=None,q=None,review_status=None,collection_id=None,*,scene=None,attributes=None,crop_depth=None,crop_job_id=None,work_job_id=None,platform_name=None,device_name=None,include_source_collections=False,annotation_kind=None,ai_pending=False,crop_label=None):
    conditions = [Asset.project_id == project.id]
    if crop_job_id is not None or work_job_id is not None:
        from services.project_workflow import result_job,output_ids
        job=await result_job(db,project,crop_job_id or work_job_id,crop_only=crop_job_id is not None)
        conditions.append(Asset.id.in_(output_ids(db,job)))
    if platform_name is not None or device_name:
        from services.image_library import image_source_condition
        conditions.append(image_source_condition(db,platform_name,device_name,Asset.device_info))
    if crop_depth is not None:
        depth=case((Asset.source_type=='model_crop',func.coalesce(Asset.device_info['crop']['depth'].as_integer(),1)),else_=0)
        conditions.append(depth==crop_depth)
    if scope=='annotation':conditions.append(eligible_assets(project))
    if image_kind!='all':conditions.append(Asset.source_type=='model_crop' if image_kind=='crops' else Asset.source_type!='model_crop')
    if crop_label:
        categories={label.lower().removeprefix('sam3_') for label in crop_label}
        label_value=func.lower(Asset.device_info['crop']['box']['label'].as_string())
        conditions.extend([Asset.source_type=='model_crop',label_value.in_(categories|{'sam3_'+label for label in categories})])
    if collection_id:
        members=select(ImageCollectionMember.image_id).join(ImageCollection,ImageCollection.id==ImageCollectionMember.collection_id).where(
            ImageCollectionMember.collection_id==collection_id,ImageCollection.tenant_id==project.tenant_id)
        direct=Asset.image_id.in_(members)
        if include_source_collections and image_kind!='originals':
            sources=members.with_only_columns(collection_key(ImageCollectionMember.image_id)).union_all(
                select(collection_key(Asset.id)).where(Asset.project_id==project.id,Asset.source_type!='model_crop',direct))
            direct=or_(direct,(Asset.source_type=='model_crop') & collection_key(
                Asset.device_info['parent_image_id'].as_string()).in_(sources))
        conditions.append(direct)
    if q and q.strip():conditions.append(Asset.original_name.icontains(q.strip(),autoescape=True))
    if scene:conditions.append(Asset.image_id.in_(select(LibraryImage.id).where(
        LibraryImage.tenant_id==project.tenant_id,await image_scene_condition(db,scene,project.tenant_id,collection_id))))
    if annotation_status:
        if annotation_status not in ("pending", "annotating", "completed", "reviewed"):
            raise HTTPException(status_code=400, detail="无效标注状态")
        conditions.append(Asset.annotation_status == annotation_status)
    if label:
        conditions.append(select(1).where(Annotation.asset_id==Asset.id,Annotation.is_latest.is_(True),
            annotation_label_condition(db,label)).exists())
    if review_status or ai_pending:
        latest=select(1).where(Annotation.asset_id==Asset.id,Annotation.is_latest.is_(True))
        if annotation_kind:latest=latest.where(Annotation.has_kind(annotation_kind))
        if review_status:
            state=Annotation.status_for_query(annotation_kind) if annotation_kind else Annotation.review_status
            latest=latest.where(state==review_status)
        if ai_pending:latest=latest.where(Annotation.ai_pending())
        conditions.append(latest.exists())
    if attributes and (attributes.attribute_key or attributes.attribute_value is not None or attributes.attribute_state!='all'):
        from services.object_attributes import attribute_query
        query=attribute_query(project,**attributes.model_dump(exclude={'review_status'}))
        conditions.append(Asset.id.in_(query.with_only_columns(Asset.id).correlate(None)))
    return conditions

async def asset_responses(db,assets,tenant_id):
    ids={asset.image_id for asset in assets if asset.image_id is not None}
    images={image.id:image for image in (await db.scalars(select(LibraryImage).where(
        LibraryImage.tenant_id==tenant_id,LibraryImage.id.in_(ids)))).all()} if ids else {}
    result=[]
    for asset in assets:
        image=images.get(asset.image_id)
        scene={key:getattr(image,key) for key in ('scene_labels','scene_status','scene_error','scene_metadata')} if image else {}
        if image:scene['scene_status']=scene_result_status(image.scene_status,image.scene_labels)
        result.append(AssetResponse.model_validate(asset).model_copy(update=scene))
    return result

@router.get("", response_model=PaginatedResponse[AssetResponse])
async def list_assets(response:Response,project_id: UUID = Query(...), annotation_status: str | None = Query(None),
                      label: str | None = Query(None,min_length=1,max_length=100),
                      crop_label: list[Annotated[str,StringConstraints(min_length=1,max_length=200)]] | None = Query(None),
                      q: str | None = Query(None,max_length=255),
                      collection_id:UUID|None=None,
                      scene:str|None=Query(None,min_length=1,max_length=60),
                      review_status:Literal['pending','approved','rejected']|None=None,
                      annotation_kind:Literal['detection','image']|None=None,ai_pending:bool=False,
                      attributes:AttributeFilters=Depends(),crop_depth:int|None=Query(None,ge=0),crop_job_id:UUID|None=None,work_job_id:UUID|None=None,
                      platform_name:str|None=Query(None,max_length=200),device_name:str|None=Query(None,max_length=200),
                      scope:Literal['all','annotation']='all',image_kind:Literal['all','originals','crops']='all',
                      pagination: PaginationParams = Depends(), tenant_id: int = Depends(get_tenant_id),
                      db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    project=await scoped_project(db, project_id, tenant_id)
    conditions=await asset_conditions(db,project,scope,image_kind,annotation_status,label,q,review_status,collection_id,scene=scene,attributes=attributes,crop_depth=crop_depth,crop_job_id=crop_job_id,work_job_id=work_job_id,platform_name=platform_name,device_name=device_name,include_source_collections=scope=='annotation',annotation_kind=annotation_kind,ai_pending=ai_pending,crop_label=crop_label)
    if crop_label:response.headers['X-Asset-Crop-Label-Filter']='1'
    if crop_job_id is not None:response.headers['X-Asset-Crop-Batch-Filter']='1'
    if work_job_id is not None:response.headers['X-Asset-Work-Filter']='1'
    if platform_name is not None or device_name:response.headers['X-Asset-Source-Filter']='1'
    if crop_depth is not None:response.headers['X-Asset-Crop-Depth-Filter']='1'
    if attributes.attribute_key:response.headers['X-Asset-Attribute-Filter']='1'
    if label:response.headers['X-Annotation-Label-Filter']='1'
    if q:response.headers['X-Asset-Filename-Filter']='1'
    if review_status:response.headers['X-Annotation-Review-Filter']='1'
    if collection_id:response.headers['X-Asset-Collection-Filter']='1'
    if scene:response.headers['X-Asset-Scene-Filter']='1'
    total = await db.scalar(select(func.count(Asset.id)).where(*conditions))
    result = await db.execute(select(Asset).where(*conditions).order_by(*ASSET_ORDER)
                              .offset(pagination.skip).limit(pagination.limit))
    return PaginatedResponse(items=await asset_responses(db,result.scalars().all(),tenant_id), total=total, page=pagination.page, page_size=pagination.page_size)

@router.get('/selection')
async def select_all_assets(response:Response,project_id:UUID,scope:Literal['all','annotation']='all',image_kind:Literal['all','originals','crops']='all',
                            q:str|None=Query(None,max_length=255),
                            collection_id:UUID|None=None,
                            scene:str|None=Query(None,min_length=1,max_length=60),
                            review_status:Literal['pending','approved','rejected']|None=None,
                            attributes:AttributeFilters=Depends(),crop_depth:int|None=Query(None,ge=0),crop_job_id:UUID|None=None,work_job_id:UUID|None=None,
                      platform_name:str|None=Query(None,max_length=200),device_name:str|None=Query(None,max_length=200),
                            annotation_status:str|None=None,label:str|None=Query(None,min_length=1,max_length=100),tenant_id:int=Depends(get_tenant_id),
                            db:AsyncSession=Depends(get_db),current_user:User=Depends(get_current_user)):
    project=await scoped_project(db,project_id,tenant_id)
    if q:response.headers['X-Asset-Filename-Filter']='1'
    if review_status:response.headers['X-Annotation-Review-Filter']='1'
    if collection_id:response.headers['X-Asset-Collection-Filter']='1'
    if scene:response.headers['X-Asset-Scene-Filter']='1'
    conditions=await asset_conditions(db,project,scope,image_kind,annotation_status,label,q,review_status,collection_id,scene=scene,attributes=attributes,crop_depth=crop_depth,crop_job_id=crop_job_id,work_job_id=work_job_id,platform_name=platform_name,device_name=device_name)
    if crop_job_id is not None:response.headers['X-Asset-Crop-Batch-Filter']='1'
    if work_job_id is not None:response.headers['X-Asset-Work-Filter']='1'
    if platform_name is not None or device_name:response.headers['X-Asset-Source-Filter']='1'
    if crop_depth is not None:response.headers['X-Asset-Crop-Depth-Filter']='1'
    if attributes.attribute_key:response.headers['X-Asset-Attribute-Filter']='1'
    ids=(await db.scalars(select(Asset.id).where(*conditions).order_by(*ASSET_ORDER))).all()
    return {'asset_ids':ids,'total':len(ids)}


@router.get('/collections')
async def project_collections(project_id:UUID,include_source_collections:bool=False,tenant_id:int=Depends(get_tenant_id),
    db:AsyncSession=Depends(get_db),current_user:User=Depends(get_current_user)):
    project=await scoped_project(db,project_id,tenant_id)
    members=ImageCollectionMember.image_id
    matching=members.in_(select(Asset.image_id).where(Asset.project_id==project_id,eligible_assets(project)))
    if include_source_collections:
        roots=select(collection_key(Asset.device_info['parent_image_id'].as_string())).where(
            Asset.project_id==project_id,Asset.source_type=='model_crop')
        matching=or_(matching,collection_key(members).in_(roots),members.in_(select(Asset.image_id).where(
            Asset.project_id==project_id,Asset.source_type!='model_crop',collection_key(Asset.id).in_(roots))))
    rows=(await db.execute(select(ImageCollection.id,ImageCollection.name)
        .join(ImageCollectionMember,ImageCollectionMember.collection_id==ImageCollection.id)
        .where(ImageCollection.tenant_id==tenant_id,matching).distinct().order_by(ImageCollection.name))).all()
    return [{'id':str(id),'name':name} for id,name in rows]

@router.get("/{asset_id}", response_model=AssetResponse)
async def get_asset(asset_id: UUID, tenant_id: int = Depends(get_tenant_id),
                    db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    return (await asset_responses(db,[await scoped_asset(db, asset_id, tenant_id)],tenant_id))[0]

@router.get("/{asset_id}/neighbors")
async def asset_neighbors(asset_id: UUID, response:Response,label:str|None=Query(None,min_length=1,max_length=100),
                          crop_label:list[Annotated[str,StringConstraints(min_length=1,max_length=200)]]|None=Query(None),
                          attributes:AttributeFilters=Depends(),
                          image_kind:Literal['all','originals','crops']='all',
                          annotation_kind:Literal['detection','image']|None=None,ai_pending:bool=False,
                          scene:str|None=Query(None,min_length=1,max_length=60),
                          review_status:Literal['pending','approved','rejected']|None=None,collection_id:UUID|None=None,work_job_id:UUID|None=None,tenant_id: int = Depends(get_tenant_id),
                          db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    asset = await scoped_asset(db, asset_id, tenant_id)
    project=await scoped_project(db,asset.project_id,tenant_id)
    require_annotation_asset(project,asset)
    conditions=await asset_conditions(db,project,'annotation',image_kind,label=label,review_status=review_status,collection_id=collection_id,
        scene=scene,attributes=attributes,work_job_id=work_job_id,include_source_collections=True,annotation_kind=annotation_kind,ai_pending=ai_pending,crop_label=crop_label)
    if crop_label:response.headers['X-Asset-Crop-Label-Filter']='1'
    if work_job_id:response.headers['X-Asset-Work-Filter']='1'
    if attributes.attribute_key:response.headers['X-Asset-Attribute-Filter']='1'
    if review_status:response.headers['X-Annotation-Review-Filter']='1'
    if collection_id:response.headers['X-Asset-Collection-Filter']='1'
    if scene:response.headers['X-Asset-Scene-Filter']='1'
    anchor_created = asset.created_at if asset.created_at is not None else select(Asset.created_at).where(Asset.id == asset.id).correlate(None).scalar_subquery()
    ahead = or_(Asset.created_at > anchor_created, and_(Asset.created_at == anchor_created, Asset.id > asset.id))
    behind = or_(Asset.created_at < anchor_created, and_(Asset.created_at == anchor_created, Asset.id < asset.id))
    is_matched = (await db.scalar(select(Asset.id).where(*conditions, Asset.id == asset.id).limit(1))) is not None
    has_filter = bool(review_status or ai_pending or label or crop_label or collection_id or scene or attributes.attribute_key or work_job_id or image_kind != 'all')
    if not is_matched:
        if has_filter:
            total = await db.scalar(select(func.count(Asset.id)).where(*conditions)) or 0
            previous = await db.scalar(select(Asset.id).where(*conditions, ahead).order_by(Asset.created_at.asc(), Asset.id.asc()).limit(1)) if total else None
            following = await db.scalar(select(Asset.id).where(*conditions, behind).order_by(*ASSET_ORDER).limit(1)) if total else None
            return dict(position=0, total=total, previous_id=previous, next_id=following, matched=False)
        raise HTTPException(404, '资产不存在')
    total = await db.scalar(select(func.count(Asset.id)).where(*conditions)) or 0
    previous = await db.scalar(select(Asset.id).where(*conditions, ahead).order_by(Asset.created_at.asc(), Asset.id.asc()).limit(1)) if total > 1 else None
    following = await db.scalar(select(Asset.id).where(*conditions, behind).order_by(*ASSET_ORDER).limit(1)) if total > 1 else None
    if previous is None:
        position = 1
    elif following is None:
        position = total
    else:
        position = (await db.scalar(select(func.count(Asset.id)).where(*conditions, ahead)) or 0) + 1
    include_matched = bool(review_status or ai_pending or crop_label or collection_id or scene or attributes.attribute_key or work_job_id or image_kind != 'all')
    return {**dict(position=position, total=total, previous_id=previous, next_id=following), **({'matched': True} if include_matched else {})}

async def original_source(db, asset, tenant_id):
    if asset.source_type!='model_crop':return asset,None
    info=asset.device_info or {};crop=info.get('crop') or {};chain=info.get('crop_chain') or [crop]
    if not isinstance(crop,dict) or not isinstance(chain,list) or not chain or not all(isinstance(step,dict) for step in chain):
        raise HTTPException(404,'原图来源记录不完整，无法定位')
    first=chain[0]
    try:parent_id=UUID(first['parent_asset_id'])
    except (KeyError,ValueError,TypeError):raise HTTPException(404,'原图来源记录不完整，无法定位') from None
    original=await db.get(Asset,parent_id)
    if original is not None and original.project_id!=asset.project_id:raise HTTPException(404,'原图不属于当前项目')
    if original is None:
        try:image_id=UUID(first['parent_image_id'])
        except (KeyError,ValueError,TypeError):raise HTTPException(404,'原图已移除，无法定位') from None
        original=await db.get(LibraryImage,image_id)
        if original is None or original.tenant_id!=tenant_id:raise HTTPException(404,'原图已移除，无法定位')
    if not first.get('parent_content_hash') or original.content_hash!=first['parent_content_hash']:
        raise HTTPException(409,'原图与裁剪来源不一致，无法定位')
    region=crop.get('root_region')
    if region is None:
        if crop.get('depth',1)>len(chain):raise HTTPException(404,'多轮裁剪来源记录不完整，无法定位')
        try:
            x=sum(step['region'][0] for step in chain[:-1]);y=sum(step['region'][1] for step in chain[:-1])
            r=crop['region'];region=[x+r[0],y+r[1],x+r[2],y+r[3]]
        except (KeyError,TypeError,IndexError):raise HTTPException(404,'裁剪坐标缺失，无法定位') from None
    if not isinstance(region,list) or len(region)!=4 or not all(type(v) is int for v in region) or not original.width or not original.height or not (0<=region[0]<region[2]<=original.width and 0<=region[1]<region[3]<=original.height):
        raise HTTPException(409,'裁剪坐标超出原图范围，无法定位')
    return original,region


@router.get('/{asset_id}/source')
async def asset_source(asset_id:UUID,tenant_id:int=Depends(get_tenant_id),db:AsyncSession=Depends(get_db),current_user:User=Depends(get_current_user)):
    original,region=await original_source(db,await scoped_asset(db,asset_id,tenant_id),tenant_id)
    return dict(original_name=original.original_name,width=original.width,height=original.height,region=region,content_url=f'/api/assets/{asset_id}/source/content')


@router.get('/{asset_id}/source/content')
async def asset_source_content(asset_id:UUID,preview:bool=False,tenant_id:int=Depends(get_tenant_id),db:AsyncSession=Depends(get_db),current_user:User=Depends(get_current_user)):
    original,_=await original_source(db,await scoped_asset(db,asset_id,tenant_id),tenant_id)
    response=await image_content(original,preview)
    await db.rollback()
    return response


@router.get("/{asset_id}/content")
async def asset_content(asset_id: UUID, preview: bool = False, tenant_id: int = Depends(get_tenant_id),
                        db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    response=await image_content(await scoped_asset(db, asset_id, tenant_id),preview)
    await db.rollback()
    return response

def image_preview(data, max_edge=1280):
    with Image.open(data) as picture:
        picture.thumbnail((max_edge, max_edge))
        output=BytesIO()
        picture.convert('RGB').save(output,'JPEG',quality=82)
        return output.getvalue()


async def image_content(asset,preview=False,*,max_edge=1280):
    storage = get_storage()
    media_type = IMAGE_TYPES.get(asset.format, ("", "application/octet-stream"))[1]
    try:
        data = await storage.get_file(asset.blob_path)
    except (FileNotFoundError, ValueError):
        # Existing uploads may still use paths under the old, configured upload root.
        legacy = Path(asset.blob_path).resolve()
        if not (isinstance(storage, LocalStorage) and legacy.is_relative_to(Path(settings.UPLOAD_DIR).resolve()) and legacy.is_file()):
            raise HTTPException(status_code=404, detail="图片文件不存在")
        if not preview:return FileResponse(legacy, media_type=media_type)
        data=legacy.open('rb')
    if preview:
        try:
            with data:
                return Response(await run_in_threadpool(image_preview,data,max_edge),media_type='image/jpeg')
        except (OSError,ValueError,Image.DecompressionBombError) as exc:
            raise HTTPException(422,'图片预览生成失败，可加载原尺寸查看') from exc
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

