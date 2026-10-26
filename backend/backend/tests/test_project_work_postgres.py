from uuid import uuid4
from alembic import command
from sqlalchemy import inspect,text,insert
from models import Base,Project
from tests.test_database_closeout_postgres import isolated_pg,pre_013,PROJECT_WORK_TABLES


def test_021_adds_project_work_and_keeps_existing_data(isolated_pg):
    engine,config=isolated_pg
    project=pre_013(engine,config);command.upgrade(config,'020')
    with engine.connect() as db:
        before=db.execute(text('SELECT id,project_name,task_type FROM projects')).all()
    command.upgrade(config,'021')
    with engine.begin() as db:
        inspector=inspect(db)
        for name in PROJECT_WORK_TABLES:
            expected=Base.metadata.tables[name]
            assert {c['name'] for c in inspector.get_columns(name)}==set(expected.c.keys())
            assert {i['name'] for i in inspector.get_indexes(name) if not i.get('duplicates_constraint')}=={i.name for i in expected.indexes}
            assert len(inspector.get_foreign_keys(name))==len(expected.foreign_key_constraints)
        assert db.execute(text('SELECT id,project_name,task_type FROM projects')).all()==before
        for task in ['caption','vector']:
            db.execute(insert(Project).values(id=uuid4(),tenant_id=1,project_name=task,task_type=task))
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='021'
