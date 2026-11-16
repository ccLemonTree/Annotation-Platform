"""Repository grouping migration uses a disposable PostgreSQL database only."""
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from tests.test_database_closeout_postgres import isolated_pg,pre_013


def test_031_preserves_records_and_keeps_component_identity_unique(isolated_pg):
    engine,config=isolated_pg
    pre_013(engine,config);command.upgrade(config,'030')
    id=uuid4()
    with engine.begin() as conn:
        conn.execute(text("""INSERT INTO model_repository_items(id,tenant_id,user_id,name,version,format,gpu_model,tensorrt_version,entry_file,file_size,files,source,status)
            VALUES (:id,1,1,'sam3-backbone-tensorrt','v1','tensorrt','Tesla T4','10.14.1','model.engine',7,'[]','{"kind":"upload"}','ready')"""),{'id':id})
        before=conn.execute(text('SELECT to_jsonb(m) FROM model_repository_items m')).scalars().all()
    command.upgrade(config,'031')
    with engine.connect() as conn:
        assert conn.execute(text("SELECT to_jsonb(m)-'group_name' FROM model_repository_items m")).scalars().all()==before
        assert conn.scalar(text('SELECT group_name FROM model_repository_items'))==''
    with engine.begin() as conn:
        conn.execute(text("UPDATE model_repository_items SET group_name='sam3' WHERE id=:id"),{'id':id})
        conn.execute(text("""INSERT INTO model_repository_items(id,tenant_id,user_id,name,group_name,version,format,gpu_model,tensorrt_version,entry_file,file_size,files,source,status)
            VALUES (:id,1,1,'sam3-text_encoder-tensorrt','sam3','v1','tensorrt','Tesla T4','10.14.1','model.engine',7,'[]','{}','ready')"""),{'id':uuid4()})
    with pytest.raises(RuntimeError,match='显式模型组'):command.downgrade(config,'030')
    with engine.connect() as conn:
        assert conn.scalar(text('SELECT version_num FROM alembic_version'))=='031'
        assert conn.scalar(text("SELECT count(*) FROM model_repository_items WHERE group_name='sam3'"))==2
