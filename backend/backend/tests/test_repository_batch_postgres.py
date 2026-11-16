"""Batch migration and database uniqueness use disposable PostgreSQL only."""
import json
from uuid import uuid4
import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from tests.test_database_closeout_postgres import isolated_pg,pre_013


@pytest.mark.parametrize('options,expected',[
    ({'dynamic_batch':False,'batch':8},'fixed:8'),
    ({'dynamic_batch':True,'min_batch':1,'opt_batch':4,'max_batch':8},'dynamic:1:4:8'),
])
def test_030_preserves_records_recovers_known_batch_and_enforces_variants(isolated_pg,options,expected):
    engine,config=isolated_pg
    pre_013(engine,config);command.upgrade(config,'029')
    run=uuid4();model=uuid4()
    statement="""INSERT INTO model_repository_items(id,tenant_id,user_id,run_id,name,version,format,gpu_model,tensorrt_version,entry_file,file_size,files,source,status)
        VALUES (:id,1,1,:run,'detector','v1','tensorrt','Tesla T4','10.14.1','model.engine',6,'[]',CAST(:source AS JSONB),'ready')"""
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO training_runs(id,tenant_id,user_id,name,kind,status,config,source,server,output_dir,inspection)
            VALUES (:id,1,1,'export','model_export','completed',CAST(:config AS JSONB),'{}','{}','/isolated','{}')"""),dict(id=run,config=json.dumps({'output':options})))
        conn.execute(text(statement),dict(id=model,run=run,source=json.dumps({'kind':'training'})))
        conn.execute(text(statement.replace("'detector'","'manual'")),dict(id=uuid4(),run=None,source=json.dumps({'kind':'upload'})))
        before=conn.execute(text("SELECT to_jsonb(m) FROM model_repository_items m ORDER BY id")).scalars().all()
    command.upgrade(config,'030')
    with engine.connect() as conn:
        assert conn.execute(text("SELECT to_jsonb(m)-'batch_profile' FROM model_repository_items m ORDER BY id")).scalars().all()==before
        assert conn.scalar(text('SELECT batch_profile FROM model_repository_items WHERE id=:id'),{'id':model})==expected
        assert conn.scalar(text("SELECT batch_profile FROM model_repository_items WHERE name='manual'"))==''
    insert=statement.replace('files,source,status)','files,source,status,batch_profile)').replace("CAST(:source AS JSONB),'ready')","CAST(:source AS JSONB),'ready',:profile)")
    with engine.begin() as conn:conn.execute(text(insert),dict(id=uuid4(),run=None,source='{}',profile='fixed:1'))
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:conn.execute(text(insert),dict(id=uuid4(),run=None,source='{}',profile=expected))
