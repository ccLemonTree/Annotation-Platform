import hashlib,json
from io import BytesIO
from uuid import uuid4
import httpx
import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select
from tests.test_project_work import work_env
from tests.test_object_attributes import schema


async def example(s,**changes):
    from models import Asset
    from services.annotations import save_annotation
    from services.storage import get_storage
    s.project.task_type='caption';s.project.label_schema={'object_attributes':schema()}
    raw=BytesIO();Image.new('RGB',(64,48),'blue').save(raw,'PNG');content=raw.getvalue()
    asset=Asset(project_id=s.project.id,original_name='correct-example.png',blob_path='example.png',content_hash=hashlib.sha256(content).hexdigest(),
        width=64,height=48,file_size=len(content),format='PNG',source_type='upload')
    s.db.add(asset);await s.db.flush();await get_storage().save_file(BytesIO(content),asset.blob_path)
    data={'attribute_schema':schema(),'attributes':{'plate':{'value':None,'reason':'不可见'},'color':{'value':'白','reason':'人工核对'}}}
    annotation=await save_annotation(s.db,asset,data,0,s.user.id)
    for key,value in changes.items():setattr(annotation,key,value)
    await s.db.commit()
    return asset,annotation


@pytest.mark.asyncio
async def test_example_image_and_correct_answer_are_sent_and_not_overwritten(work_env):
    from schemas.project_work import WorkCreate
    from services import project_work as service
    from models import Annotation
    s=work_env;reference,annotation=await example(s)
    job=await service.create_job(s.db,s.project,WorkCreate(operation='attributes',model_id=s.models[1].id,
        asset_ids=[s.asset.id,reference.id],example_asset_id=reference.id,example_annotation_version=annotation.version),s.user.id)
    assert (await service.summary(s.db,job))['total']==1
    assert job.options['attribute_example']['annotation_version']==1
    await s.db.commit()
    def provider(request):
        messages=json.loads(request.content)['messages']
        assert [message['role'] for message in messages]==['user','assistant','user']
        assert messages[0]['content'][1]['type']=='image_url' and messages[2]['content'][1]['type']=='image_url'
        assert messages[0]['content'][1]!=messages[2]['content'][1]
        assert json.loads(messages[1]['content'])['color']['value']=='白'
        assert '正确标注范例' in messages[0]['content'][0]['text']
        assert '照抄' in messages[2]['content'][0]['text']
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'color':{'value':'黑','reason':'目标为黑色'}})}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        original=await db.scalar(select(Annotation).where(Annotation.asset_id==reference.id,Annotation.is_latest.is_(True)))
        result=await db.scalar(select(Annotation).where(Annotation.asset_id==s.asset.id,Annotation.is_latest.is_(True)))
        assert original.id==annotation.id and original.version==1
        assert result.annotation_data['attributes']['color']['value']=='黑' and result.annotation_data['attributes']['plate']['value'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['auto','rejected','schema','version','foreign','only_example'])
async def test_invalid_reference_is_rejected_before_creating_batch(work_env,change):
    from schemas.project_work import WorkCreate
    from services import project_work as service
    from models import Project
    s=work_env;reference,annotation=await example(s)
    if change=='auto':annotation.annotation_source='auto'
    elif change=='rejected':annotation.review_status='rejected'
    elif change=='schema':annotation.annotation_data={**annotation.annotation_data,'attribute_schema':schema('person')}
    elif change=='foreign':
        other=Project(tenant_id=1,project_name='other project',task_type='caption');s.db.add(other);await s.db.flush();reference.project_id=other.id
    await s.db.commit()
    with pytest.raises(HTTPException):
        await service.create_job(s.db,s.project,WorkCreate(operation='attributes',model_id=s.models[1].id,
            asset_ids=[reference.id] if change=='only_example' else [s.asset.id],example_asset_id=reference.id,
            example_annotation_version=999 if change=='version' else annotation.version),s.user.id)


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['edit','delete','content','review'])
async def test_reference_changed_during_inference_prevents_late_write(work_env,change):
    from schemas.project_work import WorkCreate
    from services import project_work as service
    from services.annotations import save_annotation
    from models import Annotation,Asset
    from models.project_work import ProjectWorkItem
    s=work_env;reference,annotation=await example(s)
    job=await service.create_job(s.db,s.project,WorkCreate(operation='attributes',model_id=s.models[1].id,asset_ids=[s.asset.id],
        example_asset_id=reference.id,example_annotation_version=annotation.version),s.user.id)
    async def provider(request):
        async with s.sessions() as db:
            asset=await db.get(Asset,reference.id);current=await db.get(Annotation,annotation.id)
            if change=='edit':await save_annotation(db,asset,{**current.annotation_data,'description':'新人工说明'},1,s.user.id)
            elif change=='delete':await db.delete(current)
            elif change=='content':asset.content_hash='changed'
            else:current.review_status='rejected'
            await db.commit()
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        assert not await db.scalar(select(Annotation.id).where(Annotation.asset_id==s.asset.id))
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
        assert item.status=='failed' and '范例' in item.error


@pytest.mark.asyncio
async def test_detection_child_inherits_reference_and_original_scope_change_is_safe(work_env):
    from schemas.project_work import WorkCreate
    from services import project_work as service
    from models.project_work import ProjectWorkJob,ProjectWorkItem
    s=work_env;reference,annotation=await example(s)
    await service.create_job(s.db,s.project,WorkCreate(operation='detect_attributes',model_id=s.models[0].id,attribute_model_id=s.models[1].id,
        asset_ids=[s.asset.id],example_asset_id=reference.id,example_annotation_version=annotation.version),s.user.id)
    def provider(request):
        if request.url.path=='/detect':return httpx.Response(200,json={'detections':[{'class':'car','bbox':[0,0,32,48],'score':.9}]})
        assert len(json.loads(request.content)['messages'])==3
        return httpx.Response(200,json={'choices':[{'message':{'content':'{}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        await service.process_one(s.sessions,client);await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        child=await db.scalar(select(ProjectWorkJob).where(ProjectWorkJob.operation=='attributes'))
        assert child.options['attribute_example']['asset_id']==str(reference.id)
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==child.id))
        assert item.status=='completed'


@pytest.mark.asyncio
async def test_example_picker_lists_only_saved_manual_project_annotations(work_env,client,auth_headers):
    from services.annotations import save_annotation
    s=work_env;reference,annotation=await example(s)
    await save_annotation(s.db,s.asset,annotation.annotation_data,0,s.user.id,source='auto');await s.db.commit()
    path=f'/api/projects/{s.project.id}/preannotation-examples'
    response=client.get(path,headers=auth_headers,params={'page_size':1})
    assert response.status_code==200,response.text
    data=response.json()
    assert data['total']==1 and data['items'][0]['asset_id']==str(reference.id)
    assert data['items'][0]['annotation_version']==1 and data['items'][0]['compatible'] is True
    assert client.get(path,headers=auth_headers,params={'q':'missing'}).json()['total']==0
    assert client.get(f'/api/projects/{uuid4()}/preannotation-examples',headers=auth_headers).status_code==404
