"""
数据导出API路由
支持多种格式的数据导出
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import Optional
from uuid import UUID
import json
import csv
import io
from datetime import datetime, timezone
import hashlib
import uuid
import logging
from collections import Counter
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from starlette.background import BackgroundTask
from models import ExportSnapshot, AnnotationEvent, TrainingRun, ValidationSet
from api.asset_routes import scoped_project, scoped_asset
from services.storage import get_storage
from services.detection_export import training_package, export_rows, category_labels, coco_document, preview_splits, preview_simple_split
from services.annotation_scope import eligible_assets
from services.dataset_splits import SplitPolicy, SplitSource, source_metadata

from database import get_db_session as get_db
from models.user import User
from models.project import Project
from models.asset import Asset
from models.annotation import Annotation
from api.deps import get_current_user, get_current_superuser, get_tenant_id


router = APIRouter()


@router.get('/projects/{project_id}/summary')
async def export_summary(project_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    project = await scoped_project(db, project_id, user.tenant_id)
    rows, _ = await export_rows(db, project, False, lock=False)
    def counts(annotations):
        return dict(images=len(annotations), images_by_label=dict(Counter(
            label for ann in annotations for label in {box['label'] for box in ann.annotation_data['boxes']})))
    return dict(labels=category_labels(project, rows, None),
        all=counts([ann for _, ann in rows]),
        approved=counts([ann for _, ann in rows if ann.review_status == 'approved']))


@router.get("/projects/{project_id}/export")
async def export_project_data(
    project_id: UUID,
    format: str = Query("json", description="导出格式: json/csv/coco"),
    include_annotations: bool = Query(True, description="是否包含标注数据"),
    labels: Optional[str] = Query(None, description="逗号分隔类别名称"),
    approved_only: bool = True,
    package: bool = True,
    dataset_format:Literal['portable','swift_sft','infonce','contrastive','online_contrastive','cosine_similarity']='portable',
    sample_source:Literal['descriptions','labels','matches']='descriptions',
    instruction:str=Query('请描述这张图片。',min_length=1,max_length=2000),
    tenant_id: int = Depends(get_tenant_id),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    导出项目数据

    支持的格式:
    - **json**: 原始JSON格式
    - **csv**: CSV表格格式
    - **coco**: COCO标注格式
    """
    # 验证项目
    project = await db.get(Project, project_id)
    if not project or project.tenant_id != tenant_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    # 查询资产
    if format == 'annotation':
        from services.annotation_export import training_package as image_training_package
        stream,manifest=await image_training_package(db,project,approved_only,dataset_format,sample_source,instruction)
        db.add(AnnotationEvent(project_id=project.id,user_id=current_user.id,action='export',detail=manifest))
        await db.commit()
        return package_response(stream,f'project_{project.id}_annotations.zip')
    if format in ('yolo', 'coco'):
        if not include_annotations:
            raise HTTPException(400, '训练导出必须包含标注')
        selected = [label.strip() for label in labels.split(',') if label.strip()] if labels else None
        if format == 'coco' and not package:
            rows, _ = await export_rows(db, project, approved_only)
            names = category_labels(project, rows, selected)
            document = coco_document(rows, names, {str(a.id): a.original_name for a,_ in rows})
            return StreamingResponse(io.BytesIO(json.dumps(document,ensure_ascii=False).encode()), media_type='application/json')
        stream, manifest = await training_package(db, project, format, selected, approved_only)
        db.add(AnnotationEvent(project_id=project.id, user_id=current_user.id, action='export', detail=manifest))
        await db.commit()
        return package_response(stream, f'project_{project.id}_{format}.zip')
    stmt = select(Asset).where(Asset.project_id == project_id,eligible_assets(project))
    result = await db.execute(stmt)
    assets = result.scalars().all()

    # 查询标注
    annotations_dict = {}
    if include_annotations:
        stmt = (
            select(Annotation)
            .join(Asset)
            .where(Asset.project_id == project_id, Annotation.is_latest.is_(True),eligible_assets(project))
        )
        result = await db.execute(stmt)
        annotations = result.scalars().all()

        # 按资产ID分组
        for ann in annotations:
            if ann.asset_id not in annotations_dict:
                annotations_dict[ann.asset_id] = []
            annotations_dict[ann.asset_id].append(ann)

    # 根据格式导出
    if format == "json":
        return export_as_json(project, assets, annotations_dict)
    elif format == "csv":
        return export_as_csv(project, assets, annotations_dict)
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"不支持的导出格式: {format}"
        )


def export_as_json(project, assets, annotations_dict):
    """导出为JSON格式"""
    data = {
        "project": {
            "id": str(project.id),
            "name": project.project_name,
            "task_type": project.task_type,
            "description": project.description,
            "created_at": project.created_at.isoformat(),
        },
        "assets": [
            {
                "id": str(asset.id),
                "original_name": asset.original_name,
                "blob_path": asset.blob_path,
                "content_hash": asset.content_hash,
                "file_size": asset.file_size,
                "annotation_status": asset.annotation_status,
                "annotations": [
                    {
                        "id": str(ann.id),
                        "annotation_data": ann.annotation_data,
                        "review_status": ann.review_status,
                        "version": ann.version,
                        "created_at": ann.created_at.isoformat(),
                    }
                    for ann in annotations_dict.get(asset.id, [])
                ]
            }
            for asset in assets
        ],
        "export_time": datetime.utcnow().isoformat(),
    }

    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    return StreamingResponse(
        io.BytesIO(json_str.encode('utf-8')),
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=project_{project.id}_export.json"
        }
    )


def export_as_csv(project, assets, annotations_dict):
    """导出为CSV格式"""
    output = io.StringIO()
    writer = csv.writer(output)

    # 写入表头
    writer.writerow([
        "Asset ID",
        "Original Name",
        "File Size",
        "Annotation Status",
        "Annotations Count",
        "Approved Count",
        "Created At"
    ])

    # 写入数据
    for asset in assets:
        annotations = annotations_dict.get(asset.id, [])
        approved_count = sum(1 for ann in annotations if ann.review_status == "approved")

        writer.writerow([
            str(asset.id),
            asset.original_name,
            asset.file_size,
            asset.annotation_status,
            len(annotations),
            approved_count,
            asset.created_at.isoformat()
        ])

    csv_content = output.getvalue()
    output.close()

    return StreamingResponse(
        io.BytesIO(csv_content.encode('utf-8-sig')),  # UTF-8 BOM for Excel
        media_type="text/csv",
        headers={
            "Content-Disposition": f"attachment; filename=project_{project.id}_export.csv"
        }
    )


@router.get("/annotations/{annotation_id}/export")
async def export_single_annotation(
    annotation_id: UUID,
    format: str = Query("json", description="导出格式: json"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    导出单个标注数据

    - **annotation_id**: 标注ID
    - **format**: 导出格式（目前仅支持json）
    """
    # 查询标注
    annotation = await db.get(Annotation, annotation_id)
    if not annotation:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="标注不存在"
        )

    # 查询关联的资产
    asset = await db.get(Asset, annotation.asset_id)
    await scoped_project(db, asset.project_id, current_user.tenant_id)

    data = {
        "annotation": {
            "id": str(annotation.id),
            "annotation_data": annotation.annotation_data,
            "review_status": annotation.review_status,
            "review_comment": annotation.review_comment,
            "version": annotation.version,
            "created_at": annotation.created_at.isoformat(),
        },
        "asset": {
            "id": str(asset.id),
            "original_name": asset.original_name,
            "blob_path": asset.blob_path,
        } if asset else None,
        "export_time": datetime.utcnow().isoformat(),
    }

    json_str = json.dumps(data, ensure_ascii=False, indent=2)
    return StreamingResponse(
        io.BytesIO(json_str.encode('utf-8')),
        media_type="application/json",
        headers={
            "Content-Disposition": f"attachment; filename=annotation_{annotation_id}.json"
        }
    )

def package_response(stream, filename):
    return StreamingResponse(iter(lambda: stream.read(65536), b''), media_type='application/zip',
        headers={'Content-Disposition': f'attachment; filename={filename}'}, background=BackgroundTask(stream.close))

class SimpleSelection(BaseModel):
    mode: Literal['ratio', 'train_only', 'val_only'] = 'ratio'
    ratios: dict[str, int] = Field(default_factory=lambda: {'train': 80, 'val': 10, 'test': 10})
    budgets: dict[str, int | None] = Field(default_factory=dict)
    check_duplicates: bool = False
    phash_distance: int = Field(default=4, ge=0, le=64)
    color_distance: int = Field(default=20, ge=0, le=255)
    require_source: bool = False


class SnapshotRequest(BaseModel):
    format: Literal['yolo', 'coco'] = 'yolo'
    labels: list[str] | None = None
    approved_only: bool = True
    splits: dict[str, Literal['train', 'val', 'test']] | None = None
    policy: SplitPolicy | None = None
    selection: SimpleSelection | None = None


class SplitPreviewRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    labels: list[str] | None = None
    approved_only: bool = True
    splits: dict[str, Literal['train', 'val', 'test']] | None = None
    suggest: bool = False
    policy: SplitPolicy = Field(default_factory=SplitPolicy)
    selection: SimpleSelection | None = None


class AnnotationSnapshotRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset_format: Literal['swift_sft','infonce','contrastive','online_contrastive','cosine_similarity']
    sample_source: Literal['descriptions','labels','matches'] = 'descriptions'
    approved_only: bool = True
    instruction: str = Field(default='请描述这张图片。',min_length=1,max_length=2000)


@router.post('/projects/{project_id}/annotation-snapshots',status_code=201)
async def freeze_annotation_export(project_id:UUID,request:AnnotationSnapshotRequest,db:AsyncSession=Depends(get_db),
                                   user:User=Depends(get_current_user)):
    from services.annotation_export import training_package as image_training_package
    project=await scoped_project(db,project_id,user.tenant_id)
    stream,manifest=await image_training_package(db,project,request.approved_only,request.dataset_format,request.sample_source,request.instruction)
    return await save_snapshot(db,user,project,request.dataset_format,stream,manifest)


@router.post('/projects/{project_id}/splits/preview')
async def preview_project_splits(project_id: UUID, request: SplitPreviewRequest, db: AsyncSession = Depends(get_db),
                                 user: User = Depends(get_current_user)):
    project = await scoped_project(db, project_id, user.tenant_id)
    if request.selection is not None:
        if request.splits is not None or request.suggest:
            raise HTTPException(400, '简化划分不能同时指定手动划分或自动分组建议')
        return await preview_simple_split(db, project, request.labels, request.approved_only, request.selection.model_dump())
    if request.suggest and request.splits is not None:
        raise HTTPException(400, '自动建议不能同时提供手动划分')
    return await preview_splits(db, project, request.labels, request.approved_only, request.policy, request.splits, request.suggest)


@router.patch('/projects/{project_id}/assets/{asset_id}/split-source')
async def update_split_source(project_id: UUID, asset_id: UUID, request: SplitSource, db: AsyncSession = Depends(get_db),
                              user: User = Depends(get_current_user)):
    await scoped_project(db, project_id, user.tenant_id)
    asset = await scoped_asset(db, asset_id, user.tenant_id, for_update=True)
    if asset.project_id != project_id:
        raise HTTPException(404, '资产不存在')
    before = source_metadata(asset)
    info = dict(asset.device_info or {})
    for key, value in request.model_dump(exclude_unset=True).items():
        if value is None:
            info.pop(key, None)
        else:
            info[key] = value
    asset.device_info = info
    after = source_metadata(asset)
    db.add(AnnotationEvent(project_id=project_id, asset_id=asset.id, user_id=user.id,
                           action='update_split_source', detail={'before': before, 'after': after}))
    await db.commit()
    return dict(asset_id=str(asset.id), source=after)


@router.get('/projects/{project_id}/split-sources')
async def project_split_sources(project_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    await scoped_project(db, project_id, user.tenant_id)
    assets = (await db.execute(select(Asset).where(Asset.project_id == project_id).order_by(Asset.id))).scalars().all()
    return {str(asset.id): source_metadata(asset) for asset in assets}

@router.post('/projects/{project_id}/snapshots', status_code=201)
async def freeze_export(project_id: UUID, request: SnapshotRequest, db: AsyncSession = Depends(get_db),
                        user: User = Depends(get_current_user)):
    project = await scoped_project(db, project_id, user.tenant_id)
    if request.selection is not None and request.splits:
        raise HTTPException(400, '简化划分不能同时指定手动划分')
    stream, manifest = await training_package(db, project, request.format, request.labels, request.approved_only,
        request.splits, request.policy, request.selection.model_dump() if request.selection else None)
    return await save_snapshot(db,user,project,request.format,stream,manifest)


@router.post('/projects/{project_id}/packages')
async def download_project_package(project_id: UUID, request: SnapshotRequest, db: AsyncSession = Depends(get_db),
                                   user: User = Depends(get_current_user)):
    project = await scoped_project(db, project_id, user.tenant_id)
    if request.selection is not None and request.splits:
        raise HTTPException(400, '简化划分不能同时指定手动划分')
    stream, _ = await training_package(db, project, request.format, request.labels, request.approved_only,
        request.splits, request.policy, request.selection.model_dump() if request.selection else None)
    return package_response(stream, f'project_{project.id}_{request.format}.zip')


async def save_snapshot(db,user,project,format,stream,manifest):
    snapshot_id = uuid.uuid4()
    key = f'exports/{snapshot_id}.zip'
    try:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(65536), b''): digest.update(chunk)
        stream.seek(0)
        await get_storage().save_file(stream, key)
        snapshot = ExportSnapshot(id=snapshot_id, project_id=project.id, user_id=user.id,
            export_format=format, blob_path=key, content_hash=digest.hexdigest(), manifest=manifest)
        db.add(snapshot)
        db.add(AnnotationEvent(project_id=project.id, user_id=user.id, action='freeze_export', detail={'snapshot_id':str(snapshot_id), **manifest}))
        await db.commit()
        return {'id':str(snapshot.id), 'format':snapshot.export_format, 'content_hash':snapshot.content_hash, 'manifest':manifest,
            'created_at':snapshot.created_at.replace(tzinfo=timezone.utc)}
    finally:
        stream.close()

@router.get('/projects/{project_id}/snapshots')
async def list_snapshots(project_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user), summary_only: bool = False):
    await scoped_project(db, project_id, user.tenant_id)
    rows = (await db.execute(select(ExportSnapshot).where(ExportSnapshot.project_id == project_id)
        .order_by(ExportSnapshot.created_at.desc()))).scalars().all()
    return [dict(id=str(row.id), format=row.export_format, content_hash=row.content_hash,
        **({'asset_count': len(row.manifest.get('assets', []))} if summary_only else {'manifest': row.manifest}),
        created_at=row.created_at.replace(tzinfo=timezone.utc)) for row in rows]

@router.get('/snapshots/{snapshot_id}/download')
async def download_snapshot(snapshot_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    row = await db.get(ExportSnapshot, snapshot_id)
    if row is None: raise HTTPException(404, '冻结导出不存在')
    await scoped_project(db, row.project_id, user.tenant_id)
    try:
        stream = await get_storage().get_file(row.blob_path)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, '冻结导出文件不存在')
    return package_response(stream, f'snapshot_{row.id}_{row.export_format}.zip')


@router.delete('/snapshots/{snapshot_id}', status_code=204)
async def delete_snapshot(snapshot_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_superuser)):
    row = await db.scalar(select(ExportSnapshot).join(Project).where(
        ExportSnapshot.id == snapshot_id, Project.tenant_id == user.tenant_id).with_for_update(of=ExportSnapshot))
    if row is None:
        raise HTTPException(404, '冻结导出不存在')
    training = await db.scalar(select(TrainingRun.id).where(TrainingRun.tenant_id == user.tenant_id,
        TrainingRun.source['id'].as_string() == str(snapshot_id)).limit(1))
    validation = await db.scalar(select(ValidationSet.id).where(ValidationSet.tenant_id == user.tenant_id,
        ValidationSet.source['snapshot_id'].as_string() == str(snapshot_id)).limit(1))
    if training or validation:
        raise HTTPException(409, '冻结版本已被训练任务或验证集引用，请先删除相关任务或验证集')
    key = row.blob_path
    db.add(AnnotationEvent(project_id=row.project_id, user_id=user.id, action='delete_export',
        detail={'snapshot_id':str(snapshot_id), 'content_hash':row.content_hash}))
    await db.delete(row)
    await db.commit()
    # Remove the reference first so failed file cleanup never breaks a visible snapshot.
    try:
        await get_storage().delete_file(key)
    except Exception:
        logging.getLogger(__name__).exception('Deleted snapshot %s; archive cleanup failed', snapshot_id)
