"""Ordered and DAG processing stages backed by the existing project work queue."""
from collections import defaultdict
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import Uuid, cast, func, insert, literal, or_, select, union, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from models import Asset
from models.project_work import ProjectWorkItem, ProjectWorkJob
from schemas.model_service import ModelServiceConfig
from schemas.object_attributes import AttributeSchema
from schemas.project_work import MODEL_WORKFLOW_OPERATIONS, OPERATION_TITLES, resolve_workflow_dag
from services.image_semantics import model_snapshot
from services.model_inference import supports_prompt
from services.object_attributes import validate_choice_schema


def batch_jobs(job):
    root = job.options.get('batch_root')
    if not root:
        return select(ProjectWorkJob.id).where(ProjectWorkJob.id == job.id)
    return select(ProjectWorkJob.id).where(
        ProjectWorkJob.project_id == job.project_id,
        or_(ProjectWorkJob.id == UUID(root), ProjectWorkJob.options['batch_root'].as_string() == root),
    )


def _derived_asset_ids_select(db, base_items):
    if db.bind.dialect.name == 'postgresql':
        value = func.jsonb_array_elements_text(ProjectWorkItem.result['asset_ids']).column_valued('crop_asset_id', joins_implicitly=True)
        output = cast(value, Uuid)
    else:
        value = func.json_each(ProjectWorkItem.result, '$.asset_ids').table_valued('value', joins_implicitly=True).c.value
        output = func.replace(value, '-', '')
    return base_items.with_only_columns(output, maintain_column_froms=True)


def output_ids(db, job):
    items = select(ProjectWorkItem.asset_id).where(
        ProjectWorkItem.job_id.in_(batch_jobs(job)), ProjectWorkItem.status == 'completed',
    )
    if job.operation in ('crop', 'detect_attributes', 'img_transform'):
        return _derived_asset_ids_select(db, items)
    if job.operation == 'script':
        derived_items = select(ProjectWorkItem.asset_id).where(
            ProjectWorkItem.job_id.in_(batch_jobs(job)),
            ProjectWorkItem.status == 'completed',
            ProjectWorkItem.result['asset_ids'].is_not(None),
        )
        direct_items = items.where(
            ProjectWorkItem.result['asset_ids'].is_(None),
            ProjectWorkItem.result['passed'].as_boolean().is_(True),
        )
        return select(union(_derived_asset_ids_select(db, derived_items), direct_items).subquery().c[0])
    if job.operation in ('detect', 'filter', 'quality', 'img_filter', 'box_clean'):
        return items.where(ProjectWorkItem.result['passed'].as_boolean().is_(True))
    if job.operation == 'attributes' and job.options.get('workflow_id'):
        return items.where(ProjectWorkItem.result['attribute_skipped'].as_boolean().is_not(True))
    return items


async def result_job(db, project, job_id, crop_only=False):
    job = await db.scalar(select(ProjectWorkJob).where(ProjectWorkJob.id == job_id, ProjectWorkJob.project_id == project.id))
    if not job:
        raise HTTPException(404, '处理任务不存在')
    if crop_only and job.operation not in ('crop', 'detect_attributes', 'img_transform'):
        raise HTTPException(400, '请选择产生裁剪图的任务')
    if not crop_only and 'workflow_id' not in job.options and job.operation not in ('crop', 'detect_attributes', 'img_transform'):
        raise HTTPException(400, '该任务没有可继续处理的输出')
    return job


async def dataset_scope(db, project, work_job_id, image_kind):
    conditions = []
    if image_kind != 'all':
        conditions.append(Asset.source_type == 'model_crop' if image_kind == 'crops' else Asset.source_type != 'model_crop')
    if work_job_id:
        job = await result_job(db, project, work_job_id)
        conditions.append(Asset.id.in_(output_ids(db, job)))
    return conditions


async def enqueue(db, job, inputs, deduplicate=False):
    item_id = func.gen_random_uuid() if db.bind.dialect.name == 'postgresql' else func.lower(func.hex(func.randomblob(16)))
    rows = select(item_id, literal(job.id, type_=Uuid), Asset.id, Asset.content_hash).where(
        Asset.project_id == job.project_id, *inputs,
    ).order_by(Asset.id)
    factory = (pg_insert if db.bind.dialect.name == 'postgresql' else sqlite_insert) if deduplicate else insert
    statement = factory(ProjectWorkItem).from_select(['id', 'job_id', 'asset_id', 'content_hash'], rows)
    if deduplicate:
        statement = statement.on_conflict_do_nothing(index_elements=['job_id', 'asset_id'])
    return (await db.execute(statement)).rowcount


async def create(db, project, request, user_id):
    if request.source_job_id:
        source = await result_job(db, project, request.source_job_id)
        if source.follow_uploads or source.options.get('workflow_started') is False or await db.scalar(
            select(ProjectWorkItem.id).where(
                ProjectWorkItem.job_id.in_(batch_jobs(source)),
                ProjectWorkItem.status.in_(('pending', 'running')),
            ).limit(1)
        ):
            raise HTTPException(409, '请等待来源任务完成并关闭自动接收新图')
        inputs = [Asset.id.in_(output_ids(db, source))]
    else:
        inputs = [] if request.image_kind == 'all' else [
            Asset.source_type == 'model_crop' if request.image_kind == 'crops' else Asset.source_type != 'model_crop'
        ]
    if not request.follow_uploads and not await db.scalar(select(Asset.id).where(Asset.project_id == project.id, *inputs).limit(1)):
        raise HTTPException(400, '当前输入范围没有图片')

    topo_keys, by_key, adj, entry_keys, resolved_edges = resolve_workflow_dag(request.steps, request.edges)
    root = uuid4()
    steps: list[ProjectWorkJob] = []
    jobs_by_key: dict[str, ProjectWorkJob] = {}

    for index, node_key in enumerate(topo_keys):
        step = by_key[node_key]
        step_label = step.title or OPERATION_TITLES.get(step.operation, step.operation)
        if step.operation in MODEL_WORKFLOW_OPERATIONS:
            detection = step.operation in ('crop', 'detect', 'preannotate')
            model = await model_snapshot(db, project.tenant_id, step.model_id, 'detection' if detection else 'caption')
            protocol = model['config']['protocol']
            if (step.operation in ('filter', 'quality', 'ai_review') and protocol != 'openai_chat') or (
                protocol == 'jev_decide' and step.operation != 'attributes'
            ):
                raise HTTPException(400, f'步骤「{step_label}」请选择支持该操作的视觉模型')
            if (step.attribute_prompt or step.prompt and step.operation != 'filter') and not supports_prompt(
                ModelServiceConfig(**model['config'])
            ):
                raise HTTPException(400, f'步骤「{step_label}」所选模型不支持自由提示词')
        else:
            protocol = None
            model = {
                'id': None,
                'revision': 0,
                'kind': 'script' if step.operation == 'script' else 'builtin',
                'config': {'name': 'Python 沙箱脚本' if step.operation == 'script' else '平台内置规则'},
            }

        dumped = step.model_dump(mode='json', exclude={'operation', 'model_id'}, exclude_none=True)
        dumped['node_key'] = node_key
        if step.title:
            dumped['title'] = step.title
        job = ProjectWorkJob(
            id=uuid4() if index else root,
            project_id=project.id,
            user_id=user_id,
            operation=step.operation,
            model=model,
            options={
                **dumped,
                'workflow_id': str(root),
                'workflow_step': index,
                'workflow_started': True,
                'workflow_name': request.name,
                'workflow_streaming': True,
                'workflow_can_follow': request.follow_uploads,
                'workflow_image_kind': request.image_kind,
                'workflow_previous': str(steps[-1].id) if steps and request.edges is None else None,
            },
        )
        if step.operation == 'attributes':
            if protocol not in ('openai_chat', 'jev_decide'):
                raise HTTPException(400, f'步骤「{step_label}」请选择视觉对话或 JEV Choice 模型')
            if step.attribute_templates:
                if protocol == 'jev_decide':
                    for template in step.attribute_templates:
                        validate_choice_schema(template.config)
                job.options.update(save_attributes=True, workflow_attribute_override=True)
            else:
                definition = request.attribute_schema or (project.label_schema or {}).get('object_attributes')
                if not definition:
                    raise HTTPException(400, '请先保存当前项目的目标与属性配置')
                schema = AttributeSchema.model_validate(definition)
                if protocol == 'jev_decide':
                    validate_choice_schema(schema)
                job.options.update(
                    attribute_schema=schema.model_dump(),
                    save_attributes=True,
                    workflow_attribute_override=request.attribute_schema is not None,
                )
        if step.operation == 'caption':
            job.options['save_description'] = project.task_type in ('caption', 'vector')
        if step.operation == 'quality':
            job.options['quality_scope'] = dict(
                task_type=project.task_type,
                annotation_scope=(project.label_schema or {}).get('annotation_scope', 'all'),
            )
        if step.operation == 'crop':
            job.options['save_input_boxes'] = project.task_type == 'detection' and step.save_input_boxes is True
        if step.operation == 'script' and step.script_config:
            if step.script_config.save_attributes:
                job.options['save_attributes'] = project.task_type in ('caption', 'vector')
            if step.script_config.save_description:
                job.options['save_description'] = project.task_type in ('caption', 'vector')
        steps.append(job)
        jobs_by_key[node_key] = job

    for node_key, job in jobs_by_key.items():
        routes = [{'handle': handle, 'target_job_id': str(jobs_by_key[tgt].id)} for handle, tgt in adj[node_key]]
        job.options['workflow_routes'] = routes
        if len(routes) == 1 and routes[0]['handle'] == 'default':
            job.options['workflow_next'] = routes[0]['target_job_id']

    entry_jobs = [jobs_by_key[k] for k in entry_keys]
    steps[0].options['workflow_source_job_id'] = str(request.source_job_id) if request.source_job_id else None
    steps[0].options['workflow_edges'] = [e.model_dump() for e in resolved_edges]
    steps[0].options['workflow_entry_job_ids'] = [str(j.id) for j in entry_jobs]

    db.add_all(steps)
    await db.flush()
    if request.follow_uploads:
        from services.upload_follow import set_follow_uploads
        await set_follow_uploads(db, steps[0], True)
    if request.image_kind != 'uploads':
        for entry_job in entry_jobs:
            await enqueue(db, entry_job, inputs, deduplicate=len(entry_jobs) > 1)
    await db.commit()
    return steps


async def history_steps(db, project, workflow_id, lock=False):
    query = select(ProjectWorkJob).where(
        ProjectWorkJob.project_id == project.id,
        ProjectWorkJob.options['workflow_id'].as_string() == str(workflow_id),
    ).order_by(ProjectWorkJob.id)
    steps = sorted(
        (await db.scalars(query.with_for_update() if lock else query)).all(),
        key=lambda job: job.options['workflow_step'],
    )
    if not steps or steps[0].id != workflow_id or steps[0].options.get('workflow_hidden'):
        raise HTTPException(404, '工作流不存在')
    return steps


async def delete_history(db, project, workflow_id):
    from services.project_work import retrying_items

    steps = await history_steps(db, project, workflow_id, lock=True)
    if any(
        job.follow_uploads or job.options.get('workflow_started') is False and not job.options.get('workflow_cancelled')
        for job in steps
    ):
        raise HTTPException(409, '请关闭自动接收，并在工作流结束或取消后删除')
    retry_ids = [job.id for job in steps if job.options.get('workflow_streaming') and not job.options.get('workflow_cancelled')]
    active = await db.scalar(
        select(ProjectWorkItem.id).where(
            ProjectWorkItem.job_id.in_([job.id for job in steps]),
            or_(ProjectWorkItem.status.in_(('pending', 'running')), ProjectWorkItem.job_id.in_(retry_ids) & retrying_items()),
        ).limit(1)
    )
    if active:
        raise HTTPException(409, '请在工作流结束或取消后删除')
    for job in steps:
        job.options = {**job.options, 'workflow_hidden': True}
    await db.commit()


async def cancel_steps(db, steps):
    from services.project_work import retrying_items

    for step in steps:
        step.follow_uploads = False
        step.options = {**step.options, 'workflow_cancelled': True}
    changed = await db.execute(
        update(ProjectWorkItem).where(
            ProjectWorkItem.job_id.in_([step.id for step in steps]),
            or_(ProjectWorkItem.status.in_(('pending', 'running')), retrying_items()),
        ).values(status='cancelled', attempt=ProjectWorkItem.attempt + 1, error='已取消')
    )
    await db.commit()
    return {'changed_count': changed.rowcount}


async def cancel(db, project, workflow_id):
    steps = (
        await db.scalars(
            select(ProjectWorkJob).where(
                ProjectWorkJob.project_id == project.id,
                ProjectWorkJob.options['workflow_id'].as_string() == str(workflow_id),
            ).order_by(ProjectWorkJob.id).with_for_update()
        )
    ).all()
    if not any(step.id == workflow_id and step.options.get('workflow_step') == 0 for step in steps):
        raise HTTPException(404, '工作流不存在')
    return await cancel_steps(db, steps)


def matched_route_targets(work: dict, result: dict) -> tuple[list[UUID], list[UUID]]:
    routes = work['options'].get('workflow_routes')
    if routes is None:
        next_id = work['options'].get('workflow_next')
        routes = [{'handle': 'default', 'target_job_id': next_id}] if next_id else []
    if not routes or result.get('attribute_skipped'):
        return [], []

    operation = work['operation']
    if operation in ('detect', 'filter', 'quality', 'img_filter', 'box_clean') and result.get('passed') is not True:
        return [], []
    if operation == 'script' and result.get('passed', True) is not True and not result.get('branch'):
        return [], []

    if operation in ('crop', 'img_transform') or (operation == 'script' and result.get('asset_ids') is not None):
        asset_ids = [UUID(aid) for aid in (result.get('asset_ids') or [])]
    else:
        asset_ids = [work['asset_id']] if work.get('asset_id') else []
    if not asset_ids:
        return [], []

    if operation == 'branch':
        active_handles = set(result.get('matched_branches') or ['else'])
    elif operation == 'script' and result.get('branch'):
        branches = result['branch'] if isinstance(result['branch'], list) else [str(result['branch'])]
        active_handles = set(branches) | ({'default'} if result.get('passed', True) else set())
    else:
        active_handles = {'default'}

    targets = [UUID(r['target_job_id']) for r in routes if r.get('handle') in active_handles and r.get('target_job_id')]
    return list(dict.fromkeys(targets)), asset_ids


async def forward(db, work, result):
    target_job_ids, ids = matched_route_targets(work, result)
    if not target_job_ids or not ids:
        return
    for target_id in target_job_ids:
        job = await db.get(ProjectWorkJob, target_id)
        if job and job.project_id == work['project_id'] and not job.options.get('workflow_cancelled'):
            await enqueue(db, job, [Asset.id.in_(ids)], deduplicate=True)


async def resume_waiting_workflows(sessions):
    async with sessions() as db:
        workflow_id = ProjectWorkJob.options['workflow_id'].as_string()
        waiting = select(workflow_id).where(
            ProjectWorkJob.options['workflow_started'].as_boolean().is_(False),
            ProjectWorkJob.options['workflow_cancelled'].as_boolean().is_not(True),
        )
        jobs = (
            await db.scalars(
                select(ProjectWorkJob).where(workflow_id.in_(waiting))
                .order_by(ProjectWorkJob.options['workflow_step'].as_integer()).with_for_update()
            )
        ).all()
        if jobs:
            await db.execute(
                update(ProjectWorkItem).where(
                    ProjectWorkItem.job_id.in_([job.id for job in jobs]),
                    ProjectWorkItem.status == 'running',
                ).values(status='pending', attempt=ProjectWorkItem.attempt + 1, error=None)
            )
        workflows = defaultdict(list)
        for job in jobs:
            workflows[job.project_id, job.options['workflow_id']].append(job)
        for steps in workflows.values():
            for index, job in enumerate(steps):
                next_job = steps[index + 1] if index + 1 < len(steps) else None
                job.options = {
                    **job.options,
                    'workflow_started': True,
                    'workflow_streaming': True,
                    'workflow_can_follow': False,
                    **({'workflow_next': str(next_job.id)} if next_job and 'workflow_routes' not in job.options else {}),
                }
                if next_job and not next_job.options.get('workflow_cancelled'):
                    await enqueue(db, next_job, [Asset.id.in_(output_ids(db, job))], deduplicate=True)
        await db.commit()
