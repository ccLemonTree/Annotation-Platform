"""Version, geometry and counters shared by single and batch annotation writes."""
import math
from datetime import datetime
from fastapi import HTTPException
from sqlalchemy import select, update, func
from api.asset_routes import scoped_asset
from models import Annotation, Asset, Project, AnnotationEvent, User
from services.annotation_scope import require_annotation_asset

def detection_boxes(data, width, height, labels=None):
    if not width or not height or not isinstance(data, dict) or not isinstance(data.get('boxes'), list):
        raise HTTPException(400, '检测标注需要图片尺寸和boxes列表')
    for box in data['boxes']:
        if not isinstance(box, dict) or not isinstance(box.get('label'), str) or not box['label'].strip():
            raise HTTPException(400, '每个框必须有类别名称')
        if labels and box['label'] not in labels:
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
        Annotation.id == annotation_id, Project.tenant_id == tenant_id))
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
    latest = await db.scalar(select(Annotation).where(Annotation.asset_id == asset.id, Annotation.is_latest.is_(True)))
    after = 'pending' if latest is None else ('reviewed' if latest.review_status == 'approved' else 'completed')
    asset.annotation_status = after
    delta = int(after in ('completed', 'reviewed')) - int(before in ('completed', 'reviewed'))
    reviewed_delta = int(after == 'reviewed') - int(before == 'reviewed')
    if delta or reviewed_delta:
        await db.execute(update(Project).where(Project.id == asset.project_id).values(
            annotated_count=Project.annotated_count + delta, reviewed_count=Project.reviewed_count + reviewed_delta))

async def save_annotation(db, asset, data, base_version, user_id, source='manual'):
    project = await db.scalar(select(Project).where(Project.id == asset.project_id)
        .with_for_update().execution_options(populate_existing=True))
    require_annotation_asset(project,asset)
    if project.task_type == 'detection':
        labels = (project.label_schema or {}).get('labels')
        detection_boxes(data, asset.width, asset.height, labels if isinstance(labels, list) else None)
    elif project.task_type in ('classification', 'caption', 'vector'):
        validate_image_annotation(project, data)
    previous = await db.scalar(select(Annotation).where(Annotation.asset_id == asset.id, Annotation.is_latest.is_(True)))
    if base_version != (previous.version if previous else 0):
        raise HTTPException(409, '标注已被修改，请重新加载最新版本')
    prior_version = await db.scalar(select(func.max(Annotation.version)).where(Annotation.asset_id == asset.id)) or 0
    deleted_version = await db.scalar(select(func.max(AnnotationEvent.detail['version'].as_integer()))
        .where(AnnotationEvent.asset_id == asset.id)) or 0
    version = max(prior_version, deleted_version) + 1
    if previous:
        previous.is_latest = False
        await db.flush()
    annotation = Annotation(asset_id=asset.id, annotation_data=data, version=version, is_latest=True,
                            annotator_id=user_id if source=='manual' else None, annotation_source=source, review_status='pending')
    db.add(annotation)
    await db.flush()
    record_event(db, asset, user_id, 'update' if previous else 'create', {
        'annotation_id': str(annotation.id), 'version': version, 'previous_version': base_version, 'annotation_data': data,'source':source})
    await sync_state(db, asset)
    return annotation

async def review(db, annotation, asset, approved, comment, user_id, source='manual'):
    from models.automation import AutomationItem
    project=await db.get(Project,asset.project_id)
    negative=project.task_type=='detection' and not annotation.annotation_data.get('boxes')
    queued=await db.scalar(select(AutomationItem.id).where(AutomationItem.annotation_id==annotation.id,
        AutomationItem.annotation_version==annotation.version,AutomationItem.status=='needs_review').limit(1))
    if negative or queued:
        actor=await db.get(User,user_id)
        if source=='ai' or actor is None or actor.role!='admin':
            raise HTTPException(403,'负样本及AI转人工条目必须由管理员最终审核')
    annotation.review_status = 'approved' if approved else 'rejected'
    annotation.review_comment = comment
    annotation.reviewer_id = user_id if source=='manual' else None
    annotation.reviewed_at = datetime.utcnow()
    record_event(db, asset, user_id, 'approve' if approved else 'reject', {
        'annotation_id': str(annotation.id), 'version': annotation.version, 'comment': comment,'source':source})
    await sync_state(db, asset)

async def remove(db, annotation, asset, user_id):
    record_event(db, asset, user_id, 'delete', {'annotation_id': str(annotation.id),
        'version': annotation.version, 'annotation_data': annotation.annotation_data})
    await db.delete(annotation)
    await sync_state(db, asset)


def validate_image_annotation(project, data):
    if not isinstance(data, dict) or set(data) - {'labels', 'description', 'skipped'}:
        raise HTTPException(400, '图片标注仅支持labels、description和skipped')
    labels, description = data.get('labels', []), data.get('description', '')
    schema = project.label_schema or {}
    if (not isinstance(labels, list) or len(labels) > 100 or
        any(not isinstance(label, str) or label not in schema.get('labels', []) for label in labels) or
        len(set(labels)) != len(labels)):
        raise HTTPException(400, '分类必须为项目中不重复的类别')
    if not isinstance(description, str) or len(description) > 20000:
        raise HTTPException(400, '描述必须为不超过20000字的文本')
    if 'skipped' in data and not isinstance(data['skipped'], bool):
        raise HTTPException(400, 'skipped必须为布尔值')
    if data.get('skipped'):
        if labels or description.strip():
            raise HTTPException(400, '跳过的图片不能同时提交分类或描述')
        return
    if schema.get('classification_mode', 'single') == 'single' and len(labels) > 1:
        raise HTTPException(400, '单选项目只能选择一个类别')
    if project.task_type == 'classification' and not labels:
        raise HTTPException(400, '请选择分类或标记无法判断')
    if project.task_type in ('caption', 'vector') and not description.strip() and not labels:
        raise HTTPException(400, '请填写描述、分类或标记无法判断')
