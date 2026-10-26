"""018 is tested only against fixture-owned disposable PostgreSQL databases."""
import pytest
from alembic import command
from sqlalchemy import inspect,text
from tests.test_database_closeout_postgres import isolated_pg,pre_013


def test_018_adds_exact_run_and_gpu_hold_schema_and_preserves_old_rows(isolated_pg):
    from models.training_run import TrainingRun,TrainingGpuHold
    engine,config=isolated_pg;pre_013(engine,config);command.upgrade(config,'017')
    with engine.connect() as db:before=db.scalar(text('SELECT tenant_name FROM tenants WHERE id=1'))
    command.upgrade(config,'018')
    with engine.connect() as db:
        inspector=inspect(db)
        for model in (TrainingRun,TrainingGpuHold):
            table=model.__table__
            columns=inspector.get_columns(table.name)
            assert {c['name'] for c in columns}==set(table.c.keys())
            for c in columns:
                assert c['type'].compile(dialect=db.dialect)==table.c[c['name']].type.compile(dialect=db.dialect)
                assert c['nullable']==table.c[c['name']].nullable
            assert {i['name'] for i in inspector.get_indexes(table.name)}=={i.name for i in table.indexes}
        assert db.scalar(text('SELECT tenant_name FROM tenants WHERE id=1'))==before
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='018'
    with pytest.raises(RuntimeError,match='不得|禁止'):command.downgrade(config,'017')


def test_018_refuses_existing_tables_without_overwriting(isolated_pg):
    engine,config=isolated_pg;pre_013(engine,config);command.upgrade(config,'017')
    with engine.begin() as db:
        db.execute(text('CREATE TABLE training_runs (marker TEXT)'))
        db.execute(text("INSERT INTO training_runs VALUES ('keep')"))
    with pytest.raises(RuntimeError,match='禁止覆盖'):command.upgrade(config,'018')
    with engine.connect() as db:
        assert db.scalar(text('SELECT marker FROM training_runs'))=='keep'
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='017'


def test_gpu_uuid_hold_is_unique_across_runs_and_cannot_delete_owner(isolated_pg):
    from sqlalchemy import insert
    from sqlalchemy.exc import IntegrityError
    from uuid import uuid4
    from models.training_run import TrainingRun,TrainingGpuHold
    engine,config=isolated_pg;pre_013(engine,config);command.upgrade(config,'head')
    first,second=uuid4(),uuid4()
    with engine.begin() as db:
        for id in (first,second):db.execute(insert(TrainingRun).values(id=id,tenant_id=1,user_id=1,name='test',kind='yolo26',status='starting',config={},source={},server={},output_dir='/tmp/'+str(id),inspection={}))
        db.execute(insert(TrainingGpuHold).values(gpu_uuid='GPU-owned',run_id=first))
    with pytest.raises(IntegrityError):
        with engine.begin() as db:db.execute(insert(TrainingGpuHold).values(gpu_uuid='GPU-owned',run_id=second))
    with pytest.raises(IntegrityError):
        with engine.begin() as db:db.execute(text('DELETE FROM training_runs WHERE id=:id'),{'id':first})
