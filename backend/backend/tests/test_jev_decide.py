"""JEV choice protocol and the existing attribute annotation lifecycle."""
import base64
import csv
from copy import deepcopy
from io import BytesIO, StringIO
import json
import zipfile

import httpx
import pytest
from fastapi import HTTPException
from PIL import Image
from pydantic import ValidationError
from sqlalchemy import select
from tests.test_project_work import work_env


def definition():
    return {'target_label':'person','fields':[
        {'key':'hat','name':'帽子','instruction':'只看头部','options':['鸭舌帽','头盔','没戴帽子']},
        {'key':'clothes','name':'衣服','instruction':'只看上身','options':['反光衣','防尘服','厨师服']}]}


def config(**changes):
    from schemas.model_service import ModelServiceConfig
    return ModelServiceConfig(key='jev',name='JEV-27B-VL',kind='caption',protocol='jev_decide',
        base_url='http://model.test/v1',endpoint='/decide',**changes)


def picture():
    out=BytesIO();Image.new('RGB',(64,48),'red').save(out,'PNG');return out.getvalue()


def test_jev_configuration_needs_no_openai_model_id():
    assert config().model_name==''
    with pytest.raises(ValidationError):config().model_copy().model_validate({**config().model_dump(),'kind':'detection'})


@pytest.mark.asyncio
async def test_jev_connection_checks_protocol_info_without_inference(monkeypatch):
    from services import model_inference
    real_client=httpx.AsyncClient;calls=[]
    def provider(request):
        calls.append(request.url.path)
        return httpx.Response(200,json={'protocol':'jev27-bare-v1','model':'autotrust/JEV-27B-VL'})
    monkeypatch.setattr(model_inference.httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(provider),**kwargs))
    assert (await model_inference.test_connection(config()))['ok']
    assert calls==['/v1/decide/info']


@pytest.mark.asyncio
async def test_jev_requests_each_attribute_and_pairs_returned_options():
    from services.object_attributes import analyze
    calls=[]
    def provider(request):
        body=json.loads(request.content);calls.append(body)
        assert request.url.path=='/v1/decide'
        assert body['kind']=='choice' and body['thinking']=='off'
        assert 'person' in body['state'][0] and '只看' in body['question']
        uri=body['state'][1]['image'];assert uri.startswith('data:image/jpeg;base64,')
        assert Image.open(BytesIO(base64.b64decode(uri.split(',')[1]))).format=='JPEG'
        return httpx.Response(200,json={'options':body['options'][::-1],'probabilities':[.03,.95,.02]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        result=await analyze(config(),picture(),definition(),client)
    assert len(calls)==2 and calls[0]['options']==definition()['fields'][0]['options']
    assert result['hat']['value']=='头盔'
    assert result['hat']['probabilities']=={'没戴帽子':.03,'头盔':.95,'鸭舌帽':.02}
    assert result['clothes']['value']=='防尘服'


@pytest.mark.asyncio
@pytest.mark.parametrize('reply',[
    {},{'options':['鸭舌帽'],'probabilities':[1]},
    {'options':['鸭舌帽','头盔','头盔'],'probabilities':[.1,.8,.1]},
    {'options':['鸭舌帽','头盔','其他'],'probabilities':[.1,.8,.1]},
    *[{'options':['鸭舌帽','头盔','没戴帽子'],'probabilities':values} for values in
      [[.1,.9],[True,0,0],['0.1',.8,.1],[-.1,1,.1],[0,1.1,0],[0,float('nan'),0],[0,float('inf'),0]]]])
async def test_invalid_jev_response_is_rejected(reply):
    from services.object_attributes import analyze
    # json.dumps allows non-finite values so validation sees the actual upstream error.
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,content=json.dumps(reply)))) as client:
        with pytest.raises(HTTPException) as error:await analyze(config(),picture(),definition(),client)
    assert error.value.status_code==502


@pytest.mark.asyncio
@pytest.mark.parametrize('example,free_text',[(True,False),(False,True)])
async def test_jev_rejects_unsupported_inputs_before_sending(example,free_text):
    from services.object_attributes import analyze
    schema=definition()
    if free_text:schema['fields'][1]['options']=[]
    def unexpected(request):raise AssertionError('Invalid configuration must not call upstream')
    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected)) as client:
        with pytest.raises(HTTPException) as error:
            await analyze(config(),picture(),schema,client,example={'content':picture(),'answer':{}} if example else None)
    assert error.value.status_code==400


@pytest.mark.asyncio
@pytest.mark.parametrize('options,probabilities',[
    (['鸭舌帽','头盔','没戴帽子'],[.5,.5,0]),(['鸭舌帽','头盔','无法判断'],[.01,.01,.98])])
async def test_jev_ties_and_unknown_remain_pending_unknown(options,probabilities):
    from services.object_attributes import analyze
    schema=definition();schema['fields']=schema['fields'][:1];schema['fields'][0]['options']=options
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'options':options,'probabilities':probabilities}))) as client:
        result=await analyze(config(),picture(),schema,client)
    assert result['hat']['value'] is None and result['hat']['reason']
    assert result['hat']['probabilities']==dict(zip(options,probabilities))


@pytest.mark.asyncio
async def test_detection_jev_manual_correction_review_and_export(work_env):
    from models import Annotation,Asset
    from models.project_work import ProjectWorkJob
    from schemas.project_work import WorkCreate
    from services import project_work as service
    from services.annotations import save_annotation,review
    from services.annotation_export import training_package
    s=work_env;s.project.task_type='caption';s.project.label_schema={'object_attributes':definition()}
    model=s.models[1];model.protocol='jev_decide';model.base_url='http://model.test/v1';model.endpoint='/decide';model.model_name=''
    s.models[0].labels=['person'];await s.db.commit()
    await service.create_job(s.db,s.project,WorkCreate(operation='detect_attributes',model_id=s.models[0].id,
        attribute_model_id=model.id,asset_ids=[s.asset.id],expansion=0),s.user.id)
    def provider(request):
        if request.url.path=='/detect':return httpx.Response(200,json={'detections':[{'class':'person','bbox':[0,0,32,48],'score':.9}]})
        body=json.loads(request.content)
        return httpx.Response(200,json={'options':body['options'],'probabilities':[.02,.95,.03]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        assert await service.process_one(s.sessions,client)
        assert await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        ann=await db.scalar(select(Annotation).where(Annotation.is_latest.is_(True)))
        assert ann.review_status=='pending' and ann.annotation_source=='auto'
        probabilities=deepcopy(ann.annotation_data['attributes']['hat']['probabilities'])
        asset=await db.get(Asset,ann.asset_id)
        changed=deepcopy(ann.annotation_data);changed['attributes']['hat'].update(value='没戴帽子',reason='人工修正')
        changed['attributes']['hat'].pop('probabilities')
        corrected=await save_annotation(db,asset,changed,ann.version,s.user.id)
        assert corrected.annotation_data['attributes']['hat']['probabilities']==probabilities
        assert corrected.review_status=='pending' and corrected.annotation_source=='manual'
        forged=deepcopy(corrected.annotation_data);forged['attributes']['hat']['probabilities']={'鸭舌帽':1,'头盔':0,'没戴帽子':0}
        with pytest.raises(HTTPException):await save_annotation(db,asset,forged,corrected.version,s.user.id)
        await review(db,corrected,asset,True,'已核验',s.user.id);await db.commit()
        stream,_=await training_package(db,s.project,attribute_filters={})
        with stream,zipfile.ZipFile(stream) as archive:
            row=json.loads(archive.read('annotations.jsonl').splitlines()[0])
            assert row['attributes']['hat']['value']=='没戴帽子' and row['attributes']['hat']['probabilities']==probabilities
            rows=list(csv.DictReader(StringIO(archive.read('attributes.csv').decode('utf-8-sig'))))
            assert json.loads(rows[0]['probabilities'])==probabilities and rows[0]['value']=='没戴帽子'
        child=await db.scalar(select(ProjectWorkJob).where(ProjectWorkJob.operation=='attributes'))
        assert child.model['config']['protocol']=='jev_decide'


@pytest.mark.asyncio
async def test_manual_cannot_invent_model_probabilities(work_env):
    from services.annotations import save_annotation
    s=work_env;s.project.task_type='caption';s.project.label_schema={'object_attributes':definition()};await s.db.commit()
    data={'attribute_schema':definition(),'attributes':{field['key']:{'value':field['options'][0],'reason':'人工',
        'probabilities':dict(zip(field['options'],[1,0,0]))} for field in definition()['fields']}}
    with pytest.raises(HTTPException) as error:await save_annotation(s.db,s.asset,data,0,s.user.id)
    assert '只读证据' in error.value.detail


@pytest.mark.asyncio
async def test_failed_second_attribute_writes_no_partial_annotation(work_env):
    from models import Annotation
    from models.project_work import ProjectWorkItem
    from schemas.project_work import WorkCreate
    from services import project_work as service
    s=work_env;s.project.task_type='caption';s.project.label_schema={'object_attributes':definition()}
    model=s.models[1];model.protocol='jev_decide';model.endpoint='/decide';await s.db.commit()
    job=await service.create_job(s.db,s.project,WorkCreate(operation='attributes',model_id=model.id,asset_ids=[s.asset.id]),s.user.id)
    def provider(request):
        body=json.loads(request.content)
        return httpx.Response(200,json={'options':body['options'],'probabilities':[.02,.95,.03]} if body['options'][0]=='鸭舌帽' else {})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        assert await db.scalar(select(Annotation)) is None
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
        assert item.status=='failed' and '概率无效' in item.error


@pytest.mark.asyncio
async def test_jev_config_api_round_trip(work_env,client,auth_headers):
    response=client.post('/api/model-services',headers=auth_headers,json=config().model_dump())
    assert response.status_code==201,response.text
    record=response.json();assert record['protocol']=='jev_decide' and record['model_name']==''
    updated=config().model_dump();updated['name']='JEV更新'
    response=client.put('/api/model-services/'+record['id'],headers=auth_headers,json=updated)
    assert response.status_code==200 and response.json()['revision']==2


@pytest.mark.asyncio
async def test_jev_is_rejected_in_general_caption_validation(work_env):
    from services.multimodal_validation import model_snapshot
    s=work_env;s.models[1].protocol='jev_decide';await s.db.commit()
    with pytest.raises(HTTPException) as error:await model_snapshot(s.db,s.models[1].id,1,'caption')
    assert error.value.status_code==400
