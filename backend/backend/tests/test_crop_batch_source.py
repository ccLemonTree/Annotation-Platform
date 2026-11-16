from uuid import uuid4

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select

from tests.test_project_work import work_env


async def make_crop(s,boxes,asset_id=None):
    from schemas.project_work import WorkCreate
    from services import project_work as service
    job=await service.create_job(s.db,s.project,WorkCreate(operation='crop',model_id=s.models[0].id,
        asset_ids=[asset_id or s.asset.id],labels=['person'],expansion=0),s.user.id)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={
        'detections':[{'class':'person','bbox':box,'score':.9} for box in boxes]}))) as client:
        assert await service.process_one(s.sessions,client)
    return job


@pytest.mark.asyncio
async def test_batch_filter_keeps_exact_outputs_including_reused_crops_and_next_round(work_env):
    from api.asset_routes import asset_conditions
    from models import Asset
    from services import project_work as service
    s=work_env
    first=await make_crop(s,[[0,0,20,30],[20,0,40,30]])
    second=await make_crop(s,[[0,0,20,30],[40,0,60,30]])
    async with s.sessions() as db:
        async def outputs(job,**filters):
            conditions=await asset_conditions(db,s.project,'all','all',crop_job_id=job.id,**filters)
            return (await db.scalars(select(Asset).where(*conditions))).all()
        a=await outputs(first);b=await outputs(second)
        assert len(a)==len(b)==2
        assert len({x.id for x in a}&{x.id for x in b})==1,'A later batch must include reused crops'
        assert s.asset.id not in {x.id for x in a+b}
        assert {x.id for x in await outputs(first,crop_depth=1)}=={x.id for x in a}
        assert await outputs(first,crop_depth=2)==[]
        detail=await service.summary(db,await db.get(type(first),first.id))
        assert detail['target_labels']==['person']
    third=await make_crop(s,[[0,0,10,10]],a[0].id)
    async with s.sessions() as db:
        conditions=await asset_conditions(db,s.project,'all','all',crop_job_id=third.id)
        result=(await db.scalars(select(Asset).where(*conditions))).one()
        assert result.device_info['crop']['depth']==2
        assert result.device_info['crop']['parent_asset_id']==str(a[0].id)
        assert len(result.device_info['crop_chain'])==2


@pytest.mark.asyncio
async def test_batch_source_rejects_missing_foreign_and_non_crop_jobs(work_env):
    from api.asset_routes import asset_conditions
    from models import Project
    from models.project_work import ProjectWorkJob
    s=work_env
    other=Project(tenant_id=s.project.tenant_id,project_name='other',task_type='detection')
    s.db.add(other);await s.db.flush()
    foreign=ProjectWorkJob(project_id=other.id,user_id=s.user.id,operation='crop',model={},options={})
    caption=ProjectWorkJob(project_id=s.project.id,user_id=s.user.id,operation='caption',model={},options={})
    s.db.add_all([foreign,caption]);await s.db.commit()
    for id,code in [(uuid4(),404),(foreign.id,404),(caption.id,400)]:
        with pytest.raises(HTTPException) as error:await asset_conditions(s.db,s.project,'all','all',crop_job_id=id)
        assert error.value.status_code==code


@pytest.mark.asyncio
async def test_batch_source_with_no_completed_output_is_empty(work_env):
    from api.asset_routes import asset_conditions
    from models import Asset
    from models.project_work import ProjectWorkJob,ProjectWorkItem
    s=work_env
    job=ProjectWorkJob(project_id=s.project.id,user_id=s.user.id,operation='crop',model={},options={})
    s.db.add(job);await s.db.flush()
    s.db.add(ProjectWorkItem(job_id=job.id,asset_id=s.asset.id,content_hash=s.asset.content_hash,
        base_version=0,status='failed',result={'asset_ids':[str(s.asset.id)]}))
    await s.db.commit()
    conditions=await asset_conditions(s.db,s.project,'all','all',crop_job_id=job.id)
    assert (await s.db.scalars(select(Asset.id).where(*conditions))).all()==[]


@pytest.mark.asyncio
async def test_batch_filter_applies_to_list_and_full_selection_with_version_header(work_env,client,auth_headers):
    from models import Asset
    from models.project_work import ProjectWorkJob,ProjectWorkItem
    s=work_env
    job=ProjectWorkJob(project_id=s.project.id,user_id=s.user.id,operation='crop',model={},options={})
    crops=[Asset(project_id=s.project.id,source_type='model_crop',original_name=f'crop-{n}.png',
        blob_path=f'crop-{n}.png',content_hash=f'crop-{n}',file_size=1,width=20,height=30,annotation_status='pending') for n in range(2)]
    s.db.add_all([job,*crops]);await s.db.flush()
    s.db.add(ProjectWorkItem(job_id=job.id,asset_id=s.asset.id,content_hash=s.asset.content_hash,base_version=0,
        status='completed',result={'asset_ids':[str(a.id) for a in crops]}));await s.db.commit()
    params={'project_id':str(s.project.id),'crop_job_id':str(job.id),'image_kind':'crops'}
    listing=client.get('/api/assets',params={**params,'page_size':1},headers=auth_headers)
    selection=client.get('/api/assets/selection',params=params,headers=auth_headers)
    assert listing.status_code==selection.status_code==200
    assert listing.headers['X-Asset-Crop-Batch-Filter']==selection.headers['X-Asset-Crop-Batch-Filter']=='1'
    assert listing.json()['total']==selection.json()['total']==2
    assert listing.json()['items'][0]['id'] in selection.json()['asset_ids']
    assert client.get('/api/assets',params={**params,'crop_job_id':'not-a-uuid'},headers=auth_headers).status_code==422
