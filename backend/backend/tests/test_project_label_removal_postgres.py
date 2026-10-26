import asyncio
from time import monotonic
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import insert,select,func
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from sqlalchemy.pool import NullPool
from models import Base,Tenant,User,Project,Asset,Annotation,AnnotationEvent
from config.settings import settings
from schemas.project import ProjectLabelRemoval
from services.project_labels import label_removal_state,remove_project_label
from tests.test_database_closeout_postgres import isolated_pg


@pytest.mark.asyncio
async def test_concurrent_bulk_deletion_is_atomic_and_rechecks_preview_on_real_postgres(isolated_pg,request):
    sync_engine,_=isolated_pg
    tables=[Base.metadata.tables[name] for name in ('tenants','users','projects','images','assets','annotations','annotation_events')]
    project_id=uuid4();count=1280;ids=[uuid4() for _ in range(count)]
    box=dict(label='person',x=1,y=2,width=3,height=4);bike={**box,'label':'bike'}
    with sync_engine.begin() as db:
        Base.metadata.create_all(db,tables=tables)
        db.execute(insert(Tenant).values(id=1,tenant_name='isolated'))
        db.execute(insert(User).values(id=1,tenant_id=1,username='isolated',email='fixture@test',password_hash='fixture',role='admin',is_active=True))
        db.execute(insert(Project).values(id=project_id,tenant_id=1,project_name='isolated label cleanup',task_type='detection',
            label_schema={'labels':['person','bike']},total_assets=count,annotated_count=count,reviewed_count=count))
        db.execute(insert(Asset),[dict(id=id,project_id=project_id,original_name=str(id)+'.png',blob_path='fixture.png',content_hash=str(id),
            file_size=1,width=64,height=48,source_type='upload',annotation_status='reviewed') for id in ids])
        db.execute(insert(Annotation),[dict(asset_id=id,version=1,is_latest=True,review_status='approved',annotation_data={'boxes':[box,bike]}) for id in ids])
    engine=create_async_engine(make_url(settings.DATABASE_URL),poolclass=NullPool)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    try:
        async with sessions() as db:_,_,plan=await label_removal_state(db,project_id,1,'person')
        payload=ProjectLabelRemoval(label='person',preview_token=plan['preview_token'])
        async def apply():
            async with sessions() as db:
                try:
                    result=await remove_project_label(db,project_id,SimpleNamespace(id=1,tenant_id=1),payload)
                    await db.commit();return 200,result
                except HTTPException as error:
                    await db.rollback();return error.status_code,None
        started=monotonic();result=await asyncio.wait_for(asyncio.gather(apply(),apply()),180)
        elapsed=monotonic()-started
        assert sorted(status for status,_ in result)==[200,409]
        request.node.user_properties.extend([('affected_images',count),('concurrent_seconds',round(elapsed,2))])
        async with sessions() as db:
            project=await db.get(Project,project_id)
            assert project.annotated_count==count and project.reviewed_count==0
            latest=(await db.scalars(select(Annotation).where(Annotation.is_latest.is_(True)))).all()
            assert len(latest)==count and all(row.version==2 and row.review_status=='pending' and row.annotation_data['boxes']==[bike] for row in latest)
            assert await db.scalar(select(func.count()).select_from(Annotation))==count*2
            assert await db.scalar(select(func.count()).select_from(AnnotationEvent).where(AnnotationEvent.action=='remove_label'))==1
    finally:await engine.dispose()
