from uuid import uuid4
import asyncio
import httpx
import pytest
import pytest_asyncio
from alembic import command
from sqlalchemy import inspect,text,insert,select
from sqlalchemy.ext.asyncio import create_async_engine
from models import Annotation,Asset,Base,Project,ProjectWorkItem
from tests.test_database_closeout_postgres import isolated_pg,pre_013,PROJECT_WORK_TABLES
from tests.test_project_work import work_env


@pytest_asyncio.fixture
async def test_engine(isolated_pg):
    engine=create_async_engine(isolated_pg[0].url.set(drivername='postgresql+asyncpg'))
    tables=[Base.metadata.tables[name] for name in ('tenants','users','projects','project_members',
        'images','assets','annotations','annotation_events')]
    async with engine.begin() as connection:
        await connection.run_sync(lambda db:Base.metadata.create_all(db,tables=tables))
    yield engine
    await engine.dispose()


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


@pytest.mark.asyncio
@pytest.mark.parametrize('whole_workflow',[False,True])
async def test_cancel_while_crop_and_caption_commit(work_env,monkeypatch,whole_workflow):
    from schemas.project_work import WorkflowCreate
    from services import project_work,project_workflow
    env=work_env
    env.project.task_type='caption';await env.db.commit()
    upstream,downstream=await project_workflow.create(env.db,env.project,WorkflowCreate(steps=[
        dict(operation='crop',model_id=env.models[0].id),
        dict(operation='caption',model_id=env.models[1].id),
    ]),env.user.id)
    asset=Asset(project_id=env.project.id,source_type='model_crop',original_name='caption.png',
        blob_path='caption.png',content_hash='caption-input',file_size=1,width=8,height=8)
    env.db.add(asset);await env.db.flush()
    item=ProjectWorkItem(job_id=downstream.id,asset_id=asset.id,content_hash=asset.content_hash,status='running',attempt=1)
    env.db.add(item);await env.db.commit()

    forwarding=asyncio.Event();caption_saving=asyncio.Event();release=asyncio.Event()
    forward,save=project_workflow.forward,project_work.save_annotation
    async def hold_forward(*args):
        forwarding.set()
        await release.wait()
        return await forward(*args)
    async def track_save(*args,**kwargs):
        if kwargs.get('kind')=='image':caption_saving.set()
        return await save(*args,**kwargs)
    async def detect(*args,**kwargs):
        return {'boxes':[dict(label='person',x=0,y=0,width=8,height=8)]}
    monkeypatch.setattr(project_workflow,'forward',hold_forward)
    monkeypatch.setattr(project_work,'save_annotation',track_save)
    monkeypatch.setattr(project_work,'infer',detect)

    pid=asyncio.get_running_loop().create_future()
    async def cancel():
        async with env.sessions() as db:
            pid.set_result(await db.scalar(text('SELECT pg_backend_pid()')))
            if whole_workflow:
                return await project_workflow.cancel(db,env.project,upstream.id)
            return await project_work.action_job(db,env.project,downstream.id,'cancel')
    async def wait_for_cancel_lock():
        backend_pid=await pid
        while True:
            async with env.sessions() as db:
                if await db.scalar(text("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=:pid"),{'pid':backend_pid}):
                    return
            await asyncio.sleep(.02)

    work=dict(item_id=item.id,job_id=downstream.id,asset_id=asset.id,attempt=1,project_id=env.project.id,
        content_hash=asset.content_hash,operation='caption',options=downstream.options,model=downstream.model,
        user_id=env.user.id,base_version=0,review_state=project_work.review_state(None))
    async with httpx.AsyncClient() as client:
        tasks=[asyncio.create_task(project_work.process_one(env.sessions,client))]
        try:
            await asyncio.wait_for(forwarding.wait(),5)
            tasks.append(asyncio.create_task(project_work.finish(env.sessions,work,{'description':'已完成'},None)))
            await asyncio.wait_for(caption_saving.wait(),5)
            tasks.append(asyncio.create_task(cancel()))
            await asyncio.wait_for(wait_for_cancel_lock(),5)
            release.set()
            _,_,cancelled=await asyncio.wait_for(asyncio.gather(*tasks),10)
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
    assert cancelled=={'changed_count':1}
    async with env.sessions() as db:
        items=(await db.scalars(select(ProjectWorkItem).where(ProjectWorkItem.job_id==downstream.id))).all()
        assert sorted(row.status for row in items)==['cancelled','completed']
        annotation=await db.scalar(select(Annotation).where(Annotation.asset_id==asset.id))
        assert annotation.annotation_data['description']=='已完成'
        states=(await db.scalars(select(ProjectWorkItem.status).where(ProjectWorkItem.job_id==upstream.id))).all()
        assert states==['completed']
