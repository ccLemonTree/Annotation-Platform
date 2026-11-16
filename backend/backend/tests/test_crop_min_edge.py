from io import BytesIO
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import select

from tests.test_project_work import work_env


@pytest.mark.parametrize('value',[0,1,32768])
def test_crop_minimum_long_edge_accepts_integer_pixels(value):
    from schemas.project_work import WorkCreate
    request=WorkCreate(operation='crop',model_id=uuid4(),asset_ids=[uuid4()],min_crop_long_edge=value)
    assert request.min_crop_long_edge==value


@pytest.mark.parametrize('value',[-1,32769,True,1.5,'20',None])
def test_crop_minimum_long_edge_rejects_invalid_values(value):
    from schemas.project_work import WorkCreate
    with pytest.raises(ValidationError):
        WorkCreate(operation='crop',model_id=uuid4(),asset_ids=[uuid4()],min_crop_long_edge=value)


async def crop(s,boxes,**options):
    from schemas.project_work import WorkCreate
    from services import project_work as service
    from models import Asset
    from models.project_work import ProjectWorkItem
    job=await service.create_job(s.db,s.project,WorkCreate(operation='crop',model_id=s.models[0].id,
        asset_ids=[s.asset.id],expansion=options.pop('expansion',0),**options),s.user.id)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={
        'detections':[{'class':'person','bbox':box,'score':.9} for box in boxes]}))) as client:
        assert await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
        assets=(await db.scalars(select(Asset).where(Asset.source_type=='model_crop'))).all()
        detail=await service.summary(db,await db.get(type(job),job.id),details=True)
    return job,item,assets,detail


@pytest.mark.asyncio
async def test_filters_small_crops_and_keeps_boundary_or_tall_crops_without_resizing(work_env,monkeypatch):
    from services.storage import get_storage
    from unittest.mock import AsyncMock
    storage=get_storage();save=AsyncMock(wraps=storage.save_file);monkeypatch.setattr(storage,'save_file',save)
    _,item,assets,detail=await crop(work_env,[[0,0,5,5],[10,0,40,10],[0,5,10,45]],min_crop_long_edge=30)
    assert item.status=='completed' and item.result['detected_count']==2 and item.result['filtered_count']==1
    assert detail['filtered_crop_count']==1 and detail['counts']=={'completed':1}
    assert {(asset.width,asset.height) for asset in assets}=={(30,10),(10,40)}
    assert save.call_count==2
    for call in save.call_args_list:
        with Image.open(BytesIO(call.args[0].getvalue())) as image:assert image.size in ((30,10),(10,40))


@pytest.mark.asyncio
@pytest.mark.parametrize('box,expansion,kept',[
    ([0,0,10,20],.5,True),([50,38,64,48],1,False)])
async def test_filter_uses_final_expanded_and_clipped_dimensions(work_env,box,expansion,kept):
    _,item,assets,_=await crop(work_env,[box],expansion=expansion,min_crop_long_edge=30)
    assert len(assets)==int(kept)
    assert item.result['filtered_count']==int(not kept)
    if kept:assert (assets[0].width,assets[0].height)==(15,30)


@pytest.mark.asyncio
async def test_all_filtered_is_completed_with_size_warning_and_no_asset_write(work_env,monkeypatch):
    from services.storage import get_storage
    from models import Project
    from unittest.mock import AsyncMock
    save=AsyncMock();monkeypatch.setattr(get_storage(),'save_file',save)
    _,item,assets,detail=await crop(work_env,[[0,0,5,5]],min_crop_long_edge=30)
    assert item.status=='completed' and not assets and not item.result['asset_ids']
    assert item.result['filtered_count']==1 and '尺寸' in item.result['warning']
    assert '未检测到目标' not in item.result['warning'] and detail['filtered_crop_count']==1
    assert save.call_count==0
    async with work_env.sessions() as db:assert (await db.get(Project,work_env.project.id)).total_assets==1


@pytest.mark.asyncio
async def test_new_threshold_does_not_delete_existing_small_crops(work_env):
    _,_,existing,_=await crop(work_env,[[0,0,5,5]])
    _,item,assets,_=await crop(work_env,[[0,0,5,5]],min_crop_long_edge=30)
    assert [asset.id for asset in assets]==[existing[0].id]
    assert item.result['filtered_count']==1 and not item.result['asset_ids']


@pytest.mark.asyncio
async def test_old_job_without_option_does_not_filter(work_env):
    from schemas.project_work import WorkCreate
    from services import project_work as service
    from models import Asset
    s=work_env
    job=await service.create_job(s.db,s.project,WorkCreate(operation='crop',model_id=s.models[0].id,asset_ids=[s.asset.id],expansion=0),s.user.id)
    job.options={key:value for key,value in job.options.items() if key!='min_crop_long_edge'};await s.db.commit()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={
        'detections':[{'class':'person','bbox':[0,0,5,5],'score':.9}]}))) as client:
        assert await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        assert await db.scalar(select(Asset.id).where(Asset.source_type=='model_crop'))


@pytest.mark.asyncio
@pytest.mark.parametrize('large_target',[False,True])
async def test_attribute_pipeline_only_queues_kept_crops(work_env,large_target):
    from schemas.project_work import WorkCreate
    from services import project_work as service
    from tests.test_object_attributes import schema
    from models.project_work import ProjectWorkJob,ProjectWorkItem
    from models import Asset
    s=work_env;s.project.task_type='caption';s.project.label_schema={'object_attributes':schema('person')};await s.db.commit()
    job=await service.create_job(s.db,s.project,WorkCreate(operation='detect_attributes',model_id=s.models[0].id,
        attribute_model_id=s.models[1].id,asset_ids=[s.asset.id],expansion=0,min_crop_long_edge=30),s.user.id)
    boxes=[[0,0,5,5]]+([[10,0,50,40]] if large_target else [])
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={
        'detections':[{'class':'person','bbox':box,'score':.9} for box in boxes]}))) as client:
        assert await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        child_ids=(await db.scalars(select(ProjectWorkItem.asset_id).join(ProjectWorkJob).where(ProjectWorkJob.operation=='attributes'))).all()
        crops=(await db.scalars(select(Asset).where(Asset.source_type=='model_crop'))).all()
        assert len(child_ids)==len(crops)==int(large_target)
        assert {asset.id for asset in crops}==set(child_ids)
        summary=await service.summary(db,await db.get(ProjectWorkJob,job.id))
        assert summary['filtered_crop_count']==1 and summary['pipeline']['generated_targets']==int(large_target)
