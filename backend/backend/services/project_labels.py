from fastapi import HTTPException
import hashlib,json
from sqlalchemy import String, cast, func, select
from models import Asset, Annotation, Project, AnnotationEvent
from services.annotations import IMAGE_FIELDS, save_annotation, validate_image_annotation
from services.annotation_scope import eligible_assets
from api.asset_routes import scoped_project


def pending_yolo(project):
    return select(Annotation).join(Asset).where(
        Annotation.project_id==project.id, Annotation.is_latest.is_(True),
        Annotation.status_for_query('detection')=='pending', eligible_assets(project),
        cast(Annotation.annotation_data['boxes'], String).like('%"yolo\\_class\\_id"%', escape='\\'))


async def yolo_mapping_batches(db, project):
    batch=Annotation.annotation_data['import_source']['batch_id'].as_string()
    latest=func.max(Annotation.created_at)
    rows=await db.execute(pending_yolo(project).with_only_columns(batch,func.count(),latest)
        .group_by(batch).order_by(latest.desc()))
    return [dict(batch_id=id,images=count,latest_at=created.isoformat()+'Z') for id,count,created in rows]


async def map_yolo_classes(db, project, request, user_id):
    batch=Annotation.annotation_data['import_source']['batch_id'].as_string()
    scope=pending_yolo(project).where(batch==str(request.batch_id) if request.batch_id else batch.is_(None))
    candidates=scope.with_only_columns(Annotation.asset_id)
    # Acquire asset locks before the project row, as single-image writes do.
    assets=(await db.scalars(select(Asset.id).where(Asset.id.in_(candidates))
        .order_by(Asset.id).with_for_update())).all()
    project=await scoped_project(db,project.id,project.tenant_id,for_update=True)
    if project.task_type!='detection':
        raise HTTPException(400,'YOLO 类别映射仅支持目标检测项目')
    schema=project.label_schema or {}
    project.label_schema={**schema,'labels':list(dict.fromkeys([*(schema.get('labels') or []),*request.labels]))}
    mapping=dict(enumerate(request.labels))
    images=boxes_count=0
    for offset in range(0,len(assets),1000):
        ids=assets[offset:offset+1000]
        current=(await db.scalars(scope.where(Annotation.asset_id.in_(ids))
            .execution_options(populate_existing=True))).all()
        pending=[]
        for annotation in current:
            boxes=[]
            count=0
            for box in annotation.annotation_data['boxes']:
                category=mapping.get(box.get('yolo_class_id'))
                if category is not None:
                    box={**box,'label':category}
                    del box['yolo_class_id']
                    count+=1
                boxes.append(box)
            if count:
                pending.append((annotation,boxes))
                boxes_count+=count
        if not pending:
            continue
        affected=[annotation.asset_id for annotation,_ in pending]
        event_versions=dict((await db.execute(select(AnnotationEvent.asset_id,
            func.max(AnnotationEvent.detail['version'].as_integer())).where(AnnotationEvent.asset_id.in_(affected))
            .group_by(AnnotationEvent.asset_id))).all())
        for annotation,_ in pending:
            annotation.is_latest=False
        await db.flush()
        for annotation,boxes in pending:
            version=max(annotation.version,event_versions.get(annotation.asset_id) or 0)+1
            data={**annotation.annotation_data,'boxes':boxes}
            states={**annotation.review_by_kind,'detection_source':'manual'} if annotation.review_by_kind else None
            db.add(Annotation(project_id=project.id,asset_id=annotation.asset_id,annotation_data=data,
                version=version,is_latest=True,annotation_source='manual',annotator_id=user_id,
                review_status=annotation.review_status,review_by_kind=states))
            db.add(AnnotationEvent(project_id=project.id,asset_id=annotation.asset_id,user_id=user_id,
                action='update',detail=dict(version=version,previous_version=annotation.version,source='yolo_class_mapping')))
        images+=len(pending)
        await db.flush()
    return dict(updated_images=images,updated_boxes=boxes_count)


async def label_removal_state(db,project_id,tenant_id,label,lock=False):
    project=await scoped_project(db,project_id,tenant_id)
    locked=None
    if lock:
        # Match single-annotation writers: acquire assets before the project row.
        locked=set((await db.scalars(select(Asset.id).where(Asset.project_id==project_id).order_by(Asset.id).with_for_update())).all())
        project=await db.scalar(select(Project).where(Project.id==project_id,Project.tenant_id==tenant_id)
            .with_for_update().execution_options(populate_existing=True))
        if project is None:raise HTTPException(404,'项目不存在')
    if project.task_type!='detection':raise HTTPException(400,'批量删除类别框仅支持检测项目')
    assets=(await db.scalars(select(Asset).where(Asset.project_id==project_id,eligible_assets(project))
        .order_by(Asset.id).execution_options(populate_existing=True))).all()
    if locked is not None and any(asset.id not in locked for asset in assets):raise HTTPException(409,'项目图片已变化，请重新预览')
    by_id={asset.id:asset for asset in assets}
    annotations=(await db.scalars(select(Annotation).join(Asset).where(Asset.project_id==project_id,
        eligible_assets(project),Annotation.is_latest.is_(True)).order_by(Annotation.asset_id)
        .execution_options(populate_existing=True))).all()
    matched=[];state=[]
    for annotation in annotations:
        asset=by_id.get(annotation.asset_id)
        if asset is None:raise HTTPException(409,'项目图片已变化，请重新预览')
        boxes=annotation.annotation_data.get('boxes',[])
        kept=[box for box in boxes if box.get('label')!=label]
        if len(kept)==len(boxes):continue
        matched.append((asset,annotation,kept,len(boxes)-len(kept)))
        state.append([str(asset.id),asset.width,asset.height,asset.annotation_status,str(annotation.id),annotation.version,
            annotation.review_status,annotation.reviewer_id,annotation.reviewed_at,annotation.annotation_data])
    token=hashlib.sha256(json.dumps([str(project_id),label,project.label_schema,[str(a.id) for a in assets],state],
        sort_keys=True,default=str).encode()).hexdigest()
    summary=dict(project_id=str(project_id),project_name=project.project_name,label=label,
        scope=(project.label_schema or {}).get('annotation_scope','all'),total_images=len(assets),
        affected_images=len(matched),removed_boxes=sum(row[3] for row in matched),empty_images=sum(not row[2] for row in matched),
        reviewed_images=sum(row[1].review_status=='approved' for row in matched),preview_token=token)
    return project,matched,summary


async def remove_project_label(db,project_id,user,request):
    project,matched,summary=await label_removal_state(db,project_id,user.tenant_id,request.label,lock=True)
    if request.preview_token!=summary['preview_token']:raise HTTPException(409,'项目标注或范围已变化，请重新预览后再删除')
    for asset,annotation,kept,_ in matched:
        await save_annotation(db,asset,{**annotation.annotation_data,'boxes':kept},annotation.version,user.id,source='bulk_label_delete')
    if matched:
        db.add(AnnotationEvent(project_id=project.id,user_id=user.id,action='remove_label',detail=summary))
    return summary

async def sync_project_labels(db, project, request, user_id):
    # All annotation writers lock assets before the project counter/schema row.
    assets = (await db.scalars(select(Asset).where(Asset.project_id == project.id)
        .order_by(Asset.id).with_for_update().execution_options(populate_existing=True))).all()
    project = await db.scalar(select(Project).where(Project.id == project.id)
        .with_for_update().execution_options(populate_existing=True))
    if 'base_label_schema' in request.model_fields_set and request.base_label_schema != project.label_schema:
        raise HTTPException(409, '项目类别配置已更新，请重新打开编辑窗口')
    mapping = request.label_renames
    if mapping and 'base_label_schema' not in request.model_fields_set:
        raise HTTPException(400, '类别重命名需要原项目配置')
    if mapping and project.task_type not in ('detection','classification','caption','vector'):
        raise HTTPException(400, '当前类别同步仅支持检测项目')
    labels = (request.label_schema or {}).get('labels', [])
    current = (await db.scalars(select(Annotation).join(Asset).where(
        Asset.project_id == project.id, Annotation.is_latest.is_(True)))).all()
    if (request.label_schema or {}).get('annotation_scope')=='crops':
        active={a.id for a in assets if a.source_type=='model_crop'}
        current=[a for a in current if a.asset_id in active]
    used = {box['label'] for annotation in current for box in annotation.annotation_data.get('boxes', []) if 'yolo_class_id' not in box}
    used.update(label for annotation in current for label in annotation.annotation_data.get('labels', []))
    sources = used | set((project.label_schema or {}).get('labels', []))
    if any(source not in sources or target not in labels for source, target in mapping.items()):
        raise HTTPException(400, '重命名必须从已有类别对应到项目的新类别')
    if any(mapping.get(label, label) not in labels for label in used):
        raise HTTPException(400, '已有框使用了被移除的类别，请设置旧类别到新类别的对应关系')
    old_schema = project.label_schema
    project.label_schema = request.label_schema
    if project.task_type in ('classification','caption','vector'):
        for annotation in current:
            data={key:value for key,value in annotation.annotation_data.items() if key in IMAGE_FIELDS}
            if not data and 'boxes' in annotation.annotation_data:continue
            data['labels']=[mapping.get(label,label) for label in data.get('labels',[])]
            data['labels']=list(dict.fromkeys(data['labels']))
            validate_image_annotation(project,data,enforce_classification='boxes' not in annotation.annotation_data)
    await db.flush()
    by_id = {asset.id: asset for asset in assets}
    changed = 0
    for annotation in current:
        boxes = annotation.annotation_data.get('boxes', [])
        image_labels=annotation.annotation_data.get('labels',[])
        if not any(mapping.get(box['label'], box['label']) != box['label'] for box in boxes) and not any(mapping.get(label,label)!=label for label in image_labels):
            continue
        data = {**annotation.annotation_data}
        if 'boxes' in data:
            data['boxes']=[{**box,'label':mapping.get(box['label'],box['label'])} for box in boxes]
        if 'labels' in data:
            data['labels']=list(dict.fromkeys(mapping.get(label,label) for label in image_labels))
        await save_annotation(db, by_id[annotation.asset_id], data, annotation.version, user_id)
        changed += 1
    if old_schema != request.label_schema or changed:
        db.add(AnnotationEvent(project_id=project.id, user_id=user_id, action='project_labels',
            detail={'previous_schema': old_schema, 'schema': request.label_schema,
                    'label_renames': mapping, 'updated_images': changed}))
