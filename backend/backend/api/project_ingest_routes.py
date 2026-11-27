"""Image ingestion for external services with project-scoped credentials."""
import asyncio
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import PurePosixPath
from secrets import token_urlsafe
from time import monotonic
from typing import Annotated, Literal
from uuid import UUID, uuid4

import aiofiles
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from api.asset_routes import read_upload, scoped_project
from api.deps import get_current_superuser
from database import get_db_session
from models import Asset, ImageUpload, Project, ProjectUploadKey, User
from config.settings import settings
from services import image_uploads
from services.annotation_json_import import annotation_image_kinds, apply_annotations, import_annotations, read_annotation_records
from services.image_ingest import ResolutionFiltered, get_config
from services.image_library import assign_collection, attach_images, get_collection, register_image
from services.upload_follow import enqueue_uploads, find_streaming_follow_job, lock_follow_job
from services.yolo_import import import_label_files

router = APIRouter()
upload_key = HTTPBearer(scheme_name='ProjectUploadKey', auto_error=False,
    description='由项目管理员创建的项目上传密钥。')


class UploadKeyCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    name: str = Field(min_length=1, max_length=100)
    expires_in_days: int = Field(default=90, ge=1, le=365)


class UploadKeyInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    created_at: datetime
    expires_at: datetime


class IssuedUploadKey(UploadKeyInfo):
    token: str


class ArchiveJobInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    filename: str
    size: int
    image_kind: Literal['originals', 'crops']
    status: str
    result: dict
    error: str | None


class YoloLabelResult(BaseModel):
    filename: str
    status: Literal['imported', 'unchanged', 'conflict', 'missing', 'invalid']
    asset_id: UUID | None = None
    message: str | None = None


class YoloLabelsResult(BaseModel):
    items: list[YoloLabelResult]


class JsonLabelsResult(YoloLabelsResult):
    crop_count: int | None = None


class IngestResult(BaseModel):
    asset_ids: list[UUID]
    added_count: int
    duplicate_count: int
    queued_count: int
    queue_warning: str | None = None
    annotations: JsonLabelsResult | None = None


YoloClasses = Annotated[UploadFile | None, File(description='data.yaml、data.yml 或 classes.txt 类别表')]
YoloLabels = Annotated[list[UploadFile] | None, File(min_length=1, max_length=16, description='1–16 个同名 YOLO TXT 标签文件')]
YoloArchive = Annotated[UploadFile | None, File(description='仅含标签的 ZIP，可内置类别表')]


@router.post('/{project_id}/upload-keys', status_code=201, response_model=IssuedUploadKey)
async def issue_upload_key(project_id: UUID, request: UploadKeyCreate, response: Response,
                           user: User = Depends(get_current_superuser), db: AsyncSession = Depends(get_db_session)):
    await scoped_project(db, project_id, user.tenant_id)
    token = 'nxi_' + token_urlsafe(32)
    key = ProjectUploadKey(project_id=project_id, name=request.name, digest=sha256(token.encode()).hexdigest(),
        expires_at=datetime.now(timezone.utc) + timedelta(days=request.expires_in_days))
    db.add(key)
    await db.commit()
    response.headers['Cache-Control'] = 'no-store'
    return IssuedUploadKey(**UploadKeyInfo.model_validate(key).model_dump(), token=token)


@router.get('/{project_id}/upload-keys', response_model=list[UploadKeyInfo])
async def list_upload_keys(project_id: UUID, user: User = Depends(get_current_superuser),
                           db: AsyncSession = Depends(get_db_session)):
    await scoped_project(db, project_id, user.tenant_id)
    return (await db.scalars(select(ProjectUploadKey).where(ProjectUploadKey.project_id == project_id)
        .order_by(ProjectUploadKey.created_at.desc(), ProjectUploadKey.id))).all()


@router.delete('/{project_id}/upload-keys/{key_id}', status_code=204)
async def revoke_upload_key(project_id: UUID, key_id: UUID, user: User = Depends(get_current_superuser),
                            db: AsyncSession = Depends(get_db_session)):
    await scoped_project(db, project_id, user.tenant_id)
    await db.execute(delete(ProjectUploadKey).where(ProjectUploadKey.project_id == project_id, ProjectUploadKey.id == key_id))
    await db.commit()


async def ingest_project(project_id: UUID, credentials: HTTPAuthorizationCredentials | None = Depends(upload_key),
                         db: AsyncSession = Depends(get_db_session)) -> Project:
    token = credentials.credentials if credentials else ''
    project = None
    if token.startswith('nxi_') and len(token) == 47:
        project = await db.scalar(select(Project).join(ProjectUploadKey, ProjectUploadKey.project_id == Project.id).where(
            Project.id == project_id, ProjectUploadKey.digest == sha256(token.encode()).hexdigest(),
            ProjectUploadKey.expires_at > func.now()))
    if project is None:
        raise HTTPException(401, '项目上传密钥无效或已过期', headers={'WWW-Authenticate': 'Bearer'})
    return project


@router.post('/{project_id}/ingest/yolo-labels', response_model=YoloLabelsResult)
async def ingest_yolo_labels(classes: YoloClasses = None, files: YoloLabels = None, archive: YoloArchive = None,
                            batch_id: Annotated[UUID | None, Form()] = None,
                            project: Project = Depends(ingest_project), db: AsyncSession = Depends(get_db_session)):
    """按文件名为项目中已有图片导入待审核的 YOLO 检测标注。"""
    return await import_label_files(db, project, classes, files, archive, None, batch_id or uuid4())


@router.post('/{project_id}/yolo-labels', response_model=YoloLabelsResult)
async def upload_yolo_labels(project_id: UUID, classes: YoloClasses = None, files: YoloLabels = None, archive: YoloArchive = None,
                            batch_id: Annotated[UUID | None, Form()] = None,
                            user: User = Depends(get_current_superuser), db: AsyncSession = Depends(get_db_session)):
    project = await scoped_project(db, project_id, user.tenant_id)
    return await import_label_files(db, project, classes, files, archive, user.id, batch_id or uuid4())


@router.post('/{project_id}/description-labels')
async def upload_description_labels(project_id: UUID, file: Annotated[UploadFile, File()],
                                    user: User = Depends(get_current_superuser), db: AsyncSession = Depends(get_db_session)):
    from services.description_import import import_descriptions
    project=await scoped_project(db,project_id,user.tenant_id)
    return await import_descriptions(db,project,file,user)


@router.post('/{project_id}/annotation-labels')
async def upload_annotation_labels(project_id: UUID, file: Annotated[UploadFile, File()],
                                   crop_from_boxes: Annotated[bool, Form()] = False,
                                   user: User = Depends(get_current_superuser), db: AsyncSession = Depends(get_db_session)):
    project = await scoped_project(db, project_id, user.tenant_id)
    return await import_annotations(db, project, file, user.id, crop_from_boxes=crop_from_boxes)


@router.post('/{project_id}/ingest/annotation-labels')
async def ingest_annotation_labels(file: Annotated[UploadFile, File()], crop_from_boxes: Annotated[bool, Form()] = False,
                                   project: Project = Depends(ingest_project),
                                   db: AsyncSession = Depends(get_db_session)):
    """按文件名导入待审核的原图检测标注或裁剪图整图标注。"""
    return await import_annotations(db, project, file, None, crop_from_boxes=crop_from_boxes)


@router.post('/{project_id}/ingest/images', status_code=201, response_model=IngestResult, response_model_exclude_unset=True)
async def ingest_images(project_id: UUID,
                        files: Annotated[list[UploadFile] | None, File(min_length=1, max_length=16, description='1–16 个图片文件')] = None,
                        image_kind: Annotated[Literal['originals', 'crops'], Form(description='原图或已裁剪图片')] = 'originals',
                        annotations: Annotated[UploadFile | None, File(description='按图片文件名匹配的 JSON 标注')] = None,
                        crop_from_boxes: Annotated[bool, Form()] = False,
                        labels: YoloLabels = None, classes: YoloClasses = None,
                        project: Project = Depends(ingest_project), db: AsyncSession = Depends(get_db_session)):
    """上传图片、标注或两者一起。同内容图片去重，标签按文件名匹配。"""
    if not (files or annotations or labels) or (classes and not labels) or (annotations and labels):
        raise HTTPException(422, '请上传图片或一种标注格式；类别表须和 YOLO TXT 一起提交')
    if labels and project.task_type != 'detection':
        raise HTTPException(400, 'YOLO导入只支持目标检测项目')
    if crop_from_boxes and not annotations:
        raise HTTPException(422, '按框裁剪需要 JSON 标注')
    records = await read_annotation_records(annotations) if annotations else []
    if files:
        result, deferred_ids = await ingest_image_files(db, project, files, image_kind, annotation_image_kinds(records),
            defer_queue=bool(annotations or labels))
    else:
        result = IngestResult(asset_ids=[], added_count=0, duplicate_count=0, queued_count=0, queue_warning=None)
        deferred_ids = []
    try:
        if annotations:
            result.annotations = JsonLabelsResult.model_validate(await apply_annotations(
                db, project, records, None, crop_from_boxes=crop_from_boxes))
        elif labels:
            result.annotations = JsonLabelsResult.model_validate(
                await import_label_files(db, project, classes, labels, None, None, uuid4()))
    finally:
        if deferred_ids:
            await db.rollback()
            follow = await lock_follow_job(db, project_id)
            fresh = (await db.scalars(select(Asset).where(Asset.project_id==project_id, Asset.id.in_(deferred_ids)))).all()
            result.queued_count, result.queue_warning = await enqueue_uploads(db, follow, fresh)
            await db.commit()
    return result


async def ingest_image_files(db, project, files, image_kind, annotation_kinds, *, defer_queue):
    project_id = project.id
    follow = None if defer_queue else await lock_follow_job(db, project_id)
    group = await get_collection(db, project.tenant_id, project.project_name)
    config, _ = await get_config(db, project.tenant_id, group.id)
    uploads, kinds = [], {}
    try:
        for file in files:
            name = PurePosixPath((file.filename or '').replace('\\','/')).name.lower()
            kind = annotation_kinds.get(name, image_kind)
            values = await read_upload(file, config.min_long_edge if kind == 'originals' else 0)
            uploads.append(values)
            kinds[values['content_hash']] = kind
    except ResolutionFiltered as exc:
        raise HTTPException(422, str(exc)) from exc
    images = {}
    for digest, values in sorted({row['content_hash']: row for row in uploads}.items()):
        images[digest] = await register_image(db, project.tenant_id, values)
    image_ids = [image.id for image in images.values()]
    await assign_collection(db, project.tenant_id, image_ids, group.name)
    assets, created = [], []
    for kind in ('originals','crops'):
        ids = [image.id for digest, image in images.items() if kinds[digest] == kind]
        attached, added = await attach_images(db, project_id, project.tenant_id, ids, image_kind=kind)
        assets.extend(attached)
        created.extend(added)
    crop_entry_locked=follow is not None
    if not defer_queue and follow is None and created:
        follow = await find_streaming_follow_job(db, project_id)
    queued, warning = await enqueue_uploads(db, follow, created,crop_entry_locked=crop_entry_locked)
    by_hash = {asset.content_hash: asset.id for asset in assets}
    deferred_ids = [asset.id for asset in created] if defer_queue else []
    await db.commit()
    return IngestResult(asset_ids=[by_hash[row['content_hash']] for row in uploads], added_count=len(created),
        duplicate_count=len(files) - len(created), queued_count=queued, queue_warning=warning), deferred_ids


@router.post('/{project_id}/ingest/archives', status_code=202, response_model=ArchiveJobInfo,
    openapi_extra={'requestBody': {'required': True, 'content': {
        'application/octet-stream': {'schema': {'type': 'string', 'format': 'binary'}}}}})
async def upload_archive(project_id: UUID, request: Request, response: Response,
                         filename: Annotated[str, Query(min_length=1, max_length=255)],
                         image_kind: Literal['originals', 'crops'] = 'originals',
                         annotation_format: Literal['json', 'yolo'] | None = None,
                         crop_from_boxes: bool = False,
                         project: Project = Depends(ingest_project), db: AsyncSession = Depends(get_db_session)):
    """直接发送 ZIP、TAR、TAR.GZ 或 TGZ 文件内容，提交后台解包任务。"""
    if filename != PurePosixPath(filename.replace('\\', '/')).name or '\x00' in filename or not filename.lower().endswith(image_uploads.ARCHIVES):
        raise HTTPException(422, 'filename 必须是 ZIP 或 TAR 文件名')
    if annotation_format == 'yolo' and project.task_type != 'detection':
        raise HTTPException(400, 'YOLO导入只支持目标检测项目')
    if crop_from_boxes and annotation_format != 'json':
        raise HTTPException(422, '按框裁剪需要 JSON 标注')
    maximum = settings.MAX_IMAGE_ARCHIVE_UPLOAD_SIZE
    length = request.headers.get('content-length', '')
    if length.isdecimal() and int(length) > maximum:
        raise HTTPException(413, '压缩包超过上传大小限制')
    group = await get_collection(db, project.tenant_id, project.project_name)
    config, _ = await get_config(db, project.tenant_id, group.id)
    job_id = uuid4()
    job = ImageUpload(id=job_id, tenant_id=project.tenant_id, collection_id=group.id, project_id=project_id,
        image_kind=image_kind, annotation_format=annotation_format, filename=filename, size=0, chunk_hashes=[], resume_key=job_id.hex,
        min_long_edge=config.min_long_edge if image_kind == 'originals' or annotation_format == 'json' else 0,
        result=dict(imported=0, duplicates=0, filtered=0, invalid=0, ignored=0, errors=[], added_count=0,
                    project_duplicates=0, project_conflicts=0, queued_count=0,
                    **({'crop_from_boxes': True} if crop_from_boxes else {})))
    db.add(job)
    await db.commit()
    try:
        root = image_uploads.staging(job_id)
        await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
        size, last_update = 0, monotonic()
        async with aiofiles.open(root / 'assembled', 'wb') as output:
            async for block in request.stream():
                size += len(block)
                if size > maximum:
                    raise HTTPException(413, '压缩包超过上传大小限制')
                await output.write(block)
                if monotonic() - last_update >= 60:
                    job.size = size
                    await db.commit()
                    last_update = monotonic()
        if not size:
            raise HTTPException(422, '压缩包为空')
        job.size = size
        job.status = 'pending'
        await db.commit()
    except (Exception, asyncio.CancelledError):
        await db.rollback()
        removed = await db.scalar(delete(ImageUpload).where(ImageUpload.id == job_id,
            ImageUpload.status == 'uploading').returning(ImageUpload.id))
        await db.commit()
        if removed:
            await asyncio.to_thread(image_uploads.remove_staging, job_id)
        raise
    response.headers['Location'] = f'/api/projects/{project_id}/ingest/archives/{job_id}'
    return job


@router.get('/{project_id}/ingest/archives/{job_id}', response_model=ArchiveJobInfo)
async def archive_status(project_id: UUID, job_id: UUID, _: Project = Depends(ingest_project),
                         db: AsyncSession = Depends(get_db_session)):
    job = await db.scalar(select(ImageUpload).where(ImageUpload.id == job_id,
        ImageUpload.project_id == project_id, ImageUpload.user_id.is_(None)))
    if job is None:
        raise HTTPException(404, '压缩包上传任务不存在')
    return job
