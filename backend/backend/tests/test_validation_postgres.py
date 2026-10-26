"""Run durable validation against a disposable PostgreSQL database."""
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from tests.test_database_closeout_postgres import isolated_pg, bootstrap_module
from tests.test_validation import validation_tables, vclient, ready


@pytest_asyncio.fixture
async def test_engine(isolated_pg):
    sync_engine, _ = isolated_pg
    with sync_engine.begin() as connection:
        bootstrap_module().bootstrap_database(connection)
    engine = create_async_engine(sync_engine.url.set(drivername='postgresql+asyncpg'), poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_pg_job_locks_prevent_duplicate_inference(vclient, ready, auth_headers, test_engine):
    import asyncio
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from services import validation
    from tests.test_validation import create_run
    from unittest.mock import patch
    run = create_run(vclient, auth_headers, ready)
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def slow_infer(*args, **kwargs):
        calls.append(1)
        started.set()
        await release.wait()
        return {'boxes': [], 'warnings': ['模型未检出目标，不等于人工确认负样本']}

    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    with patch.object(validation, 'infer', slow_infer):
        task = asyncio.create_task(validation.process_one(sessions))
        await asyncio.wait_for(started.wait(), 5)
        assert await asyncio.wait_for(validation.process_one(sessions), 3) is False
        assert vclient.post('/api/validation/runs/' + run['id'] + '/cancel', headers=auth_headers).status_code == 200
        release.set()
        assert await task
    result = vclient.get('/api/validation/runs/' + run['id'], headers=auth_headers).json()
    assert len(calls) == 1
    assert result['status'] == 'canceled'
    assert result['metrics']['coverage']['succeeded'] == 0
    assert result['metrics']['coverage']['complete'] is False


@pytest.mark.asyncio
async def test_pg_claim_skips_an_actually_locked_run(vclient, ready, auth_headers, test_engine):
    import asyncio
    from uuid import UUID
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from models.validation import ValidationRun
    from services import validation
    from tests.test_validation import create_run
    run = create_run(vclient, auth_headers, ready)
    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    async with sessions() as locked:
        await locked.scalar(select(ValidationRun).where(ValidationRun.id == UUID(run['id'])).with_for_update())
        assert await asyncio.wait_for(validation.process_one(sessions), 3) is False


@pytest.mark.asyncio
async def test_pg_old_attempt_cannot_overwrite_retried_result(vclient, ready, auth_headers, test_engine):
    import asyncio
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from services import validation
    from tests.test_validation import create_run
    from unittest.mock import patch
    run = create_run(vclient, auth_headers, ready)
    started, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def infer_by_attempt(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            started.set()
            await release.wait()
            return {'boxes': [], 'warnings': []}
        return {'boxes': [dict(label='person', x=1, y=2, width=10, height=20, score=.9)], 'warnings': []}

    sessions = async_sessionmaker(test_engine, expire_on_commit=False)
    with patch.object(validation, 'infer', infer_by_attempt):
        old = asyncio.create_task(validation.process_one(sessions))
        await asyncio.wait_for(started.wait(), 5)
        endpoint = '/api/validation/runs/' + run['id']
        assert vclient.post(endpoint + '/cancel', headers=auth_headers).status_code == 200
        assert vclient.post(endpoint + '/retry', headers=auth_headers).status_code == 200
        assert await validation.process_one(sessions)
        release.set()
        assert await old
    result = vclient.get(endpoint, headers=auth_headers).json()
    assert result['metrics']['coverage']['complete'] is True
    assert (result['metrics']['tp'], result['metrics']['fp'], result['metrics']['fn']) == (1, 0, 0)
