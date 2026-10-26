"""Analytics uses isolated SQLite only; no business timing records are written."""
from datetime import datetime, timedelta, timezone
from importlib.util import find_spec
from uuid import uuid4

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker

from database import get_db_session
from models import Annotation, AnnotationEvent, Asset, Project, Tenant


def test_activity_model_contract():
    assert find_spec('models.work_session') is not None, 'persistent work activity model is missing'
    from models.work_session import WorkActivityInterval
    assert WorkActivityInterval.__tablename__ == 'work_activity_intervals'
    assert {'session_id', 'sequence', 'started_at', 'ended_at', 'activity'} <= set(WorkActivityInterval.__table__.columns.keys())


@pytest_asyncio.fixture
async def activity_table(test_engine):
    from models.work_session import WorkActivityInterval
    async with test_engine.begin() as connection:
        await connection.run_sync(WorkActivityInterval.__table__.create, checkfirst=True)


@pytest.fixture
def analytics_client(test_engine, test_user, activity_table):
    from api.analytics_routes import router
    app = FastAPI()
    app.include_router(router, prefix='/api/analytics')
    async def isolated_db():
        async with async_sessionmaker(test_engine, expire_on_commit=False)() as session:
            yield session
    app.dependency_overrides[get_db_session] = isolated_db
    with TestClient(app) as client:
        yield client


@pytest_asyncio.fixture
async def evidence(test_db, test_asset, test_user):
    """Three latest versions, an obsolete rejection, and mixed human/AI events."""
    at = datetime(2026, 10, 10, 4)
    old = Annotation(asset_id=test_asset.id, annotation_data={'boxes': []}, version=1,
                     is_latest=False, annotation_source='manual', review_status='rejected', created_at=at-timedelta(days=1))
    current = Annotation(asset_id=test_asset.id, annotation_data={'boxes': [{'label': '人'}, {'label': '人'}]},
                         version=2, is_latest=True, annotation_source='manual', annotator_id=1,
                         review_status='approved', reviewer_id=1, reviewed_at=at, created_at=at)
    test_db.add_all([old, current])
    others = []
    for i, (source, status) in enumerate([('model', 'approved'), ('manual', 'pending')]):
        asset = Asset(project_id=test_asset.project_id, original_name=f'{i}.jpg', blob_path=f'{i}.jpg',
                      content_hash=f'analytics-{i}', file_size=10, source_type='upload')
        test_db.add(asset)
        await test_db.flush()
        annotation = Annotation(asset_id=asset.id, annotation_data={'boxes': []}, version=1,
            is_latest=True, annotation_source=source, review_status=status, reviewer_id=None,
            review_comment='自动审核通过' if source == 'model' else None, created_at=at)
        test_db.add(annotation)
        others.append(annotation)
    await test_db.flush()
    events = [
        (old, 'reject', at-timedelta(days=1), {'version': 1}),
        (current, 'update', at, {'version': 2, 'previous_version': 1}),
        (current, 'approve', at+timedelta(seconds=10), {'version': 2}),
        (others[0], 'create', at, {'source': 'ai'}),
        (others[0], 'approve', at+timedelta(seconds=5), {'source': 'ai'}),
        (others[1], 'create', at+timedelta(hours=2), {}),
    ]
    for annotation, action, created_at, detail in events:
        test_db.add(AnnotationEvent(project_id=test_asset.project_id, asset_id=annotation.asset_id,
            user_id=1, action=action, detail={'annotation_id': str(annotation.id), **detail}, created_at=created_at))
    await test_db.commit()
    return at


def summary(client, headers, project_id, **params):
    response = client.get('/api/analytics', headers=headers, params={'project_id': str(project_id), **params})
    assert response.status_code == 200, response.text
    return response.json()


def test_latest_quality_manual_automatic_and_no_invented_time(analytics_client, auth_headers, test_asset, evidence):
    result = summary(analytics_client, auth_headers, test_asset.project_id,
                     start='2026-10-10T00:00:00Z', end='2026-10-11T00:00:00Z')
    quality = result['quality']
    assert quality['total'] == 3 and quality['approved'] == 2 and quality['pending'] == 1
    assert quality['rejected'] == 0 and quality['negative_samples'] == 2
    assert quality['categories'] == [{'label': '人', 'boxes': 2, 'assets': 1}]
    assert quality['sources'] == {'manual': 2, 'model': 1, 'unknown': 0}
    assert quality['review_origins'] == {'human': 1, 'automatic': 1, 'unknown': 0}
    assert quality['process_pass_rate'] == 1
    assert result['workload']['human_annotation_events'] == 2
    assert result['workload']['automatic_annotation_events'] == 1
    assert result['workload']['human_review_events'] == 1
    assert result['workload']['automatic_review_events'] == 1
    assert result['workload']['rework_events'] == 1
    assert result['efficiency']['active_seconds'] is None
    assert result['efficiency']['annotation_per_hour'] is None
    assert result['efficiency']['timing_status'] == 'not_collected'
    assert '独立测试集' in result['definitions']['quality']


def test_half_open_utc_time_filters_and_empty_period(analytics_client, auth_headers, test_asset, evidence):
    result = summary(analytics_client, auth_headers, test_asset.project_id,
                     start='2026-10-10T12:00:00+08:00', end='2026-10-10T12:00:10+08:00')
    assert result['quality']['total'] == 3
    assert result['workload']['human_review_events'] == 0
    assert result['workload']['automatic_review_events'] == 1
    empty = summary(analytics_client, auth_headers, test_asset.project_id, start='2026-10-11T00:00:00Z')
    assert empty['quality']['total'] == 0
    assert empty['workload']['human_annotation_events'] == 0


@pytest.mark.parametrize('params', [
    {'start': '2026-10-11T00:00:00Z', 'end': '2026-10-10T00:00:00Z'},
    {'start': '2026-10-11T00:00:00'}, {'start': 'bad-date'},
])
def test_invalid_time_filters(analytics_client, auth_headers, test_project, params):
    response = analytics_client.get('/api/analytics', headers=auth_headers,
                                    params={'project_id': str(test_project.id), **params})
    assert response.status_code == 422


@pytest_asyncio.fixture
async def other_project(test_db):
    tenant = Tenant(id=2, tenant_name='other')
    test_db.add(tenant)
    await test_db.flush()
    project = Project(tenant_id=2, project_name='private', task_type='detection')
    test_db.add(project)
    await test_db.commit()
    return project


def heartbeat(asset, **changes):
    end = datetime.now(timezone.utc) - timedelta(seconds=1)
    return {'project_id': str(asset.project_id), 'asset_id': str(asset.id), 'session_id': str(uuid4()),
            'sequence': 1, 'activity': 'annotation', 'started_at': (end-timedelta(seconds=20)).isoformat(),
            'ended_at': end.isoformat(), **changes}


def test_auth_and_tenant_scope(analytics_client, auth_headers, test_asset, other_project):
    assert analytics_client.get('/api/analytics').status_code == 401
    assert analytics_client.post('/api/analytics/activity', json=heartbeat(test_asset)).status_code == 401
    assert analytics_client.get('/api/analytics', headers=auth_headers,
        params={'project_id': str(other_project.id)}).status_code == 404
    assert analytics_client.post('/api/analytics/activity', headers=auth_headers,
        json=heartbeat(test_asset, project_id=str(other_project.id))).status_code == 404
    assert summary(analytics_client, auth_headers, test_asset.project_id)['quality']['total'] == 0


@pytest.mark.asyncio
async def test_idempotent_heartbeat_persistent_cap_and_multi_tab_union(analytics_client, auth_headers, test_asset, test_db):
    from models.work_session import WorkActivityInterval
    data = heartbeat(test_asset)
    first = analytics_client.post('/api/analytics/activity', headers=auth_headers, json=data)
    assert first.status_code == 200, first.text
    assert first.json()['accepted_seconds'] == 20
    duplicate = analytics_client.post('/api/analytics/activity', headers=auth_headers, json=data)
    assert duplicate.status_code == 200 and duplicate.json()['duplicate'] is True
    assert await test_db.scalar(select(func.count()).select_from(WorkActivityInterval)) == 1
    await test_db.commit()  # Release SQLite's read transaction without expiring fixture objects.
    overlap = {**data, 'session_id': str(uuid4())}
    assert analytics_client.post('/api/analytics/activity', headers=auth_headers, json=overlap).status_code == 200
    result = summary(analytics_client, auth_headers, test_asset.project_id)
    assert result['efficiency']['active_seconds'] == 20
    assert result['efficiency']['annotation_seconds'] == 20
    long = heartbeat(test_asset)
    long['started_at'] = (datetime.fromisoformat(long['ended_at'])-timedelta(seconds=90)).isoformat()
    capped = analytics_client.post('/api/analytics/activity', headers=auth_headers, json=long)
    assert capped.status_code == 200 and capped.json()['accepted_seconds'] == 30
    # Read all rows to prove capping was persisted, independent of wire response.
    rows = (await test_db.scalars(select(WorkActivityInterval))).all()
    assert max((r.ended_at-r.started_at).total_seconds() for r in rows) == 30


@pytest.mark.parametrize('changes', [
    {'sequence': 0}, {'sequence': True}, {'activity': 'automatic'}, {'session_id': 'bad'},
    {'started_at': '2026-10-10T01:00:00'},
    {'ended_at': '2099-01-01T00:00:00Z'},
    {'started_at': '2000-01-01T00:00:00Z', 'ended_at': '2000-01-01T00:00:01Z'},
])
def test_bad_heartbeat_rejected(analytics_client, auth_headers, test_asset, changes):
    response = analytics_client.post('/api/analytics/activity', headers=auth_headers,
                                     json=heartbeat(test_asset, **changes))
    assert response.status_code == 422, response.text


@pytest.mark.asyncio
async def test_interval_clipping_and_only_timed_manual_events_enter_rate(analytics_client, auth_headers, test_db, test_asset, evidence):
    from models.work_session import WorkActivityInterval
    at = evidence
    for activity in ('annotation', 'review'):
        test_db.add(WorkActivityInterval(project_id=test_asset.project_id, asset_id=test_asset.id, user_id=1,
            session_id=uuid4(), sequence=1, activity=activity, started_at=at-timedelta(seconds=10),
            ended_at=at+timedelta(seconds=20)))
    await test_db.commit()
    result = summary(analytics_client, auth_headers, test_asset.project_id,
                     start=at.replace(tzinfo=timezone.utc).isoformat(),
                     end=(at+timedelta(seconds=15)).replace(tzinfo=timezone.utc).isoformat())
    efficiency = result['efficiency']
    assert efficiency['active_seconds'] == 15  # Union across modes, too.
    assert efficiency['annotation_seconds'] == efficiency['review_seconds'] == 15
    assert efficiency['timed_annotation_events'] == efficiency['timed_review_events'] == 1
    assert efficiency['annotation_per_hour'] == 240
    assert efficiency['review_per_hour'] == 240
    assert efficiency['users'][0]['user_id'] == 1
    all_time = summary(analytics_client, auth_headers, test_asset.project_id)
    assert all_time['workload']['human_annotation_events'] == 2
    assert all_time['efficiency']['timed_annotation_events'] == 1


@pytest.mark.asyncio
async def test_explicit_event_source_wins_and_ai_review_evidence_is_not_counted_twice(
        analytics_client, auth_headers, test_db, test_asset, evidence):
    model = await test_db.scalar(select(Annotation).where(Annotation.annotation_source == 'model'))
    for action, detail in [('approve', {'source': 'manual'}), ('ai_review', {'source': 'ai', 'decision': 'needs_review'})]:
        test_db.add(AnnotationEvent(project_id=test_asset.project_id, asset_id=model.asset_id, user_id=1,
            action=action, detail={'annotation_id': str(model.id), **detail}, created_at=evidence))
    await test_db.commit()
    result = summary(analytics_client, auth_headers, test_asset.project_id,
                     start='2026-10-10T00:00:00Z', end='2026-10-11T00:00:00Z')
    assert result['workload']['human_review_events'] == 2
    assert result['workload']['automatic_review_events'] == 1


def test_heartbeat_cannot_spoof_user_or_target_unrelated_asset(analytics_client, auth_headers, test_asset):
    assert analytics_client.post('/api/analytics/activity', headers=auth_headers,
        json=heartbeat(test_asset, user_id=99)).status_code == 422
    assert analytics_client.post('/api/analytics/activity', headers=auth_headers,
        json=heartbeat(test_asset, asset_id=str(uuid4()))).status_code == 404


@pytest.mark.parametrize('source,comment', [('model', 'AI审核通过：完整'), ('manual', 'AI审核通过：完整')])
@pytest.mark.asyncio
async def test_ai_review_metadata_is_automatic_even_on_human_originals(
        analytics_client, auth_headers, test_db, test_asset, source, comment):
    test_db.add(Annotation(asset_id=test_asset.id, version=1, is_latest=True,
        annotation_data={'boxes': [{'label': '人'}]}, annotation_source=source,
        review_status='approved', reviewer_id=None, review_comment=comment))
    await test_db.commit()
    result = summary(analytics_client, auth_headers, test_asset.project_id)
    assert result['quality']['review_origins'] == {'human': 0, 'automatic': 1, 'unknown': 0}


@pytest.mark.asyncio
async def test_quality_review_origin_prefers_explicit_event_source_over_comment(
        analytics_client, auth_headers, test_db, test_asset):
    annotation = Annotation(asset_id=test_asset.id, version=1, is_latest=True,
        annotation_data={'boxes': [{'label': '人'}]}, annotation_source='manual',
        review_status='approved', reviewer_id=None, review_comment=None)
    test_db.add(annotation)
    await test_db.flush()
    event = AnnotationEvent(project_id=test_asset.project_id, asset_id=test_asset.id, user_id=1,
        action='approve', detail={'annotation_id': str(annotation.id), 'version': 1, 'source': 'ai'})
    test_db.add(event)
    await test_db.commit()
    result = summary(analytics_client, auth_headers, test_asset.project_id)
    assert result['quality']['review_origins']['automatic'] == 1
    assert result['workload']['human_review_events'] == 0
    event.detail = {**event.detail, 'source': 'manual'}
    annotation.review_comment = 'AI审核通过：旧意见'
    await test_db.commit()
    result = summary(analytics_client, auth_headers, test_asset.project_id)
    assert result['quality']['review_origins']['human'] == 1
