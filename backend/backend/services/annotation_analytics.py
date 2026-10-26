"""Workflow quality and observed human effort, never inferred annotation accuracy."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from models import Annotation, AnnotationEvent, Asset, Project, User
from models.work_session import WorkActivityInterval

MAX_INTERVAL_SECONDS = 30
MAX_HEARTBEAT_AGE_SECONDS = 120


async def scoped_project(db, project_id, tenant_id):
    project = await db.scalar(select(Project).where(Project.id == project_id, Project.tenant_id == tenant_id))
    if project is None:
        raise HTTPException(404, '项目不存在')
    return project


async def record_activity(db, user, data):
    await scoped_project(db, data.project_id, user.tenant_id)
    asset = await db.scalar(select(Asset).where(Asset.id == data.asset_id, Asset.project_id == data.project_id))
    if asset is None:
        raise HTTPException(404, '项目图片不存在')
    key = (WorkActivityInterval.user_id == user.id, WorkActivityInterval.session_id == data.session_id,
           WorkActivityInterval.sequence == data.sequence)
    existing = await db.scalar(select(WorkActivityInterval).where(*key))
    if existing is not None:
        return dict(duplicate=True, accepted_seconds=(existing.ended_at-existing.started_at).total_seconds())
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if data.ended_at > now + timedelta(seconds=5) or data.ended_at < now-timedelta(seconds=MAX_HEARTBEAT_AGE_SECONDS):
        raise HTTPException(422, '心跳必须在最近120秒内，不能来自未来')
    end = min(data.ended_at, now)
    start = max(data.started_at, end-timedelta(seconds=MAX_INTERVAL_SECONDS))
    if start >= end:
        raise HTTPException(422, '无有效活跃区间')
    interval = WorkActivityInterval(project_id=data.project_id, asset_id=data.asset_id, user_id=user.id,
        session_id=data.session_id, sequence=data.sequence, activity=data.activity, started_at=start, ended_at=end)
    try:
        async with db.begin_nested():
            db.add(interval)
            await db.flush()
    except IntegrityError:
        existing = await db.scalar(select(WorkActivityInterval).where(*key))
        if existing is None:
            raise
        return dict(duplicate=True, accepted_seconds=(existing.ended_at-existing.started_at).total_seconds())
    return dict(duplicate=False, accepted_seconds=(end-start).total_seconds())


def union_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def seconds(intervals):
    return sum((end-start).total_seconds() for start, end in intervals)


def during(timestamp, intervals):
    return any(start <= timestamp < end for start, end in intervals)


def automatic_review(annotation):
    comment = annotation.review_comment or ''
    return annotation.reviewer_id is None and (comment.startswith('AI审核通过：') or '自动审核' in comment)


async def annotation_analytics(db, user, project_id, window):
    if project_id:
        await scoped_project(db, project_id, user.tenant_id)
    scope = [Project.tenant_id == user.tenant_id]
    if project_id:
        scope.append(Project.id == project_id)

    def dated(column):
        return ([column >= window.start] if window.start else []) + ([column < window.end] if window.end else [])

    # shortcut: aggregate scoped evidence in Python; move distributions into SQL when project volume requires it.
    annotations = (await db.scalars(select(Annotation).join(Asset).join(Project).where(
        *scope, Annotation.is_latest.is_(True), *dated(Annotation.created_at)))).all()
    status = Counter(a.review_status or 'pending' for a in annotations)
    sources = Counter(a.annotation_source if a.annotation_source in ('manual', 'model') else 'unknown' for a in annotations)
    categories, category_assets = Counter(), Counter()
    negative = 0
    review_origins = Counter()
    reviewed_ids = [str(a.id) for a in annotations if a.review_status in ('approved', 'rejected')]
    review_sources = {}
    if reviewed_ids:
        review_events = (await db.scalars(select(AnnotationEvent).join(Project, Project.id == AnnotationEvent.project_id)
            .where(*scope, AnnotationEvent.action.in_(['approve', 'reject']),
                   AnnotationEvent.detail['annotation_id'].as_string().in_(reviewed_ids))
            .order_by(AnnotationEvent.created_at.desc(), AnnotationEvent.id.desc()))).all()
        for event in review_events:
            detail = event.detail or {}
            if detail.get('source') in ('manual', 'ai', 'model'):
                review_sources.setdefault(str(detail.get('annotation_id')), detail['source'])
    for annotation in annotations:
        data = annotation.annotation_data or {}
        boxes = data.get('boxes')
        if isinstance(boxes, list):
            negative += int(len(boxes) == 0)
            labels = [b['label'] for b in boxes if isinstance(b, dict) and isinstance(b.get('label'), str)]
            categories.update(labels)
            category_assets.update(set(labels))
        if annotation.review_status in ('approved', 'rejected'):
            source = review_sources.get(str(annotation.id))
            if source:
                origin = 'human' if source == 'manual' else 'automatic'
            else:
                origin = 'human' if annotation.reviewer_id is not None else ('automatic' if automatic_review(annotation) else 'unknown')
            review_origins[origin] += 1
    reviewed = status['approved'] + status['rejected']
    quality = dict(total=len(annotations), approved=status['approved'], rejected=status['rejected'], pending=status['pending'],
        negative_samples=negative, sources={key: sources[key] for key in ('manual', 'model', 'unknown')},
        review_origins={key: review_origins[key] for key in ('human', 'automatic', 'unknown')},
        process_pass_rate=status['approved']/reviewed if reviewed else None,
        categories=[dict(label=label, boxes=count, assets=category_assets[label]) for label, count in sorted(categories.items())])

    events = (await db.scalars(select(AnnotationEvent).join(Project, Project.id == AnnotationEvent.project_id)
        .where(*scope, *dated(AnnotationEvent.created_at)).order_by(AnnotationEvent.created_at, AnnotationEvent.id))).all()
    annotation_ids = set()
    for event in events:
        try:
            annotation_ids.add(UUID(str((event.detail or {}).get('annotation_id'))))
        except (ValueError, TypeError):
            pass
    event_annotations = (await db.scalars(select(Annotation).join(Asset).join(Project)
        .where(*scope, Annotation.id.in_(annotation_ids)))).all() if annotation_ids else []
    by_id = {str(a.id): a for a in event_annotations}
    workload = dict(human_annotation_events=0, automatic_annotation_events=0, human_review_events=0,
                    automatic_review_events=0, rework_events=0, unknown_events=0)
    human_events = []
    for event in events:
        if event.action not in ('create', 'update', 'approve', 'reject'):
            continue
        detail = event.detail or {}
        annotation = by_id.get(str(detail.get('annotation_id')))
        activity = 'annotation' if event.action in ('create', 'update') else 'review'
        automatic = detail.get('source') in ('ai', 'model')
        if annotation is not None and detail.get('source') not in ('manual', 'ai', 'model'):
            automatic |= (annotation.annotation_source == 'model' if activity == 'annotation' else automatic_review(annotation))
        if automatic:
            workload[f'automatic_{activity}_events'] += 1
        elif event.user_id and event.user_id > 0:
            workload[f'human_{activity}_events'] += 1
            human_events.append((event, activity))
        else:
            workload['unknown_events'] += 1
    update_events = [e for e, activity in human_events if activity == 'annotation' and e.action == 'update']
    if update_events:
        rejects = (await db.scalars(select(AnnotationEvent).join(Project, Project.id == AnnotationEvent.project_id)
            .where(*scope, AnnotationEvent.action == 'reject', AnnotationEvent.asset_id.in_({e.asset_id for e in update_events})))).all()
        workload['rework_events'] = sum(any(r.asset_id == e.asset_id and r.created_at <= e.created_at
            and (r.detail or {}).get('version') == (e.detail or {}).get('previous_version')
            and (r.detail or {}).get('version') is not None for r in rejects) for e in update_events)

    interval_scope = []
    if window.start:
        interval_scope.append(WorkActivityInterval.ended_at > window.start)
    if window.end:
        interval_scope.append(WorkActivityInterval.started_at < window.end)
    intervals = (await db.scalars(select(WorkActivityInterval).join(Project, Project.id == WorkActivityInterval.project_id)
        .where(*scope, *interval_scope))).all()
    grouped = defaultdict(list)
    for interval in intervals:
        start = max(interval.started_at, window.start) if window.start else interval.started_at
        end = min(interval.ended_at, window.end) if window.end else interval.ended_at
        grouped[(interval.user_id, interval.activity)].append((start, end))
    grouped = {key: union_intervals(values) for key, values in grouped.items()}
    user_ids = {key[0] for key in grouped} | {e.user_id for e, _ in human_events}
    names = dict((await db.execute(select(User.id, User.username).where(User.tenant_id == user.tenant_id, User.id.in_(user_ids)))).all())
    rows = []
    for user_id in sorted(user_ids):
        annotation_intervals, review_intervals = grouped.get((user_id, 'annotation'), []), grouped.get((user_id, 'review'), [])
        row = dict(user_id=user_id, username=names.get(user_id, f'用户{user_id}'),
            active_seconds=seconds(union_intervals(annotation_intervals+review_intervals)) if annotation_intervals or review_intervals else None)
        for activity, activity_intervals in [('annotation', annotation_intervals), ('review', review_intervals)]:
            duration = seconds(activity_intervals)
            count = sum(e.user_id == user_id and kind == activity for e, kind in human_events)
            timed = sum(e.user_id == user_id and kind == activity and during(e.created_at, activity_intervals) for e, kind in human_events)
            row.update({f'{activity}_seconds': duration if activity_intervals else None, f'{activity}_events': count,
                f'timed_{activity}_events': timed, f'{activity}_per_hour': timed*3600/duration if duration else None})
        rows.append(row)
    efficiency = dict(timing_status='collected' if intervals else 'not_collected', users=rows,
                      active_seconds=sum(r['active_seconds'] or 0 for r in rows) if intervals else None)
    for activity in ('annotation', 'review'):
        durations = [r[f'{activity}_seconds'] for r in rows if r[f'{activity}_seconds'] is not None]
        duration = sum(durations)
        count = sum(r[f'timed_{activity}_events'] for r in rows)
        efficiency.update({f'{activity}_seconds': duration if durations else None, f'timed_{activity}_events': count,
                           f'{activity}_per_hour': count*3600/duration if duration else None})
    return dict(quality=quality, workload=workload, efficiency=efficiency, definitions=dict(
        quality='最新版本按创建时间入选，展示当前流程状态；流程通过率=通过/(通过+拒绝)，不等于独立测试集准确率。不是历史时点快照。',
        workload='事件按发生时间计数（次，非去重图片数）；自动生成/自动审核单列；返工为驳回版本后的人工修改事件。',
        timing='UTC半开区间[start,end)；每用户跨标签页、跨模式取时间并集，总时长为人秒；分模式并集不能相加。',
        efficiency='每小时工作量仅使用落在同用户同模式活跃区间的人工事件；未计时操作不进入分子。旧数据时长未采集，不按created_at差值估算。'))
