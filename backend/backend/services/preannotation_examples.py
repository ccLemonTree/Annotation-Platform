"""Versioned manual examples shared by detection and attribute preannotation."""
import hashlib,json
from copy import deepcopy
from io import BytesIO
from uuid import UUID
from PIL import Image
from fastapi import HTTPException
from sqlalchemy import select
from models import Asset,Annotation
from services.annotations import detection_boxes
from services.storage import get_storage
from services.model_inference import image_content
from config.settings import settings


async def manual_example(db,project,asset_id):
    asset=await db.scalar(select(Asset).where(Asset.id==asset_id,Asset.project_id==project.id).execution_options(populate_existing=True))
    if asset is None:raise HTTPException(404,'正确范例图片不存在或不属于当前项目')
    ann=await db.scalar(select(Annotation).where(Annotation.asset_id==asset.id,Annotation.is_latest.is_(True)).execution_options(populate_existing=True))
    return asset,example_snapshot(project,asset,ann)


def example_snapshot(project,asset,ann):
    if not ann or ann.annotation_source!='manual' or ann.annotator_id is None or ann.review_status=='rejected' or ann.annotation_data.get('skipped'):
        raise HTTPException(400,'正确范例必须是已保存且未驳回的人工标注')
    data=ann.annotation_data
    snapshot=dict(asset_id=str(asset.id),original_name=asset.original_name,content_hash=asset.content_hash,width=asset.width,height=asset.height,
        annotation_id=str(ann.id),annotation_version=ann.version,task_type=project.task_type,
        review_state=dict(status=ann.review_status,reviewer_id=ann.reviewer_id,reviewed_at=ann.reviewed_at.isoformat() if ann.reviewed_at else None))
    if project.task_type=='detection':
        boxes=detection_boxes(data,asset.width,asset.height,(project.label_schema or {}).get('labels'))
        if len(boxes)>100:raise HTTPException(400,'正确范例最多支持100个检测框')
        snapshot.update(boxes=deepcopy(boxes),label_schema=deepcopy(project.label_schema))
    elif project.task_type in ('caption','vector'):
        from services.object_attributes import validate_attributes
        if data.get('attribute_schema')!=(project.label_schema or {}).get('object_attributes'):
            raise HTTPException(409,'正确范例的属性配置与当前项目不一致，请重新人工标注')
        values=deepcopy(data.get('attributes'));validate_attributes(data.get('attribute_schema'),values)
        snapshot.update(attributes=values,attribute_schema=deepcopy(data['attribute_schema']))
    else:raise HTTPException(400,'正确范例适用于检测和属性预标注')
    return snapshot


async def validate_example(db,project,snapshot):
    if not snapshot:return None
    asset,current=await manual_example(db,project,UUID(snapshot['asset_id']))
    if current!=snapshot:raise HTTPException(409,'正确范例图片、标注或审核已变化，请重新选择范例并创建批次')
    return asset


async def read_example_content(asset):
    with await get_storage().get_file(asset.blob_path) as stream:content=stream.read(settings.MAX_UPLOAD_SIZE+1)
    if len(content)>settings.MAX_UPLOAD_SIZE or hashlib.sha256(content).hexdigest()!=asset.content_hash:
        raise HTTPException(409,'正确范例图片内容已变化或超过大小限制')
    with Image.open(BytesIO(content)) as picture:
        if picture.size!=(asset.width,asset.height):raise HTTPException(409,'正确范例图片尺寸已变化')
    return content


def example_messages(content,prompt,example=None):
    messages=[]
    if example:
        messages=[dict(role='user',content=image_content(example['content'],'这是人工确认的正确标注范例。下面的答案只对应这张范例图片，用于说明标注标准。')),
            dict(role='assistant',content=json.dumps(example['answer'],ensure_ascii=False))]
        prompt+='当前这张才是待处理图片。请独立判断，不要照抄范例的类别、坐标或属性值；无法确认时明确表示不确定。'
    return messages+[dict(role='user',content=image_content(content,prompt))]


def json_object(text):
    def unique(pairs):
        value={}
        for key,item in pairs:
            if key in value:raise ValueError('重复字段')
            value[key]=item
        return value
    text=text.strip()
    if text.startswith('```json') and text.endswith('```'):text=text[7:-3].strip()
    elif text.startswith('```') and text.endswith('```'):text=text[3:-3].strip()
    value=json.loads(text,object_pairs_hook=unique)
    if not isinstance(value,dict):raise ValueError('需要JSON对象')
    return value
