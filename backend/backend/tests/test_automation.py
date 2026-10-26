from io import BytesIO
import asyncio
from datetime import datetime
import pytest
import pytest_asyncio
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest_asyncio.fixture(autouse=True)
async def automation_tables(test_engine):
    from models.model_service import ModelService
    from models.automation import AutomationJob, AutomationItem
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda c: [table.create(c, checkfirst=True) for table in
            (ModelService.__table__, AutomationJob.__table__, AutomationItem.__table__)])


@pytest_asyncio.fixture
async def ready(test_db, test_project, test_asset, client):
    from models.model_service import ModelService
    from services.storage import get_storage
    test_project.label_schema={'labels':['person']}
    detector=ModelService(tenant_id=1,key='det',name='Detector',kind='detection',protocol='model_service',
        base_url='http://det.test',endpoint='/detect',model_name='',enabled=True,labels=[],label_mapping={},api_key_env='')
    reviewer=ModelService(tenant_id=1,key='qwen',name='Qwen',kind='caption',protocol='openai_chat',
        base_url='http://qwen.test/v1',endpoint='/chat/completions',model_name='Qwen3.8-27B',enabled=True,labels=[],label_mapping={},api_key_env='')
    test_db.add_all([detector,reviewer]);await test_db.commit()
    image=BytesIO();Image.new('RGB',(64,48),'white').save(image,'JPEG')
    await get_storage().save_file(BytesIO(image.getvalue()),test_asset.blob_path)
    import hashlib
    test_asset.content_hash=hashlib.sha256(image.getvalue()).hexdigest();await test_db.commit()
    return dict(project_id=str(test_project.id),kind='preannotate',detector_id=str(detector.id),reviewer_id=str(reviewer.id),config={})


def test_crop_expansion_and_confidence_gate():
    from services.automation_review import expanded_bounds, auto_pass
    box=dict(x=1,y=2,width=10,height=20,score=.91,label='person')
    assert expanded_bounds(box,64,48,.2)==(0,0,13,26)
    evidence=dict(boxes=[dict(index=0,verdict='pass',reason='符合')],completeness='pass',negative='uncertain',reason='没有遗漏')
    assert auto_pass([box],evidence,.9,True)
    assert not auto_pass([{**box,'score':.9}],evidence,.9,True)
    assert not auto_pass([box],{**evidence,'completeness':'uncertain'},.9,True)
    assert not auto_pass([],dict(boxes=[],completeness='pass',negative='pass',reason='无目标'),.9,True)
    assert auto_pass([{k:v for k,v in box.items() if k!='score'}],evidence,.9,False)
    assert auto_pass([box],{**evidence,'rounds':[],'template_version':1},.9,True)


@pytest.mark.asyncio
@pytest.mark.parametrize('selected',[['bike'],[]])
@pytest.mark.parametrize('outside_verdict',['pass','fail'])
async def test_review_uses_selected_completeness_scope_without_dropping_existing_boxes(client,auth_headers,ready,test_engine,test_db,test_project,test_asset,monkeypatch,selected,outside_verdict):
    from services import automation
    test_project.label_schema={'labels':['person','bike']};await test_db.commit()
    data=dict(boxes=[dict(x=1,y=2,width=10,height=20,label=label) for label in ['person','bike']],negative=False)
    annotation=client.post('/api/annotations',headers=auth_headers,json={'asset_id':str(test_asset.id),'annotation_data':data,'base_version':0}).json()
    async def audit(config,content,boxes,labels,*args,**kwargs):
        assert labels==(selected or ['person','bike'])
        assert boxes==data['boxes']
        return dict(boxes=[dict(index=i,verdict=outside_verdict if i==0 else 'pass',reason='逐框检查') for i in range(2)],completeness='pass',negative='uncertain',reason='所选类别无遗漏')
    monkeypatch.setattr(automation,'audit_image',audit)
    response=client.post('/api/automation/jobs',headers=auth_headers,json={**ready,'kind':'review','config':{'labels':selected}})
    assert response.status_code==201,response.text
    await automation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    items=client.get('/api/automation/jobs/'+response.json()['id']+'/items',headers=auth_headers).json()['items']
    assert items[0]['status']==('approved' if outside_verdict=='pass' else 'needs_review')
    assert items[0]['evidence']['provenance']['review_labels']==(selected or ['person','bike'])
    current=client.get('/api/annotations/'+annotation['id'],headers=auth_headers).json()
    assert current['annotation_data']==data and current['version']==1


def test_review_scope_rejects_unknown_classes_but_detector_prompts_allow_aliases(client,auth_headers,ready):
    invalid=client.post('/api/automation/jobs',headers=auth_headers,json={**ready,'kind':'review','config':{'labels':['not-project-class']}})
    assert invalid.status_code==400 and '审核类别' in invalid.json()['detail']
    detector=client.post('/api/automation/jobs',headers=auth_headers,json={**ready,'config':{'labels':['a walking person']}})
    assert detector.status_code==201,detector.text


@pytest.mark.asyncio
async def test_review_queue_orders_latest_results_first_with_stable_pagination(client,auth_headers,ready,test_db):
    from uuid import UUID
    from models.automation import AutomationItem
    job=client.post('/api/automation/jobs',headers=auth_headers,json=ready).json()
    original=await test_db.scalar(select(AutomationItem).where(AutomationItem.job_id==UUID(job['id'])))
    original.status='needs_review';original.created_at=datetime(2026,1,3);original.updated_at=datetime(2026,1,4)
    newest=[]
    for number in [1,2]:
        item=AutomationItem(id=UUID(f'aaaaaaaa-0000-4000-8000-{number:012d}'),job_id=original.job_id,asset_id=original.asset_id,content_hash=original.content_hash,
            status='needs_review',created_at=datetime(2026,1,1),updated_at=datetime(2026,1,5))
        test_db.add(item);newest.append(str(item.id))
    await test_db.commit()
    def page(number):
        response=client.get('/api/automation/review-queue',headers=auth_headers,params={'page':number,'page_size':1})
        assert response.status_code==200,response.text
        return response.json()
    assert [page(i)['items'][0]['id'] for i in [1,2,3]]==newest+[str(original.id)]
    assert page(1)['total']==3
    original.updated_at=datetime(2026,1,6);await test_db.commit()
    assert page(1)['items'][0]['id']==str(original.id)


def test_structured_review_must_cover_every_box_and_allow_uncertainty():
    from services.automation_review import validate_review
    valid=dict(boxes=[dict(index=0,verdict='uncertain',reason='遮挡')],completeness='uncertain',negative='uncertain',reason='需人工')
    assert validate_review(valid,[0])['boxes'][0]['verdict']=='uncertain'
    for value in [{**valid,'boxes':[]},{**valid,'completeness':'yes'}, {**valid,'boxes':valid['boxes']*2}]:
        with pytest.raises(ValueError):validate_review(value,[0])


@pytest.mark.asyncio
@pytest.mark.parametrize('box_count',[0,1,2,3,5])
async def test_qwen_review_sends_overview_and_separate_crops_with_three_image_limit(box_count):
    import base64,json,httpx
    from schemas.model_service import ModelServiceConfig
    from services.automation_review import audit_image
    config=ModelServiceConfig(key='qwen',name='Qwen',kind='caption',protocol='openai_chat',base_url='http://qwen.test/v1',endpoint='/chat/completions',model_name='Qwen')
    image=BytesIO();Image.new('RGB',(100,80),'white').save(image,'PNG')
    boxes=[dict(x=1+15*i,y=2,width=10,height=20,label='person',score=.95) for i in range(box_count)]
    expected_rounds=[list(range(i,min(i+2,box_count))) for i in range(0,box_count,2)] or [[]]
    seen=[]
    def handle(request):
        body=json.loads(request.content);content=body['messages'][0]['content']
        assert '未选类别不要求补标，不得以其漏标判定失败' in content[0]['text']
        assert '已有框仍须逐框检查' in content[0]['text']
        indices=expected_rounds[len(seen)];seen.append(indices)
        pictures=[row for row in content if row['type']=='image_url']
        assert len(pictures)==1+len(indices)<=3
        assert '拼接' not in content[0]['text']
        decoded=[Image.open(BytesIO(base64.b64decode(row['image_url']['url'].split(',')[1]))) for row in pictures]
        assert decoded[0].size==(100,80),'Overview must be a standalone image without a canvas'
        for position,index in enumerate(indices):
            assert f'Crop #{index}:' in content[2+position*2]['text']
            assert decoded[position+1].size==((13 if index==0 else 14),26)
            red,green,blue=decoded[position+1].getpixel((5,3))
            assert red>150 and green>150 and blue<120,'Crop must retain its yellow target box'
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(dict(
            boxes=[dict(index=index,verdict='pass',reason='正确') for index in indices],
            completeness='pass',negative='uncertain',reason='完整'))}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        result=await audit_image(config,image.getvalue(),boxes,['person'],.2,client=client)
    assert seen==expected_rounds
    assert [row['index'] for row in result['boxes']]==list(range(box_count))
    assert len(result['rounds'])==len(expected_rounds)
    if box_count:assert result['rounds'][0]['crops'][0]['bounds']==[0,0,13,26]
    else:assert result['rounds'][0]['crops']==[]


def test_job_creation_scope_and_parameter_validation(client,auth_headers,ready):
    response=client.post('/api/automation/jobs',headers=auth_headers,json=ready)
    assert response.status_code==201,response.text
    job=response.json();assert job['total']==1 and job['status']=='pending'
    assert client.get('/api/automation/jobs',headers=auth_headers).json()['total']==1
    items=client.get('/api/automation/jobs/'+job['id']+'/items',headers=auth_headers).json()
    assert items['items'][0]['annotation_version']==0
    for config in [dict(auto_approve_confidence=1.1),dict(crop_expansion=-.1),dict(confidence=float('inf'))]:
        if config.get('confidence')==float('inf'):continue
        assert client.post('/api/automation/jobs',headers=auth_headers,json={**ready,'config':config}).status_code==422
    assert client.post('/api/automation/jobs',json=ready).status_code==401


@pytest.mark.asyncio
async def test_worker_saves_provenance_and_auto_passes_only_strict_positive(client,auth_headers,ready,test_engine,test_db,monkeypatch):
    from services import automation
    from models import Annotation
    calls=[]
    async def detect(*args,**kwargs):return dict(boxes=[dict(x=1,y=2,width=10,height=20,label='person',score=.95)],warnings=[])
    async def audit(*args,**kwargs):
        calls.append(1)
        return dict(boxes=[dict(index=0,verdict='pass',reason='目标正确')],completeness='pass',negative='uncertain',reason='完整')
    monkeypatch.setattr(automation,'infer',detect);monkeypatch.setattr(automation,'audit_image',audit)
    job=client.post('/api/automation/jobs',headers=auth_headers,json=ready).json()
    assert await automation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    current=client.get('/api/automation/jobs/'+job['id'],headers=auth_headers).json()
    assert current['approved_count']==1 and current['processed']==1
    async with async_sessionmaker(test_engine)() as db:
        annotation=await db.scalar(select(Annotation))
        assert annotation.annotation_source=='model' and annotation.annotator_id is None
        assert annotation.review_status=='approved' and annotation.reviewer_id is None
    assert len(calls)==1


@pytest.mark.asyncio
async def test_negative_always_requires_admin_and_keeps_evidence(client,auth_headers,ready,test_engine,monkeypatch):
    from services import automation
    async def detect(*args,**kwargs):return dict(boxes=[],warnings=[])
    async def audit(*args,**kwargs):return dict(boxes=[],completeness='pass',negative='pass',reason='没有项目目标')
    monkeypatch.setattr(automation,'infer',detect);monkeypatch.setattr(automation,'audit_image',audit)
    job=client.post('/api/automation/jobs',headers=auth_headers,json=ready).json()
    await automation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    queue=client.get('/api/automation/review-queue',headers=auth_headers).json()
    assert queue['total']==1
    item=queue['items'][0]
    assert item['status']=='needs_review' and item['evidence']['negative']=='pass'
    response=client.post('/api/automation/items/'+item['id']+'/resolve',headers=auth_headers,json={'approved':True,'comment':'管理员确认无目标'})
    assert response.status_code==200,response.text
    assert response.json()['status']=='approved'


@pytest.mark.asyncio
async def test_cancel_and_human_edit_discard_late_results(client,auth_headers,ready,test_engine,monkeypatch):
    from services import automation
    from models import Annotation
    async def detect(*args,**kwargs):
        client.post('/api/automation/jobs/'+job['id']+'/cancel',headers=auth_headers)
        return dict(boxes=[dict(x=1,y=2,width=10,height=20,label='person',score=.99)],warnings=[])
    monkeypatch.setattr(automation,'infer',detect)
    job=client.post('/api/automation/jobs',headers=auth_headers,json={**ready,'config':{'auto_review':False}}).json()
    await automation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    async with async_sessionmaker(test_engine)() as db:assert await db.scalar(select(func.count()).select_from(Annotation))==0


@pytest.mark.asyncio
async def test_manual_review_does_not_change_boxes_and_stale_admin_resolution_is_rejected(client,auth_headers,ready,test_engine,test_asset,monkeypatch):
    from services import automation
    data=dict(boxes=[dict(x=1,y=2,width=10,height=20,label='person')],negative=False)
    annotation=client.post('/api/annotations',headers=auth_headers,json={'asset_id':str(test_asset.id),'annotation_data':data,'base_version':0}).json()
    request={**ready,'kind':'review'}
    async def audit(*args,**kwargs):return dict(boxes=[dict(index=0,verdict='uncertain',reason='遮挡')],completeness='uncertain',negative='uncertain',reason='转管理员')
    monkeypatch.setattr(automation,'audit_image',audit)
    job=client.post('/api/automation/jobs',headers=auth_headers,json=request).json()
    await automation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    item=client.get('/api/automation/review-queue',headers=auth_headers).json()['items'][0]
    old=client.get('/api/annotations/'+annotation['id'],headers=auth_headers).json()
    assert old['annotation_data']==data and old['version']==1
    updated=client.put('/api/annotations/'+annotation['id'],headers=auth_headers,json={'annotation_data':data,'base_version':1})
    assert updated.status_code==200
    assert client.post('/api/automation/items/'+item['id']+'/resolve',headers=auth_headers,json={'approved':True,'comment':'过期'}).status_code==409


@pytest.mark.asyncio
async def test_qwen_unavailable_goes_to_admin_and_nonadmin_cannot_approve_negative(client,auth_headers,ready,test_engine,test_db,test_user,monkeypatch):
    from services import automation
    from services.auth import create_access_token
    async def detect(*args,**kwargs):return dict(boxes=[],warnings=[])
    async def audit(*args,**kwargs):raise HTTPException(504,'模型超时')
    monkeypatch.setattr(automation,'infer',detect);monkeypatch.setattr(automation,'audit_image',audit)
    client.post('/api/automation/jobs',headers=auth_headers,json=ready)
    await automation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    item=client.get('/api/automation/review-queue',headers=auth_headers).json()['items'][0]
    assert item['status']=='needs_review' and '未能完成判断' in item['evidence']['reason']
    test_user.role='annotator';await test_db.commit()
    token=create_access_token({'user_id':test_user.id,'tenant_id':test_user.tenant_id})
    headers={'Authorization':'Bearer '+token}
    assert client.post('/api/annotations/'+item['annotation_id']+'/review',headers=headers,json={'approved':True}).status_code==403


@pytest.mark.asyncio
async def test_manual_reviewer_decision_during_inference_is_not_overwritten(client,auth_headers,ready,test_engine,test_asset,monkeypatch):
    from services import automation
    data=dict(boxes=[dict(x=1,y=2,width=10,height=20,label='person')],negative=False)
    annotation=client.post('/api/annotations',headers=auth_headers,json={'asset_id':str(test_asset.id),'annotation_data':data,'base_version':0}).json()
    async def audit(*args,**kwargs):
        assert client.post('/api/annotations/'+annotation['id']+'/review',headers=auth_headers,json={'approved':False,'review_comment':'人工发现问题'}).status_code==200
        return dict(boxes=[dict(index=0,verdict='pass',reason='正确')],completeness='pass',negative='uncertain',reason='完整')
    monkeypatch.setattr(automation,'audit_image',audit)
    job=client.post('/api/automation/jobs',headers=auth_headers,json={**ready,'kind':'review'}).json()
    await automation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    item=client.get('/api/automation/jobs/'+job['id']+'/items',headers=auth_headers).json()['items'][0]
    assert item['status']=='skipped'
    current=client.get('/api/annotations/'+annotation['id'],headers=auth_headers).json()
    assert current['review_status']=='rejected' and current['review_comment']=='人工发现问题'
