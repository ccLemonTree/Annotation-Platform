"""Managed WeMM services and persistent, isolated test indexes."""
import asyncio
import hashlib
import hmac
import json
import logging
import shutil
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from time import time
from types import SimpleNamespace
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException
from sqlalchemy import and_, case, or_, select

from config.settings import settings
from database import AsyncSessionLocal
from models import Asset, Project
from models.project_work import ProjectWorkItem, ProjectWorkJob
from models.training_run import TrainingRun
from models.vector_test import VectorTestIndex, VectorTestItem, VectorTestModel, VectorTestSample, VectorTestSampleItem
from services import training_runs, vector_test_data as samples
from services.image_semantics import IMAGE_PROMPT, full_vector, model_snapshot as service_snapshot
from services.milvus import Milvus, space_key
from services.model_deployment_executor import sha
from services.model_inference import image_content
from services.project_workflow import enqueue, output_ids

COLLECTION = 'image_platform_vector_test_v1'
SAMPLE_COLLECTION = 'image_platform_vector_test_samples_v1'
logger = logging.getLogger(__name__)
workers = []
DETECTION_KEY = 'vector_test_detection_job'


async def detection_job(db, project):
    value = (project.label_schema or {}).get(DETECTION_KEY)
    if value is None:
        return None
    try:
        identifier = UUID(str(value))
    except ValueError:
        raise HTTPException(409, '向量测试检测任务不可用，请重新配置检测') from None
    job = await db.get(ProjectWorkJob, identifier)
    if not job or job.project_id != project.id or not job.options.get('vector_test_detection'):
        raise HTTPException(409, '向量测试检测任务不可用，请重新配置检测')
    return job


def candidates(db, job):
    if job is None:
        return Asset.source_type != 'model_crop'
    return and_(Asset.source_type == 'model_crop', Asset.id.in_(output_ids(db, job)))


async def enqueue_detection(db, job):
    if job.options.get('queue_deleted'):
        return
    queued = select(ProjectWorkItem.id).where(ProjectWorkItem.job_id == job.id,
        ProjectWorkItem.asset_id == Asset.id).exists()
    await enqueue(db, job, [Asset.source_type != 'model_crop', ~queued], deduplicate=True)


async def start_detection(db, project, user, request):
    snapshot = await service_snapshot(db, project.tenant_id, request.model_id, 'detection')
    try:
        previous = await detection_job(db, project)
    except HTTPException:
        previous = None
    labels = list(dict.fromkeys(request.labels))
    confidence = snapshot['config']['confidence'] if request.confidence is None else request.confidence
    options = dict(vector_test_detection=True, crop_from_originals=True,
                   labels=labels, confidence=confidence, expansion=0, min_crop_long_edge=0)
    if previous and previous.model == snapshot and previous.options == options:
        return previous
    if previous and await db.scalar(select(ProjectWorkItem.id).where(ProjectWorkItem.job_id == previous.id,
            ProjectWorkItem.status.in_(('pending', 'running'))).limit(1)):
        raise HTTPException(409, '当前检测仍在运行，请完成后再更换检测配置')
    job = ProjectWorkJob(project_id=project.id, user_id=user.id, operation='crop',
                         model=snapshot, options=options)
    db.add(job)
    await db.flush()
    project.label_schema = {**(project.label_schema or {}), DETECTION_KEY: str(job.id)}
    await enqueue_detection(db, job)
    await db.commit()
    return job


async def follow_detection():
    async with AsyncSessionLocal() as db:
        projects = (await db.scalars(select(Project).where(
            Project.label_schema[DETECTION_KEY].as_string().is_not(None)))).all()
        for project in projects:
            try:
                await enqueue_detection(db, await detection_job(db, project))
            except (HTTPException, ValueError, TypeError):
                logger.warning('Invalid vector test detection configuration for project %s', project.id)
        await db.commit()
    return False


def shared_candidates(coverages, excluded_hash=None):
    first, *rest = coverages
    return {key for key, digest in first.items()
            if digest != excluded_hash and all(other.get(key) == digest for other in rest)}


def model_snapshot(model):
    return dict(id=str(model.id), revision=1)


def vector_id(model, project, asset, digest):
    return hashlib.sha256(f'{model.id}:{project}:{asset}:{digest}'.encode()).hexdigest()


def token(model):
    return hmac.new(settings.JWT_SECRET_KEY.encode(), ('vector-test:'+str(model.id)).encode(), 'sha256').hexdigest()


def options(run):
    return (
        run.kind == 'model_merge' and run.status == 'completed'
        and (run.config.get('model_kind') == 'wemm'
             or run.config.get('output', {}).get('model_type') == 'wemm_embedding')
    )


async def connection(db, model, source=False):
    server = model.source['server'] if source else model.server
    conn = await training_runs.connection(db, SimpleNamespace(server=server, tenant_id=model.tenant_id))
    return ('platform_training' if conn else server['name']), conn


async def remote(db, model, action, *, from_source=False, transfer=None, **values):
    name, conn = await connection(db, model, from_source)
    payload = dict(action=action, id=str(model.id), **values)
    return await asyncio.to_thread(training_runs.bridge, name, dict(connection=conn, **(
        transfer if transfer is not None else dict(executor='vector_test', payload=payload))))


async def vectors(db, model, batches):
    requests = [[dict(role='user', content=content)] for content in batches]
    parameters = dict(port=model.port, token=token(model))
    if not any(part['type'] == 'image_url' for batch in batches for part in batch):
        return await remote(db, model, 'embed', **parameters, requests=requests)
    # The SSH tunnel tool omits jump-host routing. Send images over the
    # same jump-aware SSH and SFTP operations used for training artifacts.
    request_id = str(uuid4())
    data = json.dumps(requests, ensure_ascii=False).encode()
    if len(data) > 16*1024**2:
        raise HTTPException(413, '本批查询图片数据超过16 MiB')
    with tempfile.TemporaryDirectory(prefix='vector-query-') as temp:
        local = Path(temp)/'input.json'
        local.write_bytes(data)
        target = await remote(db, model, 'prepare_query', request_id=request_id)
        try:
            await remote(db, model, 'transfer', transfer=dict(upload=dict(
                localPath=str(local), remotePath=target['path'])))
            return await remote(db, model, 'embed', **parameters, request_id=request_id,
                                sha256=hashlib.sha256(data).hexdigest())
        except Exception:
            try:
                await remote(db, model, 'cleanup_query', request_id=request_id)
            except HTTPException:
                logger.warning('Could not clear vector query staging %s', request_id)
            raise


async def stage_model(db, model):
    source = {key: model.source[key] for key in ('root', 'artifact_path', 'runtime')}
    path = str(PurePosixPath(source['root']) / source['artifact_path'])
    if model.source['server']['id'] == model.server['id']:
        return dict(model_path=path)
    receipt = await remote(db, model, 'pack', from_source=True, source=source)
    folder = training_runs.ROOT/'.backups/vector-test-transfer'
    folder.mkdir(parents=True, exist_ok=True)
    size = receipt['bytes']
    if not 0 < size <= 513*1024**3 or shutil.disk_usage(folder).free < size+64*1024**2:
        raise HTTPException(409, '平台中转磁盘空间不足或模型包大小无效')
    with tempfile.TemporaryDirectory(prefix='vector-test-', dir=folder) as temp:
        bundle = Path(temp)/'bundle.zip'
        await remote(db, model, 'transfer', from_source=True, transfer=dict(download=dict(
            remotePath=receipt['path'], localPath=str(bundle), bytes=size)))
        if bundle.stat().st_size != size or await asyncio.to_thread(sha, bundle) != receipt['sha256']:
            raise HTTPException(409, '测试模型中转校验失败')
        target = await remote(db, model, 'prepare')
        await remote(db, model, 'transfer', transfer=dict(upload=dict(localPath=str(bundle), remotePath=target['path'])))
        staged = await remote(db, model, 'receive', sha256=receipt['sha256'])
    return dict(model_path=staged['path'], fingerprint=staged['fingerprint'])


async def operate():
    async with AsyncSessionLocal() as db:
        priority = case({'stopping': 0, 'queued': 1, 'checking': 2, 'pulling': 3}, value=VectorTestModel.state)
        model = await db.scalar(select(VectorTestModel).where(VectorTestModel.state.in_(
            ('queued', 'pulling', 'checking', 'stopping'))).order_by(priority, VectorTestModel.id).limit(1))
        if not model:
            return False
        stopping = model.state == 'stopping'
        try:
            if stopping:
                await remote(db, model, 'stop')
                model.state, model.gpu = 'stopped', None
            else:
                if model.state in ('queued', 'pulling'):
                    image = await remote(db, model, 'image')
                    if not image['ready']:
                        if model.state != 'pulling':
                            model.state = 'pulling'
                            await db.commit()
                        return False
                    stage = await stage_model(db, model)
                    await remote(db, model, 'start', **stage, gpu=model.gpu, port=model.port, token=token(model))
                    model.state = 'checking'
                    model.source = {**model.source, 'ready_deadline': int(time())+900}
                    await db.commit()
                    return True
                deadline = model.source.get('ready_deadline')
                if deadline is None:
                    deadline = int(time())+900
                    model.source = {**model.source, 'ready_deadline': deadline}
                    await db.commit()
                try:
                    status = await remote(db, model, 'status')
                except HTTPException:
                    status = {'state': 'unreachable'}
                if status.get('state') not in ('running', 'restarting', 'unreachable'):
                    raise HTTPException(409, 'vLLM 已退出，请查看日志')
                try:
                    result = await vectors(db, model, [[{'type': 'text', 'text': 'service readiness'}]])
                    full_vector(result[0].get('vector'))
                except HTTPException:
                    if time() < deadline:
                        return False
                    raise HTTPException(504, 'vLLM 就绪核验超时，请查看日志')
                model.state = 'ready'
            model.error = None
        except Exception as exc:
            logger.warning('Vector test %s: %s', model.id, type(exc).__name__)
            model.state = 'failed'
            model.error = str(exc.detail) if isinstance(exc, HTTPException) else '测试服务操作失败，请查看日志或停止后重试'
        await db.commit()
        return True


def asset_items(project_id, model_id, asset=Asset, item=VectorTestItem):
    return and_(item.project_id == project_id, item.model_id == model_id,
                item.asset_id == asset.id, item.content_hash == asset.content_hash)


def index_mode(isolated):
    return VectorTestIndex.isolated_enabled if isolated else VectorTestIndex.legacy_enabled


async def build_index(client, model_id, isolated=False):
    from services.project_work import read_content

    source, item_type = (VectorTestSample, VectorTestSampleItem) if isolated else (Asset, VectorTestItem)
    async with AsyncSessionLocal() as db:
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(minutes=10)
        rows = (await db.execute(select(Project, VectorTestModel).select_from(VectorTestIndex)
            .join(VectorTestModel, VectorTestModel.id == VectorTestIndex.model_id)
            .join(Project, Project.id == VectorTestIndex.project_id)
            .where(VectorTestModel.id == model_id, VectorTestModel.state == 'ready',
                   Project.tenant_id == VectorTestModel.tenant_id, index_mode(isolated))
            .order_by(VectorTestIndex.project_id))).all()
        for project, model in rows:
            project_id = project.id
            try:
                scope = (samples.sample_scope(await samples.detection_config(db, project_id)) if isolated
                         else candidates(db, await detection_job(db, project)))
            except HTTPException:
                continue
            items = (await db.execute(select(source, item_type)
                .outerjoin(item_type, asset_items(project_id, model.id, source, item_type))
                .where(source.project_id == project_id, scope, or_(item_type.asset_id.is_(None),
                       and_(item_type.error.is_not(None), item_type.updated_at < cutoff)))
                .order_by(source.id).limit(8))).all()
            if not items:
                continue
            # Keep input reads and inference outside the transaction used to publish results.
            await db.commit()
            contents, results = [], {}
            for asset, _ in items:
                try:
                    data = await (samples.read_content(asset) if isolated else read_content(asset))
                    contents.append((asset.id, await asyncio.to_thread(image_content, data, IMAGE_PROMPT)))
                except (HTTPException, ValueError, OSError):
                    results[asset.id] = dict(error='测试图片读取或解码失败')
            if contents:
                try:
                    values = await vectors(db, model, [content for _, content in contents])
                except HTTPException as exc:
                    values = [dict(error=str(exc.detail))]*len(contents)
                results.update((asset, value) for (asset, _), value in zip(contents, values, strict=True))
            await db.commit()
            for asset, _ in items:
                result = results[asset.id]
                error = result.get('error')
                if not error:
                    try:
                        vector = full_vector(result.get('vector'))
                        await Milvus(client, SAMPLE_COLLECTION if isolated else COLLECTION).upsert(vector_id(model, project_id, asset.id, asset.content_hash),
                            vector, model.tenant_id, project_id, asset.id, model_snapshot(model), asset.content_hash)
                    except HTTPException as exc:
                        error = str(exc.detail)
                # Searches only use committed receipts; never hold an asset lock during Milvus I/O.
                current = await db.scalar(select(source).where(source.id == asset.id, source.project_id == project_id,
                    source.content_hash == asset.content_hash).with_for_update())
                if current is None:
                    await db.commit()
                    continue
                await db.merge(item_type(project_id=project_id, model_id=model.id, asset_id=asset.id,
                    content_hash=asset.content_hash, error=error, updated_at=datetime.now(timezone.utc).replace(tzinfo=None)))
                await db.commit()
            return True
    return False


async def compare(db, client, project, models, content, text, limit, isolated=False):
    coverages = []
    source, item_type = (VectorTestSample, VectorTestSampleItem) if isolated else (Asset, VectorTestItem)
    scope = (samples.sample_scope(await samples.detection_config(db, project.id)) if isolated
             else candidates(db, await detection_job(db, project)))
    for model in models:
        rows = (await db.execute(select(source.id, source.content_hash, source.original_name)
            .join(item_type, asset_items(project.id, model.id, source, item_type)).where(source.project_id == project.id, scope,
                item_type.error.is_(None)))).all()
        coverages.append({asset: (digest, name) for asset, digest, name in rows})
    excluded = hashlib.sha256(content).hexdigest() if content is not None else None
    shared = shared_candidates([{asset: v[0] for asset, v in rows.items()} for rows in coverages], excluded)
    if not shared:
        raise HTTPException(409, '所选模型暂无共同的已索引候选图，请等待索引完成或更换查询图')
    if content is None:
        messages = [{'type': 'text', 'text': text}]
    else:
        try:
            messages = await asyncio.to_thread(image_content, content, IMAGE_PROMPT)
        except (ValueError, OSError):
            raise HTTPException(400, '查询文件无法解码为图片') from None
    await db.commit()
    results = []
    for model, rows in zip(models, coverages, strict=True):
        keys = {vector_id(model, project.id, asset, rows[asset][0]): asset for asset in shared}
        vectors_result = await vectors(db, model, [messages])
        value = vectors_result[0]
        if value.get('error'):
            raise HTTPException(502, f"{model.name}：{value['error']}")
        hits = await Milvus(client, SAMPLE_COLLECTION if isolated else COLLECTION).search(full_vector(value.get('vector')), model.tenant_id,
            project.id, space_key(model_snapshot(model)), list(keys), limit)
        results.append(dict(id=model.id, name=model.name, hits=[dict(asset_id=keys[hit['id']],
            name=rows[keys[hit['id']]][1], score=hit['distance']) for hit in hits if hit.get('id') in keys]))
    return dict(candidates=len(shared), results=results)


async def loop(action):
    while True:
        try:
            if await action():
                continue
        except Exception:
            logger.exception('Vector test worker failed')
        await asyncio.sleep(3)


async def index_loop():
    async with httpx.AsyncClient(trust_env=False) as client:
        slots = asyncio.Semaphore(4)
        active = {}

        async def index(model_id):
            async with slots:
                legacy = await build_index(client, model_id)
                isolated = await build_index(client, model_id, isolated=True)
                return legacy or isolated

        try:
            while True:
                try:
                    async with AsyncSessionLocal() as db:
                        ready = set((await db.scalars(select(VectorTestIndex.model_id)
                            .join(VectorTestModel, VectorTestModel.id == VectorTestIndex.model_id)
                            .where(VectorTestModel.state == 'ready').distinct())).all())
                    retired = [active.pop(model_id) for model_id in active.keys() - ready]
                    for task in retired:
                        task.cancel()
                    for model_id in ready - active.keys():
                        active[model_id] = asyncio.create_task(loop(lambda key=model_id: index(key)))
                    await asyncio.gather(*retired, return_exceptions=True)
                except Exception:
                    logger.exception('Vector test index scheduler failed')
                await asyncio.sleep(3)
        finally:
            for task in active.values():
                task.cancel()
            await asyncio.gather(*active.values(), return_exceptions=True)


async def detection_loop():
    async with httpx.AsyncClient(trust_env=False) as client:
        await loop(lambda: samples.detect_one(client))


async def start_worker():
    workers.extend([asyncio.create_task(loop(operate)), asyncio.create_task(index_loop()),
                    asyncio.create_task(loop(follow_detection)), asyncio.create_task(detection_loop())])


async def stop_worker():
    for task in workers:
        task.cancel()
    await asyncio.gather(*workers, return_exceptions=True)
    workers.clear()
