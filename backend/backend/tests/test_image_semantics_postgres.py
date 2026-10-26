"""Real SKIP LOCKED/JSONB/FK tests, scoped to an owned disposable database."""
import asyncio
import os
from uuid import uuid4,UUID
import httpx
import psycopg2
from psycopg2 import sql
import pytest
import pytest_asyncio
from sqlalchemy import select,func
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from tests.test_image_semantics import semantic, payload, provider
from tests.test_image_semantics import test_late_results_never_write

pytestmark=[pytest.mark.asyncio,pytest.mark.skipif(os.environ.get('SEMANTICS_POSTGRES_INTEGRATION')!='1',reason='requires explicit disposable PG integration')]


@pytest_asyncio.fixture
async def test_engine():
    from config.settings import settings
    from models import Base
    from models.image_semantic import ImageSemanticJob,ImageSemanticItem,ImageSemanticVector
    name='codex_semantics_'+uuid4().hex
    admin=psycopg2.connect(host=settings.DB_HOST,port=settings.DB_PORT,user=settings.DB_USER,password=settings.DB_PASSWORD,dbname=settings.DB_NAME)
    admin.autocommit=True;engine=None;created=False
    try:
        with admin.cursor() as cursor:cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
        created=True
        engine=create_async_engine(make_url(settings.DATABASE_URL).set(database=name),poolclass=NullPool)
        tables=[table for name,table in Base.metadata.tables.items() if name not in ('smarteye_tasks','smarteye_downloads')]
        async with engine.begin() as conn:await conn.run_sync(lambda sync:Base.metadata.create_all(sync,tables=tables))
        yield engine
    finally:
        if engine:await engine.dispose()
        if created:
            assert name.startswith('codex_semantics_') and len(name)==48
            with admin.cursor() as cursor:cursor.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
        admin.close()


async def test_concurrent_claims_cancel_and_recovery(semantic):
    from services.image_semantics import process_one,recover_running
    from models.image_semantic import ImageSemanticItem,ImageSemanticVector
    s=semantic;c=s['client']
    job=(await c.post('/api/image-semantics/jobs',json=payload(s))).json()
    started=asyncio.Event();release=asyncio.Event();calls=[]
    async def delayed(request):
        calls.append(request);started.set();await release.wait();return provider(request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(delayed)) as client:
        first=asyncio.create_task(process_one(s['sessions'],client))
        await asyncio.wait_for(started.wait(),5)
        assert await process_one(s['sessions'],client) is False
        assert (await c.post('/api/image-semantics/jobs/'+job['id']+'/cancel')).status_code==200
        release.set();assert await first
    async with s['sessions']() as db:
        assert await db.scalar(select(func.count()).select_from(ImageSemanticVector))==0
    await c.post('/api/image-semantics/jobs/'+job['id']+'/retry')
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:await process_one(s['sessions'],client)
    async with s['sessions']() as db:
        vector=await db.scalar(select(ImageSemanticVector));assert len(vector.vector)==2048
        item=await db.scalar(select(ImageSemanticItem));item.status='running';await db.commit()
    await recover_running(s['sessions'])
    assert (await c.get('/api/image-semantics/jobs/'+job['id'])).json()['counts']['failed']==1


async def test_skip_locked_skips_a_row_actually_locked_in_pg(semantic):
    from services.image_semantics import process_one
    from models.image_semantic import ImageSemanticItem
    s=semantic
    await s['client'].post('/api/image-semantics/jobs',json=payload(s))
    async with s['sessions']() as locked:
        await locked.scalar(select(ImageSemanticItem).with_for_update())
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            assert await asyncio.wait_for(process_one(s['sessions'],client),3) is False


async def test_retry_finishes_before_old_attempt_returns(semantic):
    from services.image_semantics import process_one
    from models.image_semantic import ImageSemanticVector
    s=semantic;c=s['client'];started=asyncio.Event();release=asyncio.Event()
    job=(await c.post('/api/image-semantics/jobs',json=payload(s))).json()
    async def late(request):
        started.set();await release.wait()
        if request.url.path.endswith('/embeddings'):
            return httpx.Response(200,json={'data':[{'embedding':[0.,1.]+[0.]*2046}]})
        return httpx.Response(200,json={'choices':[{'message':{'content':'old attempt'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(late)) as client:
        first=asyncio.create_task(process_one(s['sessions'],client));await asyncio.wait_for(started.wait(),5)
        await c.post('/api/image-semantics/jobs/'+job['id']+'/cancel')
        await c.post('/api/image-semantics/jobs/'+job['id']+'/retry')
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as second:await process_one(s['sessions'],second)
        release.set();await first
    async with s['sessions']() as db:
        row=await db.scalar(select(ImageSemanticVector))
        assert row.vector[0]==1 and row.caption=='一张红色图片'
