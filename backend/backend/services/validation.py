"""Frozen human truth, a single-consumer queue, and bounded metric aggregation."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import logging
import math
from uuid import UUID, uuid4

from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select, func, exists, update, delete
from models import Asset, Annotation, Project, User
from models.annotation_event import ExportSnapshot
from models.image import LibraryImage
from models.model_service import ModelService
from models.validation import ValidationSet, ValidationRun, ValidationItem
from schemas.model_service import ModelServiceConfig
from schemas.validation import ValidationConfig
from services.annotations import detection_boxes
from services.annotation_scope import eligible_assets
from services.automation import model_snapshot
from services.detection_metrics import aggregate_metrics, evaluate_image
from services.model_inference import infer
from services.storage import get_storage
from config.settings import settings
from services import multimodal_validation as multimodal

logger = logging.getLogger(__name__)
_worker = None
TERMINAL = ('completed', 'failed', 'canceled')


async def set_scope(db, id, tenant_id, lock=False):
    query = select(ValidationSet).where(ValidationSet.id == id, ValidationSet.tenant_id == tenant_id)
    if lock: query = query.with_for_update()
    row = await db.scalar(query)
    if row is None: raise HTTPException(404, '验证集不存在')
    return row


async def run_scope(db, id, tenant_id, lock=False):
    query = select(ValidationRun).where(ValidationRun.id == id, ValidationRun.tenant_id == tenant_id)
    if lock: query = query.with_for_update()
    row = await db.scalar(query)
    if row is None: raise HTTPException(404, '验证任务不存在')
    if row.model_snapshot.get('preview'):raise HTTPException(404,'请从模型效果预览查看此任务')
    return row


def public_columns(row, names):
    result = {name: getattr(row, name) for name in names.split()}
    return {key: value.replace(tzinfo=timezone.utc) if isinstance(value, datetime) and value.tzinfo is None else value
            for key, value in result.items()}


def set_dict(row):
    result=public_columns(row, 'id tenant_id project_id user_id name labels source total created_at')
    if 'queries' in row.source:result['source']={**{k:v for k,v in row.source.items() if k!='queries'},'query_count':len(row.source['queries'])}
    return result


def run_dict(row):
    result = public_columns(row, 'id set_id name status total processed failed_count config metrics error created_at updated_at')
    snapshot = row.model_snapshot
    result['model'] = {key: snapshot[key] for key in ('id', 'revision', 'name')}
    if snapshot.get('task_type'):result['model']['task_type']=snapshot['task_type']
    if snapshot.get('config') and not ModelServiceConfig.model_validate(snapshot['config']).has_confidence:
        result['model']['score_type'] = 'uncalibrated'
    if snapshot.get('source')=='checkpoint':
        checkpoint=snapshot['checkpoint']
        result['model'].update(source='checkpoint',weight_sha256=checkpoint['weight_sha256'],weight_path=checkpoint['weight_path'],
            kind=checkpoint['kind'],device=checkpoint['device'],image_digest=checkpoint['image_digest'])
        result['remote_active']=snapshot['execution']['active']
    return result


def item_dict(row):
    result=public_columns(row, 'id asset_id original_name width height status truth predictions evaluation error')
    if row.evaluation:result['evaluation']={k:v for k,v in row.evaluation.items() if k not in ('vector','queries')}
    return result


def valid_labels(labels):
    if (not isinstance(labels, list) or not labels or len(labels) > 100 or
        any(not isinstance(label, str) or not label.strip() or len(label) > 100 or label == '背景' for label in labels) or
        len(set(labels)) != len(labels)):
        raise HTTPException(400, '检测类别须为不重复的非空名称，且不含保留类别背景')
    return labels


def clean_boxes(data, width, height, labels):
    if (isinstance(width, bool) or isinstance(height, bool) or not isinstance(width, int) or
        not isinstance(height, int) or width <= 0 or height <= 0):
        raise HTTPException(400, '真值缺少有效原图尺寸')
    boxes = detection_boxes(data, width, height, labels)
    if len(boxes) > 5000: raise HTTPException(400, '单图框数超过5000')
    return [{key: box[key] for key in ('label', 'x', 'y', 'width', 'height')} for box in boxes]


async def read_sample(sample):
    """Return verified bytes from the pinned shared path, never from current assets."""
    try:
        stream = await get_storage().get_file(sample['blob_path'])
        def read():
            with stream:
                content = stream.read(settings.MAX_UPLOAD_SIZE + 1)
            if len(content) > settings.MAX_UPLOAD_SIZE or hashlib.sha256(content).hexdigest() != sample['content_hash']:
                raise HTTPException(409, '冻结原图哈希或大小不一致，不能继续验证')
            with Image.open(BytesIO(content)) as image:
                if image.size != (sample['width'], sample['height']):
                    raise HTTPException(409, '冻结原图尺寸不一致，不能继续验证')
                media_type = Image.MIME.get(image.format, 'application/octet-stream')
                image.verify()
            return content, media_type
        return await asyncio.to_thread(read)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(409, '冻结原图不存在或无法读取；报告仍保留')


async def create_set(db, request, user):
    project = await db.scalar(select(Project).where(Project.id == request.project_id,
        Project.tenant_id == user.tenant_id))
    if project is None: raise HTTPException(404, '项目不存在')
    if project.task_type in ('caption','vector'):return await multimodal.create_set(db,request,user,project)
    if project.task_type != 'detection': raise HTTPException(400, '验证集仅支持检测项目')
    source = dict(project_id=str(project.id), project_name=project.project_name, human_reviewed=True,
        kind='snapshot' if request.snapshot_id else 'project', independent=False)
    samples = []
    selected = set(request.asset_ids or [])
    if request.snapshot_id:
        snapshot = await db.scalar(select(ExportSnapshot).where(ExportSnapshot.id == request.snapshot_id,
            ExportSnapshot.project_id == project.id).with_for_update())
        if snapshot is None: raise HTTPException(404, '冻结导出不存在或不属于当前项目')
        manifest = snapshot.manifest
        labels = valid_labels(manifest.get('labels'))
        rows = manifest.get('assets')
        if not isinstance(rows, list): raise HTTPException(400, '冻结清单缺少图片列表')
        rows = [row for row in rows if isinstance(row, dict) and row.get('split') == request.split]
        try:
            if selected:
                rows = [row for row in rows if UUID(row['asset_id']) in selected]
                if {UUID(row['asset_id']) for row in rows} != selected:
                    raise HTTPException(404, '部分图片不在冻结清单的指定划分中')
            if len(rows) > 5000: raise HTTPException(400, '单验证集最多5000张，请分批选择')
            annotations = {annotation.id: annotation for annotation in (await db.scalars(select(Annotation).where(
                Annotation.id.in_({UUID(row['annotation_id']) for row in rows})))).all()}
            assets = {asset.id: asset for asset in (await db.scalars(select(Asset).where(Asset.project_id == project.id,
                Asset.id.in_({UUID(row['asset_id']) for row in rows})))).all()}
            missing_hashes = {row['content_hash'] for row in rows if UUID(row['asset_id']) not in assets or
                assets[UUID(row['asset_id'])].content_hash != row['content_hash']}
            library_images = {image.content_hash: image for image in (await db.scalars(select(LibraryImage).where(
                LibraryImage.tenant_id == user.tenant_id, LibraryImage.content_hash.in_(missing_hashes)))).all()} if missing_hashes else {}
            for row in rows:
                annotation = annotations.get(UUID(row['annotation_id']))
                if (annotation is None or annotation.asset_id != UUID(row['asset_id']) or
                    annotation.version != row['annotation_version'] or annotation.review_status != 'approved' or
                    annotation.reviewer_id is None or row.get('review_status') != 'approved'):
                    raise HTTPException(400, '冻结真值缺少人工审核证据，请对该版本人工审核并重新冻结')
                truth = clean_boxes(row['annotation_data'], row['width'], row['height'], labels)
                human_data = deepcopy(annotation.annotation_data)
                human_data['boxes'] = [box for box in human_data.get('boxes', []) if box.get('label') in labels]
                if clean_boxes(human_data, row['width'], row['height'], labels) != truth:
                    raise HTTPException(400, '冻结真值与人工审核版本不一致，请重新冻结')
                asset = assets.get(annotation.asset_id)
                if asset is not None and asset.content_hash != row['content_hash']:
                    asset = None
                library = library_images.get(row['content_hash']) if asset is None else None
                if asset is None and library is None:
                    raise HTTPException(400, '冻结图片已删除或原图无法按哈希定位，无法创建验证集')
                samples.append(dict(asset_id=row['asset_id'], annotation_id=row['annotation_id'],
                    annotation_version=row['annotation_version'], original_name=row['original_name'],
                    blob_path=(asset or library).blob_path, content_hash=row['content_hash'],
                    width=row['width'], height=row['height'], truth=truth))
                samples[-1].update(annotation_source=annotation.annotation_source,reviewer_id=annotation.reviewer_id)
        except (KeyError, TypeError, ValueError):
            raise HTTPException(400, '冻结清单真值格式无效或缺少人工审核证据')
        source.update(snapshot_id=str(snapshot.id), split=request.split,
            snapshot_content_hash=snapshot.content_hash)
    else:
        labels = valid_labels((project.label_schema or {}).get('labels'))
        query = select(Asset, Annotation).join(Annotation, (Annotation.asset_id == Asset.id) &
            Annotation.is_latest.is_(True)).where(Asset.project_id == project.id, eligible_assets(project),
                Annotation.review_status == 'approved', Annotation.reviewer_id.is_not(None))
        if selected: query = query.where(Asset.id.in_(selected))
        rows = (await db.execute(query.order_by(Asset.id).limit(5001))).all()
        if selected and {asset.id for asset, _ in rows} != selected:
            found = await db.scalar(select(func.count()).select_from(Asset).where(Asset.project_id == project.id, Asset.id.in_(selected)))
            if found != len(selected): raise HTTPException(404, '部分图片不属于当前项目')
            raise HTTPException(400, '所选图片必须全部经过人工审核通过，AI自动通过不能作为真值')
        if len(rows) > 5000: raise HTTPException(400, '单验证集最多5000张，请分批选择')
        for asset, annotation in rows:
            samples.append(dict(asset_id=str(asset.id), annotation_id=str(annotation.id),
                annotation_version=annotation.version, original_name=asset.original_name, blob_path=asset.blob_path,
                content_hash=asset.content_hash, width=asset.width, height=asset.height,
                truth=clean_boxes(annotation.annotation_data, asset.width, asset.height, labels)))
            samples[-1].update(annotation_source=annotation.annotation_source,reviewer_id=annotation.reviewer_id)
    if not samples: raise HTTPException(400, '没有符合条件的真值，请先完成管理员/人工审核通过（负样本也须确认）')
    if len({sample['asset_id'] for sample in samples}) != len(samples): raise HTTPException(400, '冻结清单含重复图片')
    imported=sum(sample.get('annotation_source')=='import_yolo' for sample in samples)
    source.update(imported_annotation_count=imported,human_reviewed=imported==0,review_policy='manual_or_admin_trusted_import')
    dataset = ValidationSet(tenant_id=user.tenant_id, project_id=project.id, user_id=user.id,
        name=request.name, labels=deepcopy(labels), samples=deepcopy(samples), source=source, total=len(samples))
    db.add(dataset)
    await db.commit()
    await db.refresh(dataset)
    return dataset


async def create_run(db, request, user):
    dataset = await set_scope(db, request.set_id, user.tenant_id, lock=True)
    if dataset.source.get('kind')=='upload_preview':raise HTTPException(400,'上传图片请从模型效果预览启动')
    kind=dataset.source.get('task_type','detection')
    if request.checkpoint:
        if (request.checkpoint.kind if request.checkpoint.kind in ('caption','embedding') else 'detection')!=kind:raise HTTPException(400,'模型用途与验证集不匹配')
        from services.checkpoint_validation import snapshot as checkpoint_snapshot
        snapshot=await checkpoint_snapshot(db,user,request.checkpoint)
    else:
        snapshot = await (model_snapshot(db, request.model_id, user.tenant_id, kind) if kind=='detection' else multimodal.model_snapshot(db,request.model_id,user.tenant_id,kind))
        snapshot['name'] = snapshot['config']['name']
        if kind=='detection' and not ModelServiceConfig.model_validate(snapshot['config']).has_confidence:
            raise HTTPException(400, '模型未提供可用检测置信度，不能计算依赖置信度的验证指标，请使用模型效果预览')
    policy = request.config.model_dump()
    if kind!='detection':snapshot['task_type']=kind
    if any(label not in dataset.labels for label in policy['label_mapping'].values()):
        raise HTTPException(400, '映射目标必须属于验证集冻结类别')
    if not request.checkpoint and kind=='detection':
        policy['label_mapping'] = {**snapshot['config']['label_mapping'], **policy['label_mapping']}
        policy['labels'] = policy['labels'] or snapshot['config']['labels']
    run = ValidationRun(set_id=dataset.id, tenant_id=user.tenant_id, user_id=user.id,
        name=request.name or dataset.name, model_snapshot=snapshot, config=policy, total=dataset.total)
    db.add(run)
    await db.flush()
    for sample in dataset.samples:
        db.add(ValidationItem(run_id=run.id, asset_id=UUID(sample['asset_id']),
            original_name=sample['original_name'], width=sample['width'], height=sample['height'], truth=deepcopy(sample['truth'])))
    await recount(db, run)
    await db.commit()
    await db.refresh(run)
    return run


async def refresh_set(db, id, user):
    from schemas.validation import ValidationSetCreate
    original=await set_scope(db,id,user.tenant_id)
    if original.source.get('kind')=='upload_preview' or not original.project_id:
        raise HTTPException(409,'此验证集没有可更新的项目来源')
    request=ValidationSetCreate(name=original.name[:185]+' · 更新标注',project_id=original.project_id,
        asset_ids=[UUID(sample['asset_id']) for sample in original.samples if sample.get('candidate',True)])
    dataset=await create_set(db,request,user)
    dataset.source={**dataset.source,'refreshed_from_set_id':str(original.id)}
    await db.commit();await db.refresh(dataset)
    return dataset


async def remove_set(db, id, user):
    dataset=await set_scope(db,id,user.tenant_id,lock=True)
    if dataset.source.get('kind')=='upload_preview':raise HTTPException(404,'请从模型效果预览删除此记录')
    runs=(await db.scalars(select(ValidationRun).where(ValidationRun.set_id==id).with_for_update())).all()
    if any(run.status in ('pending','running') or run.model_snapshot.get('execution',{}).get('active') for run in runs):
        raise HTTPException(409,'验证集仍有待执行或运行中的任务，请先取消并确认远端已停止，再删除验证集')
    ids=[run.id for run in runs]
    if ids:
        await db.execute(delete(ValidationItem).where(ValidationItem.run_id.in_(ids)))
        await db.execute(delete(ValidationRun).where(ValidationRun.id.in_(ids)))
    await db.delete(dataset);await db.commit()


async def validate_context(db, run, lock=False):
    query = select(User).where(User.id == run.user_id)
    if lock: query = query.with_for_update()
    actor = await db.scalar(query.execution_options(populate_existing=True))
    service_preview=run.model_snapshot.get('preview') and run.model_snapshot.get('source')!='checkpoint'
    if actor is None or not actor.is_active or (actor.role != 'admin' and not service_preview) or actor.tenant_id != run.tenant_id:
        raise HTTPException(403, '任务创建人已失去管理员权限，验证已停止')
    snapshot = run.model_snapshot
    if snapshot.get('source')=='checkpoint':
        from services.checkpoint_validation import validate_context as validate_checkpoint
        return await validate_checkpoint(db,run,lock)
    query = select(ModelService).where(ModelService.id == UUID(snapshot['id']), ModelService.tenant_id == run.tenant_id)
    if lock: query = query.with_for_update()
    model = await db.scalar(query.execution_options(populate_existing=True))
    if model is None or not model.enabled or model.revision != snapshot['revision'] or model.kind != multimodal.task_type(run):
        raise HTTPException(409, '模型已停用、删除或修改，请新建验证任务')


async def recount(db, run):
    await db.flush()
    counts = dict((await db.execute(select(ValidationItem.status, func.count()).where(
        ValidationItem.run_id == run.id).group_by(ValidationItem.status))).all())
    succeeded, failed, canceled = (counts.get(status, 0) for status in TERMINAL)
    pending = counts.get('pending', 0) + counts.get('running', 0)
    run.processed = succeeded + failed + canceled
    run.failed_count = failed
    if run.status != 'canceled':
        run.status = 'running' if counts.get('running') else ('pending' if counts.get('pending') else ('failed' if failed else 'completed'))
    run.metrics = dict(coverage=dict(total=run.total, succeeded=succeeded, failed=failed, pending=pending,
        canceled=canceled, complete=succeeded == run.total and failed == canceled == pending == 0))
    run.error = '部分图片验证失败，请查看逐图错误后重试' if failed else None
    run.updated_at = datetime.utcnow()


async def calculate_metrics(db, run, items=None):
    if multimodal.task_type(run)!='detection':return await multimodal.metrics(db,run,items)
    if run.model_snapshot.get('preview'):return run.metrics
    if items is None and 'images' in (run.metrics or {}) and run.status in TERMINAL: return run.metrics
    dataset = await set_scope(db, run.set_id, run.tenant_id)
    if items is None:
        rows = (await db.execute(select(ValidationItem.status, ValidationItem.truth, ValidationItem.predictions).where(
            ValidationItem.run_id == run.id).order_by(ValidationItem.created_at, ValidationItem.id))).all()
    else:
        rows = [(item.status, item.truth, item.predictions) for item in items]
    metrics = await asyncio.to_thread(aggregate_metrics, [dict(truth=truth, predictions=predictions)
        for status, truth, predictions in rows if status == 'completed'], dataset.labels,
        run.config['confidence'], run.config['match_iou'])
    counts = {status: sum(row[0] == status for row in rows) for status in (*TERMINAL, 'pending', 'running')}
    coverage = dict(total=run.total, succeeded=counts['completed'], failed=counts['failed'],
        pending=counts['pending'] + counts['running'], canceled=counts['canceled'],
        complete=counts['completed'] == run.total)
    return {**metrics, 'coverage': coverage}


async def evaluated_results(db, run, threshold, items=None, *, merge_duplicates=True):
    dataset = await set_scope(db, run.set_id, run.tenant_id)
    if items is None:
        items = (await db.scalars(select(ValidationItem).where(ValidationItem.run_id == run.id)
            .order_by(ValidationItem.created_at.desc(), ValidationItem.id))).all()
    def calculate():
        rows=[]
        for item in items:
            row=item_dict(item)
            row['standard_evaluation']=row['evaluation']
            if item.status=='completed':
                row['evaluation']=evaluate_image(item.truth,item.predictions,dataset.labels,
                    run.config['confidence'],threshold,merge_duplicates=merge_duplicates)
            rows.append(row)
        metrics=aggregate_metrics([dict(truth=item.truth,predictions=item.predictions)
            for item in items if item.status=='completed'],dataset.labels,
            run.config['confidence'],threshold,merge_duplicates=merge_duplicates)
        return metrics,rows
    return await asyncio.to_thread(calculate)


async def cancel_run(db, run):
    if run.status in ('pending', 'running'):
        run.status = 'canceled'
        await db.execute(update(ValidationItem).where(ValidationItem.run_id == run.id,
            ValidationItem.status.in_(('pending', 'running'))).values(status='canceled',
                attempt=ValidationItem.attempt + 1, error='管理员取消验证任务'))
        await recount(db, run)
        run.metrics = await calculate_metrics(db, run)
        await db.commit()
        await db.refresh(run)
    if run.model_snapshot.get('source')=='checkpoint' and run.model_snapshot['execution']['active']:
        from services.checkpoint_validation import stop
        try:await stop(db,run)
        except HTTPException:run.error='取消已请求，远端停止尚未确认，资源占用保留；请再次取消核对'
        await db.commit();await db.refresh(run)
    return run


async def retry_run(db, run):
    if run.status in ('pending', 'running'): raise HTTPException(409, '任务仍在运行，请等待或先取消')
    await validate_context(db, run)
    if run.model_snapshot.get('source')=='checkpoint':
        from services.checkpoint_validation import remote
        from services.gpu_reservations import assert_free
        if run.model_snapshot['execution']['active']:raise HTTPException(409,'先取消并确认旧验证容器已停止后再重试')
        metadata=await remote(db,run,'probe')
        if any(metadata[key]!=run.model_snapshot['checkpoint'][key] for key in ('weight_sha256','image_digest')):raise HTTPException(409,'权重或镜像已变化，请新建验证任务')
        await assert_free(db,[run.model_snapshot['checkpoint']['device']],run.id)
        run.model_snapshot={**run.model_snapshot,'execution':{'id':str(uuid4()),'active':True,'prepared':False}}
    rows = (await db.scalars(select(ValidationItem).where(ValidationItem.run_id == run.id,
        ValidationItem.status.in_(('failed', 'canceled'))).with_for_update())).all()
    if not rows: raise HTTPException(400, '没有失败或取消的图片可重试')
    for item in rows:
        item.status = 'pending'
        item.attempt += 1
        item.error = None
        item.predictions = []
        item.evaluation = {}
    run.status = 'pending'
    await recount(db, run)
    await db.commit()
    return run


async def remove_run(db, run):
    run=await cancel_run(db,run)
    if run.model_snapshot.get('source')=='checkpoint' and run.model_snapshot['execution']['active']:
        raise HTTPException(409,'远端验证停止尚未确认，请重试停止后再删除')
    await db.refresh(run,with_for_update=True)
    if run.status in ('pending','running') or (run.model_snapshot.get('source')=='checkpoint' and run.model_snapshot['execution']['active']):
        raise HTTPException(409,'任务已重新运行，请先确认停止')
    await db.delete(run)
    await db.commit()


async def process_one(sessions):
    async with sessions() as db:
        run = await db.scalar(select(ValidationRun).where(ValidationRun.status.in_(('pending', 'running')),
            exists().where(ValidationItem.run_id == ValidationRun.id, ValidationItem.status == 'pending'))
            .order_by(ValidationRun.created_at, ValidationRun.id).with_for_update(skip_locked=True).limit(1))
        if run is None: return False
        item = await db.scalar(select(ValidationItem).where(ValidationItem.run_id == run.id, ValidationItem.status == 'pending')
            .order_by(ValidationItem.created_at, ValidationItem.id).with_for_update().limit(1))
        item.status = 'running'
        item.attempt += 1
        run.status = 'running'
        run_id, item_id, attempt = run.id, item.id, item.attempt
        try:
            await validate_context(db, run)
            dataset = await set_scope(db, run.set_id, run.tenant_id)
            sample = next(sample for sample in dataset.samples if sample['asset_id'] == str(item.asset_id))
            labels, truth = deepcopy(dataset.labels), deepcopy(item.truth)
            policy = ValidationConfig.model_validate(run.config)
            config = None if run.model_snapshot.get('source')=='checkpoint' else ModelServiceConfig.model_validate(run.model_snapshot['config']).model_copy(update=dict(
                confidence=policy.inference_confidence, iou=policy.nms_iou, labels=policy.labels, label_mapping=policy.label_mapping))
        except Exception:
            item.status = 'failed'
            item.error = '冻结上下文或模型/管理员权限已变化，请检查后重试'
            if run.model_snapshot.get('source')=='checkpoint':
                from services.checkpoint_validation import stop
                await db.execute(update(ValidationItem).where(ValidationItem.run_id==run.id,ValidationItem.status=='pending').values(status='failed',error=item.error))
                try:await stop(db,run)
                except HTTPException:run.error='远端停止待确认，资源占用保留，请取消核对'
            await recount(db, run)
            if run.model_snapshot.get('source')=='checkpoint' and run.model_snapshot['execution']['active']:
                run.error='上下文已变化，远端停止待确认；资源占用保留，请取消核对'
            if run.status in TERMINAL: run.metrics = await calculate_metrics(db, run)
            await db.commit()
            return True
        await db.commit()
    predictions, evaluation, failure, batch_failure = [], {}, None, False
    try:
        content, _ = await read_sample(sample)
        # Recheck after storage I/O so cancellation cannot start a late inference.
        async with sessions() as db:
            active = await db.scalar(select(ValidationItem.id).join(ValidationRun).where(ValidationItem.id == item_id,
                ValidationItem.status == 'running', ValidationItem.attempt == attempt,
                ValidationRun.status.in_(('pending', 'running'))))
            if active is None: return True
            await validate_context(db, run)
        if run.model_snapshot.get('source')=='checkpoint':
            from services.checkpoint_validation import infer as checkpoint_infer
            result=await checkpoint_infer(sessions,run,sample)
        elif multimodal.task_type(run)!='detection':result=await multimodal.service_infer(config,content,sample,policy)
        else:result = await infer(config, content, sample['width'], sample['height'])
        if multimodal.task_type(run)!='detection':
            evaluation=multimodal.clean_result(result,multimodal.task_type(run))
            if set(sample.get('query_texts',{}))-set(evaluation.get('queries',{})):raise ValueError('缺少文本查询向量')
            result={'boxes':[]}
        predictions = result['boxes']
        unscored = result.get('score_type') == 'uncalibrated'
        if unscored and not run.model_snapshot.get('preview'):
            raise HTTPException(400, '模型未提供可用检测置信度，不能计算依赖置信度的验证指标，请使用模型效果预览')
        if run.model_snapshot.get('preview'):labels=list(dict.fromkeys(box['label'] for box in predictions))
        clean_boxes({'boxes': predictions}, sample['width'], sample['height'], labels)
        for box in predictions:
            if unscored:continue
            score = box.get('score')
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError('invalid score')
        keys = ('label', 'x', 'y', 'width', 'height') + (() if unscored else ('score',))
        predictions = [{key: box[key] for key in keys} for box in predictions]
        if not run.model_snapshot.get('preview') and multimodal.task_type(run)=='detection':
            evaluation = await asyncio.to_thread(evaluate_image, truth, predictions, labels, policy.confidence, policy.match_iou)
        await read_sample(sample)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        batch_failure=run.model_snapshot.get('source')=='checkpoint' and isinstance(error,HTTPException) and error.status_code in (503,504)
        # Never expose exception text from storage, configuration or remote services.
        failure = ('模型返回无效类别、坐标或置信度；请检查映射后重试' if isinstance(error, (ValueError, KeyError, TypeError))
            else '冻结原图读取校验或模型推理失败；请检查原图、类别映射及模型后重试')
        if multimodal.task_type(run)!='detection':failure='图片读取或描述/向量生成失败；请检查原图、模型用途、服务及容器日志后重试'
        if multimodal.task_type(run)!='detection' and isinstance(error,HTTPException) and isinstance(error.detail,str) and error.detail.startswith(('模型服务','无法连接模型服务','配置的模型鉴权')):
            failure=error.detail[:500]
        if run.model_snapshot.get('source')=='checkpoint' and isinstance(error,HTTPException) and isinstance(error.detail,str) and error.detail.startswith('验证图片包上传失败'):
            failure=error.detail
        if run.model_snapshot.get('preview'):
            failure=error.detail if isinstance(error,HTTPException) and isinstance(error.detail,str) else '图片读取或检测推理失败，请检查图片、模型接口或训练权重后重试'
        logger.warning('Validation item failed: %s', type(error).__name__)
    async with sessions() as db:
        current_run = await db.scalar(select(ValidationRun).where(ValidationRun.id == run_id).with_for_update())
        current = await db.scalar(select(ValidationItem).where(ValidationItem.id == item_id).with_for_update())
        if (current_run is None or current is None or current_run.status == 'canceled' or
            current.status != 'running' or current.attempt != attempt): return True
        try:
            await validate_context(db, current_run, lock=True)
        except HTTPException:
            failure = '模型配置或任务创建人的管理员权限已变化，结果未采纳'
        if failure:
            current.status = 'failed'
            current.error = failure
            if current_run.model_snapshot.get('source')=='checkpoint' and (batch_failure or not current_run.model_snapshot['execution']['prepared']):
                await db.execute(update(ValidationItem).where(ValidationItem.run_id==run_id,ValidationItem.status=='pending').values(status='failed',error=failure))
        else:
            current.status = 'completed'
            current.predictions = predictions
            current.evaluation = evaluation
            current.error = None
        await recount(db, current_run)
        if batch_failure and failure:current_run.error=failure
        if current_run.status in TERMINAL: current_run.metrics = await calculate_metrics(db, current_run)
        if current_run.status in TERMINAL and current_run.model_snapshot.get('source')=='checkpoint':
            from services.checkpoint_validation import stop
            try:await stop(db,current_run)
            except HTTPException:current_run.error='结果已记录，远端容器停止待确认；资源占用保留，请取消核对'
        await db.commit()
    return True


async def run_worker(sessions):
    while True:
        try:
            if not await process_one(sessions): await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.error('Validation worker failed: %s', type(error).__name__)
            await asyncio.sleep(1)


async def start_worker():
    global _worker
    if _worker is not None and not _worker.done(): return
    from database import AsyncSessionLocal
    # shortcut: single backend recovery; use leases before deploying multiple consumers.
    async with AsyncSessionLocal() as db:
        await db.execute(update(ValidationItem).where(ValidationItem.status == 'running').values(
            status='failed', attempt=ValidationItem.attempt + 1, error='服务重启中断验证，请手动重试'))
        checkpoint_runs=(await db.scalars(select(ValidationRun).where(ValidationRun.model_snapshot['source'].as_string()=='checkpoint',
            ValidationRun.model_snapshot['execution']['active'].as_boolean().is_(True)))).all()
        for run in checkpoint_runs:
            from services.checkpoint_validation import stop
            await db.execute(update(ValidationItem).where(ValidationItem.run_id==run.id,ValidationItem.status.in_(('pending','running')))
                .values(status='failed',attempt=ValidationItem.attempt+1,error='服务重启中断权重验证，请确认停止后手动重试'))
            try:await stop(db,run)
            except HTTPException:run.error='服务重启，远端验证停止待确认；资源占用保留，请取消核对'
            await recount(db,run)
            if run.model_snapshot['execution']['active']:run.error='服务重启，远端验证停止待确认；资源占用保留，请取消核对'
        runs = (await db.scalars(select(ValidationRun).where(ValidationRun.status.in_(('pending', 'running'))))).all()
        for run in runs:
            await recount(db, run)
            if run.status in TERMINAL: run.metrics = await calculate_metrics(db, run)
        await db.commit()
    _worker = asyncio.create_task(run_worker(AsyncSessionLocal))


async def stop_worker():
    global _worker
    if _worker is not None:
        _worker.cancel()
        try: await _worker
        except asyncio.CancelledError: pass
        _worker = None
