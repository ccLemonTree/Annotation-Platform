"""Independent image library, filtered browsing and project references."""
from datetime import datetime,timezone,timedelta
from uuid import UUID,uuid4
from fastapi import APIRouter,Depends,File,Form,UploadFile,HTTPException,Response
from sqlalchemy import select,func,delete
from sqlalchemy.ext.asyncio import AsyncSession
from api.deps import get_current_user,get_tenant_id,get_current_superuser
from api.asset_routes import read_upload, image_content
from database import get_db_session
from models import LibraryImage,Asset,User,ImageCollection,ImageCollectionMember,ImageUpload,Project
from services.image_ingest import get_config,ResolutionFiltered
from models.image_ingest import ImageIngestSettings
from schemas.image_ingest import ImageIngestConfig
from schemas.image import ImageResponse,ImageAssociation,CollectionAssignment,CollectionCreate,ImageAssociationRequest,ImageQuery
from schemas.common import PaginatedResponse,PaginationParams
from services.image_library import register_image,attach_images,assign_collection,get_collection

router=APIRouter()

@router.get('/ingest-settings')
async def ingest_settings(collection_id:UUID|None=None,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    config,revision=await get_config(db,user.tenant_id,collection_id)
    return {**config.model_dump(),'revision':revision}

@router.put('/ingest-settings')
async def save_ingest_settings(config:ImageIngestConfig,collection_id:UUID|None=None,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_superuser)):
    if collection_id is not None:
        row=await db.scalar(select(ImageCollection).where(ImageCollection.id==collection_id,ImageCollection.tenant_id==user.tenant_id).with_for_update())
        if row is None:raise HTTPException(404,'图片集不存在')
        row.ingest_config=config.model_dump();row.ingest_revision+=1
        revision=row.ingest_revision
    else:
        row=await db.scalar(select(ImageIngestSettings).where(ImageIngestSettings.tenant_id==user.tenant_id).with_for_update())
        if row:
            row.config=config.model_dump();row.revision+=1
        else:
            row=ImageIngestSettings(tenant_id=user.tenant_id,config=config.model_dump(),revision=1);db.add(row)
        revision=row.revision
    await db.commit()
    return {**config.model_dump(),'revision':revision}

@router.post('/ingest-settings/test')
async def test_ingest_service(config:ImageIngestConfig,user:User=Depends(get_current_superuser)):
    import httpx
    from config.settings import settings
    headers={'Authorization':'Bearer '+settings.QWEN_API_KEY} if settings.QWEN_API_KEY else {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response=await client.get(config.base_url+'/models',headers=headers)
            response.raise_for_status()
            names=[model['id'] for model in response.json()['data']]
        return {'models':names,'model_available':config.model in names}
    except (httpx.HTTPError,ValueError,KeyError,TypeError):
        raise HTTPException(502,'模型服务连接失败，请检查地址、模型名称或服务鉴权')

@router.post('/scene-classify')
async def retry_scene_classification(request:ImageAssociationRequest,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    if request.collection_id is None:raise HTTPException(400,'请选择图片集后分类，场景结果仍随原图共享')
    if not await db.scalar(select(ImageCollection.id).where(ImageCollection.id==request.collection_id,
        ImageCollection.tenant_id==user.tenant_id).with_for_update()):raise HTTPException(404,'图片集不存在')
    config,revision=await get_config(db,user.tenant_id,request.collection_id)
    if not config.classification_enabled:raise HTTPException(400,'请先在此图片集设置中启用场景分类')
    ids=sorted(set(request.image_ids))
    images=(await db.scalars(select(LibraryImage).where(LibraryImage.tenant_id==user.tenant_id,LibraryImage.source_type!='model_crop',LibraryImage.id.in_(ids),
        LibraryImage.id.in_(select(ImageCollectionMember.image_id).where(ImageCollectionMember.collection_id==request.collection_id)))
        .order_by(LibraryImage.id).with_for_update())).all()
    if len(images)!=len(ids):raise HTTPException(404,'部分图片不存在')
    for image in images:
        image.scene_status='pending';image.scene_error=None;image.scene_attempt+=1
        image.scene_config={'collection_id':str(request.collection_id),'revision':revision,'config':config.model_dump()}
    await db.commit()
    return {'queued_count':len(images)}


@router.post('/batch-upload',response_model=list[ImageResponse],status_code=201)
async def upload_images(response:Response,files:list[UploadFile]=File(...),collection_name:str|None=Form(None,max_length=200),min_long_edge:int|None=Form(None,ge=0,le=32768),tenant_id:int=Depends(get_tenant_id),
    db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    name=collection_name.strip() if collection_name is not None else datetime.now(timezone(timedelta(hours=8))).strftime('%m%d')
    if not name:
        raise HTTPException(422,'图片集名称不能为空')
    batch=str(uuid4())
    group=await get_collection(db,tenant_id,name)
    config,_=await get_config(db,tenant_id,group.id)
    minimum=config.min_long_edge if min_long_edge is None else min_long_edge
    result=[];skipped=0
    for file in files:
        try:
            values=await read_upload(file,minimum)
        except ResolutionFiltered:
            skipped+=1;continue
        result.append(await register_image(db,tenant_id,values,batch))
    response.headers['X-Images-Skipped']=str(skipped)
    result=list({image.id:image for image in result}.values())
    await assign_collection(db,tenant_id,[image.id for image in result],name)
    await db.commit()
    return await image_responses(db,result)

@router.post('/associate')
async def associate_images(request:ImageAssociation,tenant_id:int=Depends(get_tenant_id),
    db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    assets,added=await attach_images(db,request.project_id,tenant_id,request.image_ids)
    annotations={}
    if request.include_imported_annotations:
        from services.yolo_import import copy_to_project
        annotations=await copy_to_project(db,assets,user)
    await db.commit()
    return {'added_count':added,'asset_ids':[str(asset.id) for asset in assets],**annotations}

@router.get('/collections')
async def list_collections(tenant_id:int=Depends(get_tenant_id),db:AsyncSession=Depends(get_db_session),
    user:User=Depends(get_current_user)):
    rows=(await db.execute(select(ImageCollection,func.count(LibraryImage.id))
        .outerjoin(ImageCollectionMember,ImageCollectionMember.collection_id==ImageCollection.id)
        .outerjoin(LibraryImage,(LibraryImage.id==ImageCollectionMember.image_id)&(LibraryImage.source_type!='model_crop'))
        .where(ImageCollection.tenant_id==tenant_id).group_by(ImageCollection.id)
        .order_by(ImageCollection.name))).all()
    return [{'id':str(group.id),'name':group.name,'image_count':count,
        'ingest_config':(await get_config(db,tenant_id,group.id))[0].model_dump(),'ingest_revision':group.ingest_revision} for group,count in rows]

@router.post('/collections',status_code=201)
async def create_collection(request:CollectionCreate,db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    group=await get_collection(db,user.tenant_id,request.name)
    await db.commit()
    return {'id':str(group.id),'name':group.name}

@router.post('/collections/assign')
async def group_images(request:CollectionAssignment,tenant_id:int=Depends(get_tenant_id),
    db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    group,added=await assign_collection(db,tenant_id,request.image_ids,request.name)
    await db.commit()
    return {'collection_id':str(group.id),'added_count':added}

async def collection_deletion_plan(db,collection_id,tenant_id,lock=False):
    query=select(ImageCollection).where(ImageCollection.id==collection_id,ImageCollection.tenant_id==tenant_id)
    group=await db.scalar(query.with_for_update() if lock else query)
    if group is None:raise HTTPException(404,'图片集不存在')
    if await db.scalar(select(ImageUpload.id).where(ImageUpload.collection_id==collection_id,
        ImageUpload.status!='completed').limit(1)):
        raise HTTPException(409,'此图片集有未完成的上传，请先完成上传或在上传窗口清除未完成记录')
    if await db.scalar(select(LibraryImage.id).where(LibraryImage.tenant_id==tenant_id,
        LibraryImage.scene_status.in_(['pending','processing']),
        LibraryImage.scene_config['collection_id'].as_string()==str(collection_id)).limit(1)):
        raise HTTPException(409,'此图片集仍有待处理或进行中的场景分类，请等待完成后再删除')
    members=select(ImageCollectionMember.image_id).where(ImageCollectionMember.collection_id==collection_id)
    shared=select(ImageCollectionMember.image_id).where(ImageCollectionMember.image_id==LibraryImage.id,
        ImageCollectionMember.collection_id!=collection_id).exists()
    used=select(Asset.id).where(Asset.image_id==LibraryImage.id).exists()
    current=[LibraryImage.id.in_(members),LibraryImage.tenant_id==tenant_id]
    query=select(LibraryImage.id,shared.label('shared'),used.label('used')).where(*current).order_by(LibraryImage.id)
    rows=(await db.execute(query.with_for_update(of=LibraryImage) if lock else query)).all()
    kept=sum(row.shared or row.used for row in rows)
    return group,[*current,~shared,~used],{'total_images':len(rows),'deleted_images':len(rows)-kept,'kept_images':kept}

@router.get('/collections/{collection_id}/delete-preview')
async def preview_collection_deletion(collection_id:UUID,tenant_id:int=Depends(get_tenant_id),
    db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    _,_,plan=await collection_deletion_plan(db,collection_id,tenant_id)
    return plan

@router.delete('/collections/{collection_id}')
async def delete_collection(collection_id:UUID,tenant_id:int=Depends(get_tenant_id),
    db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    group,conditions,plan=await collection_deletion_plan(db,collection_id,tenant_id,lock=True)
    # Keep the collection -> image lock order used by assignment; protect shared originals.
    removed=await db.execute(delete(LibraryImage).where(*conditions).execution_options(synchronize_session=False))
    plan['deleted_images']=removed.rowcount
    plan['kept_images']=plan['total_images']-removed.rowcount
    await db.delete(group)
    await db.commit()
    return plan

async def image_responses(db,images):
    names={image.id:[] for image in images}
    if names:
        rows=(await db.execute(select(ImageCollectionMember.image_id,ImageCollection.name).join(ImageCollection)
            .where(ImageCollectionMember.image_id.in_(names)).order_by(ImageCollection.name))).all()
        for image_id,name in rows:
            names[image_id].append(name)
    return [ImageResponse.model_validate(image).model_copy(update={'collection_names':names[image.id],
        'has_imported_annotation':image.imported_annotation is not None,
        'imported_annotation_trusted':bool((image.imported_annotation or {}).get('trusted'))}) for image in images]

async def image_conditions(query,db,tenant_id):
    q,source_type,batch_id,collection_id=query.q,query.source_type,query.batch_id,query.collection_id
    scene,scene_status=query.scene,query.scene_status
    created_from,created_to=query.created_from,query.created_to
    conditions=[LibraryImage.tenant_id==tenant_id,LibraryImage.source_type!='model_crop']
    if collection_id:
        group=await db.scalar(select(ImageCollection.id).where(ImageCollection.id==collection_id,ImageCollection.tenant_id==tenant_id))
        if group is None:
            raise HTTPException(404,'图片集不存在')
        conditions.append(LibraryImage.id.in_(select(ImageCollectionMember.image_id).where(ImageCollectionMember.collection_id==collection_id)))
    if scene_status:
        conditions.append(LibraryImage.scene_status==scene_status)
    if scene:
        if db.bind.dialect.name=='sqlite':
            labels=func.json_each(LibraryImage.scene_labels).table_valued('value')
            conditions.append(select(1).select_from(labels).where(labels.c.value==scene).exists())
        else:
            conditions.append(LibraryImage.scene_labels.contains([scene]))
    if q:
        conditions.append(LibraryImage.original_name.icontains(q,autoescape=True))
    if source_type:
        conditions.append(LibraryImage.source_type==source_type)
    if batch_id:
        conditions.append(LibraryImage.batch_id==batch_id)
    created_from = created_from.astimezone(timezone.utc).replace(tzinfo=None) if created_from and created_from.tzinfo else created_from
    created_to = created_to.astimezone(timezone.utc).replace(tzinfo=None) if created_to and created_to.tzinfo else created_to
    if created_from:
        conditions.append(LibraryImage.created_at>=created_from.replace(tzinfo=None))
    if created_to:
        conditions.append(LibraryImage.created_at<=created_to.replace(tzinfo=None))
    if created_from and created_to and created_from>created_to:
        raise HTTPException(400,'起始时间不能晚于结束时间')
    return conditions

@router.get('/selection')
async def select_images(query:ImageQuery=Depends(),exclude_project_id:UUID|None=None,
    tenant_id:int=Depends(get_tenant_id),db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    conditions=await image_conditions(query,db,tenant_id)
    if exclude_project_id is not None:
        if not await db.scalar(select(Project.id).where(Project.id==exclude_project_id,Project.tenant_id==tenant_id)):
            raise HTTPException(404,'项目不存在')
        conditions.append(LibraryImage.id.not_in(select(Asset.image_id).where(
            Asset.project_id==exclude_project_id,Asset.image_id.is_not(None))))
    ids=(await db.scalars(select(LibraryImage.id).where(*conditions).order_by(LibraryImage.id))).all()
    return {'image_ids':ids,'total':len(ids)}

@router.get('',response_model=PaginatedResponse[ImageResponse])
async def list_images(query:ImageQuery=Depends(),pagination:PaginationParams=Depends(),tenant_id:int=Depends(get_tenant_id),
    db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    conditions=await image_conditions(query,db,tenant_id)
    total=await db.scalar(select(func.count(LibraryImage.id)).where(*conditions))
    items=(await db.scalars(select(LibraryImage).where(*conditions).order_by(LibraryImage.created_at.desc(),LibraryImage.id)
        .offset(pagination.skip).limit(pagination.limit))).all()
    return PaginatedResponse(items=await image_responses(db,items),total=total,page=pagination.page,page_size=pagination.page_size)

async def scoped_image(db,image_id,tenant_id,lock=False):
    query=select(LibraryImage).where(LibraryImage.id==image_id,LibraryImage.tenant_id==tenant_id,LibraryImage.source_type!='model_crop')
    if lock:
        query=query.with_for_update()
    image=await db.scalar(query)
    if image is None:
        raise HTTPException(404,'图片不存在')
    return image

@router.get('/{image_id}/content')
async def get_image_content(image_id:UUID,tenant_id:int=Depends(get_tenant_id),
    db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    return await image_content(await scoped_image(db,image_id,tenant_id))

@router.delete('/{image_id}',status_code=204)
async def delete_image(image_id:UUID,tenant_id:int=Depends(get_tenant_id),
    db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    image=await scoped_image(db,image_id,tenant_id,lock=True)
    if await db.scalar(select(Asset.id).where(Asset.image_id==image.id).limit(1)):
        raise HTTPException(409,'图片正在被项目使用，请先移除项目引用')
    # Retain immutable originals for frozen exports and SmartEye references.
    await db.delete(image)
    await db.commit()
