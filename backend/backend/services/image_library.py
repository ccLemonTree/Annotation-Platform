"""Catalog originals and attach project-specific annotation references."""
from itertools import batched
from uuid import uuid4
from copy import deepcopy
from fastapi import HTTPException
from sqlalchemy import any_, false, literal, select, update, func, or_, cast
from sqlalchemy.dialects.postgresql import ARRAY, insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from models import LibraryImage, Asset, Project, ImageCollection, ImageCollectionMember, Annotation


def id_in_condition(db, column, ids):
    items = list(ids)
    if not items:
        return false()
    if db.get_bind().dialect.name == 'postgresql':
        return column == any_(literal(items, type_=ARRAY(column.type)))
    if len(items) <= 500:
        return column.in_(items)
    return or_(*(column.in_(list(batch)) for batch in batched(items, 500)))


def iter_id_conditions(db, column, ids, chunk_size=500):
    items = list(ids)
    if not items:
        return ()
    if db.get_bind().dialect.name == 'postgresql':
        return (column == any_(literal(items, type_=ARRAY(column.type))),)
    return tuple(column.in_(list(batch)) for batch in batched(items, chunk_size))

IMAGE_FIELDS = ('blob_path','content_hash','original_name','file_size','width','height','format',
    'source_type','source_record_id','capture_time','device_info','location','quality_score','quality_metrics')

def reusable_asset():
    return or_(Asset.source_type!='model_crop', uploaded_crop())


def uploaded_crop():
    return ((Asset.source_type=='model_crop') & Asset.image_id.is_not(None)
        & Asset.device_info['uploaded_crop'].as_boolean().is_(True))


def workflow_crop():
    return (Asset.source_type=='model_crop') & Asset.image_id.is_(None)


def image_sources(db,info):
    if db.bind.dialect.name=='sqlite':
        sources=func.json_each(info,'$.sources').table_valued('value')
        field=lambda value,key:func.json_extract(value,'$.'+key)
    else:
        from sqlalchemy.dialects.postgresql import JSONB
        sources=func.jsonb_array_elements(info['sources']).table_valued('value')
        field=lambda value,key:cast(value,JSONB)[key].as_string()
    return sources,field


def image_source_condition(db,platform_name=None,device_name=None,info=LibraryImage.device_info):
    sources,field=image_sources(db,info)
    platform=lambda value:func.coalesce(func.nullif(field(value,'platform_name'),''),field(value,'plat_id'),'')
    device=lambda value:or_(*(func.coalesce(field(value,key),'').icontains(device_name,autoescape=True) for key in ('device_name','device_id')))
    if platform_name=='@upload':
        local=(platform(info)=='') & ~select(1).select_from(sources).where(platform(sources.c.value)!='').exists()
        if device_name:local=local & device(info)
        return local
    def matches(value):
        conditions=[]
        if platform_name is not None:conditions.append(platform(value)==platform_name)
        if device_name:conditions.append(device(value))
        from sqlalchemy import and_
        return and_(*conditions)
    return matches(info)|select(1).select_from(sources).where(matches(sources.c.value)).exists()


def annotation_label_condition(db,label):
    data=Annotation.annotation_data
    if db.bind.dialect.name=='sqlite':
        boxes=func.json_each(data,'$.boxes').table_valued('value')
        labels=func.json_each(data,'$.labels').table_valued('value')
        box_labels=func.json_each(boxes.c.value,'$.labels').table_valued('value')
        return or_(select(1).select_from(boxes).where(or_(func.json_extract(boxes.c.value,'$.label')==label,
                       select(1).select_from(box_labels).where(box_labels.c.value==label).correlate(boxes).exists())).exists(),
                   select(1).select_from(labels).where(labels.c.value==label).exists())
    return or_(data.contains({'boxes':[{'label':label}]}), data.contains({'boxes':[{'labels':[label]}]}),
               data.contains({'labels':[label]}))


async def image_scene_condition(db,scene,tenant_id,collection_id=None):
    from services.scene_categories import scene_filter_values,scene_labels_condition
    configs=[]
    if scene.startswith('@group:'):
        from services.image_ingest import get_config
        config,_=await get_config(db,tenant_id)
        configs.append(config)
        if collection_id:
            config,_=await get_config(db,tenant_id,collection_id)
            configs.append(config)
        else:
            configs.extend((await db.scalars(select(ImageCollection.ingest_config).where(
                ImageCollection.tenant_id==tenant_id,ImageCollection.ingest_config.is_not(None)))).all())
    return scene_labels_condition(LibraryImage.scene_labels,scene_filter_values(scene,configs),db.bind.dialect.name)


def annotation_for_project(project,data):
    data=deepcopy(data)
    key='boxes' if project.task_type=='detection' else 'labels'
    original=data.get(key,[])
    allowed=(project.label_schema or {}).get('labels') or []
    matching=[item for item in original if (item['label'] if key=='boxes' else item) in allowed]
    if original and not matching:return None,True
    if key in data:data[key]=matching
    return data,len(original)!=len(matching)


async def reuse_project_images(db,assets,user,source_project_id,include_annotations,*,include_pending=False):
    from services.annotations import IMAGE_FIELDS,save_annotation,record_event,sync_state,detection_boxes,validate_image_annotation
    source=await db.scalar(select(Project).where(Project.id==source_project_id,Project.tenant_id==user.tenant_id))
    if source is None:raise HTTPException(404,'来源项目不存在')
    target=await db.get(Project,assets[0].project_id)
    if source.id==target.id:raise HTTPException(400,'来源项目和目标项目必须不同')
    if include_annotations and (source.task_type!=target.task_type or target.task_type not in ('detection','classification','caption','vector')):
        raise HTTPException(400,'复制标注要求相同任务类型，当前仅支持检测、分类、描述和向量项目')
    statuses=('approved','pending') if include_pending else ('approved',)
    kind='detection' if source.task_type=='detection' else 'image'
    valid=Annotation.has_project_format(source.task_type)
    if kind=='image':
        valid=valid & Annotation.annotation_data['skipped'].as_boolean().is_not(True)
    rows=(await db.execute(select(Asset,Annotation).outerjoin(Annotation,
        (Annotation.asset_id==Asset.id)&Annotation.is_latest.is_(True)&valid&Annotation.status_for_query(kind).in_(statuses))
        .where(Asset.project_id==source.id,or_(reusable_asset(),workflow_crop()),
               Asset.content_hash.in_([asset.content_hash for asset in assets])))).all()
    sources={asset.content_hash:annotation for asset,annotation in rows}
    if any(asset.content_hash not in sources for asset in assets):raise HTTPException(404,'部分图片不属于所选来源项目')
    result=dict(annotated=0,reused_annotations=0,annotation_skipped=0,conflicts=0,without_annotations=0)
    if not include_annotations:return result
    existing=set((await db.scalars(select(Annotation.asset_id).where(
        Annotation.asset_id.in_([asset.id for asset in assets])))).all())
    copies=[]
    for asset in assets:
        original=sources[asset.content_hash]
        if original is None:
            result['without_annotations']+=1;continue
        if asset.id in existing:
            result['conflicts']+=1;continue
        data,filtered=annotation_for_project(target,original.annotation_data)
        if data is None:
            result['annotation_skipped']+=1;continue
        if 'boxes' in data:
            labels=(target.label_schema or {}).get('labels',[])
            detection_boxes(data,asset.width,asset.height,labels)
        else:validate_image_annotation(target,{key:value for key,value in data.items() if key in IMAGE_FIELDS})
        same_scope=set((source.label_schema or {}).get('labels') or [])==set((target.label_schema or {}).get('labels') or [])
        copies.append((asset,original,data,filtered,original.status_for(kind)=='approved' and not filtered and same_scope))
    for asset,original,data,filtered,approval_reused in copies:
        annotation=await save_annotation(db,asset,data,0,user.id,source='reuse',
            workflow_attribute_schema=data.get('attribute_schema'))
        if (original.review_by_kind or {}).get('ai_needs_review'):
            annotation.review_by_kind={**(annotation.review_by_kind or {}),'ai_needs_review':True}
        if approval_reused:
            annotation.review_status='approved';annotation.reviewer_id=None
            # Copy in SQL to preserve the stored timestamp type on older PostgreSQL schemas.
            annotation.reviewed_at=select(Annotation.reviewed_at).where(Annotation.id==original.id).scalar_subquery()
            annotation.review_comment='沿用来源项目已审核标注，非本项目人工复核'
        elif original.status_for(kind)=='pending':annotation.review_comment='从来源项目复制待审核标注'
        else:annotation.review_comment='按目标项目标签带入，标签范围或内容变化，需重新审核完整性'
        record_event(db,asset,user.id,'reuse',dict(source='reuse',annotation_id=str(annotation.id),version=annotation.version,
            source_project_id=str(source.id),source_project_name=source.project_name,source_annotation_id=str(original.id),
            source_version=original.version,source_review_status=original.status_for(kind),source_reviewer_id=original.reviewer_id,
            source_reviewed_at=original.reviewed_at.isoformat() if original.reviewed_at else None,
            source_review_comment=original.review_comment,source_annotation_source=original.annotation_source,
            source_annotator_id=original.annotator_id,filtered=filtered,approval_reused=approval_reused,
            source_labels=(source.label_schema or {}).get('labels',[]),target_labels=(target.label_schema or {}).get('labels',[])))
        await sync_state(db,asset)
        result['annotated']+=1;result['reused_annotations']+=1
    return result


async def attach_workflow_crops(db, project_id, tenant_id, crops):
    if not await db.scalar(select(Project.id).where(
        Project.id==project_id,Project.tenant_id==tenant_id).with_for_update(key_share=True)):
        raise HTTPException(404,'项目不存在')
    existing={asset.content_hash:asset for asset in (await db.scalars(select(Asset).where(
        Asset.project_id==project_id,Asset.content_hash.in_([crop.content_hash for crop in crops])))).all()}
    created=[]
    for crop in crops:
        if crop.content_hash in existing:
            continue
        asset=Asset(id=uuid4(),project_id=project_id,annotation_status='pending',
            **{name:getattr(crop,name) for name in IMAGE_FIELDS})
        db.add(asset)
        created.append(asset)
        existing[crop.content_hash]=asset
    if created:
        await db.flush()
        await db.execute(update(Project).where(Project.id==project_id).values(total_assets=Project.total_assets+len(created)))
    return [existing[crop.content_hash] for crop in crops],created

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

async def attach_images(db, project_id, tenant_id, image_ids, *, image_kind='originals', source_project_id=None):
    ids=sorted(set(image_ids))
    if not ids:
        return [],[]
    # Lock originals first so a catalog delete cannot race with attachment.
    images=[]
    for cond in iter_id_conditions(db, LibraryImage.id, ids):
        images.extend((await db.scalars(select(LibraryImage).where(cond,
            LibraryImage.tenant_id==tenant_id,LibraryImage.source_type!='model_crop').order_by(LibraryImage.id).with_for_update())).all())
    if len(images)!=len(ids):
        raise HTTPException(404,'部分图片不存在')
    project=await db.scalar(select(Project).where(Project.id==project_id,Project.tenant_id==tenant_id)
        .with_for_update(key_share=True))
    if project is None:
        raise HTTPException(404,'项目不存在')
    crop_ids=set()
    if source_project_id is not None:
        for cond in iter_id_conditions(db, Asset.image_id, ids):
            crop_ids.update((await db.scalars(select(Asset.image_id).join(Project)
                .where(Asset.project_id==source_project_id,Project.tenant_id==tenant_id,
                    cond,uploaded_crop()))).all())
    hashes=sorted({image.content_hash for image in images})
    existing={}
    for cond in iter_id_conditions(db, Asset.content_hash, hashes):
        for asset in (await db.scalars(select(Asset).where(Asset.project_id==project_id,cond))).all():
            existing[asset.content_hash]=asset
    is_pg=db.get_bind().dialect.name=='postgresql'
    upsert=pg_insert if is_pg else sqlite_insert
    to_insert=[]
    pending_hashes=[]
    seen_new=set()
    for image in images:
        if image.content_hash in existing or image.content_hash in seen_new:
            continue
        seen_new.add(image.content_hash)
        values={name:getattr(image,name) for name in IMAGE_FIELDS}
        if image_kind=='crops' or image.id in crop_ids:
            values.update(source_type='model_crop',device_info={**(image.device_info or {}),'uploaded_crop':True})
        to_insert.append(dict(id=uuid4(),project_id=project_id,image_id=image.id,annotation_status='pending',**values))
        pending_hashes.append(image.content_hash)
    inserted_ids=set()
    for batch in batched(to_insert, 500):
        stmt=upsert(Asset).values(list(batch)).on_conflict_do_nothing(index_elements=['project_id','content_hash']).returning(Asset.id)
        inserted_ids.update((await db.scalars(stmt)).all())
    if pending_hashes:
        for cond in iter_id_conditions(db, Asset.content_hash, pending_hashes):
            for asset in (await db.scalars(select(Asset).where(Asset.project_id==project_id,cond))).all():
                existing[asset.content_hash]=asset
    created=[]
    result=[]
    seen_created=set()
    for image in images:
        asset=existing[image.content_hash]
        if asset.id in inserted_ids and asset.id not in seen_created:
            seen_created.add(asset.id)
            created.append(asset)
        elif asset.id not in inserted_ids and asset.image_id is None and asset.source_type!='model_crop':
            asset.image_id=image.id
        result.append(asset)
    if created:
        await db.execute(update(Project).where(Project.id==project_id).values(total_assets=Project.total_assets+len(created)))
    await db.flush()
    return result,created

async def get_collection(db,tenant_id,name):
    group=await db.scalar(select(ImageCollection).where(ImageCollection.tenant_id==tenant_id,ImageCollection.name==name))
    if group is None:
        from services.image_ingest import get_config
        config,_=await get_config(db,tenant_id)
        try:
            async with db.begin_nested():
                group=ImageCollection(tenant_id=tenant_id,name=name,
                    ingest_config=config.model_copy(update={'classification_enabled':False}).model_dump())
                db.add(group);await db.flush()
        except IntegrityError:
            group=await db.scalar(select(ImageCollection).where(ImageCollection.tenant_id==tenant_id,ImageCollection.name==name))
            if group is None:
                raise
    return group

async def assign_collection(db,tenant_id,image_ids,name):
    ids=sorted(set(image_ids))
    group=await get_collection(db,tenant_id,name)
    # Keep membership and settings serialized without blocking upload receipt foreign keys.
    group=await db.scalar(select(ImageCollection).where(ImageCollection.id==group.id).with_for_update(key_share=True))
    if group is None:raise HTTPException(404,'图片集不存在')
    if not ids:
        return group,0
    images=[]
    for cond in iter_id_conditions(db, LibraryImage.id, ids):
        images.extend((await db.scalars(select(LibraryImage).where(LibraryImage.tenant_id==tenant_id,
            cond,LibraryImage.source_type!='model_crop').order_by(LibraryImage.id).with_for_update())).all())
    if len(images)!=len(ids):
        raise HTTPException(404,'部分图片不存在')
    from services.image_ingest import get_config
    config,_=await get_config(db,tenant_id,group.id)
    upsert=pg_insert if db.get_bind().dialect.name=='postgresql' else sqlite_insert
    added=0
    member_rows=[{'collection_id':group.id,'image_id':image.id} for image in images]
    for batch in batched(member_rows, 1000):
        stmt=upsert(ImageCollectionMember).values(list(batch)).on_conflict_do_nothing(
            index_elements=['collection_id','image_id']).returning(ImageCollectionMember.image_id)
        added+=len((await db.scalars(stmt)).all())
    if config.classification_enabled:
        scene_cfg={'collection_id':str(group.id),'revision':group.ingest_revision,'config':config.model_dump()}
        for image in images:
            if image.scene_status=='unclassified' and (image.scene_metadata or {}).get('basis')!='device_name':
                image.scene_status='pending'
                image.scene_config=scene_cfg
    await db.flush()
    return group,added

async def merge_collections(db,tenant_id,source_id,target_id):
    """Maintenance operation: caller backs up and commits after all uploads/inference drain."""
    from services.image_uploads import resume_keys
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
        job.resume_key=resume_keys(job.filename,job.size,job.chunk_hashes,target_id,job.min_long_edge,
            job.annotation_format,job.trust_annotations,job.project_id)[0]
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

