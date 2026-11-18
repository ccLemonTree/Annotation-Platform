"""
数据导出API路由
支持多种格式的数据导出
"""
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from typing import Optional
from uuid import UUID
import json
import csv
import io
from datetime import datetime, timedelta, timezone
import hashlib
import uuid
import logging
from collections import Counter
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from starlette.background import BackgroundTask
from starlette.concurrency import run_in_threadpool
from models import ExportSnapshot, ExportSnapshotJob, AnnotationEvent, TrainingRun, ValidationSet
from api.asset_routes import scoped_project, scoped_asset
from services.storage import LocalStorage, get_storage
from services.auth import create_access_token, decode_access_token
from services.snapshot_manifests import manifest_summary, read_manifest, write_manifest
from services.detection_export import training_package, export_rows, category_labels, coco_document, preview_splits, preview_simple_split
from services.annotation_scope import eligible_assets
from services.dataset_splits import SplitPolicy, SplitSource, source_metadata
from services.project_workflow import dataset_scope
from services.swift_export import EmbeddingTextSource,TrainingTextSource

from database import get_db_session as get_db
from models.user import User
from models.project import Project
from models.asset import Asset
from models.annotation import Annotation
from api.deps import get_current_user, get_current_superuser, get_tenant_id


router = APIRouter()


@router.get('/projects/{project_id}/summary')
async def export_summary(project_id: UUID, work_job_id:UUID|None=None, image_kind:Literal['all','originals','crops']='all',
                         db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    project = await scoped_project(db, project_id, user.tenant_id)
    rows, _ = await export_rows(db, project, False, lock=False, scope=await dataset_scope(db,project,work_job_id,image_kind))
    def counts(annotations):
        return dict(images=len(annotations), images_by_label=dict(Counter(
            label for ann in annotations for label in {box['label'] for box in ann.annotation_data['boxes']})))
    return dict(labels=category_labels(project, rows, None),
        all=counts([ann for _, ann in rows]),
        approved=counts([ann for _, ann in rows if ann.status_for('detection') == 'approved']))


@router.get("/projects/{project_id}/export")
async def export_project_data(
    project_id: UUID,
    format: str = Query("json", description="导出格式: json/csv/coco"),
    include_annotations: bool = Query(True, description="是否包含标注数据"),
    labels: Optional[str] = Query(None, description="逗号分隔类别名称"),
    approved_only: bool = True,
    package: bool = True,
    dataset_format:Literal['portable','swift_sft','infonce','contrastive','online_contrastive','cosine_similarity']='portable',
    sample_source:TrainingTextSource='descriptions',
    single_attribute:str|None=Query(None,max_length=3000),
    explicit_negatives:bool=False,
    automatic_negative_job_id:UUID|None=None,
    instruction:str=Query('请描述这张图片。',min_length=1,max_length=2000),
    work_job_id:UUID|None=None,
    image_kind:Literal['all','originals','crops']='all',
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
        scope=await dataset_scope(db,project,work_job_id,image_kind)
        stream,manifest=await image_training_package(db,project,approved_only,dataset_format,sample_source,instruction,scope=scope,
            source={'work_job_id':str(work_job_id) if work_job_id else None,'image_kind':image_kind} if automatic_negative_job_id else None,
            explicit_negatives=explicit_negatives,automatic_negative_job_id=automatic_negative_job_id,
            single_attribute=parse_single_attribute(single_attribute))
        db.add(AnnotationEvent(project_id=project.id,user_id=current_user.id,action='export',detail=manifest))
        await db.commit()
        return package_response(stream,f'project_{project.id}_annotations.zip')
    if format in ('yolo', 'coco'):
        if not include_annotations:
            raise HTTPException(400, '训练导出必须包含标注')
        selected = [label.strip() for label in labels.split(',') if label.strip()] if labels else None
        if format == 'coco' and not package:
            rows, _ = await export_rows(db, project, approved_only, lock=False)
            names = category_labels(project, rows, selected)
            document = coco_document(rows, names, {str(a.id): a.original_name for a,_ in rows})
            payload = json.dumps(document, ensure_ascii=False).encode()
            await db.rollback()
            return StreamingResponse(io.BytesIO(payload), media_type='application/json')
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
        resp = export_as_json(project, assets, annotations_dict)
        await db.rollback()
        return resp
    elif format == "csv":
        resp = export_as_csv(project, assets, annotations_dict)
        await db.rollback()
        return resp
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
    await db.rollback()
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
    mode: Literal['ratio', 'none', 'train_only', 'val_only'] = 'ratio'
    ratios: dict[str, int] = Field(default_factory=lambda: {'train': 80, 'val': 10, 'test': 10})
    budgets: dict[str, int | None] = Field(default_factory=dict)
    check_duplicates: bool = False
    group_similar: bool = False
    phash_distance: int = Field(default=4, ge=0, le=64)
    color_distance: int = Field(default=20, ge=0, le=255)
    require_source: bool = False

    @model_validator(mode='after')
    def grouped_check(self):
        if self.mode=='none':
            self.group_similar=self.check_duplicates=self.require_source=False
            self.budgets={}
        elif self.group_similar:self.check_duplicates=True
        return self


class DatasetScopeRequest(BaseModel):
    work_job_id:UUID|None=None
    image_kind:Literal['all','originals','crops']='all'

    def source(self):
        return {'work_job_id':str(self.work_job_id) if self.work_job_id else None,'image_kind':self.image_kind}


class SingleAttributeRequest(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    key:str|None=Field(default=None,pattern=r'^[a-z][a-z0-9_]{0,49}$')
    query_value:str|None=Field(default=None,min_length=1,max_length=500)
    reverse:bool=False
    positive_count:int|None=Field(default=None,ge=1,le=10000)
    semantics:Literal['forward','reverse','both']='forward'
    sample_limit:int|None=Field(default=None,ge=1,le=1000000)
    negative_count:int=Field(default=3,ge=0,le=20)


def parse_single_attribute(value):
    if value is not None:
        try:return SingleAttributeRequest.model_validate_json(value).model_dump()
        except ValueError:raise HTTPException(422,'单属性参数无效') from None


class SnapshotRequest(DatasetScopeRequest):
    format: Literal['yolo', 'coco'] = 'yolo'
    labels: list[str] | None = None
    approved_only: bool = True
    splits: dict[str, Literal['train', 'val', 'test']] | None = None
    policy: SplitPolicy | None = None
    selection: SimpleSelection | None = None
    preview_token:str|None=Field(default=None,min_length=64,max_length=64)
    version_label:str=Field(default='',max_length=100)


class SplitPreviewRequest(DatasetScopeRequest):
    model_config = ConfigDict(extra='forbid')
    labels: list[str] | None = None
    approved_only: bool = True
    splits: dict[str, Literal['train', 'val', 'test']] | None = None
    suggest: bool = False
    policy: SplitPolicy = Field(default_factory=SplitPolicy)
    selection: SimpleSelection | None = None


class AnnotationSnapshotRequest(DatasetScopeRequest):
    model_config = ConfigDict(extra='forbid')
    dataset_format: Literal['swift_sft','infonce','contrastive','online_contrastive','cosine_similarity']
    sample_source: TrainingTextSource = 'descriptions'
    approved_only: bool = True
    explicit_negatives:bool=False
    automatic_negative_job_id:UUID|None=None
    single_attribute:SingleAttributeRequest|None=None
    version_label:str=Field(default='',max_length=100)
    instruction: str = Field(default='请描述这张图片。',min_length=1,max_length=2000)
    preview_token:str|None=Field(default=None,min_length=64,max_length=64)


@router.post('/projects/{project_id}/annotation-snapshots/preview')
async def preview_annotation_snapshot(project_id:UUID,request:AnnotationSnapshotRequest,db:AsyncSession=Depends(get_db),
                                      user:User=Depends(get_current_user)):
    from services.annotation_export import training_package as image_training_package
    project=await scoped_project(db,project_id,user.tenant_id)
    scope=await dataset_scope(db,project,request.work_job_id,request.image_kind)
    return await image_training_package(db,project,request.approved_only,request.dataset_format,request.sample_source,request.instruction,
        scope=scope,source=request.source(),preview=True,explicit_negatives=request.explicit_negatives,
        automatic_negative_job_id=request.automatic_negative_job_id,
        single_attribute=request.single_attribute.model_dump() if request.single_attribute else None)


@router.post('/projects/{project_id}/annotation-snapshots',status_code=201)
async def freeze_annotation_export(project_id:UUID,request:AnnotationSnapshotRequest,db:AsyncSession=Depends(get_db),
                                   user:User=Depends(get_current_user)):
    return await freeze_annotation_package(db,user,project_id,request)


async def freeze_annotation_package(db,user,project_id,request,*,progress=None,job=None):
    from services.annotation_export import training_package as image_training_package
    project=await scoped_project(db,project_id,user.tenant_id)
    scope=await dataset_scope(db,project,request.work_job_id,request.image_kind)
    stream,manifest=await image_training_package(db,project,request.approved_only,request.dataset_format,request.sample_source,request.instruction,
        scope=scope,source=request.source(),preview_token=request.preview_token,explicit_negatives=request.explicit_negatives,
        automatic_negative_job_id=request.automatic_negative_job_id,
        single_attribute=request.single_attribute.model_dump() if request.single_attribute else None,progress=progress)
    return await save_snapshot(db,user,project,request.dataset_format,stream,manifest,request.version_label,job=job)


class EmbeddingNegativesRequest(BaseModel):
    text:str=Field(min_length=1)
    version:int=Field(ge=0)
    asset_ids:list[UUID]=Field(max_length=20)


class NegativeRecommendationRequest(DatasetScopeRequest):
    source:EmbeddingTextSource
    approved_only:bool=True


class NegativeRecommendationJobRequest(NegativeRecommendationRequest):
    automatic:bool=False


class NegativeRecommendationSelection(EmbeddingNegativesRequest):
    asset_id:UUID
    asset_ids:list[UUID]=Field(min_length=1,max_length=3)


class NegativeRecommendationConfirmation(NegativeRecommendationRequest):
    items:list[NegativeRecommendationSelection]=Field(min_length=1,max_length=12)


class ExcludedRecommendation(BaseModel):
    asset_id:UUID
    negative_id:UUID


class RecommendationBatchConfirmation(BaseModel):
    excluded:list[ExcludedRecommendation]=Field(default_factory=list,max_length=36)


@router.post('/projects/{project_id}/embedding-negative-recommendations/jobs/status')
async def recommendation_job_status(project_id:UUID,request:NegativeRecommendationJobRequest,
                                    db:AsyncSession=Depends(get_db),user:User=Depends(get_current_superuser)):
    from services.embedding_recommendation_jobs import latest,status
    project=await scoped_project(db,project_id,user.tenant_id)
    return await status(db,await latest(db,project,request),include_sample=True)


@router.post('/projects/{project_id}/embedding-negative-recommendations/jobs',status_code=202)
async def start_recommendation_job(project_id:UUID,request:NegativeRecommendationJobRequest,
                                   db:AsyncSession=Depends(get_db),user:User=Depends(get_current_superuser)):
    from services.embedding_recommendation_jobs import start
    project=await scoped_project(db,project_id,user.tenant_id,for_update=True)
    return await start(db,project,request,user.id)


@router.post('/projects/{project_id}/embedding-negative-recommendations/jobs/{job_id}/confirm',status_code=202)
async def confirm_recommendation_job(project_id:UUID,job_id:UUID,request:RecommendationBatchConfirmation,
                                     db:AsyncSession=Depends(get_db),user:User=Depends(get_current_superuser)):
    from services.embedding_recommendation_jobs import OPERATION,queue_confirmation
    from models import ProjectWorkJob
    project=await scoped_project(db,project_id,user.tenant_id,for_update=True)
    job=await db.scalar(select(ProjectWorkJob).where(ProjectWorkJob.id==job_id,
        ProjectWorkJob.project_id==project.id,ProjectWorkJob.operation==OPERATION))
    if job is None:raise HTTPException(404,'推荐批次不存在')
    return await queue_confirmation(db,job,request,user.id)


@router.post('/projects/{project_id}/embedding-negative-recommendations')
async def recommend_embedding_negatives(project_id:UUID,request:NegativeRecommendationRequest,after:UUID|None=None,
                                        db:AsyncSession=Depends(get_db),user:User=Depends(get_current_superuser)):
    from services.embedding_negative_recommendations import recommend
    project=await scoped_project(db,project_id,user.tenant_id)
    scope=await dataset_scope(db,project,request.work_job_id,request.image_kind)
    return await recommend(db,project,request.source,request.approved_only,scope,after)


@router.post('/projects/{project_id}/embedding-negative-recommendations/confirm')
async def confirm_embedding_negatives(project_id:UUID,request:NegativeRecommendationConfirmation,
                                       db:AsyncSession=Depends(get_db),user:User=Depends(get_current_superuser)):
    from services.embedding_negative_recommendations import confirm
    project=await scoped_project(db,project_id,user.tenant_id)
    scope=await dataset_scope(db,project,request.work_job_id,request.image_kind)
    return await confirm(db,project,request.source,request.approved_only,scope,request.items,user.id)


@router.get('/projects/{project_id}/embedding-negatives/{asset_id}')
async def get_embedding_negatives(project_id:UUID,asset_id:UUID,source:EmbeddingTextSource,
                                  db:AsyncSession=Depends(get_db),user:User=Depends(get_current_superuser)):
    from services.embedding_negatives import configured
    project=await scoped_project(db,project_id,user.tenant_id)
    asset=await scoped_asset(db,asset_id,user.tenant_id)
    if asset.project_id!=project.id:raise HTTPException(404,'图片不属于当前项目')
    _,row,text=await configured(db,project,asset,source)
    hits=row.hits if row and row.query['text']==text and row.query['content_hash']==asset.content_hash else []
    return dict(text=text,version=row.version if row else 0,asset_ids=[hit['asset_id'] for hit in hits])


@router.put('/projects/{project_id}/embedding-negatives/{asset_id}')
async def set_embedding_negatives(project_id:UUID,asset_id:UUID,source:EmbeddingTextSource,request:EmbeddingNegativesRequest,
                                  db:AsyncSession=Depends(get_db),user:User=Depends(get_current_superuser)):
    from services.embedding_negatives import save
    project=await scoped_project(db,project_id,user.tenant_id)
    asset=await scoped_asset(db,asset_id,user.tenant_id,for_update=True)
    if asset.project_id!=project.id:raise HTTPException(404,'图片不属于当前项目')
    return await save(db,project,asset,source,request,user.id)


@router.post('/projects/{project_id}/splits/preview')
async def preview_project_splits(project_id: UUID, request: SplitPreviewRequest, db: AsyncSession = Depends(get_db),
                                 user: User = Depends(get_current_user)):
    project = await scoped_project(db, project_id, user.tenant_id)
    scope=await dataset_scope(db,project,request.work_job_id,request.image_kind)
    if request.selection is not None:
        if request.splits is not None or request.suggest:
            raise HTTPException(400, '简化划分不能同时指定手动划分或自动分组建议')
        return await preview_simple_split(db, project, request.labels, request.approved_only, request.selection.model_dump(),scope=scope)
    if request.suggest and request.splits is not None:
        raise HTTPException(400, '自动建议不能同时提供手动划分')
    return await preview_splits(db, project, request.labels, request.approved_only, request.policy, request.splits, request.suggest,scope=scope)


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
    return await freeze_detection_package(db,user,project_id,request)


async def freeze_detection_package(db,user,project_id,request,*,progress=None,job=None):
    project = await scoped_project(db, project_id, user.tenant_id)
    if request.selection is not None and request.splits:
        raise HTTPException(400, '简化划分不能同时指定手动划分')
    scope=await dataset_scope(db,project,request.work_job_id,request.image_kind)
    stream, manifest = await training_package(db, project, request.format, request.labels, request.approved_only,
        request.splits, request.policy, request.selection.model_dump() if request.selection else None,
        scope=scope,source=request.source(),preview_token=request.preview_token,progress=progress)
    return await save_snapshot(db,user,project,request.format,stream,manifest,request.version_label,job=job)


@router.post('/projects/{project_id}/snapshots/jobs', status_code=202)
async def queue_detection_snapshot(project_id:UUID,request:SnapshotRequest,db:AsyncSession=Depends(get_db),
                                   user:User=Depends(get_current_user)):
    from services.snapshot_jobs import enqueue
    if request.selection is not None and request.splits:
        raise HTTPException(400, '简化划分不能同时指定手动划分')
    return await enqueue(db,user,project_id,'detection',request)


@router.post('/projects/{project_id}/annotation-snapshots/jobs', status_code=202)
async def queue_annotation_snapshot(project_id:UUID,request:AnnotationSnapshotRequest,db:AsyncSession=Depends(get_db),
                                    user:User=Depends(get_current_user)):
    from services.snapshot_jobs import enqueue
    return await enqueue(db,user,project_id,'annotation',request)


@router.get('/projects/{project_id}/snapshot-jobs')
async def list_snapshot_jobs(project_id:UUID,db:AsyncSession=Depends(get_db),user:User=Depends(get_current_user)):
    from services.snapshot_jobs import ACTIVE, summary
    await scoped_project(db,project_id,user.tenant_id)
    job=ExportSnapshotJob
    recent=select(job.id).where(job.project_id==project_id,job.status.not_in(ACTIVE))
    recent=recent.order_by(job.created_at.desc(),job.id.desc()).limit(10)
    rows=await db.scalars(select(job).where(job.project_id==project_id,
        job.status.in_(ACTIVE)|job.id.in_(recent)).order_by(job.created_at.desc(),job.id.desc()))
    return [summary(row) for row in rows]


@router.post('/projects/{project_id}/packages')
async def download_project_package(project_id: UUID, request: SnapshotRequest, db: AsyncSession = Depends(get_db),
                                   user: User = Depends(get_current_user)):
    project = await scoped_project(db, project_id, user.tenant_id)
    if request.selection is not None and request.splits:
        raise HTTPException(400, '简化划分不能同时指定手动划分')
    scope=await dataset_scope(db,project,request.work_job_id,request.image_kind)
    stream, _ = await training_package(db, project, request.format, request.labels, request.approved_only,
        request.splits, request.policy, request.selection.model_dump() if request.selection else None,
        scope=scope,source=request.source(),preview_token=request.preview_token)
    pid = project.id
    await db.rollback()
    return package_response(stream, f'project_{pid}_{request.format}.zip')


async def save_snapshot(db,user,project,format,stream,manifest,version_label='',*,job=None):
    if version_label.strip():manifest={**manifest,'version_label':version_label.strip()}
    snapshot_id = job.id if job else uuid.uuid4()
    key = f'exports/{snapshot_id}.zip'
    manifest_path = f'exports/manifests/{snapshot_id}.json'
    storage = get_storage()
    saved = False
    try:
        task_type = project.task_type
        if job:
            job.status = 'saving'
            job.packed = job.total = len(manifest['assets'])
            await db.commit()
        def checksum():
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            stream.seek(0)
            return digest
        digest = await run_in_threadpool(checksum)
        await storage.save_file(stream, key)
        saved = True
        await write_manifest(manifest, manifest_path)
        project = await scoped_project(db, project.id, user.tenant_id, for_update=True)
        if project.task_type != task_type:
            raise HTTPException(409, '项目任务类型已变化，请刷新后重新导出')
        summary = manifest_summary(manifest)
        snapshot = ExportSnapshot(id=snapshot_id, project_id=project.id, user_id=user.id,
            export_format=format, blob_path=key, content_hash=digest, manifest_path=manifest_path,
            list_metadata=summary)
        db.add(snapshot)
        db.add(AnnotationEvent(project_id=project.id, user_id=user.id, action='freeze_export',
            detail=dict(summary, snapshot_id=str(snapshot_id), manifest_path=manifest_path, format=format)))
        if job:
            job.status = 'completed'
        await db.commit()
        if job:
            return
        return {'id':str(snapshot.id), 'format':snapshot.export_format, 'content_hash':snapshot.content_hash, 'manifest':manifest,
            **({'version_label':manifest['version_label']} if 'version_label' in manifest else {}),
            'created_at':snapshot.created_at.replace(tzinfo=timezone.utc)}
    except Exception:
        if saved:
            for path in (key, manifest_path):
                try:
                    await storage.delete_file(path)
                except Exception:
                    logging.getLogger(__name__).exception('Could not remove unpublished snapshot file %s', path)
        raise
    finally:
        stream.close()

@router.get('/projects/{project_id}/snapshots')
async def list_snapshots(project_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user),
                         summary_only: bool = False, training_only: bool = False, detection_only: bool = False):
    await scoped_project(db, project_id, user.tenant_id)
    scope = [ExportSnapshot.project_id == project_id]
    if detection_only:
        scope.append(ExportSnapshot.export_format.in_(('yolo', 'coco')))
    if training_only or summary_only:
        snapshot=ExportSnapshot
        manifest=snapshot.manifest
        assets=manifest['assets']
        if db.bind.dialect.name=='sqlite':
            fallback=func.json_object('training',manifest['training'],'version_label',manifest['version_label'].as_string(),
                'asset_count',func.coalesce(func.json_array_length(assets),0))
        else:
            fallback=func.jsonb_build_object('training',manifest['training'],'version_label',manifest['version_label'],
                'asset_count',func.coalesce(func.jsonb_array_length(assets.cast(JSONB)),0))
        metadata=func.coalesce(snapshot.list_metadata,fallback,type_=JSONB)
        rows=(await db.execute(select(snapshot.id,snapshot.export_format,snapshot.content_hash,snapshot.created_at,metadata)
            .where(*scope).order_by(snapshot.created_at.desc(),snapshot.id.desc()))).all()
        listed=[]
        for id,format,content_hash,created_at,meta in rows:
            item=dict(id=str(id),format=format,content_hash=content_hash,created_at=created_at.replace(tzinfo=timezone.utc))
            if training_only:
                item['manifest']={'training':meta.get('training')}
                if meta.get('version_label'):
                    item['version_label']=meta['version_label']
            else:
                item.update(version_label=meta.get('version_label'),asset_count=meta['asset_count'],
                    sample_count=(meta.get('training') or {}).get('samples'))
            listed.append(item)
        return listed
    rows = (await db.execute(select(ExportSnapshot).where(*scope)
        .order_by(ExportSnapshot.created_at.desc()))).scalars().all()
    listed = []
    for row in rows:
        manifest = await read_manifest(row.manifest_path, row.manifest)
        listed.append(dict(id=str(row.id), format=row.export_format, content_hash=row.content_hash,
            version_label=manifest.get('version_label'), manifest=manifest,
            created_at=row.created_at.replace(tzinfo=timezone.utc)))
    return listed

async def snapshot_for_download(db, snapshot_id, user):
    row = await db.get(ExportSnapshot, snapshot_id)
    if row is None: raise HTTPException(404, '冻结导出不存在')
    await scoped_project(db, row.project_id, user.tenant_id)
    return row


async def snapshot_archive(row):
    storage = get_storage()
    filename = f'snapshot_{row.id}_{row.export_format}.zip'
    try:
        if isinstance(storage, LocalStorage):
            path = storage.resolve_path(row.blob_path)
            if not path.is_file():
                raise FileNotFoundError
            return FileResponse(path, filename=filename, media_type='application/zip',
                                headers={'Cache-Control': 'private, no-store'})
        stream = await storage.get_file(row.blob_path)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, '冻结导出文件不存在')
    return package_response(stream, filename)


@router.get('/snapshots/{snapshot_id}/download')
async def download_snapshot(snapshot_id: UUID, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)):
    row = await snapshot_for_download(db, snapshot_id, user)
    resp = await snapshot_archive(row)
    await db.rollback()
    return resp


@router.post('/snapshots/{snapshot_id}/browser-download', status_code=204)
async def authorize_snapshot_download(snapshot_id: UUID, request: Request,
                                      db: AsyncSession = Depends(get_db), user: User = Depends(get_current_superuser)):
    row = await snapshot_for_download(db, snapshot_id, user)
    if not await get_storage().file_exists(row.blob_path):
        raise HTTPException(404, '冻结导出文件不存在')
    lifetime = timedelta(hours=2)
    ticket = create_access_token({'snapshot_download': str(row.id), 'download_user_id': user.id,
                                  'tenant_id': user.tenant_id}, lifetime)
    response = Response(status_code=204, headers={'Cache-Control': 'no-store'})
    response.set_cookie('snapshot_download', ticket, max_age=int(lifetime.total_seconds()),
                        path=request.url.path, secure=request.url.scheme == 'https',
                        httponly=True, samesite='strict')
    return response


@router.get('/snapshots/{snapshot_id}/browser-download')
async def browser_snapshot_download(snapshot_id: UUID, request: Request, db: AsyncSession = Depends(get_db)):
    cookie = request.cookies.get('snapshot_download')
    ticket = decode_access_token(cookie) if cookie else None
    if not ticket or ticket.get('snapshot_download') != str(snapshot_id) or not ticket.get('download_user_id'):
        raise HTTPException(401, '下载授权已失效，请从冻结版本列表重新下载')
    user = await db.get(User, ticket['download_user_id'])
    if user is None or not user.is_active or user.role != 'admin' or user.tenant_id != ticket.get('tenant_id'):
        raise HTTPException(403, '当前账号无权下载此冻结版本')
    row = await snapshot_for_download(db, snapshot_id, user)
    resp = await snapshot_archive(row)
    await db.rollback()
    return resp


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
    manifest_path = row.manifest_path
    db.add(AnnotationEvent(project_id=row.project_id, user_id=user.id, action='delete_export',
        detail={'snapshot_id':str(snapshot_id), 'content_hash':row.content_hash}))
    await db.delete(row)
    await db.commit()
    # Remove the reference first so failed file cleanup never breaks a visible snapshot.
    storage = get_storage()
    for path in (key, manifest_path):
        if not path:
            continue
        try:
            await storage.delete_file(path)
        except Exception:
            logging.getLogger(__name__).exception('Deleted snapshot %s; file cleanup failed for %s', snapshot_id, path)
