import json
from types import SimpleNamespace
import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from tests.test_project_work import work_env


def schema(target='car'):
    return {'target_label':target,'fields':[
        {'key':'plate','name':'车牌','instruction':'只填写清晰可辨的完整车牌','options':[]},
        {'key':'color','name':'颜色','instruction':'目标表面颜色','options':['黑','白']}]}


def test_editable_attribute_schema_rejects_duplicates_and_invalid_keys():
    from schemas.object_attributes import AttributeSchema
    assert AttributeSchema.model_validate(schema('person')).target_label=='person'
    value=schema();value['fields'].append(value['fields'][0])
    with pytest.raises(ValidationError):AttributeSchema.model_validate(value)
    value=schema();value['fields'][0]['key']='../plate'
    with pytest.raises(ValidationError):AttributeSchema.model_validate(value)


def test_model_unknown_and_invalid_values_are_never_guessed():
    from services.object_attributes import parse_attributes
    result=parse_attributes(json.dumps({'plate':{'value':'无法判断','reason':'模糊'},'color':{'value':'紫','reason':'猜测'}}),schema())
    assert result=={'plate':{'value':None,'reason':'模糊'},'color':{'value':None,'reason':'模型返回了可选值之外的结果'}}
    with pytest.raises(HTTPException,match='有效的属性JSON') as error:
        parse_attributes('{}',schema())
    assert error.value.status_code==502
    with pytest.raises(HTTPException):parse_attributes('不是JSON',schema())
    with pytest.raises(HTTPException):parse_attributes('{"plate":{"value":"A"},"plate":{"value":"B"}}',schema())


def test_model_extra_fields_are_discarded_without_invalidating_attributes():
    from services.object_attributes import parse_attributes
    output={'plate':{'value':'A12345','reason':'清晰可见'},'color':{'value':'白','reason':'车身可见'}}
    response={'target_label':'car','summary':'额外说明',**output,'plate':{**output['plate'],'confidence':0.9}}
    assert parse_attributes(json.dumps(response),schema())==output
    with pytest.raises(HTTPException):
        parse_attributes(json.dumps({**response,'plate':{'value':['A12345'],'reason':'清晰可见'}}),schema())


def test_model_fields_array_uses_only_configured_keys():
    from services.object_attributes import parse_attributes
    response={'target_label':{'value':'truck'},'fields':[
        {'key':'plate','name':'车牌','value':None,'reason':'遮挡'},
        {'key':'color','name':'颜色','value':'白','reason':'车身可见'},
        {'key':'unused','value':'忽略'}]}
    assert parse_attributes(json.dumps(response),schema())=={
        'plate':{'value':None,'reason':'遮挡'},'color':{'value':'白','reason':'车身可见'}}
    del response['fields'][:2]
    with pytest.raises(HTTPException) as error:
        parse_attributes(json.dumps(response),schema())
    assert error.value.status_code==502


@pytest.mark.asyncio
@pytest.mark.parametrize('manual',[False,True])
async def test_targeted_attribute_retry_preserves_existing_annotation(work_env,monkeypatch,manual):
    from sqlalchemy import select
    from models import Annotation,ProjectWorkItem
    from schemas.project_work import WorkflowCreate
    from services import object_attributes,project_work,project_workflow

    env=work_env
    env.project.task_type='caption'
    await env.db.commit()
    job,=await project_workflow.create(env.db,env.project,WorkflowCreate(attribute_schema=schema(),steps=[
        dict(operation='attributes',model_id=env.models[1].id)]),env.user.id)
    old=Annotation(project_id=env.project.id,asset_id=env.asset.id,version=1,is_latest=True,review_status='pending',
        annotation_source='manual' if manual else 'auto',annotation_data={'description':'已有图片描述'})
    env.db.add(old)
    await env.db.flush()
    item=await env.db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
    item.result={'attribute_retry_annotation_id':str(old.id)}
    await env.db.commit()

    now=1000
    calls=[]
    monkeypatch.setattr(project_work,'time',lambda:now)
    async def response(*args,**kwargs):
        calls.append(1)
        result={} if len(calls)==1 else {'fields':[{'key':'color','value':'白','reason':'车身可见'}]}
        return {'choices':[{'message':{'content':json.dumps(result)}}]}
    monkeypatch.setattr(object_attributes,'response_json',response)
    async with httpx.AsyncClient() as client:
        assert await project_work.process_one(env.sessions,client)
        async with env.sessions() as db:
            first=await db.get(ProjectWorkItem,item.id)
            assert first.status==('cancelled' if manual else 'failed')
            if not manual:assert first.result['retry_at']>now
        if not manual:
            now+=60
            assert await project_work.process_one(env.sessions,client)
    async with env.sessions() as db:
        latest=await db.scalar(select(Annotation).where(Annotation.asset_id==env.asset.id,Annotation.is_latest.is_(True)))
        assert latest.annotation_data['description']=='已有图片描述'
        assert latest.version==(1 if manual else 2)
        if not manual:assert latest.annotation_data['attributes']['color']['value']=='白'
    assert len(calls)==(0 if manual else 2)


def test_structured_annotations_validate_and_keep_configuration_snapshot():
    from services.annotations import validate_image_annotation
    config=schema()
    project=SimpleNamespace(task_type='caption',label_schema={'object_attributes':config})
    data={'labels':[],'description':'','attribute_schema':config,'attributes':{
        'plate':{'value':None,'reason':'遮挡'},'color':{'value':'白','reason':'车身清楚可见'}}}
    validate_image_annotation(project,data)
    data['attributes']['color']['value']='紫'
    with pytest.raises(HTTPException):validate_image_annotation(project,data)


def test_manual_values_are_normalized_for_exact_filtering():
    from services.annotations import validate_image_annotation
    config=schema();project=SimpleNamespace(task_type='caption',label_schema={'object_attributes':config})
    data={'attribute_schema':config,'attributes':{'plate':{'value':None,'reason':' 遮挡 '},'color':{'value':' 白 ','reason':' 可见 '}}}
    validate_image_annotation(project,data)
    assert data['attributes']['color']['value']=='白' and data['attributes']['plate']['reason']=='遮挡'


@pytest.mark.asyncio
@pytest.mark.parametrize('target',['car',['car','bus']])
async def test_detection_chains_per_target_attributes_and_exports(work_env,target):
    from schemas.project_work import WorkCreate
    from services import project_work as service
    from services.annotation_export import training_package
    from models import Asset,Annotation
    from models.project_work import ProjectWorkJob,ProjectWorkItem
    from sqlalchemy import select
    import zipfile
    s=work_env;s.project.task_type='caption';s.project.label_schema={'object_attributes':schema(target)};await s.db.commit()
    job=await service.create_job(s.db,s.project,WorkCreate(operation='detect_attributes',model_id=s.models[0].id,
        attribute_model_id=s.models[1].id,asset_ids=[s.asset.id],expansion=0),s.user.id)
    def provider(request):
        if request.url.path=='/detect':
            assert b'car' in request.content
            if isinstance(target,list):assert b'bus' in request.content
            return httpx.Response(200,json={'detections':[{'class':'car','bbox':[0,0,32,48],'score':.9},
                {'class':'bus' if isinstance(target,list) else 'car','bbox':[32,0,64,48],'score':.9},{'class':'person','bbox':[0,0,64,48],'score':.9}]})
        body=json.loads(request.content)
        assert '无法判断' in body['messages'][0]['content'][0]['text']
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'plate':{'value':None,'reason':'看不到车牌'},'color':{'value':'白','reason':'可见'}})}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        assert await service.process_one(s.sessions,client)
        assert await service.process_one(s.sessions,client)
        assert await service.process_one(s.sessions,client)
        assert not await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        crops=(await db.scalars(select(Asset).where(Asset.source_type=='model_crop'))).all()
        assert len(crops)==2 and {c.device_info['crop']['box']['label'] for c in crops}==set(target if isinstance(target,list) else [target])
        annotations=(await db.scalars(select(Annotation).where(Annotation.is_latest.is_(True)))).all()
        assert len(annotations)==2 and all(a.annotation_data['attributes']['plate']['value'] is None for a in annotations)
        assert all(a.annotation_source=='auto' and a.review_status=='pending' for a in annotations)
        parent=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
        assert parent.result['attribute_job_id']
        stream,_=await training_package(db,s.project,approved_only=False)
        with stream,zipfile.ZipFile(stream) as archive:
            rows=[json.loads(line) for line in archive.read('annotations.jsonl').splitlines()]
            assert len(rows)==2 and rows[0]['attribute_schema']==schema(target)
            assert rows[0]['attributes']['plate']['value'] is None
            assert 'attributes.csv' in archive.namelist()
            if isinstance(target,list):assert 'car;bus' in archive.read('attributes.csv').decode('utf-8-sig')


@pytest.mark.asyncio
async def test_attributes_do_not_overwrite_manual_edits(work_env):
    from schemas.project_work import WorkCreate
    from services import project_work as service
    from services.annotations import save_annotation
    from models import Asset,Annotation
    from models.project_work import ProjectWorkItem
    from sqlalchemy import select
    s=work_env;s.project.task_type='caption';s.project.label_schema={'object_attributes':schema()};await s.db.commit()
    job=await service.create_job(s.db,s.project,WorkCreate(operation='attributes',model_id=s.models[1].id,asset_ids=[s.asset.id]),s.user.id)
    async def provider(request):
        async with s.sessions() as db:
            asset=await db.get(Asset,s.asset.id)
            await save_annotation(db,asset,{'description':'人工修改'},0,s.user.id);await db.commit()
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        ann=await db.scalar(select(Annotation).where(Annotation.is_latest.is_(True)))
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
        assert ann.annotation_data=={'description':'人工修改'} and item.status=='failed'


@pytest.mark.asyncio
async def test_attribute_filter_is_applied_before_pagination_and_export(work_env):
    from models import Asset
    from services.annotations import save_annotation
    from services.object_attributes import attribute_query
    from services.annotation_export import training_package
    from sqlalchemy import func,select
    import zipfile
    s=work_env;s.project.task_type='caption';s.project.label_schema={'object_attributes':schema()};await s.db.commit()
    digest=s.asset.content_hash;s.asset.content_hash='original-fixture';await s.db.flush()
    for index,value in enumerate(['白','黑',None]):
        asset=Asset(project_id=s.project.id,blob_path=s.asset.blob_path,content_hash=s.asset.content_hash+str(index),
            original_name=str(index)+'.png',source_type='model_crop',file_size=1,width=64,height=48,format='PNG')
        if value=='白':asset.content_hash=digest
        s.db.add(asset);await s.db.flush()
        await save_annotation(s.db,asset,{'attribute_schema':schema(),'attributes':{
            'plate':{'value':None,'reason':'遮挡'},'color':{'value':value,'reason':'可见' if value else '模糊'}}},0,s.user.id)
    await s.db.commit()
    filters={'attribute_key':'color','attribute_value':'白'}
    pending=Asset(project_id=s.project.id,blob_path=s.asset.blob_path,content_hash='pending-fixture',original_name='pending.png',source_type='model_crop',file_size=1,width=64,height=48,format='PNG')
    s.db.add(pending);await s.db.commit()
    analyzed=attribute_query(s.project,attribute_key='color',attribute_state='analyzed')
    assert await s.db.scalar(select(func.count()).select_from(analyzed.subquery()))==3
    missing=attribute_query(s.project,attribute_key='color',attribute_state='missing')
    assert await s.db.scalar(select(func.count()).select_from(missing.subquery()))==1
    query=attribute_query(s.project,**filters)
    assert await s.db.scalar(select(func.count()).select_from(query.subquery()))==1
    assert len((await s.db.execute(query.limit(1))).all())==1
    unknown=attribute_query(s.project,attribute_key='color',attribute_state='unknown')
    assert await s.db.scalar(select(func.count()).select_from(unknown.subquery()))==1
    stream,manifest=await training_package(s.db,s.project,approved_only=False,attribute_filters=filters)
    with stream,zipfile.ZipFile(stream) as archive:
        assert len(archive.read('annotations.jsonl').splitlines())==1
    assert manifest['attribute_filters']==filters


@pytest.mark.asyncio
async def test_manual_cannot_forge_attribute_schema(work_env):
    from services.annotations import save_annotation
    s=work_env;s.project.task_type='caption';s.project.label_schema={'object_attributes':schema()}
    fake=schema();fake['fields'][1]['options']=[]
    with pytest.raises(HTTPException) as error:
        await save_annotation(s.db,s.asset,{'attribute_schema':fake,'attributes':{
            'plate':{'value':None,'reason':'遮挡'},'color':{'value':'紫','reason':'猜测'}}},0,s.user.id)
    assert error.value.status_code==409


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['schema','cancel','model'])
async def test_late_attribute_results_are_fenced(work_env,change):
    from schemas.project_work import WorkCreate
    from services import project_work as service
    from models import Project,ModelService,Annotation
    from models.project_work import ProjectWorkItem
    from sqlalchemy import select
    s=work_env;s.project.task_type='caption';s.project.label_schema={'object_attributes':schema()};await s.db.commit()
    job=await service.create_job(s.db,s.project,WorkCreate(operation='attributes',model_id=s.models[1].id,asset_ids=[s.asset.id]),s.user.id)
    async def provider(request):
        async with s.sessions() as db:
            if change=='schema':
                project=await db.get(Project,s.project.id);project.label_schema={'object_attributes':schema('person')}
            elif change=='model':
                model=await db.get(ModelService,s.models[1].id);model.revision+=1
            else:
                project=await db.get(Project,s.project.id);await service.action_job(db,project,job.id,'cancel')
            await db.commit()
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        assert not await db.scalar(select(Annotation.id))
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
        assert item.status==('cancelled' if change=='cancel' else 'failed')


@pytest.mark.asyncio
async def test_attribute_api_scopes_and_edits_historical_schema(client,auth_headers,test_db,test_project,test_asset,test_user):
    from services.annotations import save_annotation
    from models import Project,Tenant
    from sqlalchemy import select
    test_project.task_type='caption';test_project.label_schema={'object_attributes':schema()};await test_db.flush()
    data={'attribute_schema':schema(),'attributes':{'plate':{'value':None,'reason':'遮挡'},'color':{'value':'白','reason':'可见'}}}
    ann=await save_annotation(test_db,test_asset,data,0,test_user.id)
    test_db.add(Tenant(id=99,tenant_name='foreign'));await test_db.flush()
    other=Project(tenant_id=99,project_name='foreign',task_type='caption');test_db.add(other);await test_db.commit()
    assert client.get(f'/api/projects/{other.id}/attributes',headers=auth_headers).status_code==404
    result=client.get(f'/api/projects/{test_project.id}/attributes',headers=auth_headers,params={'attribute_key':'color','attribute_value':'白','page_size':1})
    assert result.status_code==200,result.text
    assert result.json()['total']==1 and result.json()['items'][0]['attributes']['color']['value']=='白'
    test_project.label_schema={'object_attributes':schema('person')};await test_db.commit()
    changed={**data,'attributes':{**data['attributes'],'color':{'value':'黑','reason':'人工修正'}}}
    result=client.put(f'/api/annotations/{ann.id}',headers=auth_headers,json={'base_version':ann.version,'annotation_data':changed})
    assert result.status_code==200,result.text
    assert result.json()['annotation_data']['attribute_schema']['target_label']=='car'


@pytest.mark.asyncio
async def test_attributes_member_access_and_management_boundary(client,auth_headers,test_db,test_project,test_user):
    from models import User,ProjectMember
    from services.auth import create_access_token
    member=User(tenant_id=1,username='attribute-member',email='member@fixture.test',role='user',is_active=True,password_hash='unused')
    test_db.add(member);await test_db.flush();await test_db.commit()
    headers={'Authorization':'Bearer '+create_access_token({'user_id':member.id})}
    path=f'/api/projects/{test_project.id}/attributes'
    assert client.get(path,headers=headers).status_code==404
    test_db.add(ProjectMember(project_id=test_project.id,user_id=member.id));await test_db.commit()
    assert client.get(path,headers=headers).status_code==200
    assert client.get(path+'/export',headers=headers).status_code==403
    assert client.post(f'/api/projects/{test_project.id}/work/jobs',headers=headers,json={}).status_code==403


@pytest.mark.asyncio
async def test_cancel_detection_batch_cancels_its_queued_attribute_items(work_env):
    from schemas.project_work import WorkCreate
    from services import project_work as service
    from models.project_work import ProjectWorkItem
    from sqlalchemy import select
    s=work_env;s.project.task_type='caption';s.project.label_schema={'object_attributes':schema()};await s.db.commit()
    job=await service.create_job(s.db,s.project,WorkCreate(operation='detect_attributes',model_id=s.models[0].id,
        attribute_model_id=s.models[1].id,asset_ids=[s.asset.id]),s.user.id)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={
        'detections':[{'class':'car','bbox':[0,0,30,48],'score':.9}]}))) as client:
        await service.process_one(s.sessions,client)
    count=await service.action_job(s.db,s.project,job.id,'cancel')
    assert count['changed_count']==1
    async with s.sessions() as db:
        items=(await db.scalars(select(ProjectWorkItem))).all()
        assert sorted(item.status for item in items)==['cancelled','completed']


@pytest.mark.asyncio
async def test_truncated_attributes_are_rejected(work_env):
    from services.object_attributes import analyze
    from schemas.model_service import ModelServiceConfig
    s=work_env
    config=ModelServiceConfig.model_validate(s.models[1])
    from services.project_work import read_content
    content=await read_content(s.asset)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={
        'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}))) as client:
        with pytest.raises(HTTPException) as error:await analyze(config,content,schema(),client)
    assert error.value.status_code==502
