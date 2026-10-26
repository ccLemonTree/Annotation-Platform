"""Catalog originals and attach project-specific annotation references."""
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from models import LibraryImage, Asset, Project, ImageCollection, ImageCollectionMember

IMAGE_FIELDS = ('blob_path','content_hash','original_name','file_size','width','height','format',
    'source_type','source_record_id','capture_time','device_info','location','quality_score','quality_metrics')

async def register_image(db, tenant_id, values, batch_id=None):
    image = await db.scalar(select(LibraryImage).where(LibraryImage.tenant_id==tenant_id,
        LibraryImage.content_hash==values['content_hash']))
    if image:
        return image
    image=LibraryImage(tenant_id=tenant_id,batch_id=batch_id,**values)
    try:
        async with db.begin_nested():
            db.add(image)
            await db.flush()
    except IntegrityError:
        image=await db.scalar(select(LibraryImage).where(LibraryImage.tenant_id==tenant_id,
            LibraryImage.content_hash==values['content_hash']))
        if image is None:
            raise
    return image

async def attach_images(db, project_id, tenant_id, image_ids):
    ids=sorted(set(image_ids))
    # Lock originals first so a catalog delete cannot race with attachment.
    images=(await db.scalars(select(LibraryImage).where(LibraryImage.id.in_(ids),
        LibraryImage.tenant_id==tenant_id,LibraryImage.source_type!='model_crop').order_by(LibraryImage.id).with_for_update())).all()
    if len(images)!=len(ids):
        raise HTTPException(404,'部分图片不存在')
    project=await db.scalar(select(Project).where(Project.id==project_id,Project.tenant_id==tenant_id)
        .with_for_update())
    if project is None:
        raise HTTPException(404,'项目不存在')
    added=0
    result=[]
    for image in images:
        asset=await db.scalar(select(Asset).where(Asset.project_id==project_id,Asset.content_hash==image.content_hash))
        if asset is None:
            asset=Asset(id=uuid4(),project_id=project_id,image_id=image.id,
                annotation_status='pending',**{name:getattr(image,name) for name in IMAGE_FIELDS})
            db.add(asset)
            await db.flush()
            added+=1
        elif asset.image_id is None and asset.source_type!='model_crop':
            asset.image_id=image.id
        result.append(asset)
    if added:
        await db.execute(update(Project).where(Project.id==project_id).values(total_assets=Project.total_assets+added))
    return result,added
async def get_collection(db,tenant_id,name):
    group=await db.scalar(select(ImageCollection).where(ImageCollection.tenant_id==tenant_id,ImageCollection.name==name))
    if group is None:
        from services.image_ingest import get_config
        config,_=await get_config(db,tenant_id)
        try:
            async with db.begin_nested():
                group=ImageCollection(tenant_id=tenant_id,name=name,ingest_config=config.model_dump())
                db.add(group);await db.flush()
        except IntegrityError:
            group=await db.scalar(select(ImageCollection).where(ImageCollection.tenant_id==tenant_id,ImageCollection.name==name))
            if group is None:
                raise
    return group

async def assign_collection(db,tenant_id,image_ids,name):
    ids=sorted(set(image_ids))
    group=await get_collection(db,tenant_id,name)
    group=await db.scalar(select(ImageCollection).where(ImageCollection.id==group.id).with_for_update())
    if group is None:raise HTTPException(404,'图片集不存在')
    images=(await db.scalars(select(LibraryImage.id).where(LibraryImage.tenant_id==tenant_id,
        LibraryImage.id.in_(ids),LibraryImage.source_type!='model_crop').order_by(LibraryImage.id).with_for_update())).all()
    if len(images)!=len(ids):
        raise HTTPException(404,'部分图片不存在')
    from services.image_ingest import get_config
    config,_=await get_config(db,tenant_id,group.id)
    added=0
    for image_id in images:
        if await db.get(ImageCollectionMember,(group.id,image_id)) is None:
            db.add(ImageCollectionMember(collection_id=group.id,image_id=image_id))
            added+=1
        image=await db.get(LibraryImage,image_id)
        if config.classification_enabled and image.scene_status=='unclassified':
            image.scene_status='pending'
            image.scene_config={'collection_id':str(group.id),'revision':group.ingest_revision,'config':config.model_dump()}
    await db.flush()
    return group,added

async def merge_collections(db,tenant_id,source_id,target_id):
    """Maintenance operation: caller backs up and commits after all uploads/inference drain."""
    import hashlib,json
    from sqlalchemy import delete,literal,func
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from models import ImageUpload
    if source_id==target_id:raise HTTPException(400,'请选择两个不同的图片集')
    groups=(await db.scalars(select(ImageCollection).where(ImageCollection.tenant_id==tenant_id,
        ImageCollection.id.in_([source_id,target_id])).order_by(ImageCollection.id).with_for_update())).all()
    if len(groups)!=2:raise HTTPException(404,'图片集不存在')
    source=next(group for group in groups if group.id==source_id)
    target=next(group for group in groups if group.id==target_id)
    members=select(ImageCollectionMember.image_id).where(ImageCollectionMember.collection_id.in_([source_id,target_id]))
    images=(await db.scalars(select(LibraryImage).where(LibraryImage.id.in_(members)).order_by(LibraryImage.id).with_for_update())).all()
    if any(image.scene_status=='processing' for image in images):raise HTTPException(409,'请等待正在进行的场景分类完成后再合并')
    jobs=(await db.scalars(select(ImageUpload).where(ImageUpload.collection_id.in_([source_id,target_id]))
        .order_by(ImageUpload.id).with_for_update())).all()
    if any(job.status!='completed' for job in jobs):raise HTTPException(409,'请先完成或清除未完成的上传记录')
    before=await db.scalar(select(func.count()).select_from(ImageCollectionMember).where(ImageCollectionMember.collection_id==target_id))
    create=pg_insert if db.bind.dialect.name=='postgresql' else sqlite_insert
    await db.execute(create(ImageCollectionMember).from_select(['collection_id','image_id'],
        select(literal(target_id,type_=ImageCollectionMember.collection_id.type),ImageCollectionMember.image_id)
        .where(ImageCollectionMember.collection_id==source_id)).on_conflict_do_nothing(index_elements=['collection_id','image_id']))
    moved=0
    for job in jobs:
        if job.collection_id!=source_id:continue
        job.collection_id=target_id
        identity=[job.filename,job.size,job.chunk_hashes,str(target_id),job.min_long_edge]
        if job.annotation_format:identity.extend([job.annotation_format,job.trust_annotations])
        job.resume_key=hashlib.sha256(json.dumps(identity,separators=(',',':')).encode()).hexdigest()
        moved+=1
    for image in images:
        if image.scene_config and image.scene_config.get('collection_id')==str(source_id):
            image.scene_config={**image.scene_config,'collection_id':str(target_id),'revision':target.ingest_revision}
    await db.flush()
    await db.execute(delete(ImageCollectionMember).where(ImageCollectionMember.collection_id==source_id))
    source_name=source.name
    await db.delete(source);await db.flush()
    count=await db.scalar(select(func.count()).select_from(ImageCollectionMember).where(ImageCollectionMember.collection_id==target_id))
    return {'source':source_name,'target':target.name,'image_count':count,'added_count':count-before,'uploads_moved':moved}

