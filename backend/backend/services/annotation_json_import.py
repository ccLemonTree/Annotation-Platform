"""Import filename-keyed JSON into the project's versioned annotations."""
import asyncio
import json
import re
from collections import Counter, defaultdict
from hashlib import sha256
from io import BytesIO
from itertools import batched
from pathlib import PurePosixPath

from fastapi import HTTPException
from PIL import Image, PngImagePlugin
from sqlalchemy import String, func, select, type_coerce

from api.asset_routes import scoped_project
from config.settings import settings
from models import Annotation, Asset
from services.annotation_scope import eligible_assets
from services.annotations import register_detection_labels, save_annotation
from services.image_library import iter_id_conditions
from services.object_attributes import UNKNOWN


def unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('重复的 JSON 键')
    return result


def valid_text(value, limit):
    return isinstance(value, str) and len(value) <= limit and not re.search(r'[\x00\ud800-\udfff]', value)


def annotation_fields(record, *, detection=False):
    allowed = {'label', 'attribute', 'description'}
    if detection:
        allowed.add('box')
    if not isinstance(record, dict) or record.keys() - allowed:
        raise ValueError('标注字段应为 label、attribute、description，检测框另含 box')
    labels = record.get('label', [])
    if (not isinstance(labels, list) or len(labels) > 100 or
            any(not valid_text(label, 100) or not label.strip() for label in labels)):
        raise ValueError('label 必须为有效的标签名称数组')
    labels = [label.strip() for label in labels]
    if len(set(labels)) != len(labels):
        raise ValueError('同一条标注中的标签不能重复')
    description = record.get('description', '')
    if not valid_text(description, 20000):
        raise ValueError('description 必须为不超过 20000 字的文本')
    attributes = record.get('attribute', {})
    if not isinstance(attributes, dict):
        raise ValueError('attribute 必须为属性名称到文本或 null 的对象')
    values = {}
    for key, value in attributes.items():
        if not key and value == '':
            continue
        if (not valid_text(key, 60) or not key.strip() or key.strip() in values or
                value is not None and not valid_text(value, 500)):
            raise ValueError('attribute 包含无效的名称或属性值')
        values[key.strip()] = value.strip() if value is not None else None
    return labels, values, description


def parse_annotation(record, asset):
    if isinstance(record, list):
        boxes = []
        for box in record:
            labels, attributes, description = annotation_fields(box, detection=True)
            coords = box.get('box')
            if (not isinstance(coords, list) or len(coords) != 4 or not asset.width or not asset.height or
                    any(type(value) not in (int, float) or not 0 <= value <= limit
                        for value, limit in zip(coords, (asset.width, asset.height, asset.width, asset.height)))):
                raise ValueError('box 必须为图片内的四个像素坐标 [左, 上, 右, 下]')
            if not labels:
                raise ValueError('每个检测框的 label 至少需要一个类别')
            left, top, right, bottom = coords
            boxes.append(dict(x=left, y=top, width=right-left, height=bottom-top, label=labels[0],
                              labels=labels, attributes=attributes, description=description))
        return {'boxes': boxes}, [box['label'] for box in boxes]

    labels, attributes, description = annotation_fields(record)
    return whole_image_data(labels, attributes, description), labels


def whole_image_data(labels, attributes, description):
    data = {'labels': labels, 'description': description}
    if attributes:
        if len(attributes) > 30:
            raise ValueError('一张裁剪图最多支持 30 个属性')
        fields, values = [], {}
        for i, (name, value) in enumerate(sorted(attributes.items()), 1):
            key = f'field_{i}'
            fields.append({'key': key, 'name': name})
            known = bool(value) and value.casefold() not in UNKNOWN
            values[key] = dict(value=value if known else None, reason='' if known else '导入数据未提供明确属性值')
        data.update(attribute_schema={'target_label': labels or '整图', 'fields': fields}, attributes=values)
    return data


def parse_annotation_records(raw):
    if len(raw) > settings.MAX_UPLOAD_SIZE:
        raise HTTPException(413, '标注文件超过上传大小限制')
    try:
        records = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_object)
    except (ValueError, RecursionError) as exc:
        raise HTTPException(422, '请选择有效且键名不重复的 UTF-8 JSON 文件') from exc
    if not isinstance(records, dict) or not records:
        raise HTTPException(422, 'JSON 必须是以图片文件名为键的非空对象')
    return [(PurePosixPath(path.replace('\\', '/')).name, record) for path, record in records.items()]


async def read_annotation_records(file):
    return parse_annotation_records(await file.read(settings.MAX_UPLOAD_SIZE + 1))


def annotation_image_kinds(entries):
    kinds = defaultdict(set)
    for name, record in entries:
        if isinstance(record, (dict, list)):
            kinds[name.lower()].add('crops' if isinstance(record, dict) else 'originals')
    return {name: next(iter(values)) for name, values in kinds.items() if len(values) == 1}


def box_crop_payloads(image, asset, boxes, seen):
    from services.project_work import crop_region

    for box in boxes:
        region = crop_region(box, image.width, image.height, 0.0)
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('project_crop', json.dumps([str(asset.project_id), str(asset.id), region]))
        fields = json.dumps([box['labels'], box['attributes'], box['description']], sort_keys=True)
        if seen.setdefault((region, box['label']), fields) != fields:
            metadata.add_text('project_crop_annotation', sha256(fields.encode()).hexdigest())
        output = BytesIO()
        image.crop(region).save(output, 'PNG', pnginfo=metadata)
        yield dict(content=output.getvalue(),width=region[2]-region[0],height=region[3]-region[1],
                   region=list(region),box=box)


async def create_json_crops(db, project, asset, boxes, actor_id):
    from services.project_work import persist_derived_crops
    from services.storage import get_storage

    if not boxes:
        return []
    ids = []
    seen = {}
    def load_image(file):
        with Image.open(file) as image:
            return image.convert('RGB')
    try:
        with await get_storage().get_file(asset.blob_path) as source:
            image = await asyncio.to_thread(load_image, source)
        with image:
            for chunk in batched(boxes, 16):
                payloads = await asyncio.to_thread(lambda: list(box_crop_payloads(image, asset, chunk, seen)))
                crop_ids, derived = await persist_derived_crops(db, project, asset, payloads)
                annotated = set(await db.scalars(select(Annotation.asset_id).where(
                    Annotation.asset_id.in_([crop.id for crop in derived]), Annotation.is_latest.is_(True))))
                unannotated = {crop.id:(crop,box) for crop,box in zip(derived,chunk) if crop.id not in annotated}
                if unannotated:
                    # A manual edit locks the crop before taking the project lock.
                    locked = set(await db.scalars(select(Asset.id).where(Asset.id.in_(unannotated))
                        .order_by(Asset.id).with_for_update(skip_locked=True)))
                    if locked != set(unannotated):
                        raise HTTPException(409, '裁剪图正在编辑，请重试导入')
                    for crop, box in unannotated.values():
                        data = whole_image_data(box['labels'], box['attributes'], box['description'])
                        data['import_source'] = {'source':'json_labels', 'box_labels':box['labels']}
                        await save_annotation(db, crop, data, 0, actor_id or 0, source='import_json',
                            workflow_attribute_schema=data.get('attribute_schema'))
                ids.extend(crop_ids)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (OSError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, '原图文件无法读取或裁剪') from exc
    return ids


async def import_annotations(db, project, file, actor_id, *, crop_from_boxes=False):
    return await apply_annotations(db, project, await read_annotation_records(file), actor_id,
        crop_from_boxes=crop_from_boxes)


async def apply_annotations(db, project, entries, actor_id, *, commit=True, crop_from_boxes=False):
    keys = Counter((name.lower(), isinstance(record, dict)) for name, record in entries if valid_text(name, 255))
    matches = defaultdict(list)
    project_id = project.id
    name_column = type_coerce(func.lower(Asset.original_name), String)
    for condition in iter_id_conditions(db, name_column, {name for name, _ in keys}):
        assets = await db.scalars(select(Asset).where(Asset.project_id == project_id, eligible_assets(project),
            condition).order_by(Asset.id).with_for_update())
        for asset in assets:
            matches[asset.original_name.lower(), asset.source_type == 'model_crop'].append(asset)
    project = await scoped_project(db, project_id, project.tenant_id, for_update=True)
    existing = {}
    ids = [assets[0].id for key, assets in matches.items() if keys[key] == len(assets) == 1]
    for condition in iter_id_conditions(db, Annotation.asset_id, ids):
        rows = await db.scalars(select(Annotation).where(condition, Annotation.is_latest.is_(True)))
        existing.update((row.asset_id, row.annotation_data) for row in rows)

    items = []
    box_labels = set()
    crop_ids = set()
    for name, record in entries:
        item = {'filename': name if valid_text(name, 255) else '无效文件名'}
        items.append(item)
        key = name.lower(), isinstance(record, dict)
        assets = matches.get(key, [])
        if not valid_text(name, 255) or name in ('', '.', '..') or not isinstance(record, (dict, list)):
            item.update(status='invalid', message='需要图片文件名和对应的标注列表或对象')
        elif keys[key] > 1 or len(assets) > 1:
            item.update(status='conflict', message='JSON 或项目中有同名图片，无法唯一匹配')
        elif not assets:
            item.update(status='missing', message='找不到同名且图片类型匹配的图片')
        else:
            asset = assets[0]
            item['asset_id'] = asset.id
            try:
                data, labels = parse_annotation(record, asset)
            except ValueError as exc:
                item.update(status='invalid', message=str(exc))
                continue
            data['import_source'] = {'source': 'json_labels'}
            previous = existing.get(asset.id)
            if previous is not None and previous != data:
                item.update(status='conflict', message='图片已有标注，保留原记录')
                continue
            try:
                async with db.begin_nested():
                    if previous is None:
                        await register_detection_labels(db, project, [{'label': label} for label in labels])
                        await save_annotation(db, asset, data, 0, actor_id or 0, source='import_json',
                            workflow_attribute_schema=data.get('attribute_schema'))
                    created_crops = await create_json_crops(db, project, asset, data.get('boxes', []), actor_id) if crop_from_boxes else []
            except HTTPException as exc:
                item.update(status='invalid', message=str(exc.detail))
                # A rolled back savepoint expires the SQLAlchemy project's changed label schema.
                await db.refresh(project)
            else:
                item['status'] = 'unchanged' if previous is not None else 'imported'
                box_labels.update(label for box in data.get('boxes', []) for label in box['labels'][1:])
                crop_ids.update(created_crops)
    schema = project.label_schema or {}
    if box_labels - set(schema.get('imported_box_labels') or []):
        project.label_schema = {**schema, 'imported_box_labels': sorted(box_labels | set(schema.get('imported_box_labels') or []))}
    if commit:
        await db.commit()
    return {'items': items, **({'crop_count':len(crop_ids)} if crop_from_boxes else {})}
