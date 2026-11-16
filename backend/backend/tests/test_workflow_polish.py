import asyncio

import pytest
from fastapi import HTTPException

from tests.test_project_work import work_env
from tests.test_project_reuse import reusable
from tests.test_smarteye_management import managed


@pytest.mark.asyncio
@pytest.mark.parametrize('service',['automation','image_semantics'])
async def test_current_offline_model_cannot_create_annotation_task(work_env,service):
    from services import automation,image_semantics
    s=work_env;model=s.models[0]
    model.connection_check={'status':'offline','checked_revision':model.revision,'detail':'offline'}
    await s.db.commit()
    with pytest.raises(HTTPException,match='离线'):
        if service=='automation':await automation.model_snapshot(s.db,model.id,s.user.tenant_id,'detection')
        else:await image_semantics.model_snapshot(s.db,s.user.tenant_id,model.id,'detection')


@pytest.mark.asyncio
async def test_old_configuration_offline_result_does_not_block_new_model(work_env):
    from services.image_semantics import model_snapshot
    s=work_env;model=s.models[0]
    model.connection_check={'status':'offline','checked_revision':model.revision-1}
    await s.db.commit()
    assert (await model_snapshot(s.db,s.user.tenant_id,model.id,'detection'))['id']==str(model.id)


@pytest.mark.asyncio
async def test_image_platform_and_device_filters_match_same_source_and_selection(client,auth_headers,reusable,test_db):
    source,_,images,assets,_=reusable
    images[0].device_info={'sources':[{'platform_name':'平台一','device_name':'门口'},{'platform_name':'平台二','device_name':'仓库'}]}
    images[2].device_info={'platform_name':'平台二','device_name':'门口'}
    assets[0].device_info=images[0].device_info
    assets[2].device_info=images[2].device_info
    await test_db.commit()
    params={'platform_name':'平台二','device_name':'门口','page_size':1}
    listing=client.get('/api/images',headers=auth_headers,params=params)
    selection=client.get('/api/images/selection',headers=auth_headers,params=params)
    assert listing.status_code==selection.status_code==200
    assert listing.json()['total']==selection.json()['total']==1
    assert listing.json()['items'][0]['id']==str(images[2].id)
    assert selection.json()['image_ids']==[str(images[2].id)]
    assert client.get('/api/images/selection',headers=auth_headers,params={'device_name':'%'}).json()['total']==0
    platforms=client.get('/api/images/source-platforms',headers=auth_headers)
    assert platforms.status_code==200
    assert set(platforms.json())=={'平台一','平台二'}
    params['project_id']=str(source.id)
    project_images=client.get('/api/assets',headers=auth_headers,params=params)
    project_selection=client.get('/api/assets/selection',headers=auth_headers,params=params)
    assert project_images.json()['total']==project_selection.json()['total']==1
    assert project_selection.json()['asset_ids']==[str(assets[2].id)]


def test_local_upload_filter_respects_device_search(client,auth_headers,reusable):
    assert client.get('/api/images',headers=auth_headers,params={'platform_name':'@upload'}).json()['total']==2
    assert client.get('/api/images',headers=auth_headers,params={'platform_name':'@upload','device_name':'不存在'}).json()['total']==0
    assert client.get('/api/images/source-platforms',headers=auth_headers).json()==['@upload']


def test_camera_position_is_an_independent_and_filter_before_pagination(managed):
    from models import ImageIngestSettings
    from schemas.image_ingest import ImageIngestConfig
    from integrations.smarteye.runtime import get_runtime
    module,client,rows=managed
    rows['a']={'plat_id':'a','platform_name':'A','state':'1'}
    config=ImageIngestConfig(scenes=['学校','道路','高位摄像头','低位摄像头'],scene_groups=[
        {'name':'公共区域','scenes':['学校']},{'name':'交通场景','scenes':['道路']},
        {'name':'摄像机位','scenes':['高位摄像头','低位摄像头']}])
    with get_runtime().engine.begin() as conn:
        conn.execute(ImageIngestSettings.__table__.insert().values(tenant_id=1,revision=1,config=config.model_dump()))
    for index,labels in enumerate([['学校','高位摄像头'],['学校','低位摄像头'],['道路','高位摄像头']]):
        module.update_device(1,'a',str(index),{'scene_status':'completed','scene_labels':labels})
    response=client.get('/devices',query_string={'scene':'学校','camera_position':'高位摄像头','page_size':1})
    assert response.status_code==200
    assert response.json['data']['total']==1
    assert response.json['data']['items'][0]['device_id']=='0'
    assert client.get('/devices',query_string={'camera_position':'低位摄像头'}).json['data']['total']==1


@pytest.mark.asyncio
@pytest.mark.parametrize('image_kind,queued',[('originals',1),('crops',0)])
async def test_capture_default_collection_and_workflow(work_env,image_kind,queued):
    from io import BytesIO
    from PIL import Image
    from sqlalchemy import create_engine,select
    from config.settings import settings
    from integrations.smarteye.runtime import Runtime,metadata
    from models import ImageCollection
    from models.project_work import ProjectWorkItem
    from schemas.project_work import WorkflowCreate
    from services.project_workflow import create
    s=work_env;engine=create_engine('sqlite:///'+str(s.db.bind.url.database))
    metadata.create_all(engine);runtime=Runtime(engine,settings.STORAGE_LOCAL_PATH)
    flow=await create(s.db,s.project,WorkflowCreate(image_kind=image_kind,follow_uploads=True,
        steps=[dict(operation='caption',model_id=s.models[1].id)]),s.user.id)
    try:
        runtime.create('project-default','device-capture',{'status':'running'},
            {'tenant_id':s.user.tenant_id,'user_id':s.user.id,'project_id':str(s.project.id),'min_long_edge':0})
        data=BytesIO();Image.new('RGB',(64,48),'blue').save(data,'PNG')
        for name in ('sample.png','duplicate.png'):
            await asyncio.to_thread(runtime.save_image,'project-default',runtime.root/'smarteye'/'project-default'/name,data.getvalue(),1,
                collection_name='平台名称')
        with engine.connect() as conn:
            assert conn.scalar(select(ImageCollection.name))==s.project.project_name
            assert len(conn.execute(select(ProjectWorkItem.id).where(ProjectWorkItem.job_id==flow[0].id)).all())==queued+(image_kind=='originals')
    finally:engine.dispose()
