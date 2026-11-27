"""Version, geometry and counters shared by single and batch annotation writes."""
import math
from copy import deepcopy
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select, update, func
from api.asset_routes import scoped_asset
from models import Annotation, Asset, Project, AnnotationEvent, User
from services.annotation_scope import require_annotation_asset
from services.project_access import project_condition

IMAGE_FIELDS={'labels','description','skipped','attributes','attribute_schema'}
KIND_FIELDS={'image':IMAGE_FIELDS,'detection':{'boxes','negative'}}


def annotation_kinds(data):
    return [kind for kind in KIND_FIELDS if ('boxes' in data if kind=='detection' else any(data.get(key) for key in IMAGE_FIELDS))]


def overall_review(statuses):
    values=set(statuses)
    return 'pending' if 'pending' in values else 'rejected' if 'rejected' in values else 'approved'


def _lock_project_shared(query, db):
    if db.get_bind().dialect.name == 'postgresql':
        return query.with_for_update(read=True, key_share=True)
    return query.with_for_update()

async def register_detection_labels(db, project, boxes):
    schema=project.label_schema or {}
    existing=schema.get('labels') or []
    labels=list(dict.fromkeys([*existing,*(box['label'] for box in boxes)]))
    if labels!=existing:
        project.label_schema={**schema,'labels':labels}
        # Production sessions disable autoflush; save_annotation reloads the locked project.
        await db.flush()

def detection_boxes(data, width, height, labels=None, *, allow_unassigned=False):
    if not width or not height or not isinstance(data, dict) or not isinstance(data.get('boxes'), list):
        raise HTTPException(400, '检测标注需要图片尺寸和boxes列表')
    for box in data['boxes']:
        if not isinstance(box, dict) or not isinstance(box.get('label'), str) or not box['label'].strip():
            raise HTTPException(400, '每个框必须有类别名称')
        if 'yolo_class_id' in box and not allow_unassigned:
            raise HTTPException(400, '请先为所有导入框设置类别')
        if 'yolo_class_id' not in box and labels and box['label'] not in labels:
            raise HTTPException(400, '类别不在项目标签中')
        values = [box.get(k) for k in ('x', 'y', 'width', 'height')]
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in values):
            raise HTTPException(400, '框坐标必须为有限数字')
        x, y, w, h = values
        if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > width or y + h > height:
            raise HTTPException(400, '框必须位于图片内且宽高大于零')
    return data['boxes']

async def locked_annotation(db, annotation_id, tenant_id):
    asset_id = await db.scalar(select(Annotation.asset_id).join(Asset).join(Project).where(
        Annotation.id == annotation_id, Project.tenant_id == tenant_id,project_condition(db)))
    if asset_id is None:
        raise HTTPException(404, '标注不存在')
    asset = await scoped_asset(db, asset_id, tenant_id, for_update=True)
    annotation = await db.get(Annotation, annotation_id, populate_existing=True)
    if annotation is None:
        raise HTTPException(404, '标注不存在')
    if not annotation.is_latest:
        raise HTTPException(409, '此版本已过期，请重新打开最新标注')
    return annotation, asset

def record_event(db, asset, user_id, action, detail):
    db.add(AnnotationEvent(project_id=asset.project_id, asset_id=asset.id,
                           user_id=user_id, action=action, detail=detail))

async def sync_state(db, asset):
    before = asset.annotation_status
    await db.flush()
    project=await db.get(Project,asset.project_id)
    kind='detection' if project.task_type=='detection' else 'image'
    latest = await db.scalar(select(Annotation).where(Annotation.asset_id == asset.id, Annotation.is_latest.is_(True),
        Annotation.has_project_format(project.task_type)))
    after = 'pending' if latest is None else ('reviewed' if latest.status_for(kind) == 'approved' else 'completed')
    asset.annotation_status = after
    delta = int(after in ('completed', 'reviewed')) - int(before in ('completed', 'reviewed'))
    reviewed_delta = int(after == 'reviewed') - int(before == 'reviewed')
    if delta or reviewed_delta:
        await db.execute(update(Project).where(Project.id == asset.project_id).values(
            annotated_count=Project.annotated_count + delta, reviewed_count=Project.reviewed_count + reviewed_delta))

async def save_annotation(db, asset, data, base_version, user_id, source='manual', workflow_attribute_schema=None, kind=None,
                          allow_crop_source=False):
    project = await db.scalar(_lock_project_shared(
        select(Project).where(Project.id == asset.project_id, project_condition(db)), db
    ).execution_options(populate_existing=True))
    if project is None:raise HTTPException(404,'项目不存在或已撤销分配')
    if not (allow_crop_source and source=='auto' and kind=='detection'):
        require_annotation_asset(project,asset)
    previous = await db.scalar(select(Annotation).where(Annotation.asset_id == asset.id, Annotation.is_latest.is_(True)))
    if base_version != (previous.version if previous else 0):
        raise HTTPException(409, '标注已被修改，请重新加载最新版本')
    prior_data = previous.annotation_data if previous else {}
    provenance = prior_data.get('import_source')
    json_import = isinstance(provenance, dict) and provenance.get('source') == 'json_labels'
    if kind is not None:
        expected='detection' if project.task_type=='detection' else 'image'
        if project.task_type=='segmentation' or kind!=expected:
            raise HTTPException(400,'标注内容与项目的标注格式不匹配')
        fields=KIND_FIELDS[kind]
        if set(data)-fields:
            raise HTTPException(400,'本次提交包含其他标注类型的字段')
        if kind=='image':
            other_fields={'description','attribute_schema','attributes'} if project.task_type=='classification' else {'labels'}
            if any(data.get(key) for key in other_fields):
                raise HTTPException(400,'本次提交包含其他标注类型的字段')
            data={key:value for key,value in data.items() if key not in other_fields}
            if json_import and not data.get('skipped'):
                data.update({key: deepcopy(prior_data[key]) for key in other_fields if key in prior_data})
    if source=='manual' and isinstance(data.get('attributes'),dict):
        prior=previous.annotation_data if previous else {}
        evidence=prior.get('attributes',{}) if data.get('attribute_schema')==prior.get('attribute_schema') else {}
        for key,item in data['attributes'].items():
            if not isinstance(item,dict):continue
            probabilities=evidence.get(key,{}).get('probabilities')
            if 'probabilities' in item and item['probabilities']!=probabilities:
                raise HTTPException(400,'模型概率为只读证据，不能人工修改或伪造')
            if probabilities is not None:item['probabilities']=deepcopy(probabilities)
    if 'attribute_schema' in data:
        current_schema=(project.label_schema or {}).get('object_attributes')
        old_schema=(previous.annotation_data or {}).get('attribute_schema') if previous else None
        if data['attribute_schema'] is None or data['attribute_schema'] not in (current_schema,old_schema,workflow_attribute_schema):
            raise HTTPException(409,'属性配置已变化，请使用当前配置或该标注原有的配置')
    image_data={key:value for key,value in data.items() if key in IMAGE_FIELDS}
    if 'boxes' in data or kind=='detection' or project.task_type=='detection' and kind is None and not any(image_data.values()):
        if 'description' in data and (not isinstance(data['description'],str) or len(data['description'])>20000):
            raise HTTPException(400,'图片描述必须为不超过20000字符的文本')
        labels = (project.label_schema or {}).get('labels')
        imported_boxes = (previous.annotation_data.get('boxes', []) if previous else [])
        unresolved = {(box['label'], box['yolo_class_id']) for box in imported_boxes if 'yolo_class_id' in box}
        detection_boxes(data, asset.width, asset.height, labels if isinstance(labels, list) else None,
                        allow_unassigned=source=='import_yolo' or bool(unresolved))
        if unresolved and any('yolo_class_id' in box for box in data['boxes']) and 'import_source' in previous.annotation_data:
            data={**data,'import_source':previous.annotation_data['import_source']}
        if source!='import_yolo' and any(
            type(box['yolo_class_id']) is not int or (box['label'], box['yolo_class_id']) not in unresolved
            for box in data['boxes'] if 'yolo_class_id' in box
        ):
            raise HTTPException(400, '只能保留当前标注中已有的待设置类别')
    if kind=='image' or kind is None and ('boxes' not in data or any(image_data.values())):
        crop_source = data.get('import_source') if source == 'import_json' else provenance
        inherited_labels = ()
        if asset.source_type == 'model_crop' and isinstance(crop_source, dict) and crop_source.get('source') == 'json_labels':
            inherited_labels = crop_source.get('box_labels') or ()
        validate_image_annotation(project,image_data,enforce_classification='boxes' not in data,
            inherited_labels=inherited_labels)
    if json_import and kind is not None:
        hidden_fields = IMAGE_FIELDS if kind == 'detection' else KIND_FIELDS['detection']
        data = {**data, **{key: prior_data[key] for key in hidden_fields if key in prior_data},
                'import_source': prior_data['import_source']}
    prior_version = await db.scalar(select(func.max(Annotation.version)).where(Annotation.asset_id == asset.id)) or 0
    deleted_version = await db.scalar(select(func.max(AnnotationEvent.detail['version'].as_integer()))
        .where(AnnotationEvent.asset_id == asset.id)) or 0
    version = max(prior_version, deleted_version) + 1
    if previous:
        previous.is_latest = False
        await db.flush()
    statuses={}
    mixed=len(annotation_kinds(data))>1
    if mixed:
        original=previous.annotation_data if previous else {}
        for item,fields in KIND_FIELDS.items():
            unchanged=all(data.get(key)==original.get(key) for key in fields)
            preserved=previous is not None and item!=kind and unchanged
            statuses[item]=(previous.status_for(item) or 'pending') if preserved else 'pending'
            if item=='detection':
                statuses['detection_source']=((previous.review_by_kind or {}).get('detection_source') or previous.annotation_source) if preserved else source
    if previous and kind=='image' and (previous.review_by_kind or {}).get('ai_needs_review'):
        statuses['ai_needs_review']=True
    status=overall_review(statuses[item] for item in KIND_FIELDS) if mixed else 'pending'
    annotation = Annotation(asset_id=asset.id, project_id=asset.project_id, annotation_data=data, version=version, is_latest=True,
                            annotator_id=user_id if source=='manual' else None, annotation_source=source, review_status=status,
                            review_by_kind=statuses or None)
    db.add(annotation)
    await db.flush()
    record_event(db, asset, user_id, 'update' if previous else 'create', {
        'annotation_id': str(annotation.id), 'version': version, 'previous_version': base_version, 'annotation_data': data,'source':source})
    await sync_state(db, asset)
    return annotation

async def review(db, annotation, asset, approved, comment, user_id, source='manual', kind=None):
    from models.automation import AutomationItem
    project=await db.scalar(_lock_project_shared(
        select(Project).where(Project.id==asset.project_id,project_condition(db)), db))
    if project is None:raise HTTPException(404,'项目不存在或已撤销分配')
    if kind is not None and kind!=('detection' if project.task_type=='detection' else 'image'):
        raise HTTPException(400,'标注内容与项目的标注格式不匹配')
    kinds=annotation_kinds(annotation.annotation_data)
    if kind is None:
        if len(kinds)>1:raise HTTPException(422,'请选择要审核的标注类型')
        kind=kinds[0] if kinds else None
    if kind is not None and kind not in kinds:raise HTTPException(400,'当前图片没有该类型标注')
    if approved and kind=='detection' and any('yolo_class_id' in box for box in annotation.annotation_data['boxes']):
        raise HTTPException(400,'请先为所有导入框设置类别')
    negative=kind=='detection' and annotation.annotation_data.get('boxes')==[]
    queued=await db.scalar(select(AutomationItem.id).where(AutomationItem.annotation_id==annotation.id,
        AutomationItem.annotation_version==annotation.version,AutomationItem.status=='needs_review').limit(1))
    if negative or queued and kind!='image':
        actor=await db.get(User,user_id)
        if source=='ai' or actor is None or actor.role!='admin':
            raise HTTPException(403,'负样本及AI转人工条目必须由管理员最终审核')
    status = 'approved' if approved else 'rejected'
    if len(kinds)>1 or annotation.review_by_kind:
        statuses={item:(status if item==kind else annotation.status_for(item) or 'pending') for item in kinds}
        if (annotation.review_by_kind or {}).get('detection_source'):
            statuses['detection_source']=annotation.review_by_kind['detection_source']
        if kind!='detection' and (annotation.review_by_kind or {}).get('ai_needs_review'):
            statuses['ai_needs_review']=True
        annotation.review_by_kind=statuses
        annotation.review_status=overall_review(statuses[item] for item in kinds)
    else:
        annotation.review_status=status
    annotation.review_comment = comment
    annotation.reviewer_id = user_id if source=='manual' else None
    annotation.reviewed_at = datetime.utcnow()
    record_event(db, asset, user_id, 'approve' if approved else 'reject', {
        'annotation_id': str(annotation.id), 'version': annotation.version, 'comment': comment,'source':source,'kind':kind})
    await sync_state(db, asset)

async def remove(db, annotation, asset, user_id):
    record_event(db, asset, user_id, 'delete', {'annotation_id': str(annotation.id),
        'version': annotation.version, 'annotation_data': annotation.annotation_data})
    await db.delete(annotation)
    await sync_state(db, asset)


def validate_image_annotation(project, data, enforce_classification=True, *, inherited_labels=()):
    if not isinstance(data, dict) or set(data) - IMAGE_FIELDS:
        raise HTTPException(400, '图片标注仅支持类别、描述、跳过状态和结构化属性')
    labels, description = data.get('labels', []), data.get('description', '')
    schema = project.label_schema or {}
    allowed = schema.get('labels') or ()
    if (not isinstance(labels, list) or len(labels) > 100 or
        any(not isinstance(label, str) or label not in allowed and label not in inherited_labels for label in labels) or
        len(set(labels)) != len(labels)):
        raise HTTPException(400, '分类必须为项目中不重复的类别')
    if not isinstance(description, str) or len(description) > 20000:
        raise HTTPException(400, '描述必须为不超过20000字的文本')
    if 'skipped' in data and not isinstance(data['skipped'], bool):
        raise HTTPException(400, 'skipped必须为布尔值')
    if data.get('skipped'):
        if labels or description.strip() or data.get('attributes') or data.get('attribute_schema'):
            raise HTTPException(400, '跳过的图片不能同时提交分类或描述')
        return
    if 'attributes' in data or 'attribute_schema' in data:
        from services.object_attributes import validate_attributes
        validate_attributes(data.get('attribute_schema'),data.get('attributes'))
    if (project.task_type == 'classification' and schema.get('classification_mode', 'single') == 'single'
            and len(labels) > 1 and not set(labels).issubset(inherited_labels)):
        raise HTTPException(400, '单选项目只能选择一个类别')
    if enforce_classification and project.task_type == 'classification' and not labels:
        raise HTTPException(400, '请选择分类或标记无法判断')
    if not description.strip() and not labels and not data.get('attributes'):
        raise HTTPException(400, '请填写描述、分类或标记无法判断')
