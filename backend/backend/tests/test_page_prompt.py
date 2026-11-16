"""Request-local prompts do not rewrite shared model configuration or output contracts."""
import json

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from tests.test_project_work import work_env
from tests.test_current_image_analysis import analysis_env,request,url
from tests.test_object_attributes import schema


def reply():
    return {'choices':[{'message':{'content':json.dumps({'plate':{'value':None,'reason':'遮挡'},
        'color':{'value':'白','reason':'可见'}})}}]}


@pytest.mark.parametrize('operation',['crop','caption','attributes','detect_attributes'])
def test_batch_prompt_accepts_model_default_length(operation):
    from schemas.project_work import WorkCreate
    from uuid import uuid4
    args=dict(operation=operation,model_id=uuid4(),asset_ids=[uuid4()],prompt='x'*8000)
    if operation=='detect_attributes':args['attribute_model_id']=uuid4()
    assert len(WorkCreate(**args).prompt)==8000
    with pytest.raises(ValidationError):WorkCreate(**{**args,'prompt':'x'*8001})


def test_quality_requirement_keeps_existing_2000_limit():
    from schemas.project_work import WorkCreate
    from uuid import uuid4
    args=dict(operation='quality',model_id=uuid4(),asset_ids=[uuid4()],prompt='x'*2000)
    assert len(WorkCreate(**args).prompt)==2000
    with pytest.raises(ValidationError):WorkCreate(**{**args,'prompt':'x'*2001})


@pytest.mark.asyncio
async def test_batch_attributes_use_supplement_without_replacing_schema_contract(work_env):
    from services import project_work as service
    from schemas.project_work import WorkCreate
    from models import Annotation,ModelService
    from models.project_work import ProjectWorkItem
    s=work_env;s.project.task_type='caption';s.project.label_schema={'object_attributes':schema()};await s.db.commit()
    requirement='只分析当前车，车牌有遮挡不要补猜'
    job=await service.create_job(s.db,s.project,WorkCreate(operation='attributes',model_id=s.models[1].id,
        asset_ids=[s.asset.id],prompt=requirement),s.user.id)
    def provider(request):
        text=json.loads(request.content)['messages'][-1]['content'][0]['text']
        assert requirement in text and '只返回JSON对象' in text and '属性定义' in text
        assert '目标表面颜色' in text and '"key": "plate"' in text
        return httpx.Response(200,json=reply())
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
        assert item.status=='completed'
        assert (await db.scalar(select(Annotation))).annotation_data['attributes']['plate']['value'] is None
        model=await db.get(ModelService,s.models[1].id)
        assert model.revision==1 and model.chat_prompts=={}


@pytest.mark.asyncio
async def test_detect_attributes_child_inherits_only_attribute_prompt(work_env):
    from services import project_work as service
    from schemas.project_work import WorkCreate
    from models.project_work import ProjectWorkJob,ProjectWorkItem
    s=work_env;s.project.task_type='caption';s.project.label_schema={'object_attributes':schema()};await s.db.commit()
    requirement='区分白车和银车，遮挡时无法判断'
    job=await service.create_job(s.db,s.project,WorkCreate(operation='detect_attributes',model_id=s.models[0].id,
        attribute_model_id=s.models[1].id,asset_ids=[s.asset.id],prompt=requirement),s.user.id)
    def provider(request):
        if request.url.path=='/detect':
            assert b'name="prompt"' not in request.content,'Attribute instructions must not become detector labels'
            return httpx.Response(200,json={'detections':[{'class':'car','bbox':[0,0,32,48],'score':.9}]})
        assert requirement in json.loads(request.content)['messages'][-1]['content'][0]['text']
        return httpx.Response(200,json=reply())
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        await service.process_one(s.sessions,client)
        async with s.sessions() as db:
            parent=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
            from uuid import UUID
            child=await db.get(ProjectWorkJob,UUID(parent.result['attribute_job_id']))
            assert child.options['prompt']==requirement
        await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        assert (await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==child.id))).status=='completed'


@pytest.mark.asyncio
async def test_crop_grounding_forwards_current_batch_prompt(work_env):
    from services import project_work as service
    from schemas.project_work import WorkCreate
    s=work_env;s.models[0].endpoint='/detect/grounding';await s.db.commit()
    await service.create_job(s.db,s.project,WorkCreate(operation='crop',model_id=s.models[0].id,
        asset_ids=[s.asset.id],prompt='person; face'),s.user.id)
    def provider(request):
        assert b'name="prompt"' in request.content and b'person; face' in request.content
        return httpx.Response(200,json={'detections':[]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        await service.process_one(s.sessions,client)


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['crop','caption','attributes','detect_attributes'])
async def test_batch_rejects_unsupported_prompt_before_queueing(work_env,operation):
    from services import project_work as service
    from schemas.project_work import WorkCreate
    from models.project_work import ProjectWorkJob
    s=work_env;s.project.task_type='caption';s.project.label_schema={'object_attributes':schema()}
    if operation=='caption':s.models[1].protocol='model_service'
    if operation in ('attributes','detect_attributes'):s.models[1].protocol='jev_decide'
    await s.db.commit()
    args=dict(operation=operation,model_id=s.models[0 if operation in ('crop','detect_attributes') else 1].id,
        asset_ids=[s.asset.id],prompt='本次补充要求')
    if operation=='detect_attributes':args['attribute_model_id']=s.models[1].id
    with pytest.raises(HTTPException) as exc:await service.create_job(s.db,s.project,WorkCreate(**args),s.user.id)
    assert exc.value.status_code==400 and '提示词' in str(exc.value.detail)
    assert await s.db.scalar(select(ProjectWorkJob.id)) is None


@pytest.mark.asyncio
async def test_jev_analyzer_explicitly_rejects_free_prompt():
    from services.object_attributes import analyze
    from tests.test_jev_decide import config,definition,picture
    def unexpected(request):raise AssertionError('Unsupported prompt must not reach model')
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(HTTPException) as exc:
            await analyze(config(),picture(),definition(),client,prompt='补充要求')
    assert exc.value.status_code==400 and '提示词' in str(exc.value.detail)


@pytest.mark.asyncio
async def test_single_image_models_expose_only_safe_prompt_defaults(analysis_env):
    s=analysis_env;default='只描述可见车辆。'*800
    assert len(default)<=8000
    s.models['qwen'].chat_prompts={'caption':default};await s.db.commit()
    rows=s.client.get(url(s,'analysis-models'),headers=s.member,params={'mode':'description'}).json()
    model=next(row for row in rows if row['name']=='qwen')
    assert model['prompt']==default and model['supports_prompt'] is True
    unsupported=next(row for row in rows if row['name']=='caption')
    assert unsupported['prompt']=='' and unsupported['supports_prompt'] is False
    assert all(set(row)=={'id','name','revision','unavailable_reason','prompt','supports_prompt'} for row in rows)
    attributes=s.client.get(url(s,'analysis-models'),headers=s.member,params={'mode':'attributes'}).json()
    from schemas.prompts import ATTRIBUTES_PROMPT
    assert all(row['prompt']==ATTRIBUTES_PROMPT and row['supports_prompt'] is True for row in attributes)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['description','attributes'])
async def test_single_image_prompt_reaches_model_without_annotation_or_config_writes(analysis_env,monkeypatch,mode):
    from models import Annotation,AnnotationEvent,ModelService
    from sqlalchemy import func
    s=analysis_env;requirement='只描述清晰可见的车牌，不要猜测'
    real_client=httpx.AsyncClient
    def provider(request):
        text=json.loads(request.content)['messages'][-1]['content'][0]['text']
        assert requirement in text
        if mode=='attributes':
            assert '只返回JSON对象' in text and '车身颜色' in text
            return httpx.Response(200,json=reply())
        assert text==requirement
        return httpx.Response(200,json={'choices':[{'message':{'content':'车辆的车牌不可见'}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(provider),**kwargs))
    response=s.client.post(url(s),headers=s.member,json=request(s,mode=mode,prompt=requirement))
    assert response.status_code==200,response.text
    assert ('attributes' if mode=='attributes' else 'description') in response.json()
    assert await s.db.scalar(select(func.count()).select_from(Annotation))==0
    assert await s.db.scalar(select(func.count()).select_from(AnnotationEvent))==0
    model=await s.db.get(ModelService,s.models['qwen'].id,populate_existing=True)
    assert model.revision==1 and model.chat_prompts=={}


@pytest.mark.asyncio
async def test_single_image_prompt_length_and_unsupported_model(analysis_env,monkeypatch):
    from services import image_semantics
    s=analysis_env
    async def caption(config,content,prompt,client):assert len(prompt)==8000;return '当前图片'
    monkeypatch.setattr(image_semantics,'caption',caption)
    assert s.client.post(url(s),headers=s.member,json=request(s,mode='description',prompt='x'*8000)).status_code==200
    assert s.client.post(url(s),headers=s.member,json=request(s,mode='description',prompt='x'*8001)).status_code==422
    response=s.client.post(url(s),headers=s.member,json=request(s,'caption',mode='description',prompt='补充要求'))
    assert response.status_code==400 and '提示词' in response.json()['detail']
