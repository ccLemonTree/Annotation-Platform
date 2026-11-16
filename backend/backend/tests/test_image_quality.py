"""Quality inference may delete only a fenced, explicitly selected project asset."""
import json
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select,update

from models import Asset,Annotation,Project,User,Tenant
from models.project_work import ProjectWorkItem
from schemas.project_work import WorkCreate, WorkflowCreate
from tests.test_project_work import work_env

REQUIREMENTS='主体车辆完整位于图内，未被裁断或严重遮挡；多车重叠无法区分主体则不合格。'


def request(s,**kwargs):
    return WorkCreate(operation='quality',model_id=s.models[1].id,asset_ids=[s.asset.id],prompt=REQUIREMENTS,**kwargs)


def provider(passed=True,reason='主体车辆完整可见',**kwargs):
    def respond(req):
        body=json.loads(req.content)
        assert body['temperature']==0 and body['chat_template_kwargs']['enable_thinking'] is False
        assert REQUIREMENTS in body['messages'][-1]['content'][0]['text']
        assert body['messages'][-1]['content'][1]['image_url']['url'].startswith('data:image/')
        assert req.extensions['timeout']['read']==30
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(dict(passed=passed,reason=reason,**kwargs),ensure_ascii=False)}}]})
    return respond


@pytest.mark.parametrize('payload',[{'passed':'false','reason':'局部车辆'},{'passed':0,'reason':'局部车辆'},
    {'passed':False,'reason':''},{'passed':False,'reason':'x'*1001},{'passed':False},
    {'passed':False,'reason':'局部车辆','extra':True},[],None])
def test_quality_requires_explicit_boolean_and_nonempty_reason(payload):
    from services.image_quality import parse_result
    with pytest.raises(HTTPException) as error:parse_result(json.dumps(payload))
    assert error.value.status_code==502


def test_quality_accepts_strict_result_and_rejects_duplicate_fields():
    from services.image_quality import parse_result
    assert parse_result('```json\n{"passed":false,"reason":"车身被裁断"}\n```')=={'passed':False,'reason':'车身被裁断'}
    with pytest.raises(HTTPException):parse_result('{"passed":true,"passed":false,"reason":"不确定"}')


def test_quality_requires_requirement_and_disallows_attribute_example():
    with pytest.raises(ValidationError):WorkCreate(operation='quality',model_id=uuid4(),asset_ids=[uuid4()])
    with pytest.raises(ValidationError):WorkCreate(operation='quality',model_id=uuid4(),asset_ids=[uuid4()],prompt='  ')


@pytest.mark.asyncio
@pytest.mark.parametrize('passed',[True,False,None])
async def test_workflow_quality_routes_passes_and_deletes_failed_crops(work_env,monkeypatch,passed):
    from services import project_work,project_workflow
    s=work_env
    async def detect(*args,**kwargs):
        return {'boxes':[dict(label='person',x=1,y=2,width=14,height=15)]}
    monkeypatch.setattr(project_work,'infer',detect)
    crop,quality,next_step=await project_workflow.create(s.db,s.project,WorkflowCreate(steps=[
        dict(operation='crop',model_id=s.models[0].id,labels=['person'],save_input_boxes=False),
        dict(operation='quality',model_id=s.models[1].id,prompt=REQUIREMENTS),
        dict(operation='detect',model_id=s.models[0].id,labels=['person']),
    ]),s.user.id)
    response=provider(passed) if passed is not None else lambda req:httpx.Response(503)
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        assert await project_work.process_one(s.sessions,client)
        async with s.sessions() as db:
            crop_item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==crop.id))
            derived=UUID(crop_item.result['asset_ids'][0])
            db.add(Annotation(asset_id=derived,version=1,is_latest=True,review_status='pending',
                annotation_data={'description':'裁剪结果标注'}))
            await db.commit()
        assert await project_work.process_one(s.sessions,client)
    async with s.sessions() as db:
        assert await db.get(Asset,s.asset.id) is not None
        assert bool(await db.get(Asset,derived)) is (passed is not False)
        annotation=await db.scalar(select(Annotation).where(Annotation.asset_id==derived))
        assert bool(annotation) is (passed is not False)
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==quality.id))
        assert item.status==('failed' if passed is None else 'completed')
        if passed is not None:assert item.result['deleted'] is (not passed)
        downstream=await db.scalar(select(ProjectWorkItem.asset_id).where(ProjectWorkItem.job_id==next_step.id))
        assert downstream==(derived if passed else None)
        output=(await db.scalars(project_workflow.output_ids(db,quality))).all()
        assert output==([derived] if passed else [])
        details=await project_work.summary(db,quality)
        assert details['quality_counts']=={'passed':int(passed is True),'deleted':int(passed is False)}


@pytest.mark.asyncio
@pytest.mark.parametrize('passed',[True,False])
async def test_quality_keeps_good_image_or_deletes_only_selected_image(work_env,passed):
    from services import project_work as service
    s=work_env
    original_data={'boxes':[{'x':1,'y':2,'width':10,'height':12,'label':'person'}]}
    annotation=Annotation(asset_id=s.asset.id,version=1,is_latest=True,review_status='approved',annotation_source='manual',annotation_data=original_data)
    s.db.add(annotation);s.asset.annotation_status='reviewed';s.project.annotated_count=1;s.project.reviewed_count=1
    other_project=Project(tenant_id=1,project_name='other project',task_type='detection')
    s.db.add(other_project);await s.db.flush()
    other=Asset(project_id=other_project.id,original_name='same original.png',blob_path=s.asset.blob_path,content_hash=s.asset.content_hash,file_size=s.asset.file_size,width=64,height=48,source_type='upload')
    s.db.add(other);await s.db.commit()
    selected_id=s.asset.id;annotation_id=annotation.id;name=s.asset.original_name
    job=await service.create_job(s.db,s.project,request(s),s.user.id)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider(passed))) as client:
        assert await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id));project=await db.get(Project,s.project.id)
        assert item.status=='completed' and item.result['passed'] is passed and item.result['deleted'] is (not passed)
        assert item.result['original_name']==name and await db.get(Asset,other.id) is not None
        if passed:
            saved=await db.get(Annotation,annotation_id)
            assert saved.annotation_data==original_data and saved.version==1 and saved.review_status=='approved'
            assert (project.total_assets,project.annotated_count,project.reviewed_count)==(1,1,1)
        else:
            assert await db.get(Asset,selected_id) is None and await db.get(Annotation,annotation_id) is None
            assert item.asset_id is None
            assert (project.total_assets,project.annotated_count,project.reviewed_count)==(0,0,0)
        details=await service.summary(db,await db.get(type(job),job.id),True)
        assert details['quality_counts']=={'passed':int(passed),'deleted':int(not passed)}
        assert details['quality_requirement']==REQUIREMENTS and details['items'][0]['original_name']==name
        assert (await service.action_job(db,project,job.id,'retry'))['changed_count']==0


@pytest.mark.asyncio
async def test_failed_crop_quality_preserves_source_original_and_immutable_storage(work_env):
    from services import project_work as service
    from services.storage import get_storage
    s=work_env
    source=Asset(project_id=s.project.id,original_name='source.png',blob_path='source.png',content_hash='source',file_size=1,width=128,height=96,source_type='upload')
    s.db.add(source);await s.db.flush()
    s.asset.source_type='model_crop';s.asset.device_info={'crop':{'parent_asset_id':str(source.id),'region':[0,0,64,48]}}
    s.project.label_schema={'annotation_scope':'crops'};s.project.total_assets=2;await s.db.commit()
    job=await service.create_job(s.db,s.project,request(s),s.user.id)
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider(False,'主体被图像边界截断'))) as client:
        assert await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        assert await db.get(Asset,s.asset.id) is None and await db.get(Asset,source.id) is not None
        assert (await db.get(Project,s.project.id)).total_assets==1
    assert await get_storage().file_exists(s.asset.blob_path)


@pytest.mark.asyncio
@pytest.mark.parametrize('response',['invalid','truncated','timeout','malformed_choice'])
async def test_quality_protocol_or_network_failure_never_deletes(work_env,response):
    from services import project_work as service
    s=work_env;job=await service.create_job(s.db,s.project,request(s),s.user.id)
    def respond(req):
        if response=='timeout':raise httpx.ReadTimeout('isolated timeout',request=req)
        if response=='malformed_choice':return httpx.Response(200,json={'choices':[False]})
        return httpx.Response(200,json={'choices':[{'finish_reason':'length' if response=='truncated' else 'stop','message':{'content':'{"passed":false,"reason":"局部车辆"}' if response=='truncated' else '{"passed":"false","reason":"局部车辆"}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:assert await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
        assert item.status=='failed' and await db.get(Asset,s.asset.id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize('finish_reason',[None,'content_filter','tool_calls','function_call','unknown'])
async def test_quality_requires_explicit_stop_before_deletion(work_env,finish_reason):
    from services import project_work as service
    s=work_env;job=await service.create_job(s.db,s.project,request(s),s.user.id)
    def respond(req):
        choice={'message':{'content':'{"passed":false,"reason":"车辆被裁断"}'}}
        if finish_reason is not None:choice['finish_reason']=finish_reason
        return httpx.Response(200,json={'choices':[choice]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:assert await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
        assert item.status=='failed' and await db.get(Asset,s.asset.id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['cancel','model','annotation','review','actor','scope','content'])
async def test_quality_revalidates_before_destructive_result(work_env,change):
    from services import project_work as service
    s=work_env;ann=Annotation(asset_id=s.asset.id,version=1,is_latest=True,review_status='pending',annotation_data={'boxes':[]})
    s.db.add(ann);await s.db.commit();job=await service.create_job(s.db,s.project,request(s),s.user.id)
    async def respond(req):
        async with s.sessions() as db:
            if change=='cancel':await db.execute(update(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id).values(status='cancelled',attempt=2))
            elif change=='model':await db.execute(update(type(s.models[1])).where(type(s.models[1]).id==s.models[1].id).values(revision=2))
            elif change=='annotation':await db.execute(update(Annotation).where(Annotation.id==ann.id).values(version=2))
            elif change=='review':await db.execute(update(Annotation).where(Annotation.id==ann.id).values(review_status='approved'))
            elif change=='actor':await db.execute(update(User).where(User.id==s.user.id).values(role='user'))
            elif change=='scope':await db.execute(update(Project).where(Project.id==s.project.id).values(label_schema={'annotation_scope':'crops'}))
            elif change=='content':await db.execute(update(Asset).where(Asset.id==s.asset.id).values(content_hash='changed'))
            await db.commit()
        return provider(False,'车辆被裁断')(req)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:assert await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
        assert item.status==('cancelled' if change=='cancel' else 'failed')
        assert await db.get(Asset,s.asset.id) is not None and await db.get(Annotation,ann.id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize('edit_during_inference',[True,False])
async def test_quality_detects_save_delete_aba_but_accepts_existing_history(work_env,edit_during_inference):
    from services import project_work as service
    from services.annotations import save_annotation,remove
    s=work_env
    async def save_then_remove():
        async with s.sessions() as db:
            asset=await db.scalar(select(Asset).where(Asset.id==s.asset.id).with_for_update())
            annotation=await save_annotation(db,asset,{'boxes':[{'x':1,'y':1,'width':10,'height':10,'label':'person'}]},0,s.user.id)
            await remove(db,annotation,asset,s.user.id);await db.commit()
    if not edit_during_inference:await save_then_remove()
    job=await service.create_job(s.db,s.project,request(s),s.user.id)
    async def respond(req):
        if edit_during_inference:await save_then_remove()
        return provider(False,'车辆被裁断')(req)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:assert await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
        assert item.status==('failed' if edit_during_inference else 'completed')
        assert (await db.get(Asset,s.asset.id) is not None) is edit_during_inference


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['offline','wrong_protocol','wrong_tenant','non_admin'])
async def test_quality_rejects_unavailable_models_and_non_admin_creation(work_env,change):
    from services import project_work as service
    s=work_env
    if change=='offline':s.models[1].connection_check={'status':'offline','checked_revision':1}
    elif change=='wrong_protocol':s.models[1].protocol='model_service'
    elif change=='wrong_tenant':s.db.add(Tenant(id=2,tenant_name='other'));await s.db.flush();s.models[1].tenant_id=2
    else:s.user.role='user'
    await s.db.commit()
    with pytest.raises(HTTPException):await service.create_job(s.db,s.project,request(s),s.user.id)
