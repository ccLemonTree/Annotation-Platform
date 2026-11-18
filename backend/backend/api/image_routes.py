"""Independent image library, filtered browsing and project references."""
from datetime import datetime,timezone,timedelta
from uuid import UUID,uuid4
from fastapi import APIRouter,Depends,File,Form,UploadFile,HTTPException,Response
from sqlalchemy import select,func,delete,true,case,literal,or_
from sqlalchemy.ext.asyncio import AsyncSession
from api.deps import get_current_user,get_tenant_id,get_current_superuser
from api.asset_routes import read_upload, image_content
from database import get_db_session
from models import LibraryImage,Asset,User,ImageCollection,ImageCollectionMember,ImageUpload,Project,Annotation
from services.image_ingest import get_config,ResolutionFiltered
from services.project_access import image_condition,project_condition
from models.image_ingest import ImageIngestSettings
from schemas.image_ingest import ImageIngestConfig
from services.scene_categories import scene_result_status,has_scene_labels
from schemas.image import ImageResponse,ImageAssociation,CollectionAssignment,CollectionCreate,ImageAssociationRequest,ImageQuery
from schemas.common import PaginatedResponse,PaginationParams
from services.image_library import register_image,attach_images,attach_workflow_crops,assign_collection,get_collection,reuse_project_images,reusable_asset,workflow_crop,annotation_label_condition,image_scene_condition,image_sources,image_source_condition,iter_id_conditions

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
    images=[]
    for cond in iter_id_conditions(db, LibraryImage.id, ids):
        images.extend((await db.scalars(select(LibraryImage).where(LibraryImage.tenant_id==user.tenant_id,LibraryImage.source_type!='model_crop',cond,
            LibraryImage.id.in_(select(ImageCollectionMember.image_id).where(ImageCollectionMember.collection_id==request.collection_id)))
            .order_by(LibraryImage.id))).all())
    if len(images)!=len(ids):raise HTTPException(404,'部分图片不存在')
    device_sources=[];device_images=[]
    for image in images:
        info=image.device_info or {}
        sources=info.get('sources') or ([info] if info.get('plat_id') and info.get('device_id') else [])
        if sources:
            device_sources.extend(sources)
            device_images.append(image)
            image.scene_attempt+=1
            continue
        image.scene_status='pending';image.scene_error=None;image.scene_attempt+=1
        image.scene_config={'collection_id':str(request.collection_id),'revision':revision,'config':config.model_dump()}
    if device_sources:
        from integrations.smarteye.device_scenes import queue_device_retry
        from integrations.smarteye.runtime import inherit_device_scenes
        def queue_devices(session):
            conn=session.connection()
            queue_device_retry(conn,user.tenant_id,device_sources,{'user_id':user.id,'tenant_id':user.tenant_id})
            for image in device_images:inherit_device_scenes(conn,image.id,user.tenant_id)
        await db.run_sync(queue_devices)
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
    from api.asset_routes import scoped_project
    await scoped_project(db,request.project_id,tenant_id)
    crops=[]
    follow_job=None
    if request.upload_id is not None:
        from api.image_upload_routes import scoped
        from models import ImageUploadItem
        from services.upload_follow import lock_follow_job
        upload=await scoped(db,request.upload_id,user)
        if upload.project_id!=request.project_id or upload.status!='completed':
            raise HTTPException(409,'此上传记录尚未入库完成或不属于当前项目')
        ids=set(request.image_ids)
        count=sum([await db.scalar(select(func.count()).select_from(ImageUploadItem).where(
            ImageUploadItem.upload_id==upload.id,cond)) or 0 for cond in iter_id_conditions(db, ImageUploadItem.image_id, ids)])
        if count!=len(ids):
            raise HTTPException(409,'部分图片不属于本次上传')
        follow_job=await lock_follow_job(db,request.project_id)
    else:
        ids=set(request.image_ids)
        if request.source_project_id is not None:
            source=await scoped_project(db,request.source_project_id,tenant_id)
            if source.id==request.project_id:
                raise HTTPException(400,'来源项目和目标项目必须不同')
            crops=[]
            for cond in iter_id_conditions(db, Asset.id, sorted(ids)):
                crops.extend((await db.scalars(select(Asset).where(
                    Asset.project_id==source.id,workflow_crop(),cond).order_by(Asset.id))).all())
            ids.difference_update(crop.id for crop in crops)
        count=sum([await db.scalar(select(func.count(LibraryImage.id)).where(LibraryImage.tenant_id==tenant_id,
            cond,image_condition(db))) or 0 for cond in iter_id_conditions(db, LibraryImage.id, ids)])
        if count!=len(ids):
            raise HTTPException(404,'部分图片不存在')
    image_ids=ids if request.upload_id is None else request.image_ids
    assets,created=await attach_images(db,request.project_id,tenant_id,image_ids,
        image_kind=upload.image_kind if request.upload_id is not None else 'originals',
        source_project_id=request.source_project_id) if image_ids else ([],[])
    if crops:
        crop_assets,new_crops=await attach_workflow_crops(db,request.project_id,tenant_id,crops)
        assets.extend(crop_assets)
        created.extend(new_crops)
    crop_entry_locked=follow_job is not None
    if request.upload_id is not None and created and follow_job is None:
        # A workflow can be enabled while this upload waits for the project lock.
        from services.upload_follow import find_streaming_follow_job
        follow_job=await find_streaming_follow_job(db,request.project_id)
    annotations={}
    if request.source_project_id is not None:
        annotations=await reuse_project_images(db,assets,user,request.source_project_id,request.include_source_annotations,
            include_pending=request.include_pending_source_annotations)
    if request.include_imported_annotations:
        from services.yolo_import import copy_to_project
        annotations=await copy_to_project(db,assets,user)
    queued=0
    warning=None
    if follow_job is not None:
        from services.upload_follow import enqueue_uploads
        queued,warning=await enqueue_uploads(db,follow_job,created,crop_entry_locked=crop_entry_locked)
    await db.commit()
    return {'added_count':len(created),'skipped_count':len(assets)-len(created),'asset_ids':[str(asset.id) for asset in assets],
        **({'preannotation_queued':queued,'preannotation_warning':warning} if request.upload_id else {}),**annotations}

@router.get('/collections')
async def list_collections(tenant_id:int=Depends(get_tenant_id),db:AsyncSession=Depends(get_db_session),
    user:User=Depends(get_current_user)):
    counts=(select(ImageCollectionMember.collection_id,func.count().label('image_count'))
        .join(ImageCollection,ImageCollectionMember.collection_id==ImageCollection.id)
        .join(LibraryImage,LibraryImage.id==ImageCollectionMember.image_id)
        .where(ImageCollection.tenant_id==tenant_id,LibraryImage.tenant_id==tenant_id,
            LibraryImage.source_type!='model_crop',image_condition(db))
        .group_by(ImageCollectionMember.collection_id).subquery())
    rows=(await db.execute(select(ImageCollection,func.coalesce(counts.c.image_count,0))
        .outerjoin(counts,counts.c.collection_id==ImageCollection.id).where(ImageCollection.tenant_id==tenant_id)
        .order_by(ImageCollection.name))).all()
    defaults=(await get_config(db,tenant_id))[0] if any(group.ingest_config is None for group,_ in rows) else None
    return [{'id':str(group.id),'name':group.name,'image_count':count,
        'ingest_config':(defaults if group.ingest_config is None else ImageIngestConfig.model_validate(group.ingest_config)).model_dump(),
        'ingest_revision':group.ingest_revision} for group,count in rows]

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
        'scene_status':scene_result_status(image.scene_status,image.scene_labels),
        'has_imported_annotation':image.imported_annotation is not None,
        'imported_annotation_trusted':bool((image.imported_annotation or {}).get('trusted'))}) for image in images]

async def image_conditions(query,db,tenant_id):
    q,source_type,batch_id,collection_id=query.q,query.source_type,query.batch_id,query.collection_id
    scene,scene_status=query.scene,query.scene_status
    created_from,created_to=query.created_from,query.created_to
    conditions=[LibraryImage.tenant_id==tenant_id,LibraryImage.source_type!='model_crop',image_condition(db)]
    if query.platform_name is not None or query.device_name:
        conditions.append(image_source_condition(db,query.platform_name,query.device_name))
    if query.source_project_id is not None:
        if not await db.scalar(select(Project.id).where(Project.id==query.source_project_id,Project.tenant_id==tenant_id,project_condition(db))):
            raise HTTPException(404,'来源项目不存在')
    if query.source_project_id is not None or query.label or query.review_status:
        source=select(1).select_from(Asset).join(Project,Project.id==Asset.project_id).where(
            Project.tenant_id==tenant_id,project_condition(db),reusable_asset(),Asset.content_hash==LibraryImage.content_hash)
        if query.source_project_id is not None:source=source.where(Asset.project_id==query.source_project_id)
        if query.label or query.review_status:
            source=source.join(Annotation,(Annotation.asset_id==Asset.id)&Annotation.is_latest.is_(True))
            if query.label:source=source.where(annotation_label_condition(db,query.label))
            if query.review_status:
                statuses=('approved','pending') if query.review_status=='approved_or_pending' else ('approved',)
                state=case((Project.task_type=='detection',Annotation.status_for_query('detection')),
                    else_=Annotation.status_for_query('image'))
                valid=case(
                    (Project.task_type=='detection',Annotation.has_project_format('detection')),
                    (Project.task_type=='classification',Annotation.has_project_format('classification')),
                    else_=Annotation.has_project_format('caption'))
                source=source.where(state.in_(statuses),valid,or_(Project.task_type=='detection',
                    Annotation.annotation_data['skipped'].as_boolean().is_not(True)))
        conditions.append(source.exists())
    if collection_id:
        group=await db.scalar(select(ImageCollection.id).where(ImageCollection.id==collection_id,ImageCollection.tenant_id==tenant_id))
        if group is None:
            raise HTTPException(404,'图片集不存在')
        conditions.append(LibraryImage.id.in_(select(ImageCollectionMember.image_id).where(ImageCollectionMember.collection_id==collection_id)))
    if scene_status:
        conditions.append(LibraryImage.scene_status==('completed' if scene_status=='unmatched' else scene_status))
        if scene_status in ('completed','unmatched'):
            matched=has_scene_labels(LibraryImage.scene_labels,db.bind.dialect.name)
            conditions.append(~matched if scene_status=='unmatched' else matched)
    if scene:
        conditions.append(await image_scene_condition(db,scene,tenant_id,collection_id))
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


async def reuse_candidates(query,db,tenant_id,exclude_project_id=None):
    conditions=await image_conditions(query,db,tenant_id)
    library=select(LibraryImage.id,LibraryImage.created_at,literal(False).label('crop')).where(*conditions)
    if exclude_project_id is not None:
        library=library.where(LibraryImage.id.not_in(select(Asset.image_id).where(
            Asset.project_id==exclude_project_id,Asset.image_id.is_not(None))))
    if query.source_project_id is None or query.source_type not in (None,'model_crop'):
        return library.subquery()

    from api.asset_routes import scoped_project,asset_conditions
    project=await scoped_project(db,query.source_project_id,tenant_id)
    crop_conditions=await asset_conditions(db,project,'all','crops',q=query.q,collection_id=query.collection_id,
        include_source_collections=True,scene=query.scene,platform_name=query.platform_name,device_name=query.device_name)
    crop=select(Asset.id,Asset.created_at,literal(True).label('crop')).where(*crop_conditions,workflow_crop())
    if query.label or query.review_status:
        latest=select(1).where(Annotation.asset_id==Asset.id,Annotation.is_latest.is_(True))
        if query.label:
            latest=latest.where(annotation_label_condition(db,query.label))
        if query.review_status:
            statuses=('approved','pending') if query.review_status=='approved_or_pending' else ('approved',)
            kind='detection' if project.task_type=='detection' else 'image'
            latest=latest.where(Annotation.status_for_query(kind).in_(statuses),Annotation.has_project_format(project.task_type))
            if kind=='image':
                latest=latest.where(Annotation.annotation_data['skipped'].as_boolean().is_not(True))
        crop=crop.where(latest.exists())
    if query.batch_id:
        crop=crop.where(Asset.device_info['crop']['job_id'].as_string()==query.batch_id)
    if query.scene_status and query.scene_status!='unclassified':
        return library.subquery()
    if query.created_from:
        start=query.created_from
        crop=crop.where(Asset.created_at>=(start.astimezone(timezone.utc) if start.tzinfo else start).replace(tzinfo=None))
    if query.created_to:
        end=query.created_to
        crop=crop.where(Asset.created_at<=(end.astimezone(timezone.utc) if end.tzinfo else end).replace(tzinfo=None))
    if exclude_project_id is not None:
        target=Asset.__table__.alias()
        crop=crop.where(~select(1).where(target.c.project_id==exclude_project_id,
            target.c.content_hash==Asset.content_hash).exists())
    return library.union_all(crop).subquery()

@router.get('/source-platforms')
async def source_platforms(collection_id:UUID|None=None,project_id:UUID|None=None,tenant_id:int=Depends(get_tenant_id),
    db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    if project_id is not None:
        from api.asset_routes import scoped_project,asset_conditions
        project=await scoped_project(db,project_id,tenant_id)
        conditions=await asset_conditions(db,project,'all','all',collection_id=collection_id)
        table=Asset
    else:
        conditions=await image_conditions(ImageQuery(collection_id=collection_id),db,tenant_id)
        table=LibraryImage
    sources,field=image_sources(db,table.device_info)
    def name(value):return func.coalesce(func.nullif(field(value,'platform_name'),''),field(value,'plat_id'))
    direct=select(name(table.device_info).label('name')).where(*conditions)
    expanded=select(name(sources.c.value).label('name')).select_from(table).join(sources,true()).where(*conditions)
    names=sorted(value for value in (await db.scalars(direct.union(expanded))).all() if value)
    if await db.scalar(select(table.id).where(*conditions,image_source_condition(db,'@upload',info=table.device_info)).limit(1)):
        names.append('@upload')
    return names


@router.get('/selection')
async def select_images(query:ImageQuery=Depends(),exclude_project_id:UUID|None=None,
    tenant_id:int=Depends(get_tenant_id),db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    if exclude_project_id is not None:
        if not await db.scalar(select(Project.id).where(Project.id==exclude_project_id,Project.tenant_id==tenant_id)):
            raise HTTPException(404,'项目不存在')
    candidates=await reuse_candidates(query,db,tenant_id,exclude_project_id)
    ids=(await db.scalars(select(candidates.c.id).order_by(candidates.c.id))).all()
    return {'image_ids':ids,'total':len(ids)}

@router.get('',response_model=PaginatedResponse[ImageResponse])
async def list_images(query:ImageQuery=Depends(),pagination:PaginationParams=Depends(),tenant_id:int=Depends(get_tenant_id),
    db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user),reuse_project_id:UUID|None=None):
    if reuse_project_id is not None:
        from api.asset_routes import scoped_project
        await scoped_project(db,reuse_project_id,tenant_id)
    candidates=await reuse_candidates(query,db,tenant_id)
    total=await db.scalar(select(func.count()).select_from(candidates))
    page=(await db.execute(select(candidates).order_by(candidates.c.created_at.desc(),candidates.c.id)
        .offset(pagination.skip).limit(pagination.limit))).all()
    library_ids=[row.id for row in page if not row.crop]
    crop_ids=[row.id for row in page if row.crop]
    images=(await db.scalars(select(LibraryImage).where(LibraryImage.id.in_(library_ids)))).all() if library_ids else []
    crops=(await db.scalars(select(Asset).where(Asset.id.in_(crop_ids)))).all() if crop_ids else []
    items={(image.id,False):image for image in await image_responses(db,images)}
    items.update({(asset.id,True):ImageResponse.model_validate(asset).model_copy(update={'asset_id':asset.id}) for asset in crops})
    ordered=[items[row.id,row.crop] for row in page if (row.id,row.crop) in items]
    if reuse_project_id is not None and ordered:
        used=set((await db.scalars(select(Asset.content_hash).where(Asset.project_id==reuse_project_id,
            Asset.content_hash.in_([item.content_hash for item in ordered])))).all())
        ordered=[item.model_copy(update={'in_target_project':item.content_hash in used}) for item in ordered]
    return PaginatedResponse(items=ordered,
        total=total,page=pagination.page,page_size=pagination.page_size)

async def scoped_image(db,image_id,tenant_id,lock=False):
    query=select(LibraryImage).where(LibraryImage.id==image_id,LibraryImage.tenant_id==tenant_id,LibraryImage.source_type!='model_crop',image_condition(db))
    if lock:
        query=query.with_for_update()
    image=await db.scalar(query)
    if image is None:
        raise HTTPException(404,'图片不存在')
    return image

@router.get('/{image_id}/content')
async def get_image_content(image_id:UUID,preview:bool=False,tenant_id:int=Depends(get_tenant_id),
    db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    response=await image_content(await scoped_image(db,image_id,tenant_id),preview=preview,max_edge=480)
    await db.rollback()
    return response

@router.delete('/{image_id}',status_code=204)
async def delete_image(image_id:UUID,tenant_id:int=Depends(get_tenant_id),
    db:AsyncSession=Depends(get_db_session),user:User=Depends(get_current_user)):
    image=await scoped_image(db,image_id,tenant_id,lock=True)
    if await db.scalar(select(Asset.id).where(Asset.image_id==image.id).limit(1)):
        raise HTTPException(409,'图片正在被项目使用，请先移除项目引用')
    # Retain immutable originals for frozen exports and SmartEye references.
    await db.delete(image)
    await db.commit()
