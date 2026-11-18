"""Private image storage and detection for project retrieval tests."""
import asyncio
import hashlib
import json
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import HTTPException
import numpy as np
from PIL import Image, ImageDraw, PngImagePlugin
from sqlalchemy import String, and_, func, or_, select

from config.settings import settings
from database import AsyncSessionLocal
from models import Project
from models.vector_test import VectorTestDataset, VectorTestSample
from schemas.model_service import ModelServiceConfig
from services.image_semantics import model_snapshot
from services.model_inference import infer
from services.project_work import crop_region
from services.storage import LocalStorage


def storage():
    return LocalStorage(settings.STORAGE_LOCAL_PATH)


def original_values(project_id, digest, extension, source_type, *, device_name=None, **metadata):
    return dict(project_id=project_id, content_hash=digest, source_type=source_type,
                device_name=(device_name or '')[:255] or None,
                blob_path=f'vector-test/{project_id}/originals/{digest}.{extension}',
                **{key: metadata[key] for key in ('original_name', 'width', 'height', 'format')})


async def read_content(sample):
    with await storage().get_file(sample.blob_path) as file:
        return file.read()


async def detection_config(db, project_id):
    dataset = await db.get(VectorTestDataset, project_id)
    return dataset.detection if dataset else None


def sample_scope(detection, targets=None):
    if targets is False or (targets is None and detection is None):
        return VectorTestSample.parent_id.is_(None)
    if not detection:
        return VectorTestSample.id.is_(None)
    return and_(VectorTestSample.parent_id.is_not(None),
                VectorTestSample.detection_revision == UUID(detection['id']))


async def configure_detection(db, project, request):
    snapshot = await model_snapshot(db, project.tenant_id, request.model_id, 'detection')
    options = dict(model=snapshot, labels=list(dict.fromkeys(request.labels)), confidence=(
        snapshot['config']['confidence'] if request.confidence is None else request.confidence))
    dataset = await db.get(VectorTestDataset, project.id)
    if dataset is None:
        dataset = VectorTestDataset(project_id=project.id, detection={})
        db.add(dataset)
    previous = dataset.detection
    failed = previous and await db.scalar(select(VectorTestSample.id).where(
        VectorTestSample.project_id == project.id, VectorTestSample.parent_id.is_(None),
        VectorTestSample.detection_revision == UUID(previous['id']),
        VectorTestSample.detection_error.is_not(None)).limit(1))
    if not previous or failed or any(previous[key] != value for key, value in options.items()):
        dataset.detection = dict(id=str(uuid4()), **options)
    await db.commit()
    return dataset.detection


async def progress(db, project_id, detection):
    revision = UUID(detection['id'])
    counts = {'pending': 0, 'failed': 0, 'completed': 0}
    rows = await db.execute(select(VectorTestSample.detection_revision, VectorTestSample.detection_error.is_not(None),
        func.count()).where(VectorTestSample.project_id == project_id, VectorTestSample.parent_id.is_(None))
        .group_by(VectorTestSample.detection_revision, VectorTestSample.detection_error.is_not(None)))
    for current, failed, count in rows:
        counts['pending' if current != revision else 'failed' if failed else 'completed'] += count
    return dict(id=revision, name=detection['model']['config']['name'], labels=detection['labels'],
                confidence=detection['confidence'], counts=counts)


def cut_targets(content, boxes, labels):
    with Image.open(BytesIO(content)) as original:
        image = original.convert('RGB')
        for box in boxes:
            if labels and box['label'] not in labels:
                continue
            region = crop_region(box, image.width, image.height, 0)
            if region[0] < region[2] and region[1] < region[3]:
                crop = image.crop(region)
                output = BytesIO()
                metadata = PngImagePlugin.PngInfo()
                metadata.add_text('vector_test_region', json.dumps(region))
                crop.save(output, 'PNG', pnginfo=metadata)
                yield output.getvalue(), crop.size


def locate_crop(original, crop):
    source, target = np.asarray(original), np.asarray(crop)
    height, width = crop.height, crop.width
    rows, cols = original.height - height + 1, original.width - width + 1
    if rows <= 0 or cols <= 0:
        return None
    matches = np.ones((rows, cols), dtype=bool)
    anchors = [(height // 2, width // 2), (0, 0), (height - 1, width - 1),
               (0, width - 1), (height - 1, 0)]
    for y, x in anchors:
        for channel in range(3):
            matches &= source[y:y + rows, x:x + cols, channel] == target[y, x, channel]
        if np.count_nonzero(matches) <= 4096:
            break
    else:
        # shortcut: skip highly repetitive crops when five sampled pixels still leave over 4096 possible locations.
        return None
    region = None
    for y, x in np.argwhere(matches):
        if np.array_equal(source[y:y + height, x:x + width], target):
            if region is not None:
                return None
            region = (int(x), int(y), int(x + width), int(y + height))
    return region


def result_preview(original, target=None, region=None):
    with Image.open(BytesIO(original)) as source:
        image = source.convert('RGB')
    if target is not None:
        with Image.open(BytesIO(target)) as crop:
            saved = crop.info.get('vector_test_region')
            region = json.loads(saved) if saved else locate_crop(image, crop.convert('RGB'))

    original_width, original_height = image.size
    image.thumbnail((1440, 1440), Image.Resampling.LANCZOS)
    if (isinstance(region, (list, tuple)) and len(region) == 4 and all(type(value) is int for value in region)
            and 0 <= region[0] < region[2] <= original_width and 0 <= region[1] < region[3] <= original_height):
        left, top, right, bottom = region
        draw = ImageDraw.Draw(image)
        bounds = (round(left * image.width / original_width), round(top * image.height / original_height),
                  round((right - 1) * image.width / original_width), round((bottom - 1) * image.height / original_height))
        draw.rectangle(bounds, outline='#171717', width=5)
        draw.rectangle(bounds, outline='#ffdc38', width=3)
    output = BytesIO()
    image.save(output, 'JPEG', quality=88)
    return output.getvalue()


async def detect_one(client):
    async with AsyncSessionLocal() as db:
        row = (await db.execute(select(VectorTestSample, VectorTestDataset.detection)
            .join(VectorTestDataset, VectorTestDataset.project_id == VectorTestSample.project_id)
            .where(VectorTestSample.parent_id.is_(None), or_(VectorTestSample.detection_revision.is_(None),
                func.replace(VectorTestSample.detection_revision.cast(String), '-', '') !=
                func.replace(VectorTestDataset.detection['id'].as_string(), '-', '')))
            .order_by(VectorTestSample.created_at, VectorTestSample.id).limit(1))).first()
        if row is None:
            return False
        sample, detection = row
        revision = UUID(detection['id'])
        await db.commit()
        crops, error = [], None
        try:
            config = ModelServiceConfig.model_validate(detection['model']['config'])
            labels = detection['labels']
            config = config.model_copy(update=dict(confidence=detection['confidence'], labels=labels or config.labels))
            content = await read_content(sample)
            result = await infer(config, content, sample.width, sample.height, client=client)
            matched = {config.label_mapping.get(label, label) for label in labels}
            crops = await asyncio.to_thread(lambda: list(cut_targets(content, result['boxes'], matched)))
        except (HTTPException, OSError, ValueError) as exc:
            error = str(exc.detail)[:500] if isinstance(exc, HTTPException) else '测试原图读取或切图失败'
        project = await db.scalar(select(Project.id).where(Project.id == sample.project_id).with_for_update())
        if not project:
            return True
        if await detection_config(db, project) != detection:
            return True
        await db.refresh(sample)
        if sample.detection_revision == revision:
            return True
        seen = set()
        for content, (width, height) in crops:
            digest = hashlib.sha256(content).hexdigest()
            if digest in seen:
                continue
            seen.add(digest)
            key = hashlib.sha256(f'{revision}:{sample.id}:{digest}'.encode()).hexdigest()
            path = f'vector-test/{project}/targets/{key}.png'
            await storage().save_file(BytesIO(content), path)
            db.add(VectorTestSample(project_id=project, parent_id=sample.id, content_hash=digest, blob_path=path,
                original_name=f'{Path(sample.original_name).stem[:220]}_{digest[:12]}.png', width=width, height=height,
                format='PNG', source_type='model_crop', device_name=sample.device_name, detection_revision=revision))
        sample.detection_revision = revision
        sample.detection_error = error
        await db.commit()
        return True
