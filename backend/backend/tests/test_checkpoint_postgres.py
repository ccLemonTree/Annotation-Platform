"""GPU reservations serialize across training and validation in an owned test database."""
import asyncio
import json
from uuid import uuid4
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from sqlalchemy.pool import NullPool
from alembic import command
from tests.test_database_closeout_postgres import isolated_pg,pre_013


@pytest.mark.asyncio
async def test_validation_reservation_blocks_training_after_concurrent_commit(isolated_pg):
    from config.settings import settings
    from services.gpu_reservations import assert_free
    from fastapi import HTTPException
    engine,config=isolated_pg
    await asyncio.to_thread(pre_013,engine,config)
    await asyncio.to_thread(command.upgrade,config,'head')
    db_engine=create_async_engine(settings.DATABASE_URL,poolclass=NullPool)
    sessions=async_sessionmaker(db_engine,expire_on_commit=False)
    dataset,run=uuid4(),uuid4()
    try:
        async with sessions() as first,sessions() as second:
            await assert_free(first,['GPU-checkpoint'])
            await first.execute(text("INSERT INTO validation_sets(id,tenant_id,user_id,name,labels,source,samples,total) VALUES(:id,1,1,'test','[]','{}','[]',0)"),{'id':dataset})
            await first.execute(text("""INSERT INTO validation_runs(id,set_id,tenant_id,user_id,name,status,model_snapshot,config,metrics,total,processed,failed_count)
                VALUES(:id,:set,1,1,'test','pending',
                CAST(:snapshot AS jsonb),'{}','{}',0,0,0)"""),{'id':run,'set':dataset,'snapshot':json.dumps({'source':'checkpoint','checkpoint':{'device':'GPU-checkpoint'},'execution':{'active':True}})})
            waiting=asyncio.create_task(assert_free(second,['GPU-checkpoint']))
            await asyncio.sleep(.1);assert not waiting.done()
            await first.commit()
            with pytest.raises(HTTPException) as error:await waiting
            assert error.value.status_code==409
            await second.rollback()
            await assert_free(second,['GPU-other'])
    finally:await db_engine.dispose()
