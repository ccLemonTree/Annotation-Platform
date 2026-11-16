"""Description migration must preserve existing artifacts in disposable PostgreSQL."""
from uuid import uuid4
from alembic import command
from sqlalchemy import inspect,text
from tests.test_database_closeout_postgres import isolated_pg,pre_013


def test_029_preserves_existing_repository_record(isolated_pg):
    engine,config=isolated_pg
    pre_013(engine,config);command.upgrade(config,'028')
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO model_repository_items(id,tenant_id,user_id,name,version,format,gpu_model,tensorrt_version,entry_file,file_size,files,source,status) VALUES (:id,1,1,'detector','v1','pt','','','best.pt',7,'[]','{}','ready')"),{'id':uuid4()})
        before=conn.execute(text('SELECT id,name,version,format,files,source FROM model_repository_items')).all()
    command.upgrade(config,'029')
    with engine.connect() as conn:
        assert conn.execute(text('SELECT id,name,version,format,files,source FROM model_repository_items')).all()==before
        assert conn.execute(text('SELECT description FROM model_repository_items')).scalar_one()==''
        assert not next(c for c in inspect(conn).get_columns('model_repository_items') if c['name']=='description')['nullable']
        assert conn.scalar(text('SELECT version_num FROM alembic_version'))=='029'
