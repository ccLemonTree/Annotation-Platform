"""Workflow quality and observed human effort, never inferred annotation accuracy."""
from collections import Counter, defaultdict
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import and_, case, or_, select, cast, String, func, true
from sqlalchemy.dialects.postgresql import JSONB
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
    position=bisect_right(intervals,timestamp,key=lambda interval:interval[0])-1
    return position>=0 and timestamp<intervals[position][1]


def automatic_review(annotation):
    comment = annotation.review_comment or ''
    return annotation.reviewer_id is None and (comment.startswith('AI审核通过：') or '自动审核' in comment)


def count_rework(updates, rejects):
    earliest={}
    for event in rejects:
        version=event.version
        if version is not None:
            key=(event.asset_id,version)
            earliest[key]=min(earliest.get(key,event.created_at),event.created_at)
    return sum((event.asset_id,event.previous_version) in earliest and
        earliest[(event.asset_id,event.previous_version)]<=event.created_at for event in updates)


def _norm_id(value) -> str:
    return str(value).replace('-', '').lower() if value is not None else ''


async def annotation_analytics(db, user, project_id, window):
    if project_id:
        await scoped_project(db, project_id, user.tenant_id)
    scope = [Project.tenant_id == user.tenant_id]
    if project_id:
        scope.append(Project.id == project_id)

    def dated(column):
        return ([column >= window.start] if window.start else []) + ([column < window.end] if window.end else [])

    is_pg = db.get_bind().dialect.name == 'postgresql'
    ann_conditions = [*scope, *( [Annotation.project_id == project_id] if project_id else [] )]
    latest_conditions = [*ann_conditions, (Annotation.is_latest == true()), *dated(Annotation.created_at)]

    # 1. SQL GROUP BY for latest annotation status, source, and negative samples (no full annotation_data loaded)
    status_expr = func.coalesce(Annotation.review_status, 'pending')
    source_expr = case(
        (Annotation.annotation_source.in_(('manual', 'model', 'reuse')), Annotation.annotation_source),
        else_='unknown',
    )
    if is_pg:
        is_neg_expr = and_(
            func.jsonb_typeof(Annotation.annotation_data['boxes']) == 'array',
            func.jsonb_array_length(Annotation.annotation_data['boxes']) == 0,
        )
    else:
        is_neg_expr = and_(
            func.json_type(Annotation.annotation_data, '$.boxes') == 'array',
            func.json_array_length(Annotation.annotation_data, '$.boxes') == 0,
        )

    agg_rows = (await db.execute(
        select(
            status_expr,
            source_expr,
            func.count(),
            func.coalesce(func.sum(case((is_neg_expr, 1), else_=0)), 0),
        )
        .select_from(Annotation)
        .join(Project, Annotation.project_id == Project.id)
        .where(*latest_conditions)
        .group_by(status_expr, source_expr)
    )).all()

    status = Counter()
    sources = Counter()
    total_annotations = 0
    negative = 0
    for st, src, cnt, neg_cnt in agg_rows:
        status[st] += cnt
        sources[src] += cnt
        total_annotations += cnt
        negative += int(neg_cnt or 0)

    # 2. SQL GROUP BY for box categories & distinct asset counts
    category_list = []
    if total_annotations > 0:
        if is_pg:
            boxes_arr = case(
                (func.jsonb_typeof(Annotation.annotation_data['boxes']) == 'array', Annotation.annotation_data['boxes']),
                else_=cast('[]', JSONB),
            )
            box_elems = func.jsonb_array_elements(boxes_arr).table_valued('value')
            box_json = cast(box_elems.c.value, JSONB)
            label_col = box_json['label'].as_string()
            cat_rows = (await db.execute(
                select(label_col, func.count(), func.count(func.distinct(Annotation.asset_id)))
                .select_from(Annotation)
                .join(Project, Annotation.project_id == Project.id)
                .join(box_elems, true())
                .where(*latest_conditions, func.jsonb_typeof(box_json['label']) == 'string')
                .group_by(label_col)
                .order_by(label_col)
            )).all()
        else:
            boxes_src = case(
                (func.json_type(Annotation.annotation_data, '$.boxes') == 'array', Annotation.annotation_data),
                else_='{"boxes":[]}',
            )
            box_elems = func.json_each(boxes_src, '$.boxes').table_valued('value')
            label_col = func.json_extract(box_elems.c.value, '$.label')
            cat_rows = (await db.execute(
                select(label_col, func.count(), func.count(func.distinct(Annotation.asset_id)))
                .select_from(Annotation)
                .join(Project, Annotation.project_id == Project.id)
                .join(box_elems, true())
                .where(*latest_conditions, func.json_type(box_elems.c.value, '$.label') == 'text')
                .group_by(label_col)
                .order_by(label_col)
            )).all()
        category_list = [
            dict(label=lbl, boxes=int(box_cnt), assets=int(asset_cnt))
            for lbl, box_cnt, asset_cnt in cat_rows
            if isinstance(lbl, str)
        ]

    # 3. SQL Hash/Merge LEFT JOIN for review_origins (O(N+M), no correlated N^2 scan, no func.replace on PG)
    review_origins = Counter()
    reviewed = status['approved'] + status['rejected']
    if reviewed > 0:
        ev_src_col = AnnotationEvent.detail['source'].as_string()
        ev_ann_col = AnnotationEvent.detail['annotation_id'].as_string()
        ev_key_expr = ev_ann_col if is_pg else func.replace(ev_ann_col, '-', '')
        ann_key_expr = cast(Annotation.id, String) if is_pg else func.replace(cast(Annotation.id, String), '-', '')
        ranked_ev = (
            select(
                ev_key_expr.label('ann_key'),
                ev_src_col.label('src'),
                func.row_number().over(
                    partition_by=ev_key_expr,
                    order_by=(AnnotationEvent.created_at.desc(), AnnotationEvent.id.desc()),
                ).label('rn'),
            )
            .join(Project, Project.id == AnnotationEvent.project_id)
            .where(
                *scope,
                *( [AnnotationEvent.project_id == project_id] if project_id else [] ),
                AnnotationEvent.action.in_(('approve', 'reject')),
                ev_ann_col.is_not(None),
                ev_src_col.in_(('manual', 'ai', 'model')),
            )
            .subquery('ranked_rev_ev')
        )
        latest_rev_ev = (
            select(ranked_ev.c.ann_key, ranked_ev.c.src)
            .where(ranked_ev.c.rn == 1)
            .cte('latest_rev_ev')
        )
        auto_comment_cond = and_(
            Annotation.reviewer_id.is_(None),
            or_(
                func.coalesce(Annotation.review_comment, '').like('AI审核通过：%'),
                func.coalesce(Annotation.review_comment, '').like('%自动审核%'),
            ),
        )
        origin_expr = case(
            (latest_rev_ev.c.src == 'manual', 'human'),
            (latest_rev_ev.c.src.in_(('ai', 'model')), 'automatic'),
            (Annotation.reviewer_id.is_not(None), 'human'),
            (auto_comment_cond, 'automatic'),
            else_='unknown',
        )
        reviewed_subq = (
            select(origin_expr.label('origin'))
            .select_from(Annotation)
            .join(Project, Annotation.project_id == Project.id)
            .outerjoin(latest_rev_ev, latest_rev_ev.c.ann_key == ann_key_expr)
            .where(*latest_conditions, Annotation.review_status.in_(('approved', 'rejected')))
            .subquery()
        )
        origin_rows = (await db.execute(
            select(reviewed_subq.c.origin, func.count()).group_by(reviewed_subq.c.origin)
        )).all()
        for orig, cnt in origin_rows:
            review_origins[orig] += cnt

    quality = dict(
        total=total_annotations,
        approved=status['approved'],
        rejected=status['rejected'],
        pending=status['pending'],
        negative_samples=negative,
        sources={
            **{key: sources[key] for key in ('manual', 'model', 'unknown')},
            **({'reuse': sources['reuse']} if sources['reuse'] else {}),
        },
        review_origins={key: review_origins[key] for key in ('human', 'automatic', 'unknown')},
        process_pass_rate=status['approved'] / reviewed if reviewed else None,
        categories=category_list,
    )

    # 4. Scalar projection for AnnotationEvent (never load full detail JSONB TOAST)
    event_source = AnnotationEvent.detail['source'].as_string()
    event_ann_str = AnnotationEvent.detail['annotation_id'].as_string()
    event_prev_ver = AnnotationEvent.detail['previous_version'].as_integer()
    tracked_actions = ('create', 'update', 'approve', 'reject', 'match_judgment', 'match_review')
    event_base_where = [
        *scope,
        *( [AnnotationEvent.project_id == project_id] if project_id else [] ),
        *dated(AnnotationEvent.created_at),
    ]
    event_rows = (await db.execute(
        select(
            AnnotationEvent.id,
            AnnotationEvent.asset_id,
            AnnotationEvent.user_id,
            AnnotationEvent.action,
            AnnotationEvent.created_at,
            event_source,
            event_ann_str,
            event_prev_ver,
        )
        .join(Project, Project.id == AnnotationEvent.project_id)
        .where(
            *event_base_where,
            AnnotationEvent.action.in_(tracked_actions),
            or_(event_source.is_(None), event_source != 'reuse'),
        )
        .order_by(AnnotationEvent.created_at, AnnotationEvent.id)
    )).all()

    by_id = {}
    if event_rows and any(row[6] and row[5] not in ('manual', 'ai', 'model') for row in event_rows):
        if is_pg:
            missing_ids = (
                select(event_ann_str)
                .join(Project, Project.id == AnnotationEvent.project_id)
                .where(
                    *event_base_where,
                    AnnotationEvent.action.in_(tracked_actions),
                    event_ann_str.is_not(None),
                    or_(event_source.is_(None), event_source.not_in(('manual', 'ai', 'model', 'reuse'))),
                )
                .correlate(None)
            )
            ann_filter = cast(Annotation.id, String).in_(missing_ids)
        else:
            missing_ids = (
                select(func.replace(event_ann_str, '-', ''))
                .join(Project, Project.id == AnnotationEvent.project_id)
                .where(
                    *event_base_where,
                    AnnotationEvent.action.in_(tracked_actions),
                    event_ann_str.is_not(None),
                    or_(event_source.is_(None), event_source.not_in(('manual', 'ai', 'model', 'reuse'))),
                )
                .correlate(None)
            )
            ann_filter = func.replace(cast(Annotation.id, String), '-', '').in_(missing_ids)
        event_annotations = (await db.execute(
            select(Annotation.id, Annotation.annotation_source, Annotation.reviewer_id, Annotation.review_comment)
            .join(Project, Annotation.project_id == Project.id)
            .where(*ann_conditions, ann_filter)
        )).all()
        by_id = {_norm_id(a.id): a for a in event_annotations}

    workload = dict(human_annotation_events=0, automatic_annotation_events=0, human_review_events=0,
                    automatic_review_events=0, rework_events=0, unknown_events=0)
    human_events = []
    for ev_id, asset_id, ev_user_id, action, created_at, src, ann_id_str, prev_ver in event_rows:
        annotation = by_id.get(_norm_id(ann_id_str)) if ann_id_str else None
        activity = 'annotation' if action in ('create', 'update', 'match_judgment') else 'review'
        automatic = src in ('ai', 'model')
        if annotation is not None and src not in ('manual', 'ai', 'model'):
            automatic |= (annotation.annotation_source == 'model' if activity == 'annotation' else automatic_review(annotation))
        if automatic:
            workload[f'automatic_{activity}_events'] += 1
        elif ev_user_id and ev_user_id > 0:
            workload[f'human_{activity}_events'] += 1
            human_events.append((SimpleNamespace(
                id=ev_id, asset_id=asset_id, user_id=ev_user_id, action=action,
                created_at=created_at, previous_version=prev_ver,
            ), activity))
        else:
            workload['unknown_events'] += 1
    for activity in ('annotation', 'review'):
        workload[f'human_{activity}_images'] = len({e.asset_id for e, kind in human_events
            if kind == activity and e.asset_id is not None})
    update_events = [e for e, activity in human_events if activity == 'annotation' and e.action == 'update']
    if update_events:
        update_assets = (
            select(AnnotationEvent.asset_id)
            .join(Project, Project.id == AnnotationEvent.project_id)
            .where(*event_base_where, AnnotationEvent.action == 'update')
            .correlate(None)
        )
        reject_rows = (await db.execute(
            select(
                AnnotationEvent.asset_id,
                AnnotationEvent.created_at,
                AnnotationEvent.detail['version'].as_integer(),
            )
            .join(Project, Project.id == AnnotationEvent.project_id)
            .where(
                *scope,
                *( [AnnotationEvent.project_id == project_id] if project_id else [] ),
                AnnotationEvent.action == 'reject',
                AnnotationEvent.asset_id.in_(update_assets),
            )
        )).all()
        rejects = [SimpleNamespace(asset_id=r[0], created_at=r[1], version=r[2]) for r in reject_rows]
        workload['rework_events'] = count_rework(update_events, rejects)

    # 5. Stream scalar columns for WorkActivityInterval
    interval_scope = []
    if window.start:
        interval_scope.append(WorkActivityInterval.ended_at > window.start)
    if window.end:
        interval_scope.append(WorkActivityInterval.started_at < window.end)
    interval_where = [
        *scope,
        *( [WorkActivityInterval.project_id == project_id] if project_id else [] ),
        *interval_scope,
    ]
    interval_cols_query = (
        select(
            WorkActivityInterval.user_id,
            WorkActivityInterval.activity,
            WorkActivityInterval.started_at,
            WorkActivityInterval.ended_at,
        )
        .join(Project, Project.id == WorkActivityInterval.project_id)
        .where(*interval_where)
        .execution_options(yield_per=2000)
    )
    grouped = defaultdict(list)
    has_intervals = False
    async for uid, act, started_at, ended_at in await db.stream(interval_cols_query):
        has_intervals = True
        start = max(started_at, window.start) if window.start else started_at
        end = min(ended_at, window.end) if window.end else ended_at
        grouped[(uid, act)].append((start, end))
    grouped = {key: union_intervals(values) for key, values in grouped.items()}
    user_ids = {key[0] for key in grouped} | {e.user_id for e, _ in human_events}
    names = (
        dict((await db.execute(select(User.id, User.username).where(
            User.tenant_id == user.tenant_id, User.id.in_(sorted(user_ids))))).all())
        if user_ids else {}
    )
    rows = []
    for user_id in sorted(user_ids):
        annotation_intervals, review_intervals = grouped.get((user_id, 'annotation'), []), grouped.get((user_id, 'review'), [])
        row = dict(user_id=user_id, username=names.get(user_id, f'用户{user_id}'),
            active_seconds=seconds(union_intervals(annotation_intervals+review_intervals)) if annotation_intervals or review_intervals else None)
        for activity, activity_intervals in [('annotation', annotation_intervals), ('review', review_intervals)]:
            duration = seconds(activity_intervals)
            count = sum(e.user_id == user_id and kind == activity for e, kind in human_events)
            images = len({e.asset_id for e, kind in human_events
                if e.user_id == user_id and kind == activity and e.asset_id is not None})
            timed = sum(e.user_id == user_id and kind == activity and during(e.created_at, activity_intervals) for e, kind in human_events)
            row.update({f'{activity}_seconds': duration if activity_intervals else None, f'{activity}_events': count,
                f'{activity}_images': images,
                f'timed_{activity}_events': timed, f'{activity}_per_hour': timed*3600/duration if duration else None})
        rows.append(row)
    efficiency = dict(timing_status='collected' if has_intervals else 'not_collected', users=rows,
                      active_seconds=sum(r['active_seconds'] or 0 for r in rows) if has_intervals else None)
    for activity in ('annotation', 'review'):
        durations = [r[f'{activity}_seconds'] for r in rows if r[f'{activity}_seconds'] is not None]
        duration = sum(durations)
        count = sum(r[f'timed_{activity}_events'] for r in rows)
        efficiency.update({f'{activity}_seconds': duration if durations else None, f'timed_{activity}_events': count,
                           f'{activity}_per_hour': count*3600/duration if duration else None})
    return dict(quality=quality, workload=workload, efficiency=efficiency, definitions=dict(
        quality='最新版本按创建时间入选，展示当前流程状态；流程通过率=通过/(通过+拒绝)，不等于独立测试集准确率。不是历史时点快照。',
        workload='事件按发生时间计数（次），包含人工匹配判定/审核；人工图片张数按窗口内发生人工事件的图片去重，每用户分标注/审核去重，总量跨用户去重，不能按用户相加；无图片ID的事件不计张数。自动生成/自动审核单列；复用操作不计人工或自动工作量；返工为驳回版本后的人工修改事件。',
        timing='UTC半开区间[start,end)；每用户跨标签页、跨模式取时间并集，总时长为人秒；分模式并集不能相加。',
        efficiency='每小时工作量仅使用落在同用户同模式活跃区间的人工事件；未计时操作不进入分子。旧数据时长未采集，不按created_at差值估算。'))
