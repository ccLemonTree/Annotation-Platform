"""Editable core instructions stay separate from runtime schemas and supplements."""
import json
from uuid import UUID,uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.test_project_work import work_env
from tests.test_current_image_analysis import analysis_env,request,url
from tests.test_automation import ready,automation_tables
from tests.test_object_attributes import schema


@pytest.mark.parametrize('target',['batch','batch_detection','single','detection'])
def test_core_overrides_accept_8000_and_reject_8001(target):
    from schemas.project_work import WorkCreate
    from schemas.annotation import CurrentImageAnalysis
    from schemas.automation import AutomationConfig
    if target=='batch':model=WorkCreate;args=dict(operation='attributes',model_id=uuid4(),asset_ids=[uuid4()]);field='attribute_prompt'
    elif target=='batch_detection':model=WorkCreate;args=dict(operation='crop',model_id=uuid4(),asset_ids=[uuid4()]);field='detection_prompt'
    elif target=='single':model=CurrentImageAnalysis;args=dict(model_id=uuid4(),model_revision=1);field='attribute_prompt'
    else:model=AutomationConfig;args={};field='detection_prompt'
    assert getattr(model(**args,**{field:'x'*8000}),field)=='x'*8000
    with pytest.raises(ValidationError):model(**args,**{field:'x'*8001})


def test_non_attribute_batch_cannot_silently_ignore_attribute_core():
    from schemas.project_work import WorkCreate
    with pytest.raises(ValidationError):WorkCreate(operation='crop',model_id=uuid4(),asset_ids=[uuid4()],attribute_prompt='属性核心')


def test_non_example_automation_cannot_silently_ignore_detection_core():
    from schemas.automation import AutomationCreate
    with pytest.raises(ValidationError):AutomationCreate(project_id=uuid4(),kind='preannotate',detector_id=uuid4(),
        config={'auto_review':False,'detection_prompt':'范例检测核心'})


def test_caption_batch_cannot_silently_ignore_detector_prompt():
    from schemas.project_work import WorkCreate
    with pytest.raises(ValidationError):WorkCreate(operation='caption',model_id=uuid4(),asset_ids=[uuid4()],detection_prompt='person')


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['crop','detect_attributes'])
async def test_dedicated_detection_prompt_reaches_grounding_without_leaking_to_attributes(work_env,operation):
    from services import project_work as service
    from schemas.project_work import WorkCreate
    s=work_env;s.models[0].endpoint='/detect/grounding'
    s.project.task_type='caption';s.project.label_schema={'object_attributes':schema()};await s.db.commit()
    args=dict(operation=operation,model_id=s.models[0].id,asset_ids=[s.asset.id],detection_prompt='car; person',
        prompt='legacy detector text' if operation=='crop' else '属性补充单独使用')
    if operation=='detect_attributes':args['attribute_model_id']=s.models[1].id
    await service.create_job(s.db,s.project,WorkCreate(**args),s.user.id)
    seen=[]
    def provider(request):
        seen.append(request.url.path)
        if request.url.path=='/detect/grounding':
            assert b'name="prompt"' in request.content and b'car; person' in request.content
            assert args['prompt'].encode() not in request.content
            return httpx.Response(200,json={'detections':[{'class':'car','bbox':[0,0,32,48]}]})
        text=json.loads(request.content)['messages'][-1]['content'][0]['text']
        assert 'car; person' not in text and args['prompt'] in text
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        await service.process_one(s.sessions,client)
        if operation=='detect_attributes':await service.process_one(s.sessions,client)
    assert len(seen)==(2 if operation=='detect_attributes' else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['crop','detect_attributes'])
async def test_dedicated_detector_prompt_is_rejected_for_fixed_detectors(work_env,operation):
    from services import project_work as service
    from schemas.project_work import WorkCreate
    s=work_env;s.project.task_type='caption';s.project.label_schema={'object_attributes':schema()};await s.db.commit()
    args=dict(operation=operation,model_id=s.models[0].id,asset_ids=[s.asset.id],detection_prompt='car')
    if operation=='detect_attributes':args['attribute_model_id']=s.models[1].id
    with pytest.raises(HTTPException) as exc:await service.create_job(s.db,s.project,WorkCreate(**args),s.user.id)
    assert exc.value.status_code==400 and '提示词' in str(exc.value.detail)


@pytest.mark.asyncio
async def test_safe_defaults_and_single_model_metadata_expose_actual_core(analysis_env):
    from schemas import prompts
    s=analysis_env
    assert getattr(prompts,'ATTRIBUTES_PROMPT',None)
    assert getattr(prompts,'DETECTION_PREANNOTATION_PROMPT',None)
    defaults=s.client.get('/api/model-services/prompt-defaults',headers=s.admin)
    assert defaults.status_code==200,defaults.text
    assert defaults.json()['attributes']==prompts.ATTRIBUTES_PROMPT
    assert defaults.json()['detection_preannotation']==prompts.DETECTION_PREANNOTATION_PROMPT
    assert s.client.get('/api/model-services/prompt-defaults').status_code==401
    rows=s.client.get(url(s,'analysis-models'),headers=s.member,params={'mode':'attributes'}).json()
    assert all(row['prompt']==prompts.ATTRIBUTES_PROMPT and row['supports_prompt'] for row in rows)
    assert all(set(row)=={'id','name','revision','unavailable_reason','prompt','supports_prompt'} for row in rows)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['attributes','detect_attributes'])
async def test_batch_attribute_core_replaces_default_and_preserves_supplement_contract_and_child(work_env,operation):
    from services import project_work as service
    from schemas.project_work import WorkCreate
    from models import ModelService
    from models.project_work import ProjectWorkJob,ProjectWorkItem
    s=work_env;s.project.task_type='caption';s.project.label_schema={'object_attributes':schema()};await s.db.commit()
    core='本任务只分析完整车辆上的可见车牌和颜色。'
    supplement='补充：反光区域不要当成车身颜色。'
    args=dict(operation=operation,model_id=s.models[0 if operation=='detect_attributes' else 1].id,
        asset_ids=[s.asset.id],attribute_prompt=core,prompt=supplement)
    if operation=='detect_attributes':args['attribute_model_id']=s.models[1].id
    job=await service.create_job(s.db,s.project,WorkCreate(**args),s.user.id)
    seen=[]
    def provider(request):
        if request.url.path=='/detect':
            assert core.encode() not in request.content
            return httpx.Response(200,json={'detections':[{'class':'car','bbox':[0,0,32,48],'score':.9}]})
        text=json.loads(request.content)['messages'][-1]['content'][0]['text'];seen.append(text)
        assert text.startswith(core) and supplement in text and '逐项核对可见证据' not in text
        assert all(value in text for value in ['只返回JSON对象','value必须为null','属性定义','"key": "plate"','"options": ["黑", "白"]','car'])
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"plate":{"value":null,"reason":"遮挡"},"color":{"value":"白","reason":"可见"}}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        await service.process_one(s.sessions,client)
        if operation=='detect_attributes':
            async with s.sessions() as db:
                parent=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
                child=await db.get(ProjectWorkJob,UUID(parent.result['attribute_job_id']))
                assert child.options['attribute_prompt']==core and child.options['prompt']==supplement
            await service.process_one(s.sessions,client)
    assert len(seen)==1
    async with s.sessions() as db:
        model=await db.get(ModelService,s.models[1].id)
        assert model.revision==1 and model.chat_prompts=={}
        items=(await db.scalars(select(ProjectWorkItem))).all()
        assert all(item.status=='completed' for item in items)


@pytest.mark.asyncio
async def test_single_attribute_core_and_supplement_are_independent_and_do_not_write(analysis_env,monkeypatch):
    from models import Annotation,ModelService
    from sqlalchemy import func
    s=analysis_env;core='只核对当前车辆可见证据。';supplement='补充：车牌模糊时无法判断。'
    real_client=httpx.AsyncClient
    def provider(request):
        text=json.loads(request.content)['messages'][-1]['content'][0]['text']
        assert text.startswith(core) and supplement in text
        assert '属性定义' in text and '只返回JSON对象' in text and '车身颜色' in text
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(provider),**kwargs))
    response=s.client.post(url(s),headers=s.member,json=request(s,attribute_prompt=core,prompt=supplement))
    assert response.status_code==200,response.text
    assert response.json()['attributes']['plate']['value'] is None
    assert await s.db.scalar(select(func.count()).select_from(Annotation))==0
    model=await s.db.get(ModelService,s.models['qwen'].id,populate_existing=True)
    assert model.revision==1 and model.chat_prompts=={}
    response=s.client.post(url(s),headers=s.member,json=request(s,mode='description',attribute_prompt=core))
    assert response.status_code==400,response.text


@pytest.mark.asyncio
async def test_jev_rejects_attribute_core_before_request(work_env):
    from services.object_attributes import analyze
    from services import project_work
    from schemas.project_work import WorkCreate
    from tests.test_jev_decide import config,definition,picture
    def unexpected(request):raise AssertionError('JEV cannot consume free-form core instructions')
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(HTTPException) as exc:await analyze(config(),picture(),definition(),client,attribute_prompt='核心')
    assert exc.value.status_code==400
    s=work_env;s.project.task_type='caption';s.project.label_schema={'object_attributes':definition()};s.models[1].protocol='jev_decide';await s.db.commit()
    with pytest.raises(HTTPException) as exc:await project_work.create_job(s.db,s.project,
        WorkCreate(operation='attributes',model_id=s.models[1].id,asset_ids=[s.asset.id],attribute_prompt='核心'),s.user.id)
    assert exc.value.status_code==400


@pytest.mark.asyncio
@pytest.mark.parametrize('core',['本任务只检测完整的人，不检测照片上的人物。','   '])
async def test_example_detection_core_keeps_json_labels_coordinates_and_supplement(client,auth_headers,ready,test_db,test_engine,test_project,test_user,monkeypatch,core):
    from models import ModelService
    from models.automation import AutomationJob
    from services import automation,detection_preannotation
    from schemas import prompts
    from tests.test_detection_examples import reference
    ref,ann=await reference(test_db,test_project,test_user)
    response=client.post('/api/automation/jobs',headers=auth_headers,json={**ready,'detector_id':None,
        'preannotation_model_id':ready['reviewer_id'],'reviewer_id':None,'example_asset_id':str(ref.id),
        'example_annotation_version':ann.version,'config':{'auto_review':False,'detection_prompt':core,'preannotation_prompt':'补充：遮挡不猜测'}})
    assert response.status_code==201,response.text
    job_id=UUID(response.json()['id']);seen=[]
    async def provider(*args,**kwargs):
        text=kwargs['json']['messages'][-1]['content'][0]['text'];seen.append(text)
        assert text.startswith(core if core.strip() else prompts.DETECTION_PREANNOTATION_PROMPT)
        assert '补充：遮挡不猜测' in text
        assert all(value in text for value in ['只输出JSON','归一化到0至1000','最多100框','仅使用项目类别','"person"'])
        return {'choices':[{'message':{'content':'{"boxes":[],"uncertain":true,"reason":"遮挡"}'}}]}
    monkeypatch.setattr(detection_preannotation,'response_json',provider)
    assert await automation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    assert len(seen)==1
    job=await test_db.get(AutomationJob,job_id)
    assert job.config['detection_prompt']==core
    model=await test_db.get(ModelService,UUID(ready['reviewer_id']),populate_existing=True)
    assert model.revision==1 and model.chat_prompts=={}
