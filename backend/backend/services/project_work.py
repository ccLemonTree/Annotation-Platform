"""Bounded, durable project inference; immutable inputs and fenced results."""
import asyncio
import hashlib
import json
import logging
import math
from collections import defaultdict
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from time import time
from uuid import UUID
import httpx
from PIL import Image,PngImagePlugin
from fastapi import HTTPException
from sqlalchemy import and_, case, select, update, func, or_
from sqlalchemy.exc import DBAPIError
from models import Asset, Project, Annotation, AnnotationEvent, User, LibraryImage
from models.project_work import ProjectWorkJob, ProjectWorkItem, MatchQuery
from schemas.model_service import ModelServiceConfig
from services.annotations import IMAGE_FIELDS, save_annotation, record_event, register_detection_labels, review
from services.image_library import iter_id_conditions
from models.automation import AutomationItem
from services.automation_review import audit_image, auto_pass
from services.image_semantics import model_snapshot, validate_snapshots, caption, embedding, PREPROCESSING
from services.annotation_scope import crops_only,eligible_assets,require_annotation_asset
from services.model_inference import infer,supports_prompt
from services.milvus import Milvus, space_key
from services.storage import get_storage
from services.automation import review_state
from services.upload_follow import accepts_uploaded_crop_entry,crop_accepts_originals,set_follow_uploads
from services.project_access import image_condition,project_condition
from schemas.object_attributes import AttributeSchema
from services.object_attributes import analyze,validate_choice_schema
from services.preannotation_examples import manual_example,validate_example,read_example_content
from config.settings import settings

_worker=None
logger=logging.getLogger(__name__)
MAX_AUTO_RETRY_ATTEMPTS = 5
INTERRUPTED_ERROR='服务重启中断，请重试'
LEGACY_RETRY_ERRORS=(
    INTERRUPTED_ERROR,
    '模型服务超时，请稍后重试',
    '无法连接模型服务，请检查网络和服务地址',
    '模型未返回有效的属性JSON，请重试或人工标注',
)


def retrying_items():
    retry_at = ProjectWorkItem.result['retry_at'].as_integer()
    legacy_retry = and_(
        ProjectWorkItem.status == 'failed',
        or_(retry_at.is_not(None), ProjectWorkItem.error.in_(LEGACY_RETRY_ERRORS)),
    )
    return and_(
        ProjectWorkItem.asset_id.is_not(None),
        ProjectWorkItem.attempt < MAX_AUTO_RETRY_ATTEMPTS,
        or_(ProjectWorkItem.status == 'retry_wait', legacy_retry),
    )


def crop_region(box,width,height,expansion):
    x,y,w,h=(box[key] for key in ('x','y','width','height'))
    return (max(0,math.floor(x-w*expansion)),max(0,math.floor(y-h*expansion)),
        min(width,math.ceil(x+w*(1+expansion))),min(height,math.ceil(y+h*(1+expansion))))


async def create_job(db,project,request,user_id):
    task_type=project.task_type
    attributes=request.operation in ('detect_attributes','attributes')
    if request.operation=='quality':
        actor=await db.get(User,user_id)
        if not actor or not actor.is_active or actor.tenant_id!=project.tenant_id or actor.role!='admin':
            raise HTTPException(403,'图片质量检查及自动删除需要管理员权限')
    if attributes and project.task_type not in ('caption','vector'):
        raise HTTPException(400,'请在描述或向量项目使用目标属性分析')
    if request.operation=='caption' and project.task_type not in ('caption','vector'):
        raise HTTPException(400,'描述生成适用于描述或向量项目')
    if request.operation=='index' and project.task_type!='vector':
        raise HTTPException(400,'请在向量标注项目建立索引')
    image_ids=sorted(set(request.asset_ids)|({request.example_asset_id} if request.example_asset_id else set()))
    images=[]
    for cond in iter_id_conditions(db, Asset.id, image_ids, chunk_size=500):
        images.extend((await db.scalars(select(Asset).where(Asset.project_id==project.id,cond)
            .order_by(Asset.id).with_for_update())).all())
    if len(images)!=len(image_ids):raise HTTPException(404,'部分项目图片或正确范例不存在')
    kind={'crop':'detection','caption':'caption','index':'embedding','detect_attributes':'detection','attributes':'caption','quality':'caption'}[request.operation]
    snapshot=await model_snapshot(db,project.tenant_id,request.model_id,kind)
    detector_prompt=request.detection_prompt or (request.prompt if request.operation=='crop' else '')
    if (detector_prompt or request.prompt and request.operation=='caption') and not supports_prompt(ModelServiceConfig(**snapshot['config'])):
        raise HTTPException(400,'所选模型不支持本次自由提示词，请使用检测类别或选择支持提示词的视觉模型')
    if request.operation=='quality' and snapshot['config']['protocol']!='openai_chat':
        raise HTTPException(400,'图片质量检查需要OpenAI视觉对话模型')
    if not attributes and snapshot['config']['protocol']=='jev_decide':
        raise HTTPException(400,'JEV choice模型仅用于目标属性分析')
    attribute_model=await model_snapshot(db,project.tenant_id,request.attribute_model_id,'caption') if request.operation=='detect_attributes' else None
    project=await db.scalar(select(Project).where(Project.id==project.id).with_for_update().execution_options(populate_existing=True))
    if project.task_type!=task_type:raise HTTPException(409,'项目任务类型已变化，请刷新后再创建任务')
    options=request.model_dump(mode='json',exclude={'operation','model_id','asset_ids','attribute_model_id','example_asset_id','example_annotation_version','crops_only','follow_uploads'})
    if request.operation=='crop':options['crop_from_originals']=all(image.source_type!='model_crop' for image in images)
    if request.operation=='quality':options['quality_scope']=dict(task_type=project.task_type,annotation_scope=(project.label_schema or {}).get('annotation_scope','all'))
    if attributes:
        config=(project.label_schema or {}).get('object_attributes')
        if not config:raise HTTPException(400,'请先保存目标属性配置')
        options['attribute_schema']=AttributeSchema.model_validate(config).model_dump()
        analysis_model=attribute_model or snapshot
        if analysis_model['config']['protocol'] not in ('openai_chat','jev_decide'):raise HTTPException(400,'属性分析需要OpenAI视觉对话或JEV choice模型')
        if analysis_model['config']['protocol']=='jev_decide':
            if request.prompt or request.attribute_prompt:raise HTTPException(400,'JEV choice协议不支持自由提示词，请使用属性判断说明和可选值')
            validate_choice_schema(AttributeSchema.model_validate(config),request.example_asset_id)
        if attribute_model:options['attribute_model']=attribute_model
        if request.example_asset_id:
            _,reference=await manual_example(db,project,request.example_asset_id)
            if reference['annotation_version']!=request.example_annotation_version:raise HTTPException(409,'正确范例已修改，请刷新后重新选择')
            options['attribute_example']=reference
            images=[image for image in images if image.id!=request.example_asset_id]
            if not images:raise HTTPException(400,'正确范例不参与待处理，请至少选择一张其他图片')
        if request.operation=='detect_attributes':
            if any(image.source_type=='model_crop' for image in images):raise HTTPException(400,'检测后属性分析请选择原图；裁剪图可直接分析属性')
            options['labels']=AttributeSchema.model_validate(options['attribute_schema']).target_labels
    if request.operation not in ('crop','detect_attributes'):
        for image in images:require_annotation_asset(project,image)
    latest = {}
    if request.operation != 'crop':
        for cond in iter_id_conditions(db, Annotation.asset_id, [a.id for a in images], chunk_size=500):
            for a in (await db.scalars(select(Annotation).where(cond, Annotation.is_latest.is_(True)))).all():
                latest[a.asset_id] = a
    # A save/delete cycle can restore no latest annotation; immutable events still advance.
    quality_events = {}
    if request.operation == 'quality':
        for cond in iter_id_conditions(db, AnnotationEvent.asset_id, [a.id for a in images], chunk_size=500):
            for aid, cnt in (await db.execute(select(AnnotationEvent.asset_id, func.count()).where(cond).group_by(AnnotationEvent.asset_id))).all():
                quality_events[aid] = cnt
    job=ProjectWorkJob(project_id=project.id,user_id=user_id,operation=request.operation,model=snapshot,
        options=options)
    db.add(job);await db.flush()
    if request.follow_uploads:
        await set_follow_uploads(db,job,True)
    db.add_all([ProjectWorkItem(job_id=job.id,asset_id=a.id,content_hash=a.content_hash,
        base_version=latest[a.id].version if a.id in latest else 0,result={'review_state':review_state(latest.get(a.id)),
            **({'quality_event_count':quality_events.get(a.id,0)} if request.operation=='quality' else {})}) for a in images])
    await db.commit()
    return job


async def scoped_job(db,project,job_id,*,lock=True):
    query=select(ProjectWorkJob).where(ProjectWorkJob.id==job_id,ProjectWorkJob.project_id==project.id,
        ProjectWorkJob.operation!='embedding_negatives',
        ProjectWorkJob.options['queue_deleted'].as_boolean().is_not(True))
    job=await db.scalar(query.with_for_update() if lock else query)
    if not job:raise HTTPException(404,'处理批次不存在')
    return job


def _normalize_status_counts(raw_counts: dict[str, int]) -> dict[str, int]:
    counts = {st: cnt for st, cnt in raw_counts.items() if st != 'retry_wait' and cnt}
    failed_total = raw_counts.get('failed', 0) + raw_counts.get('retry_wait', 0)
    if failed_total:
        counts['failed'] = failed_total
    return counts


async def summaries(db, jobs_list):
    if not jobs_list:
        return []
    batch_roots = {}
    for job in jobs_list:
        root_str = (job.options or {}).get('batch_root')
        if root_str:
            batch_roots.setdefault((job.project_id, root_str), set()).add(job.id)
    batch_member_map: dict[tuple[UUID, str], set[UUID]] = defaultdict(set)
    for (proj_id, root_str), _ in batch_roots.items():
        member_ids = (await db.scalars(select(ProjectWorkJob.id).where(
            ProjectWorkJob.project_id == proj_id,
            or_(ProjectWorkJob.id == UUID(root_str), ProjectWorkJob.options['batch_root'].as_string() == root_str),
        ))).all()
        batch_member_map[(proj_id, root_str)].update(member_ids)

    job_scope_ids: dict[UUID, set[UUID]] = {}
    all_target_job_ids: set[UUID] = set()
    for job in jobs_list:
        root_str = (job.options or {}).get('batch_root')
        scope_ids = set(batch_member_map[(job.project_id, root_str)]) if root_str else {job.id}
        if not scope_ids:
            scope_ids = {job.id}
        job_scope_ids[job.id] = scope_ids
        all_target_job_ids.update(scope_ids)

    passed_col = ProjectWorkItem.result['passed'].as_boolean()
    skipped_col = ProjectWorkItem.result['attribute_skipped'].as_boolean()
    is_completed = ProjectWorkItem.status == 'completed'
    per_job_status: dict[UUID, dict[str, int]] = defaultdict(dict)
    per_job_filtered: dict[UUID, int] = defaultdict(int)
    per_job_passed_true: dict[UUID, int] = defaultdict(int)
    per_job_passed_false: dict[UUID, int] = defaultdict(int)
    per_job_passed_not_false: dict[UUID, int] = defaultdict(int)
    per_job_attr_skipped: dict[UUID, int] = defaultdict(int)
    per_job_retrying: dict[UUID, int] = defaultdict(int)

    for cond in iter_id_conditions(db, ProjectWorkItem.job_id, sorted(all_target_job_ids)):
        agg_rows = (await db.execute(
            select(
                ProjectWorkItem.job_id,
                ProjectWorkItem.status,
                func.count(),
                func.sum(ProjectWorkItem.result['filtered_count'].as_integer()),
                func.sum(case((and_(is_completed, passed_col.is_(True)), 1), else_=0)),
                func.sum(case((and_(is_completed, passed_col.is_(False)), 1), else_=0)),
                func.sum(case((and_(is_completed, passed_col.is_not(False)), 1), else_=0)),
                func.sum(case((and_(is_completed, skipped_col.is_(True)), 1), else_=0)),
                func.sum(case((retrying_items(), 1), else_=0)),
            )
            .where(cond)
            .group_by(ProjectWorkItem.job_id, ProjectWorkItem.status)
        )).all()
        for jid, st, cnt, filt, p_true, p_false, p_not_false, a_skip, r_cnt in agg_rows:
            per_job_status[jid][st] = per_job_status[jid].get(st, 0) + (cnt or 0)
            per_job_filtered[jid] += filt or 0
            per_job_passed_true[jid] += p_true or 0
            per_job_passed_false[jid] += p_false or 0
            per_job_passed_not_false[jid] += p_not_false or 0
            per_job_attr_skipped[jid] += a_skip or 0
            per_job_retrying[jid] += r_cnt or 0

    det_attr_jobs = [job for job in jobs_list if job.operation == 'detect_attributes']
    det_links_by_job: dict[UUID, list[tuple[str | None, int | None, int | None]]] = defaultdict(list)
    all_child_ids: set[UUID] = set()
    if det_attr_jobs:
        for cond in iter_id_conditions(db, ProjectWorkItem.job_id, [j.id for j in det_attr_jobs]):
            link_rows = (await db.execute(select(
                ProjectWorkItem.job_id,
                ProjectWorkItem.result['attribute_job_id'].as_string(),
                ProjectWorkItem.result['detected_count'].as_integer(),
                ProjectWorkItem.result['skipped_annotated_count'].as_integer(),
            ).where(cond))).all()
            for jid, attr_jid, det_cnt, skip_cnt in link_rows:
                det_links_by_job[jid].append((attr_jid, det_cnt, skip_cnt))
                if attr_jid:
                    all_child_ids.add(UUID(attr_jid))

    child_status_by_job: dict[UUID, dict[str, int]] = defaultdict(dict)
    valid_child_project: dict[UUID, UUID] = {}
    if all_child_ids:
        for cond in iter_id_conditions(db, ProjectWorkItem.job_id, sorted(all_child_ids)):
            c_rows = (await db.execute(
                select(ProjectWorkJob.project_id, ProjectWorkItem.job_id, ProjectWorkItem.status, func.count())
                .join(ProjectWorkJob, ProjectWorkJob.id == ProjectWorkItem.job_id)
                .where(cond)
                .group_by(ProjectWorkJob.project_id, ProjectWorkItem.job_id, ProjectWorkItem.status)
            )).all()
            for pid, cid, st, cnt in c_rows:
                valid_child_project[cid] = pid
                child_status_by_job[cid][st] = child_status_by_job[cid].get(st, 0) + (cnt or 0)

    failure_related_all: set[UUID] = set()
    job_related_map: dict[UUID, set[UUID]] = {}
    for job in jobs_list:
        scope_ids = job_scope_ids[job.id]
        raw_merged: dict[str, int] = defaultdict(int)
        for sid in scope_ids:
            for st, cnt in per_job_status.get(sid, {}).items():
                raw_merged[st] += cnt
        counts = _normalize_status_counts(raw_merged)
        related = set(scope_ids)
        child_failed = 0
        if job.operation == 'detect_attributes':
            cids = {UUID(r[0]) for r in det_links_by_job.get(job.id, []) if r[0]}
            related.update(cids)
            for cid in cids:
                if valid_child_project.get(cid) == job.project_id:
                    c_raw = child_status_by_job.get(cid, {})
                    child_failed += c_raw.get('failed', 0) + c_raw.get('retry_wait', 0)
        job_related_map[job.id] = related
        if counts.get('failed') or child_failed:
            failure_related_all.update(related)

    failure_rows_by_job: dict[UUID, list[tuple[UUID, str, str, int]]] = defaultdict(list)
    if failure_related_all:
        for cond in iter_id_conditions(db, ProjectWorkItem.job_id, sorted(failure_related_all)):
            f_rows = (await db.execute(
                select(ProjectWorkJob.project_id, ProjectWorkItem.job_id, ProjectWorkJob.operation, ProjectWorkItem.error, func.count())
                .join(ProjectWorkJob, ProjectWorkJob.id == ProjectWorkItem.job_id)
                .where(cond, ProjectWorkItem.status.in_(('failed', 'retry_wait')), ProjectWorkItem.error.is_not(None))
                .group_by(ProjectWorkJob.project_id, ProjectWorkItem.job_id, ProjectWorkJob.operation, ProjectWorkItem.error)
            )).all()
            for pid, jid, op_name, err_text, cnt in f_rows:
                failure_rows_by_job[jid].append((pid, op_name, err_text, cnt))

    results = []
    for job in jobs_list:
        scope_ids = job_scope_ids[job.id]
        raw_merged = defaultdict(int)
        filtered_crop_count = 0
        retrying_total = 0
        for sid in scope_ids:
            for st, cnt in per_job_status.get(sid, {}).items():
                raw_merged[st] += cnt
            filtered_crop_count += per_job_filtered.get(sid, 0)
            retrying_total += per_job_retrying.get(sid, 0)
        counts = _normalize_status_counts(raw_merged)
        result = dict(
            id=str(job.id),
            project_id=str(job.project_id),
            operation=job.operation,
            model_id=job.model['id'],
            revision=job.model['revision'],
            created_at=job.created_at.replace(tzinfo=timezone.utc) if job.created_at.tzinfo is None else job.created_at,
            total=sum(counts.values()),
            counts=counts,
            model_name=job.model.get('config', {}).get('name', ''),
            follow_uploads=job.follow_uploads,
            min_crop_long_edge=job.options.get('min_crop_long_edge', 0),
            filtered_crop_count=filtered_crop_count,
        )
        if job.operation == 'quality':
            p_true = per_job_passed_true.get(job.id, 0)
            p_false = per_job_passed_false.get(job.id, 0)
            result.update(quality_counts=dict(passed=p_true, deleted=p_false), quality_requirement=job.options['prompt'])
            if job.options.get('workflow_id'):
                result['passed_count'] = p_true
        if job.operation == 'crop':
            result['target_labels'] = job.options.get('labels') or job.model.get('config', {}).get('labels', [])
        if 'workflow_id' in job.options:
            result.update(
                workflow_started=job.options['workflow_started'],
                workflow_streaming=job.options.get('workflow_streaming', False),
                workflow_cancelled=job.options.get('workflow_cancelled', False),
                workflow_can_follow=job.options.get('workflow_can_follow', job.options.get('workflow_streaming', False)),
                node_key=job.options.get('node_key'),
                title=job.options.get('title'),
                workflow_routes=job.options.get('workflow_routes'),
            )
            if accepts_uploaded_crop_entry(job):
                result.update(workflow_can_route_uploaded_crops=True, uploaded_crop_job_id=job.options.get('uploaded_crop_job_id'))
            if counts.get('failed') and result['workflow_streaming'] and not result['workflow_cancelled'] and not job.options.get('queue_deleted'):
                result['retrying_count'] = retrying_total
        if job.operation in ('detect', 'filter', 'img_filter', 'box_clean'):
            result['passed_count'] = per_job_passed_true.get(job.id, 0)
        elif job.operation == 'script':
            result['passed_count'] = per_job_passed_not_false.get(job.id, 0)
        if 'batch_root' in job.options:
            result['batch_count'] = len(scope_ids)
        definition = job.options.get('attribute_schema')
        if definition:
            result.update(
                target_labels=AttributeSchema.model_validate(definition).target_labels,
                attribute_fields=[field['name'] for field in definition['fields']],
            )
        if job.operation == 'attributes' and job.options.get('workflow_id'):
            result['attribute_skipped_count'] = per_job_attr_skipped.get(job.id, 0)
        if job.operation == 'detect_attributes':
            links = det_links_by_job.get(job.id, [])
            child_ids = {UUID(row[0]) for row in links if row[0]}
            raw_attr_counts: dict[str, int] = defaultdict(int)
            for cid in child_ids:
                if valid_child_project.get(cid) == job.project_id:
                    for st, cnt in child_status_by_job.get(cid, {}).items():
                        raw_attr_counts[st] += cnt
            attribute_counts = _normalize_status_counts(raw_attr_counts)
            result.update(
                attribute_model_name=job.options.get('attribute_model', {}).get('config', {}).get('name', ''),
                pipeline=dict(
                    detection=dict(total=sum(counts.values()), counts=counts),
                    attributes=dict(total=sum(attribute_counts.values()), counts=attribute_counts),
                    generated_targets=sum(row[1] or 0 for row in links),
                    preserved_annotations=sum(row[2] or 0 for row in links),
                    child_batches=len(child_ids),
                ),
            )
            if attribute_counts.get('pending') and not attribute_counts.get('running'):
                result['waiting_reason'] = (
                    '原图检测尚未结束，属性分析排队等待；当前队列先处理原图检测。'
                    if counts.get('pending') or counts.get('running')
                    else '属性分析已入队，等待前序任务完成。'
                )
            elif not child_ids and (counts.get('pending') or counts.get('running')):
                result['waiting_reason'] = '等待检测产生目标切图后，再执行属性分析。'
        if counts.get('failed') or result.get('pipeline', {}).get('attributes', {}).get('counts', {}).get('failed'):
            merged_failures: dict[tuple[str, str], int] = defaultdict(int)
            for r_jid in job_related_map[job.id]:
                for pid, op_name, err_text, cnt in failure_rows_by_job.get(r_jid, []):
                    if pid == job.project_id:
                        merged_failures[(op_name, err_text)] += cnt
            top_failures = sorted(merged_failures.items(), key=lambda kv: -kv[1])[:3]
            result['failure_reasons'] = [
                dict(operation=op_name, reason=err_text, count=cnt)
                for (op_name, err_text), cnt in top_failures
            ]
        reference = job.options.get('attribute_example')
        if reference:
            result['example'] = {key: reference[key] for key in ('asset_id', 'original_name', 'annotation_version')}
        results.append(result)
    return results


async def summary(db,job,details=False,page=1):
    from services.project_workflow import batch_jobs
    result = (await summaries(db, [job]))[0]
    if details:
        jobs = batch_jobs(job) if 'batch_root' in job.options else select(ProjectWorkJob.id).where(ProjectWorkJob.id == job.id)
        definition = job.options.get('attribute_schema')
        attribute_templates = job.options.get('attribute_templates')
        if definition and job.options.get('workflow_id'):result['attribute_schema']=definition
        if attribute_templates:result['attribute_templates']=attribute_templates
        if job.operation=='crop':result['crop_from_originals']=await crop_accepts_originals(db,job)
        items=(await db.execute(select(ProjectWorkItem,retrying_items()).where(ProjectWorkItem.job_id.in_(jobs))
            .order_by(ProjectWorkItem.id).offset((page-1)*20).limit(20))).all()
        assets={asset.id:asset for asset in (await db.scalars(select(Asset).where(Asset.project_id==job.project_id,Asset.id.in_([item.asset_id for item,_ in items if item.asset_id])))).all()}
        result.update(page=page,items=[dict(id=str(i.id),asset_id=str(i.asset_id) if i.asset_id else None,original_name=assets[i.asset_id].original_name if i.asset_id in assets else (i.result or {}).get('original_name'),
            status='failed' if i.status=='retry_wait' else i.status,attempt=i.attempt,result=i.result,error=i.error,
            auto_retry=bool(retrying and result.get('retrying_count'))) for i,retrying in items])
        if job.operation=='attributes':
            parent=(await db.execute(select(ProjectWorkJob.id,Asset.original_name).join(ProjectWorkItem,ProjectWorkItem.job_id==ProjectWorkJob.id)
                .outerjoin(Asset,Asset.id==ProjectWorkItem.asset_id).where(ProjectWorkJob.project_id==job.project_id,
                    ProjectWorkItem.result['attribute_job_id'].as_string()==str(job.id)).limit(1))).first()
            if parent:result['parent']=dict(job_id=str(parent[0]),original_name=parent[1])
    return result


async def delete_job(db,project,job_id):
    job=await scoped_job(db,project,job_id)
    if job.follow_uploads:raise HTTPException(409,'请先关闭自动加入新上传图片')
    state=await summary(db,job,details=job.operation=='attributes')
    if state.get('parent'):raise HTTPException(409,'请在所属主批次删除任务')
    stages=[state['counts'],state.get('pipeline',{}).get('attributes',{}).get('counts',{})]
    if state.get('retrying_count') or any(counts.get('pending') or counts.get('running') for counts in stages):
        raise HTTPException(409,'任务仍在等待或处理中，请完成或取消后删除')
    # Keep item results for crop provenance and project vector search.
    job.options={**job.options,'queue_deleted':True}
    await db.commit()


async def action_job(db,project,job_id,action):
    from services.project_workflow import batch_jobs,cancel_steps
    job=await scoped_job(db,project,job_id)
    if job.options.get('workflow_hidden'):raise HTTPException(409,'工作流已从历史任务删除')
    if action=='cancel' and job.options.get('workflow_id'):
        return await cancel_steps(db,[job])
    if action=='cancel':job.follow_uploads=False
    items=(await db.scalars(select(ProjectWorkItem).where(ProjectWorkItem.job_id.in_(batch_jobs(job)))
        .order_by(ProjectWorkItem.id).with_for_update())).all()
    if action=='retry':
        await validate_snapshots(db,project.tenant_id,[job.model,job.options.get('attribute_model')],lock=False)
        if job.options.get('attribute_schema') and not job.options.get('workflow_attribute_override') and job.options['attribute_schema']!=(project.label_schema or {}).get('object_attributes'):
            raise HTTPException(409,'属性配置已变更，请新建批次')
        await validate_example(db,project,job.options.get('attribute_example'))
        if job.options.get('workflow_cancelled'):
            job.options={key:value for key,value in job.options.items() if key!='workflow_cancelled'}
    count=0
    for item in items:
        child=(item.result or {}).get('attribute_job_id')
        if child and action=='cancel':
            children=(await db.scalars(select(ProjectWorkItem).join(ProjectWorkJob).where(ProjectWorkJob.id==UUID(child),
                ProjectWorkJob.project_id==project.id,ProjectWorkItem.status.in_(['pending','running','retry_wait'])).with_for_update())).all()
            for entry in children:entry.status='cancelled';entry.attempt+=1;entry.error='上游批次已取消';count+=1
        if action=='cancel' and item.status in ('pending','running','retry_wait'):
            item.status='cancelled';item.attempt+=1;item.error='已取消';count+=1
        if action=='retry' and item.status in ('failed','retry_wait','cancelled') and item.asset_id:
            item.status='pending';item.attempt+=1;item.error=None;count+=1
            if item.result and 'retry_at' in item.result:
                item.result={k:v for k,v in item.result.items() if k!='retry_at'} or None
    await db.commit()
    return {'changed_count':count}


async def read_content(asset):
    with await get_storage().get_file(asset.blob_path) as file:content=file.read()
    if hashlib.sha256(content).hexdigest()!=asset.content_hash:
        raise HTTPException(409,'图片内容与记录不一致，请重新导入')
    with Image.open(BytesIO(content)) as image:
        if image.size!=(asset.width,asset.height):raise HTTPException(409,'图片尺寸已变更')
    return content


def crop_label(asset):
    crop=(asset.device_info or {}).get('crop') or {}
    return (crop.get('box') or {}).get('label') if asset.source_type=='model_crop' else None


def crop_attribute_template(asset,templates):
    label=crop_label(asset)
    if not label:
        if asset.source_type=='model_crop' and (asset.device_info or {}).get('uploaded_crop') and len(templates)==1:
            return None,templates[0]['id'],templates[0]['config']
        return None,None,None
    candidates={target.casefold():(template,target) for template in templates
        for target in AttributeSchema.model_validate(template['config']).target_labels}
    key=label.casefold()
    match=candidates.get(key) or candidates.get(key.removeprefix('sam3_'))
    if match:
        template,target=match
        return label,template['id'],{**template['config'],'target_label':target}
    return label,None,None


async def save_crop_source_boxes(db,asset,boxes,user_id):
    previous=await db.scalar(select(Annotation).where(Annotation.asset_id==asset.id,Annotation.is_latest.is_(True)))
    project=await db.scalar(select(Project).where(Project.id==asset.project_id).with_for_update()
        .execution_options(populate_existing=True))
    await register_detection_labels(db,project,boxes)
    if previous and 'boxes' in previous.annotation_data:
        return {'source_annotation_preserved':True}
    annotation=await save_annotation(db,asset,{'boxes':boxes,'negative':not boxes},previous.version if previous else 0,
        user_id,source='auto',kind='detection',allow_crop_source=True)
    return {'source_annotation_id':str(annotation.id)}


async def finish_detection_annotation(db,work,project,asset,result):
    project=await db.scalar(select(Project).where(Project.id==project.id).with_for_update().execution_options(populate_existing=True))
    require_annotation_asset(project,asset)
    current=await db.scalar(select(Annotation).where(Annotation.asset_id==asset.id,Annotation.is_latest.is_(True)))
    state={**review_state(current),'status':current.status_for('detection')} if current else {}
    if (current.version if current else 0)!=work['base_version'] or state!=work['review_state']:
        return {'reason':'标注或审核结论已变化，保留当前结果'}
    if work['operation']=='preannotate':
        if 'boxes' in result:
            result.update(await save_crop_source_boxes(db,asset,result.pop('boxes'),work['user_id']))
        return result
    if 'evidence' not in result:
        return result
    if (project.label_schema or {}).get('labels',[])!=work['project_labels']:
        return {'reason':'项目类别已变化，保留人工审核'}
    evidence=result['evidence']
    pending_automation=await db.scalar(select(AutomationItem.id).where(AutomationItem.annotation_id==current.id,
        AutomationItem.annotation_version==current.version,AutomationItem.status=='needs_review').limit(1))
    if result['approved'] and not pending_automation:
        await review(db,current,asset,True,'AI审核通过：'+result['reason'],work['user_id'],source='ai',kind='detection')
        result['reason']='AI 审核通过：'+result['reason']
    else:
        result.update(approved=False,reason='转人工审核：'+result['reason'])
        current.review_by_kind={**(current.review_by_kind or {}),'ai_needs_review':True}
    record_event(db,asset,work['user_id'],'ai_review',dict(job_id=work['options']['workflow_id'],
        annotation_id=str(current.id),version=current.version,source='ai',
        decision='approved' if result['approved'] else 'needs_review',evidence=evidence))
    return result


def _reencode_crop_with_label(raw: bytes, project_id, asset_id, region, label: str) -> tuple[bytes, str]:
    with Image.open(BytesIO(raw)) as image:
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('project_crop', image.info.get('project_crop') or json.dumps([str(project_id), str(asset_id), region]))
        metadata.add_text('project_crop_label', label)
        output = BytesIO()
        image.save(output, 'PNG', pnginfo=metadata)
    encoded = output.getvalue()
    return encoded, hashlib.sha256(encoded).hexdigest()


async def persist_derived_crops(db, project, asset, raw_crops, *, expansion=0.0, model=None, job_id=None):
    if not raw_crops:
        return [], []
    storage = get_storage()
    root = (asset.device_info or {}).get('parent_image_id') or str(asset.image_id or asset.id)
    prepared = []
    for crop in raw_crops:
        raw = crop.pop('content')
        digest = hashlib.sha256(raw).hexdigest()
        parent_crop = (asset.device_info or {}).get('crop') or {}
        parent_region = parent_crop.get('root_region') or parent_crop.get('region') or [0, 0, asset.width, asset.height]
        region = crop.get('region') or [0, 0, crop['width'], crop['height']]
        box = crop.get('box') or {'label': crop.get('label') or 'crop', 'x': region[0], 'y': region[1], 'width': crop['width'], 'height': crop['height']}
        crop_info = dict(
            parent_asset_id=str(asset.id), parent_image_id=str(asset.image_id) if asset.image_id else None,
            parent_blob_path=asset.blob_path, parent_content_hash=asset.content_hash,
            region=region, box=box, expansion=expansion,
            model_id=(model or {}).get('id'), revision=(model or {}).get('revision', 0),
            job_id=str(job_id) if job_id else None,
            depth=parent_crop.get('depth', 1 if asset.source_type == 'model_crop' else 0) + 1,
            root_region=[parent_region[0] + region[0], parent_region[1] + region[1],
                         parent_region[0] + region[2], parent_region[1] + region[3]],
        )
        info = {**(asset.device_info or {}), 'parent_image_id': root, 'crop': crop_info,
                'crop_chain': [*deepcopy((asset.device_info or {}).get('crop_chain') or ([parent_crop] if parent_crop else [])), crop_info]}
        prepared.append(dict(raw=raw, digest=digest, region=region, box=box, info=info, width=crop['width'], height=crop['height']))

    # Encode PNG metadata and persist immutable crop files BEFORE acquiring Project FOR UPDATE lock.
    initial_hashes = sorted({entry['digest'] for entry in prepared})
    pre_existing = {}
    for cond in iter_id_conditions(db, Asset.content_hash, initial_hashes):
        for existing_asset in (await db.scalars(select(Asset).where(Asset.project_id == project.id, cond))).all():
            pre_existing[existing_asset.content_hash] = existing_asset

    seen_label_by_hash: dict[str, str] = {}
    for entry in prepared:
        digest = entry['digest']
        label = entry['box']['label']
        conflict_db = pre_existing.get(digest)
        conflict_batch = digest in seen_label_by_hash and seen_label_by_hash[digest] != label
        if conflict_batch or (
            conflict_db is not None
            and (
                conflict_db.source_type != 'model_crop'
                or ((conflict_db.device_info or {}).get('crop') or {}).get('box', {}).get('label') != label
            )
        ):
            raw_new, digest_new = await asyncio.to_thread(
                _reencode_crop_with_label, entry['raw'], project.id, asset.id, entry['region'], label
            )
            entry['raw'] = raw_new
            entry['digest'] = digest_new
        seen_label_by_hash.setdefault(entry['digest'], label)
        path = f"project-crops/{project.id}/{entry['digest'][:2]}/{entry['digest']}.png"
        entry['path'] = path
        await storage.save_file(BytesIO(entry['raw']), path)

    # Now acquire Project FOR UPDATE lock solely for quick DB metadata registration.
    await db.scalar(select(Project).where(Project.id == project.id).with_for_update())
    final_hashes = sorted({entry['digest'] for entry in prepared})
    existing = {}
    for cond in iter_id_conditions(db, Asset.content_hash, final_hashes):
        for existing_asset in (await db.scalars(select(Asset).where(Asset.project_id == project.id, cond))).all():
            existing[existing_asset.content_hash] = existing_asset

    ids = []
    derived_assets = []
    created_count = 0
    for entry in prepared:
        digest = entry['digest']
        raw = entry['raw']
        path = entry['path']
        box = entry['box']
        derived = existing.get(digest)
        if derived and (
            derived.source_type != 'model_crop'
            or ((derived.device_info or {}).get('crop') or {}).get('box', {}).get('label') != box['label']
        ):
            raw, digest = await asyncio.to_thread(
                _reencode_crop_with_label, raw, project.id, asset.id, entry['region'], box['label']
            )
            path = f'project-crops/{project.id}/{digest[:2]}/{digest}.png'
            await storage.save_file(BytesIO(raw), path)
            derived = existing.get(digest) or await db.scalar(
                select(Asset).where(Asset.project_id == project.id, Asset.content_hash == digest)
            )
        if derived is None:
            derived = Asset(
                project_id=project.id, image_id=None, blob_path=path, content_hash=digest,
                original_name=f'{asset.id}_{digest[:12]}.png', file_size=len(raw),
                width=entry['width'], height=entry['height'], format='PNG',
                source_type='model_crop', device_info=entry['info'], annotation_status='pending',
            )
            db.add(derived)
            await db.flush()
            existing[digest] = derived
            created_count += 1
        ids.append(str(derived.id))
        derived_assets.append(derived)
    if created_count:
        await db.execute(update(Project).where(Project.id == project.id).values(total_assets=Project.total_assets + created_count))
    return list(dict.fromkeys(ids)), derived_assets


async def _save_auto_boxes_if_editable(db, project, asset, boxes, work):
    project = await db.scalar(select(Project).where(Project.id == project.id).with_for_update().execution_options(populate_existing=True))
    try:
        require_annotation_asset(project, asset)
    except HTTPException:
        return {'boxes_not_in_annotation_scope': True}
    current = await db.scalar(select(Annotation).where(Annotation.asset_id == asset.id, Annotation.is_latest.is_(True)))
    state = {**review_state(current), 'status': current.status_for('detection')} if current else {}
    if (current.version if current else 0) != work['base_version'] or state != work['review_state']:
        return {'source_annotation_preserved': True}
    det_source = ((current.review_by_kind or {}).get('detection_source') or current.annotation_source) if current else None
    if current and (det_source == 'manual' or current.status_for('detection') != 'pending' or current.reviewer_id is not None):
        return {'source_annotation_preserved': True}
    await register_detection_labels(db, project, boxes)
    annotation = await save_annotation(db, asset, {'boxes': boxes, 'negative': not boxes}, current.version if current else 0,
                                       work['user_id'], source='auto', kind='detection')
    return {'annotation_id': str(annotation.id)}


async def finish(sessions,work,result,error):
    async with sessions() as db:
        try:
            if work['options'].get('workflow_id'):
                job_ids={work['job_id']}
                if next_id:=work['options'].get('workflow_next'):
                    job_ids.add(UUID(next_id))
                for route in (work['options'].get('workflow_routes') or []):
                    if route.get('target_job_id'):
                        job_ids.add(UUID(route['target_job_id']))
                # Cancel locks the job before touching items. Take compatible worker locks in that order too.
                await db.execute(select(ProjectWorkJob.id).where(ProjectWorkJob.id.in_(job_ids))
                    .order_by(ProjectWorkJob.id).with_for_update(read=True,key_share=True))
            asset=await db.scalar(select(Asset).where(Asset.id==work['asset_id']).with_for_update())
            project=await db.get(Project,work['project_id'])
            if not project:return
            item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.id==work['item_id']).with_for_update())
            if not item or item.status!='running' or item.attempt!=work['attempt']:return
            if not asset or asset.project_id!=project.id or asset.content_hash!=work['content_hash']:
                raise HTTPException(409,'图片已删除或变更')
            if work['operation']=='quality':
                project=await db.scalar(select(Project).where(Project.id==project.id).with_for_update().execution_options(populate_existing=True))
                if work['options']['quality_scope']!=dict(task_type=project.task_type,annotation_scope=(project.label_schema or {}).get('annotation_scope','all')):
                    raise HTTPException(409,'项目标注范围已变更，请新建图片质量批次')
            if work['options'].get('attribute_example'):
                project=await db.scalar(select(Project).where(Project.id==project.id).with_for_update().execution_options(populate_existing=True))
                await validate_example(db,project,work['options']['attribute_example'])
            await validate_snapshots(db,project.tenant_id,[work['model'],work['options'].get('attribute_model')])
            attribute_schema=work['options'].get('attribute_schema')
            if attribute_schema and not work['options'].get('workflow_attribute_override') and attribute_schema!=(project.label_schema or {}).get('object_attributes'):
                raise HTTPException(409,'属性配置已变更，请新建批次')
            actor=await db.scalar(select(User).where(User.id==work['user_id']).with_for_update(read=True)) if work['operation']=='quality' else await db.get(User,work['user_id'])
            if not actor or not actor.is_active or actor.tenant_id!=project.tenant_id:
                raise HTTPException(403,'任务创建人已失去访问权限')
            if attribute_schema and actor.role!='admin':raise HTTPException(403,'属性分析批次创建人已失去管理员权限')
            if work['options'].get('workflow_id') and actor.role!='admin':raise HTTPException(403,'工作流创建人已失去管理员权限')
            if work['operation']=='quality' and actor.role!='admin':raise HTTPException(403,'质量检查批次创建人已失去管理员权限')
            if error:raise error
            write_image=work['options'].get('save_attributes') or work['options'].get('save_description')
            if work['operation'] not in ('crop','detect_attributes') and not work['options'].get('workflow_id'):
                require_annotation_asset(project,asset)
            if work['operation']=='quality':
                from services.image_quality import validate_result
                result=validate_result(result)
                previous=await db.scalar(select(Annotation).where(Annotation.asset_id==asset.id,Annotation.is_latest.is_(True)))
                events=await db.scalar(select(func.count()).select_from(AnnotationEvent).where(AnnotationEvent.asset_id==asset.id))
                if events!=work.get('quality_event_count') or (previous.version if previous else 0)!=work['base_version'] or review_state(previous)!=work['review_state']:
                    raise HTTPException(409,'图片操作记录或标注审核已变化，未删除图片；请重试工作流或新建质量批次')
                result.update(deleted=not result['passed'],original_name=asset.original_name)
                if not result['passed']:
                    # shortcut: use project deletion semantics; shared immutable files stay with storage maintenance.
                    item.asset_id=None
                    await db.delete(asset)
                    await db.execute(update(Project).where(Project.id==project.id).values(total_assets=Project.total_assets-1,
                        annotated_count=Project.annotated_count-int(asset.annotation_status in ('completed','reviewed')),
                        reviewed_count=Project.reviewed_count-int(asset.annotation_status=='reviewed')))
            elif work['operation'] in ('preannotate','ai_review'):
                if project.task_type=='detection':
                    result=await finish_detection_annotation(db,work,project,asset,result)
            elif work['operation'] in ('caption','attributes') and project.task_type in ('caption','vector') and not result.get('attribute_skipped') and (not work['options'].get('workflow_id') or
                    write_image and (not crops_only(project) or asset.source_type=='model_crop')):
                previous=await db.scalar(select(Annotation).where(Annotation.asset_id==asset.id,Annotation.is_latest.is_(True)))
                if review_state(previous)!=work['review_state']:
                    raise HTTPException(409,'人工审核结论已修改，请新建描述批次')
                data={key:deepcopy(value) for key,value in previous.annotation_data.items() if key in IMAGE_FIELDS} if previous else {}
                data.pop('skipped',None)
                if work['operation']=='attributes':
                    data.update(attributes=result['attributes'],attribute_schema=attribute_schema)
                else:data['description']=result['description']
                annotation=await save_annotation(db,asset,data,work['base_version'],work['user_id'],source='auto',
                    workflow_attribute_schema=attribute_schema if work['options'].get('workflow_attribute_override') else None,kind='image')
                result['annotation_id']=str(annotation.id)
            elif work['operation'] in ('crop','detect_attributes'):
                filtered_count=result.get('filtered_count',0)
                source_annotation={}
                crop_boxes=[crop['box'] for crop in result['crops'] if crop.get('box')]
                ids, derived_assets = await persist_derived_crops(db, project, asset, result['crops'],
                    expansion=work['options'].get('expansion', 0.0), model=work['model'], job_id=item.job_id)
                if project.task_type=='detection' and work['options'].get('save_input_boxes') and result.get('source_boxes'):
                    source_annotation=await save_crop_source_boxes(db,asset,result['source_boxes'],work['user_id'])
                elif project.task_type=='detection' and work['options'].get('workflow_id') and crop_boxes:
                    project=await db.scalar(select(Project).where(Project.id==project.id).with_for_update().execution_options(populate_existing=True))
                    await register_detection_labels(db,project,crop_boxes)
                result=dict(asset_ids=ids,detected_count=len(ids),**source_annotation,
                    filtered_count=filtered_count,
                    warning=('检测到的目标均被尺寸阈值过滤，未生成裁剪图' if filtered_count else '未检测到目标；仅标注裁剪图时，此原图不进入标注队列') if not ids else '')
                if work['operation']=='detect_attributes' and derived_assets:
                    existing=set((await db.scalars(select(Annotation.asset_id).where(
                        Annotation.asset_id.in_([a.id for a in derived_assets]),Annotation.is_latest.is_(True)))).all())
                    targets={a.id:a for a in derived_assets if a.id not in existing}
                    if targets:
                        child=ProjectWorkJob(project_id=project.id,user_id=work['user_id'],operation='attributes',
                            model=work['options']['attribute_model'],options={'attribute_schema':attribute_schema,
                                'parent_job_id':str(work['job_id']),
                                'prompt':work['options'].get('prompt',''),
                                'attribute_prompt':work['options'].get('attribute_prompt',''),
                                **({'attribute_example':deepcopy(work['options']['attribute_example'])} if work['options'].get('attribute_example') else {})})
                        db.add(child);await db.flush()
                        db.add_all([ProjectWorkItem(job_id=child.id,asset_id=a.id,content_hash=a.content_hash,base_version=0,
                            result={'review_state':{}}) for a in targets.values()])
                        result['attribute_job_id']=str(child.id)
                    result['skipped_annotated_count']=len(existing)
                record_event(db,asset,work['user_id'],'crop',result)
            elif work['operation']=='img_transform':
                ids, _ = await persist_derived_crops(db, project, asset, result.pop('crops', []),
                    expansion=work['options'].get('expansion', 0.0), model=work['model'], job_id=item.job_id)
                result = dict(asset_ids=ids, detected_count=len(ids), reason=result.get('reason', ''))
                record_event(db, asset, work['user_id'], 'img_transform', result)
            elif work['operation']=='box_clean':
                cleaned_boxes = result.pop('boxes', [])
                if project.task_type=='detection' and (work['options'].get('box_clean_config') or {}).get('save_boxes', True):
                    result.update(await _save_auto_boxes_if_editable(db, project, asset, cleaned_boxes, work))
            elif work['operation']=='script':
                script_cfg = work['options'].get('script_config') or {}
                if result.get('derived'):
                    raw_derived = []
                    for entry in result.pop('derived'):
                        w, h = entry['width'], entry['height']
                        label = entry.get('label') or 'script'
                        box = entry.get('box') if isinstance(entry.get('box'), dict) else {'label': label, 'x': 0, 'y': 0, 'width': w, 'height': h}
                        raw_derived.append({'content': entry['content'], 'width': w, 'height': h, 'region': [0, 0, w, h], 'box': box, 'label': label})
                    ids, _ = await persist_derived_crops(db, project, asset, raw_derived,
                        expansion=work['options'].get('expansion', 0.0), model=work['model'], job_id=item.job_id)
                    result['asset_ids'] = ids
                    result['detected_count'] = len(ids)
                else:
                    if 'boxes' in result:
                        script_boxes = result.pop('boxes')
                        result['detected_count'] = len(script_boxes)
                        if project.task_type=='detection' and script_cfg.get('save_boxes', True):
                            result.update(await _save_auto_boxes_if_editable(db, project, asset, script_boxes, work))
                    has_desc = 'description' in result and script_cfg.get('save_description', True)
                    has_attr = 'attributes' in result and script_cfg.get('save_attributes', True)
                    if (has_desc or has_attr) and project.task_type in ('caption', 'vector') and (not crops_only(project) or asset.source_type=='model_crop'):
                        previous = await db.scalar(select(Annotation).where(Annotation.asset_id==asset.id, Annotation.is_latest.is_(True)))
                        if review_state(previous) == work['review_state'] and (not previous or previous.annotation_source != 'manual'):
                            data = {key: deepcopy(value) for key, value in previous.annotation_data.items() if key in IMAGE_FIELDS} if previous else {}
                            data.pop('skipped', None)
                            if has_desc:
                                data['description'] = result['description']
                            if has_attr:
                                data['attributes'] = result['attributes']
                            annotation = await save_annotation(db, asset, data, previous.version if previous else 0, work['user_id'], source='auto', kind='image')
                            result['annotation_id'] = str(annotation.id)
            item.result=result;item.status='completed';item.error=None
            if work['options'].get('workflow_routes') is not None or work['options'].get('workflow_next'):
                from services.project_workflow import forward
                await forward(db,work,result)
            await db.commit()
        except (HTTPException,DBAPIError) as exc:
            if isinstance(exc,DBAPIError):
                if not work['options'].get('workflow_id') or getattr(exc.orig,'sqlstate',None) not in ('40P01','40001'):
                    raise
                exc=HTTPException(503,'数据库事务冲突，稍后自动重试')
            await db.rollback()
            # Rollback can expire ORM state. Recheck the fence in a new transaction.
            item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.id==work['item_id']).with_for_update())
            if item and item.status=='running' and item.attempt==work['attempt']:
                job_row=await db.get(ProjectWorkJob,work['job_id'])
                job_opts=(job_row.options if job_row else work['options']) or {}
                metadata={key:value for key,value in (item.result or {}).items() if key!='retry_at'}
                can_auto_retry=(
                    bool(work['options'].get('workflow_id'))
                    and job_opts.get('workflow_streaming',False)
                    and not job_opts.get('workflow_cancelled',False)
                    and not job_opts.get('queue_deleted',False)
                    and (exc.status_code==429 or exc.status_code>=500)
                    and item.attempt<MAX_AUTO_RETRY_ATTEMPTS
                    and item.asset_id is not None
                )
                if can_auto_retry:
                    metadata['retry_at']=int(time())+min(300,15*2**min(item.attempt-1,5))
                    item.status='retry_wait'
                else:
                    item.status='failed'
                item.error=str(exc.detail)[:500]
                item.result=metadata or None
                await db.commit()


async def _fail_unexpected(sessions, work):
    try:
        async with sessions() as db:
            item = await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.id == work['item_id']).with_for_update())
            if item and item.status == 'running' and item.attempt == work['attempt']:
                metadata = {k: v for k, v in (item.result or {}).items() if k != 'retry_at'}
                item.status = 'failed'
                item.error = '任务执行异常中断，请检查配置后重试'
                item.result = metadata or None
                await db.commit()
    except Exception:
        logger.exception('Failed to persist fallback error state for item %s', work['item_id'])


async def process_one(sessions,client):
    async with sessions() as db:
        now_ts = int(time())
        is_pg = db.get_bind().dialect.name == 'postgresql'
        active = (
            select(ProjectWorkItem.job_id, func.count().label('running'))
            .where(ProjectWorkItem.status == 'running')
            .group_by(ProjectWorkItem.job_id)
            .cte('active_work')
        )
        stage = case(
            (ProjectWorkJob.options['workflow_streaming'].as_boolean().is_(True),
             ProjectWorkJob.options['workflow_step'].as_integer()),
            else_=0,
        )
        workflow_active = and_(
            ProjectWorkJob.options['workflow_streaming'].as_boolean().is_(True),
            ProjectWorkJob.options['workflow_cancelled'].as_boolean().is_not(True),
            ProjectWorkJob.options['queue_deleted'].as_boolean().is_not(True),
        )
        if is_pg:
            retry_due_cond = and_(
                ProjectWorkItem.status == 'retry_wait',
                ProjectWorkItem.asset_id.is_not(None),
                ProjectWorkItem.attempt < MAX_AUTO_RETRY_ATTEMPTS,
                func.coalesce(ProjectWorkItem.result['retry_at'].as_integer(), 0) <= now_ts,
            )
        else:
            retry_due_cond = and_(
                retrying_items(),
                func.coalesce(ProjectWorkItem.result['retry_at'].as_integer(), 0) <= now_ts,
            )
        has_pending = select(1).where(
            ProjectWorkItem.job_id == ProjectWorkJob.id,
            ProjectWorkItem.status == 'pending',
        ).exists()
        has_retry_due = and_(
            workflow_active,
            select(1).where(ProjectWorkItem.job_id == ProjectWorkJob.id, retry_due_cond).exists(),
        )
        # Step 1: pick prioritized candidate job_ids on small project_work_jobs table.
        candidates = (await db.execute(
            select(ProjectWorkJob.id, has_pending.label('has_pending'), workflow_active.label('wf_active'))
            .outerjoin(active, active.c.job_id == ProjectWorkJob.id)
            .where(or_(has_pending, has_retry_due))
            .order_by(ProjectWorkJob.created_at, func.coalesce(active.c.running, 0), stage.desc(), ProjectWorkJob.id)
            .limit(32)
        )).all()
        # Step 2: claim first unlocked item directly via ix_project_work_claim(status, job_id, id).
        item = None
        for jid, job_has_pending, wf_active in candidates:
            if job_has_pending:
                item = await db.scalar(
                    select(ProjectWorkItem)
                    .where(ProjectWorkItem.job_id == jid, ProjectWorkItem.status == 'pending')
                    .order_by(ProjectWorkItem.id)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
            if item is None and wf_active:
                item = await db.scalar(
                    select(ProjectWorkItem)
                    .where(ProjectWorkItem.job_id == jid, retry_due_cond)
                    .order_by(ProjectWorkItem.id)
                    .with_for_update(skip_locked=True)
                    .limit(1)
                )
            if item is not None:
                break
        if not item:return False
        job=await db.get(ProjectWorkJob,item.job_id);project=await db.get(Project,job.project_id)
        item.status='running';item.attempt+=1
        work=dict(item_id=item.id,job_id=job.id,asset_id=item.asset_id,attempt=item.attempt,project_id=job.project_id,
            tenant_id=project.tenant_id,operation=job.operation,model=deepcopy(job.model),options=deepcopy(job.options),
            content_hash=item.content_hash,base_version=item.base_version,user_id=job.user_id)
        work['review_state']=(item.result or {}).get('review_state',{})
        if (job.options.get('save_attributes') or job.options.get('save_description')
                or job.operation in ('preannotate','ai_review','branch','box_clean','img_transform','script')):
            previous=await db.scalar(select(Annotation).where(Annotation.asset_id==item.asset_id,Annotation.is_latest.is_(True)))
            retry_annotation_id=(item.result or {}).get('attribute_retry_annotation_id')
            if retry_annotation_id and (not previous or str(previous.id)!=retry_annotation_id
                    or previous.annotation_source!='auto' or previous.status_for('image')!='pending'
                    or previous.reviewer_id is not None):
                item.status='cancelled';item.error='标注或审核已变更，保留现有标注'
                await db.commit()
                return True
            work.update(base_version=previous.version if previous else 0,review_state=review_state(previous))
            if job.operation in ('preannotate','ai_review','branch','box_clean','img_transform','script'):
                work['annotation_data']=deepcopy(previous.annotation_data) if previous else {}
                work['annotation_source']=((previous.review_by_kind or {}).get('detection_source') or previous.annotation_source) if previous else None
                if previous and job.operation in ('preannotate','ai_review','box_clean','script'):
                    work['review_state']['status']=previous.status_for('detection')
        if job.operation=='quality':
            if job.options.get('workflow_id'):
                previous=await db.scalar(select(Annotation).where(Annotation.asset_id==item.asset_id,Annotation.is_latest.is_(True)))
                events=await db.scalar(select(func.count()).select_from(AnnotationEvent).where(AnnotationEvent.asset_id==item.asset_id))
                work.update(base_version=previous.version if previous else 0,review_state=review_state(previous),quality_event_count=events)
            else:
                work['quality_event_count']=(item.result or {}).get('quality_event_count')
        work['match_baseline']=deepcopy((item.result or {}).get('match_baseline',{}))
        await db.commit()
    try:
        if work['operation']=='match_review':
            from services.match_review import process
            return await process(sessions,work,client)
        if work['operation']=='embedding_negatives':
            from services.embedding_recommendation_jobs import process
            return await process(sessions,work)
        result=error=None
        attribute_template_id=None
        options=work['options']
        reference=None
        try:
            async with sessions() as db:
                asset=await db.get(Asset,work['asset_id']) if work['asset_id'] else None
                if not asset or asset.content_hash!=work['content_hash'] or asset.project_id!=work['project_id']:raise HTTPException(409,'图片已删除或变更')
                if work['operation'] not in ('crop','detect_attributes') and not work['options'].get('workflow_id'):
                    require_annotation_asset(await db.get(Project,work['project_id']),asset)
                if work['operation'] in ('preannotate','ai_review'):
                    project=await db.get(Project,work['project_id'])
                    require_annotation_asset(project,asset)
                    labels=(project.label_schema or {}).get('labels') or []
                if options.get('attribute_templates'):
                    label,attribute_template_id,schema=crop_attribute_template(asset,options['attribute_templates'])
                    if schema is None:
                        result={'attribute_skipped':True,'reason':f'裁剪类别「{label}」未匹配属性模板' if label else '图片没有检测裁剪类别'}
                    else:
                        options['attribute_schema']=schema
                elif work['operation']=='attributes' and options.get('workflow_id') and (label:=crop_label(asset)):
                    targets=AttributeSchema.model_validate(options['attribute_schema']).target_labels
                    if label.casefold().removeprefix('sam3_') not in {target.casefold().removeprefix('sam3_') for target in targets}:
                        result={'attribute_skipped':True,'reason':f'裁剪类别「{label}」未选为属性分析目标'}
                if result is None:
                    if work['operation'] in ('branch', 'box_clean'):
                        content = b''
                        width, height = asset.width, asset.height
                    else:
                        content = await read_content(asset)
                        width, height = asset.width, asset.height
                    asset_meta = dict(original_name=asset.original_name, source_type=asset.source_type, crop_label=crop_label(asset))
                    existing_phashes = []
                    if work['operation'] == 'img_filter' and (options.get('img_filter_config') or {}).get('dedup_phash_distance') is not None:
                        phash_col = ProjectWorkItem.result['phash'].as_string()
                        existing_phashes = list((await db.scalars(select(phash_col).where(
                            ProjectWorkItem.job_id == work['job_id'], ProjectWorkItem.status == 'completed',
                            ProjectWorkItem.result['passed'].as_boolean().is_(True), phash_col.is_not(None),
                        ).limit(5000))).all())
                await validate_snapshots(db,work['tenant_id'],[work['model'],work['options'].get('attribute_model')],lock=False)
                if work['options'].get('attribute_schema') and not work['options'].get('workflow_attribute_override') and work['options']['attribute_schema']!=((await db.get(Project,work['project_id'])).label_schema or {}).get('object_attributes'):
                    raise HTTPException(409,'属性配置已变更，请新建批次')
                reference_asset=await validate_example(db,await db.get(Project,work['project_id']),work['options'].get('attribute_example'))
                if reference_asset is not None and work['operation']=='attributes':
                    reference=dict(content=await read_example_content(reference_asset),answer=work['options']['attribute_example']['attributes'])
            if result is not None:
                await finish(sessions,work,result,None)
                return True
            if work['operation'] in ('branch', 'img_filter', 'box_clean', 'img_transform', 'script'):
                from services import workflow_builtins as builtins
                from services.script_sandbox import run_script
                ann_data = work.get('annotation_data') or {}
                boxes = ann_data.get('boxes') or []
                attrs = ann_data.get('attributes') or {}
                desc = ann_data.get('description') or ''
                if work['operation'] == 'branch':
                    result = builtins.execute_branch(width=width, height=height, crop_label=asset_meta['crop_label'],
                                                     boxes=boxes, attributes=attrs, config=options['branch_config'])
                elif work['operation'] == 'img_filter':
                    result = await asyncio.to_thread(builtins.execute_img_filter, content, options['img_filter_config'], existing_phashes)
                elif work['operation'] == 'box_clean':
                    result = builtins.execute_box_clean(boxes, width, height, options['box_clean_config'])
                elif work['operation'] == 'img_transform':
                    result = await asyncio.to_thread(builtins.execute_img_transform, content, project_id=str(work['project_id']),
                                                     asset_id=str(work['asset_id']), boxes=boxes, crop_label=asset_meta['crop_label'],
                                                     config=options['img_transform_config'])
                else:
                    scfg = options['script_config']
                    result = await run_script(scfg['code'], content, width=width, height=height,
                                              original_name=asset_meta['original_name'], source_type=asset_meta['source_type'],
                                              crop_label=asset_meta['crop_label'], boxes=boxes, attributes=attrs,
                                              description=desc, timeout_seconds=scfg.get('timeout_seconds', 5.0))
                await finish(sessions, work, result, None)
                return True
            config=ModelServiceConfig(**work['model']['config'])
            if work['operation']=='caption':
                result={'description':await caption(config,content,options['prompt'],client)}
            elif work['operation'] in ('quality','filter'):
                from services.image_quality import analyze as quality_analyze
                result=await quality_analyze(config,content,options['prompt'],client)
            elif work['operation']=='attributes':
                result={'attributes':await analyze(config,content,options['attribute_schema'],client,example=reference,
                    prompt=options.get('prompt',''),attribute_prompt=options.get('attribute_prompt',''))}
                if attribute_template_id:result['attribute_template_id']=attribute_template_id
            elif work['operation']=='index':
                vector=await embedding(config,content=content,client=client)
                key=f'{work["item_id"]}_{work["attempt"]}'
                await Milvus(client).upsert(key,vector,work['tenant_id'],work['project_id'],work['asset_id'],work['model'],work['content_hash'])
                result={'vector_id':key,'space':space_key(work['model'])}
            elif work['operation']=='ai_review':
                boxes=work['annotation_data'].get('boxes')
                req_labels = options.get('labels') or []
                if not boxes or work['review_state'].get('status')!='pending':
                    result={'reason':'待审核的正样本才执行 AI 审核，负样本由人工审核'}
                elif any(label not in labels for label in req_labels) or any(box['label'] not in labels for box in boxes):
                    result={'reason':'审核类别与当前项目类别不匹配，保留人工审核'}
                else:
                    work['project_labels']=labels
                    try:
                        evidence=await audit_image(config,content,boxes,req_labels or labels,options['expansion'],options['prompt'],client)
                    except HTTPException as exc:
                        evidence=dict(boxes=[],completeness='uncertain',negative='uncertain',reason=str(exc.detail))
                    approved=auto_pass(boxes,evidence,options['auto_approve_confidence'],work['annotation_source'] in ('auto','model'))
                    result=dict(approved=approved,evidence=evidence,reason=evidence['reason'])
            else:
                if work['operation']=='preannotate' and 'boxes' in work['annotation_data']:
                    await finish(sessions,work,{'reason':'已保留已有检测框或负样本'},None)
                    return True
                req_labels = options.get('labels') or []
                config=config.model_copy(update={'confidence':options['confidence'],'labels':req_labels or config.labels})
                detection=await infer(config,content,width,height,options.get('detection_prompt','') or
                    (options.get('prompt','') if work['operation'] in ('crop','detect','preannotate') else ''),client=client)
                if work['operation'] in ('detect','preannotate'):
                    labels={config.label_mapping.get(label,label) for label in req_labels}
                    boxes=[box for box in detection['boxes'] if not labels or box['label'] in labels]
                    outcome=dict(boxes=boxes,detected_count=len(boxes))
                    if work['operation']=='detect':outcome['passed']=bool(boxes)
                    elif not boxes:outcome['reason']='未检出目标，已保存为待审核的候选负样本'
                    await finish(sessions,work,outcome,None)
                    return True
                crops=[];source_boxes=[];filtered_count=0
                labels={config.label_mapping.get(label,label) for label in req_labels}
                with Image.open(BytesIO(content)) as image:
                    image=image.convert('RGB')
                    for box in detection['boxes']:
                        if work['operation']=='detect_attributes' and box['label'] not in req_labels:continue
                        if labels and box['label'] not in labels:continue
                        if options.get('save_input_boxes'):source_boxes.append(box)
                        region=crop_region(box,width,height,options['expansion'])
                        if max(region[2]-region[0],region[3]-region[1])<options.get('min_crop_long_edge',0):
                            filtered_count+=1
                            continue
                        metadata=PngImagePlugin.PngInfo()
                        metadata.add_text('project_crop',json.dumps([str(work['project_id']),str(work['asset_id']),region]))
                        output=BytesIO();image.crop(region).save(output,'PNG',pnginfo=metadata)
                        crops.append(dict(content=output.getvalue(),width=region[2]-region[0],height=region[3]-region[1],region=list(region),box=box))
                result={'crops':crops,'filtered_count':filtered_count,'source_boxes':source_boxes}
        except asyncio.CancelledError:
            raise
        except HTTPException as exc:
            error=exc
        except (OSError,ValueError,Image.DecompressionBombError):
            error=HTTPException(400,'图片或推理结果无法读取，请检查配置后重试')
        except Exception:
            logger.exception('Unexpected error processing project work item %s', work['item_id'])
            error=HTTPException(500,'处理任务执行异常，请检查配置后重试')
        await finish(sessions,work,result,error)
        return True
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception('Unhandled failure in project work item %s', work['item_id'])
        await _fail_unexpected(sessions, work)
        return True


def query_response(query):
    return dict(id=str(query.id),query=query.query,space=query.space,hits=query.hits,version=query.version,created_at=query.created_at)


async def query_with_content(db,query):
    response=query_response(query)
    project=await db.get(Project,query.project_id)
    allowed={str(id):source for id,source in (await db.execute(select(Asset.id,Asset.source_type).where(Asset.project_id==query.project_id,
        Asset.id.in_([UUID(h['asset_id']) for h in query.hits]),eligible_assets(project)))).all()}
    response['hits']=[{**hit,'source_type':allowed[hit['asset_id']]} for hit in response['hits'] if hit['asset_id'] in allowed]
    annotations=(await db.scalars(select(Annotation).join(Asset).where(Asset.project_id==query.project_id,
        Annotation.asset_id.in_([UUID(h['asset_id']) for h in query.hits]),Annotation.is_latest.is_(True)))).all()
    current={str(a.asset_id):a.annotation_data for a in annotations}
    response['hits']=[{**hit,'description':current.get(hit['asset_id'],{}).get('description',''),
        'labels':current.get(hit['asset_id'],{}).get('labels',[])} for hit in response['hits']]
    return response


async def search(db,project,request,client):
    if project.task_type!='vector':raise HTTPException(400,'请在向量标注项目检索')
    project_id,tenant_id=project.id,project.tenant_id
    snapshot=await model_snapshot(db,tenant_id,request.model_id,'embedding')
    key=space_key(snapshot)
    vector_id_col = ProjectWorkItem.result['vector_id'].as_string()
    rows=(await db.execute(select(Asset.id, Asset.content_hash, vector_id_col)
        .join(ProjectWorkJob,ProjectWorkJob.id==ProjectWorkItem.job_id)
        .join(Asset,Asset.id==ProjectWorkItem.asset_id).where(ProjectWorkJob.project_id==project_id,
            ProjectWorkJob.operation=='index',ProjectWorkItem.status=='completed',
            ProjectWorkItem.result['space'].as_string()==key,vector_id_col.is_not(None),
            Asset.project_id==project_id,ProjectWorkItem.content_hash==Asset.content_hash,
            eligible_assets(project)).order_by(ProjectWorkJob.created_at,ProjectWorkJob.id))).all()
    by_asset={asset_id:(vec_id,dict(asset_id=str(asset_id),content_hash=content_hash))
              for asset_id,content_hash,vec_id in rows}
    allowed=dict(by_asset.values())
    content=None
    query={'text':request.text} if request.text else {'image_id':str(request.image_id)}
    if request.image_id:
        image=await db.scalar(select(LibraryImage).where(LibraryImage.id==request.image_id,LibraryImage.tenant_id==tenant_id,LibraryImage.source_type!='model_crop',image_condition(db)))
        if not image:raise HTTPException(404,'查询图片不存在')
        content=await read_content(image)
        query.update(content_hash=image.content_hash,blob_path=image.blob_path)
    if not allowed:raise HTTPException(409,'当前项目和模型版本尚无可用向量，请先建立索引')
    await db.rollback()
    vector=await embedding(ModelServiceConfig(**snapshot['config']),content=content,text=request.text,client=client)
    raw_hits=await Milvus(client).search(vector,tenant_id,project_id,key,list(allowed),request.top_k)
    project=await db.scalar(select(Project).where(Project.id==project_id,Project.tenant_id==tenant_id,project_condition(db)).with_for_update())
    if project is None:raise HTTPException(404,'项目不存在或已撤销分配')
    await validate_snapshots(db,tenant_id,[snapshot])
    if request.image_id:
        present=await db.scalar(select(LibraryImage.id).where(LibraryImage.id==request.image_id,
            LibraryImage.tenant_id==tenant_id,LibraryImage.content_hash==query['content_hash'],image_condition(db)))
        if not present:raise HTTPException(409,'查询期间图片已删除或变更')
    assets=(await db.scalars(select(Asset).where(Asset.project_id==project_id,Asset.id.in_(
        [UUID(allowed[h['id']]['asset_id']) for h in raw_hits if h.get('id') in allowed])))).all()
    current={str(a.id):a for a in assets}
    hits=[];seen=set()
    for hit in raw_hits:
        meta=allowed.get(hit.get('id'));asset=current.get(meta['asset_id']) if meta else None
        if not asset or asset.content_hash!=meta['content_hash'] or hit.get('content_hash')!=asset.content_hash or asset.id in seen:continue
        seen.add(asset.id)
        hits.append(dict(asset_id=str(asset.id),image_id=str(asset.image_id) if asset.image_id else None,
            original_name=asset.original_name,content_hash=asset.content_hash,blob_path=asset.blob_path,
            score=hit['distance'],judgment='unknown'))
    saved=MatchQuery(project_id=project_id,query=query,space=dict(model_id=snapshot['id'],revision=snapshot['revision'],
        dimensions=2048,preprocessing=PREPROCESSING),hits=hits)
    db.add(saved);await db.commit()
    return await query_with_content(db,saved)


async def judge(db,project,query_id,asset_id,judgment,base_version,user_id,approved=None):
    query=await db.scalar(select(MatchQuery).where(MatchQuery.id==query_id,MatchQuery.project_id==project.id,MatchQuery.is_retrieval_query())
        .with_for_update().execution_options(populate_existing=True))
    if not query:raise HTTPException(404,'检索记录不存在')
    if base_version!=query.version:raise HTTPException(409,'匹配判定已修改，请重新加载')
    asset=await db.scalar(select(Asset).where(Asset.id==asset_id,Asset.project_id==project.id))
    hits=deepcopy(query.hits);hit=next((h for h in hits if h['asset_id']==str(asset_id)),None)
    if not asset or not hit:raise HTTPException(404,'图片不在本次检索结果中')
    require_annotation_asset(project,asset)
    if hit.get('content_hash') and hit['content_hash']!=asset.content_hash:raise HTTPException(409,'图片内容已变更')
    if approved is None:
        hit.pop('ai_review',None)
        hit.update(judgment=judgment,user_id=user_id,judged_at=datetime.now(timezone.utc).isoformat(),
            review_status='pending',reviewer_id=None,reviewed_at=None)
    else:
        if hit['judgment']=='unknown':raise HTTPException(400,'尚未判定的匹配关系不能审核')
        hit.update(review_status='approved' if approved else 'rejected',reviewer_id=user_id,reviewed_at=datetime.now(timezone.utc).isoformat())
        if hit.get('ai_review'):hit['ai_review']={**hit['ai_review'],'status':'resolved'}
    query.hits=hits;query.version+=1
    record_event(db,asset,user_id,'match_judgment' if approved is None else 'match_review',
        dict(query_id=str(query.id),judgment=hit['judgment'],review_status=hit['review_status'],version=query.version))
    await db.commit()
    return await query_with_content(db,query)


async def recover_running(sessions):
    async with sessions() as db:
        neg_jobs = select(ProjectWorkJob.id).where(ProjectWorkJob.operation == 'embedding_negatives')
        await db.execute(update(ProjectWorkItem).where(ProjectWorkItem.status == 'running', ProjectWorkItem.job_id.in_(neg_jobs))
            .values(status='pending', attempt=ProjectWorkItem.attempt + 1, error=None))
        streaming_jobs = select(ProjectWorkJob.id).where(
            ProjectWorkJob.options['workflow_streaming'].as_boolean().is_(True),
            ProjectWorkJob.options['workflow_cancelled'].as_boolean().is_not(True),
            ProjectWorkJob.options['queue_deleted'].as_boolean().is_not(True),
        )
        await db.execute(update(ProjectWorkItem).where(
            ProjectWorkItem.status == 'running',
            ProjectWorkItem.job_id.in_(streaming_jobs),
            ProjectWorkItem.asset_id.is_not(None),
            ProjectWorkItem.attempt + 1 < MAX_AUTO_RETRY_ATTEMPTS,
        ).values(status='retry_wait', attempt=ProjectWorkItem.attempt + 1, error=INTERRUPTED_ERROR))
        await db.execute(update(ProjectWorkItem).where(ProjectWorkItem.status == 'running').values(
            status='failed', attempt=ProjectWorkItem.attempt + 1, error=INTERRUPTED_ERROR))
        # Migrate legacy failed auto-retry items to retry_wait so PostgreSQL never scans historical failed rows.
        retry_at = ProjectWorkItem.result['retry_at'].as_integer()
        await db.execute(update(ProjectWorkItem).where(
            ProjectWorkItem.status == 'failed',
            ProjectWorkItem.job_id.in_(streaming_jobs),
            ProjectWorkItem.asset_id.is_not(None),
            ProjectWorkItem.attempt < MAX_AUTO_RETRY_ATTEMPTS,
            or_(retry_at.is_not(None), ProjectWorkItem.error.in_(LEGACY_RETRY_ERRORS)),
        ).values(status='retry_wait'))
        await db.commit()


async def run_worker(sessions):
    # NodePort balances TCP connections; fresh requests avoid pinning batches to a replica.
    limits = httpx.Limits(max_connections=settings.PROJECT_WORK_CONCURRENCY, max_keepalive_connections=0)
    async with httpx.AsyncClient(follow_redirects=False, limits=limits) as client:
        in_flight = 0
        needs_recovery = False
        gate = asyncio.Lock()

        async def _run_recovery_locked():
            nonlocal needs_recovery
            needs_recovery = False
            try:
                await recover_running(sessions)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('Project annotation worker recovery error')

        async def consume():
            nonlocal in_flight, needs_recovery
            while True:
                async with gate:
                    if needs_recovery and in_flight == 0:
                        await _run_recovery_locked()
                    in_flight += 1
                busy = False
                failed = False
                try:
                    busy = await process_one(sessions, client)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    failed = True
                    logger.exception('Project annotation worker consumer error')
                finally:
                    async with gate:
                        if failed:
                            needs_recovery = True
                        in_flight -= 1
                        if needs_recovery and in_flight == 0:
                            await _run_recovery_locked()
                if not busy:
                    await asyncio.sleep(1)

        await asyncio.gather(*(consume() for _ in range(settings.PROJECT_WORK_CONCURRENCY)))


async def start_worker():
    global _worker
    if _worker and not _worker.done():return
    from database import AsyncSessionLocal
    from services.project_workflow import resume_waiting_workflows
    # shortcut: one backend process; use leases before running multiple workers.
    await resume_waiting_workflows(AsyncSessionLocal)
    await recover_running(AsyncSessionLocal)
    _worker=asyncio.create_task(run_worker(AsyncSessionLocal))


async def stop_worker():
    global _worker
    if _worker:
        _worker.cancel()
        try:await _worker
        except asyncio.CancelledError:pass
        _worker=None
