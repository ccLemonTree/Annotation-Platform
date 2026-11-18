import asyncio
import tarfile
from io import BytesIO
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Annotated
from uuid import UUID
from zipfile import BadZipFile

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.asset_routes import IMAGE_TYPES, scoped_project, validate_image_upload
from api.deps import get_current_superuser
from config.settings import settings
from database import get_db_session
from models import Asset, Project, TaskType, User
from models.project_work import ProjectWorkItem
from models.training_run import TrainingRun
from models.vector_test import VectorTestIndex, VectorTestItem, VectorTestModel, VectorTestSample, VectorTestSampleItem
from services import training_runs, vector_test as service, vector_test_data as samples
from services.gpu_reservations import assert_free
from services.image_uploads import ARCHIVES, import_items, next_item
from services.project_work import read_content as read_asset
from services.training_logs import normalize_log

router = APIRouter()
Db = Annotated[AsyncSession, Depends(get_db_session)]
Admin = Annotated[User, Depends(get_current_superuser)]


class DatasetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class Deploy(BaseModel):
    run_id: UUID
    server_id: UUID
    gpu: str = Field(pattern=r'^GPU-[0-9a-fA-F-]{36}$')
    port: int = Field(ge=1024, le=65535)


class Detect(BaseModel):
    model_id: UUID
    labels: list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]] = Field(default_factory=list, max_length=100)
    confidence: float | None = Field(default=None, ge=0, le=1)


async def model_scope(db, user, identifier, lock=False):
    query = select(VectorTestModel).where(VectorTestModel.id == identifier, VectorTestModel.tenant_id == user.tenant_id)
    model = await db.scalar(query.with_for_update() if lock else query)
    if model is None:
        raise HTTPException(404, '测试模型不存在')
    return model


def summary(model):
    return dict(id=model.id, run_id=model.run_id, name=model.name, server=model.server['name'],
                gpu=model.gpu, port=model.port, state=model.state, error=model.error)


@router.get('/datasets')
async def datasets(db: Db, user: Admin):
    rows = await db.scalars(select(Project).where(Project.tenant_id == user.tenant_id,
        Project.task_type == TaskType.VECTOR,
        Project.label_schema['vector_test'].as_boolean().is_(True)).order_by(Project.created_at.desc()))
    return [dict(id=p.id, name=p.project_name) for p in rows]


@router.post('/datasets', status_code=201)
async def new_dataset(request: DatasetCreate, db: Db, user: Admin):
    name = request.name.strip()
    if not name:
        raise HTTPException(400, '请输入测试数据集名称')
    if await db.scalar(select(Project.id).where(Project.tenant_id == user.tenant_id, Project.project_name == name).limit(1)):
        raise HTTPException(409, '数据集或项目名称已存在')
    project = Project(tenant_id=user.tenant_id, task_type=TaskType.VECTOR, project_name=name,
                      label_schema={'vector_test': True, 'annotation_scope': 'all'})
    db.add(project)
    await db.commit()
    return dict(id=project.id, name=project.project_name)


@router.get('/sources')
async def sources(db: Db, user: Admin):
    rows = await db.scalars(select(TrainingRun).where(TrainingRun.tenant_id == user.tenant_id,
        TrainingRun.kind == 'model_merge', TrainingRun.status == 'completed').order_by(TrainingRun.created_at.desc()))
    return [dict(id=r.id, name=r.name, server_id=r.server['id']) for r in rows if service.options(r)]


@router.get('/models')
async def models(db: Db, user: Admin):
    rows = await db.scalars(select(VectorTestModel).where(VectorTestModel.tenant_id == user.tenant_id)
                           .order_by(VectorTestModel.name, VectorTestModel.id))
    return [summary(model) for model in rows]


@router.post('/models', status_code=202)
async def deploy(request: Deploy, db: Db, user: Admin):
    run = await training_runs.scoped(db, request.run_id, user.tenant_id)
    if not service.options(run):
        raise HTTPException(400, '请选择已完成的 WeMM LoRA 合并任务')
    server_id = str(request.server_id)
    server = await training_runs.server_snapshot(db, server_id, user.tenant_id)
    await assert_free(db, [request.gpu])
    collision = await db.scalar(select(VectorTestModel.id).where(
        VectorTestModel.server['connection_key'].as_string() == server['connection_key'],
        VectorTestModel.gpu.is_not(None), VectorTestModel.port == request.port).limit(1))
    if collision:
        raise HTTPException(409, '所选服务器端口已有测试服务占用')
    model = VectorTestModel(tenant_id=user.tenant_id, run_id=run.id, name=run.name, server=server,
        source=dict(server=run.server, root=str(PurePosixPath(run.output_dir)/run.config['output_subdir']),
                    runtime='vllm', artifact_path='merged'),
        gpu=request.gpu, port=request.port, state='queued')
    db.add(model)
    await db.commit()
    return summary(model)


@router.post('/models/{identifier}/stop', status_code=202)
async def stop(identifier: UUID, db: Db, user: Admin):
    model = await model_scope(db, user, identifier, lock=True)
    if model.state not in ('queued', 'pulling', 'checking', 'ready', 'failed'):
        raise HTTPException(409, '等待当前部署或停止操作完成后再操作')
    model.state = 'stopped' if model.state == 'queued' else 'stopping'
    model.gpu = None
    model.error = None
    await db.commit()
    return summary(model)


@router.get('/models/{identifier}/logs')
async def logs(identifier: UUID, db: Db, user: Admin):
    model = await model_scope(db, user, identifier)
    result = await service.remote(db, model, 'logs', token=service.token(model))
    return dict(logs=normalize_log(result['logs']))


@router.get('/datasets/{identifier}/images')
async def list_samples(identifier: UUID, db: Db, user: Admin, targets: bool = False,
                       page: Annotated[int, Field(ge=1)] = 1):
    await scoped_project(db, identifier, user.tenant_id)
    detection = await samples.detection_config(db, identifier)
    conditions = (VectorTestSample.project_id == identifier, samples.sample_scope(detection, targets))
    total = await db.scalar(select(func.count()).select_from(VectorTestSample).where(*conditions))
    rows = (await db.scalars(select(VectorTestSample).where(*conditions)
        .order_by(VectorTestSample.created_at.desc(), VectorTestSample.id).offset((page-1)*12).limit(12))).all()
    return dict(total=total, items=[dict(id=row.id, original_name=row.original_name, source_type=row.source_type,
        device_name=row.device_name, parent_id=row.parent_id) for row in rows])


@router.get('/datasets/{identifier}/images/{sample_id}/content')
async def sample_content(identifier: UUID, sample_id: UUID, db: Db, user: Admin):
    await scoped_project(db, identifier, user.tenant_id)
    row = await db.scalar(select(VectorTestSample).where(VectorTestSample.project_id == identifier,
                                                         VectorTestSample.id == sample_id))
    if not row:
        raise HTTPException(404, '测试图片不存在')
    path = samples.storage().resolve_path(row.blob_path)
    media_type = IMAGE_TYPES[row.format][1]
    await db.rollback()
    if not path.is_file():
        raise HTTPException(404, '测试图片文件不存在')
    return FileResponse(path, media_type=media_type, headers={'Cache-Control':'private, no-store'})


@router.get('/datasets/{identifier}/images/{sample_id}/preview')
async def result_image(identifier: UUID, sample_id: UUID, db: Db, user: Admin, isolated: bool = False):
    await scoped_project(db, identifier, user.tenant_id)
    source = VectorTestSample if isolated else Asset
    read = samples.read_content if isolated else read_asset
    row = await db.scalar(select(source).where(source.project_id == identifier, source.id == sample_id))
    if row is None:
        raise HTTPException(404, '测试图片不存在')
    crop = {} if isolated else (row.device_info or {}).get('crop') or {}
    parent_id = row.parent_id if isolated else crop.get('parent_asset_id')
    parent = None
    if parent_id:
        try:
            parent_id = UUID(str(parent_id))
        except ValueError:
            parent_id = None
        if parent_id:
            parent = await db.scalar(select(source).where(source.project_id == identifier, source.id == parent_id))
    region = crop.get('region') if parent else None
    primary_row = SimpleNamespace(blob_path=(parent or row).blob_path, content_hash=(parent or row).content_hash)
    target_row = SimpleNamespace(blob_path=row.blob_path, content_hash=row.content_hash) if isolated and parent is not None else None
    await db.rollback()
    try:
        original = await read(primary_row)
        target = await read(target_row) if target_row is not None else None
        image = await asyncio.to_thread(samples.result_preview, original, target, region)
    except (OSError, ValueError) as exc:
        raise HTTPException(404, '无法读取测试图片') from exc
    return Response(image, media_type='image/jpeg', headers={'Cache-Control':'private, no-store'})


@router.post('/datasets/{identifier}/images', status_code=201)
async def upload_samples(identifier: UUID, file: UploadFile, db: Db, user: Admin):
    await scoped_project(db, identifier, user.tenant_id)
    filename = file.filename or ''
    result = dict(added=0, duplicate=0, invalid=0)

    async def save(image):
        content, metadata = await validate_image_upload(image)
        values = samples.original_values(identifier, metadata.pop('content_hash'), IMAGE_TYPES[metadata['format']][0],
                                         **metadata)
        await scoped_project(db, identifier, user.tenant_id, for_update=True)
        exists = await db.scalar(select(VectorTestSample.id).where(VectorTestSample.project_id == identifier,
            VectorTestSample.blob_path == values['blob_path']))
        if exists is None:
            await samples.storage().save_file(BytesIO(content), values['blob_path'])
            db.add(VectorTestSample(**values))
        await db.commit()
        result['added' if exists is None else 'duplicate'] += 1

    if not filename.lower().endswith(ARCHIVES):
        await save(file)
        return result
    if (file.size or 0) > settings.MAX_IMAGE_ARCHIVE_UPLOAD_SIZE:
        raise HTTPException(413, '压缩包超过上传大小限制')
    await db.commit()
    entries = import_items(file.file, filename, 0)
    try:
        while entry := await asyncio.to_thread(next_item, entries):
            _, name, content, status, *_ = entry
            if status == 'ignored':
                continue
            if content is None:
                result['invalid'] += 1
                continue
            try:
                await save(UploadFile(BytesIO(content), filename=name))
            except HTTPException as exc:
                if exc.status_code not in (400, 413):
                    raise
                result['invalid'] += 1
    except (BadZipFile, tarfile.TarError, ValueError, EOFError) as exc:
        raise HTTPException(400, f'无法读取测试压缩包：{exc}') from None
    finally:
        entries.close()
    return result


@router.get('/datasets/{identifier}/indexes')
async def indexes(identifier: UUID, db: Db, user: Admin, isolated: bool = False):
    project = await scoped_project(db, identifier, user.tenant_id)
    source, item = (VectorTestSample, VectorTestSampleItem) if isolated else (Asset, VectorTestItem)
    job = None if isolated else await service.detection_job(db, project)
    detection = await samples.detection_config(db, identifier) if isolated else None
    scope = samples.sample_scope(detection) if isolated else service.candidates(db, job)
    count = await db.scalar(select(func.count()).select_from(source).where(source.project_id == identifier, scope))
    model_ids = (await db.scalars(select(VectorTestIndex.model_id).where(VectorTestIndex.project_id == project.id,
                                                                         service.index_mode(isolated)))).all()
    result = []
    for model_id in model_ids:
        rows = (await db.execute(select(item.error, func.count()).join(source,
            service.asset_items(identifier, model_id, source, item)).where(source.project_id == identifier, scope)
            .group_by(item.error))).all()
        complete = sum(n for error, n in rows if error is None)
        failed = sum(n for error, n in rows if error is not None)
        result.append(dict(model_id=model_id, complete=complete, failed=failed,
                           error=next((e for e, _ in rows if e), None)))
    progress = dict(total=count, items=result)
    if isolated:
        progress['originals'] = await db.scalar(select(func.count()).select_from(source).where(
            source.project_id == identifier, source.parent_id.is_(None)))
        if detection:
            progress['detection'] = await samples.progress(db, identifier, detection)
    elif job:
        rows = await db.execute(select(ProjectWorkItem.status, func.count()).where(ProjectWorkItem.job_id == job.id)
                                .group_by(ProjectWorkItem.status))
        progress['detection'] = dict(id=job.id, name=job.model['config']['name'], counts=dict(rows.all()),
                                     labels=job.options['labels'], confidence=job.options['confidence'])
    return progress


@router.post('/datasets/{identifier}/detection', status_code=201)
async def detect(identifier: UUID, request: Detect, db: Db, user: Admin, isolated: bool = False):
    project = await scoped_project(db, identifier, user.tenant_id, for_update=True)
    if isolated:
        return dict(id=(await samples.configure_detection(db, project, request))['id'])
    job = await service.start_detection(db, project, user, request)
    return dict(id=job.id)


@router.put('/datasets/{identifier}/indexes/{model_id}')
async def add_index(identifier: UUID, model_id: UUID, db: Db, user: Admin, isolated: bool = False):
    await scoped_project(db, identifier, user.tenant_id, for_update=True)
    model = await model_scope(db, user, model_id, lock=True)
    if model.state != 'ready':
        raise HTTPException(409, '测试模型未就绪')
    index = await db.get(VectorTestIndex, (identifier, model_id))
    if index is None:
        db.add(VectorTestIndex(project_id=identifier, model_id=model_id, legacy_enabled=not isolated, isolated_enabled=isolated))
    elif isolated:
        index.isolated_enabled = True
    else:
        index.legacy_enabled = True
    await db.commit()
    return dict(enabled=True)


@router.post('/datasets/{identifier}/search')
async def compare(identifier: UUID, db: Db, user: Admin, model_ids: Annotated[list[UUID], Form()],
                  text: Annotated[str, Form(max_length=2000)] = '', image: Annotated[UploadFile | None, File()] = None,
                  top_k: Annotated[int, Form(ge=1, le=30)] = 10, isolated: bool = False):
    project = await scoped_project(db, identifier, user.tenant_id)
    text = text.strip()
    if bool(text) == bool(image) or not 1 <= len(model_ids) <= 4 or len(set(model_ids)) != len(model_ids):
        raise HTTPException(400, '请选择一到四个不同模型，并提供查询文本或图片')
    ready = []
    for model_id in model_ids:
        model = await model_scope(db, user, model_id)
        index = await db.get(VectorTestIndex, (identifier, model_id))
        if model.state != 'ready' or not index or not (index.isolated_enabled if isolated else index.legacy_enabled):
            raise HTTPException(409, '请选择当前数据集已开始构建索引的就绪模型')
        ready.append(model)
    content = (await validate_image_upload(image))[0] if image else None
    async with httpx.AsyncClient(trust_env=False) as client:
        return await service.compare(db, client, project, ready, content, text, top_k, isolated=isolated)
