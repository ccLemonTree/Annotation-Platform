"""Task-local review instructions preserve immutable model snapshots and legacy supplements."""
import json
from uuid import UUID,uuid4

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.test_automation import ready,automation_tables
from tests.test_project_work import work_env


@pytest.mark.parametrize('field',['review_prompt','preannotation_prompt','match_review_prompt'])
def test_core_and_example_prompt_limits(field):
    from schemas.automation import AutomationConfig
    from schemas.project_work import MatchReviewBatch
    if field=='match_review_prompt':
        schema=MatchReviewBatch;args={'model_id':uuid4()};field='review_prompt'
    else:schema=AutomationConfig;args={}
    assert getattr(schema(**args,**{field:'x'*8000}),field)=='x'*8000
    with pytest.raises(ValidationError):schema(**args,**{field:'x'*8001})


def test_existing_review_supplement_limits_and_defaults_remain_compatible():
    from schemas.automation import AutomationConfig
    from schemas.project_work import MatchReviewBatch
    for schema,args in [(AutomationConfig,{}),(MatchReviewBatch,{'model_id':uuid4()})]:
        assert schema(**args,prompt='x'*2000).prompt=='x'*2000
        with pytest.raises(ValidationError):schema(**args,prompt='x'*2001)
        assert schema(**args).review_prompt==''
    assert AutomationConfig().preannotation_prompt==''


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['override','blank','legacy'])
async def test_automation_review_uses_task_core_and_preserves_supplement_and_snapshot(client,auth_headers,ready,test_engine,test_db,monkeypatch,mode):
    from models import ModelService
    from models.automation import AutomationJob
    from services import automation,automation_review
    model=await test_db.get(ModelService,UUID(ready['reviewer_id']))
    model.chat_prompts={'review':'全局审核核心'};await test_db.commit()
    supplement='原有补充要求只核对可见证据'
    config={'prompt':supplement}
    if mode!='legacy':config['review_prompt']='本任务审核核心' if mode=='override' else '   '
    response=client.post('/api/automation/jobs',headers=auth_headers,json={**ready,'config':config})
    assert response.status_code==201,response.text
    job_id=UUID(response.json()['id']);snapshot=await test_db.get(AutomationJob,job_id)
    original_snapshot=json.loads(json.dumps(snapshot.models))
    assert original_snapshot['reviewer']['config']['chat_prompts']['review']=='全局审核核心'
    if mode=='legacy':
        snapshot.config={key:value for key,value in snapshot.config.items() if key not in ('review_prompt','preannotation_prompt')}
    await test_db.commit()
    async def detect(*args,**kwargs):return {'boxes':[dict(x=1,y=2,width=10,height=20,label='person',score=.99)],'warnings':[]}
    monkeypatch.setattr(automation,'infer',detect)
    seen=[]
    async def provider(*args,**kwargs):
        text=kwargs['json']['messages'][0]['content'][0]['text'];seen.append(text)
        assert text.startswith('本任务审核核心' if mode=='override' else '全局审核核心')
        assert supplement in text and '系统输出约束' in text and '本轮编号' in text
        assert '未选类别不要求补标' in text and 'person' in text
        return {'choices':[{'message':{'content':json.dumps(dict(boxes=[dict(index=0,verdict='pass',reason='目标可见')],
            completeness='pass',negative='uncertain',reason='符合'))}}]}
    monkeypatch.setattr(automation_review,'response_json',provider)
    assert await automation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    assert len(seen)==1
    await test_db.refresh(model);await test_db.refresh(snapshot)
    assert model.revision==1 and model.chat_prompts=={'review':'全局审核核心'}
    assert snapshot.models==original_snapshot
    assert snapshot.approved_count==1


@pytest.mark.asyncio
async def test_example_preannotation_prompt_supplements_fixed_json_and_coordinate_contract(client,auth_headers,ready,test_db,test_engine,test_project,test_user,monkeypatch):
    from models import ModelService
    from models.automation import AutomationJob
    from services import automation,detection_preannotation
    from tests.test_detection_examples import reference
    ref,ann=await reference(test_db,test_project,test_user)
    requirement='仅框出完整且无遮挡的人，不猜测被遮挡的人'
    response=client.post('/api/automation/jobs',headers=auth_headers,json={**ready,'detector_id':None,
        'preannotation_model_id':ready['reviewer_id'],'reviewer_id':None,'example_asset_id':str(ref.id),
        'example_annotation_version':ann.version,'config':{'auto_review':False,'preannotation_prompt':requirement}})
    assert response.status_code==201,response.text
    snapshot=await test_db.get(AutomationJob,UUID(response.json()['id']));original_snapshot=json.loads(json.dumps(snapshot.models))
    await test_db.commit()
    seen=[]
    async def provider(*args,**kwargs):
        messages=kwargs['json']['messages'];text=messages[-1]['content'][0]['text'];seen.append(text)
        assert requirement in text and '只输出JSON' in text and '归一化到0至1000' in text and '最多100框' in text
        assert '"person"' in text and [row['role'] for row in messages]==['user','assistant','user']
        return {'choices':[{'message':{'content':'{"boxes":[],"uncertain":true,"reason":"人员遮挡"}'}}]}
    monkeypatch.setattr(detection_preannotation,'response_json',provider)
    assert await automation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    assert len(seen)==1
    model=await test_db.get(ModelService,UUID(ready['reviewer_id']),populate_existing=True)
    await test_db.refresh(snapshot)
    assert model.revision==1 and model.chat_prompts=={} and snapshot.models==original_snapshot
    assert snapshot.config['preannotation_prompt']==requirement


@pytest.mark.parametrize('config',[{'preannotation_prompt':'范例补充','auto_review':False},
    {'review_prompt':'审核核心','auto_review':False}])
def test_nonapplicable_new_prompt_is_rejected_instead_of_ignored(client,auth_headers,ready,config):
    response=client.post('/api/automation/jobs',headers=auth_headers,json={**ready,'config':config})
    assert response.status_code==422,response.text


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['override','blank','legacy'])
async def test_match_review_uses_task_core_with_original_supplement_without_changing_snapshot(work_env,mode):
    from models import ModelService
    from services.match_review import create_review_jobs
    from services.project_work import process_one
    from schemas.project_work import MatchReviewBatch
    from tests.test_match_ai_review import pending_query
    s=work_env;s.models[1].chat_prompts={'match_review':'全局匹配审核核心'};await s.db.commit()
    query=await pending_query(s)
    args={'model_id':s.models[1].id,'query_ids':[query.id],'prompt':'原有匹配补充要求'}
    if mode!='legacy':args['review_prompt']='本任务匹配核心' if mode=='override' else '   '
    job,=await create_review_jobs(s.db,s.project,MatchReviewBatch(**args),s.user.id)
    original_snapshot=json.loads(json.dumps(job.model))
    if mode=='legacy':
        job.options={key:value for key,value in job.options.items() if key!='review_prompt'};await s.db.commit()
    else:assert job.options['review_prompt']==args['review_prompt'].strip()
    seen=[]
    def provider(request):
        text=json.loads(request.content)['messages'][0]['content'];seen.append(text)
        assert text.startswith('本任务匹配核心' if mode=='override' else '全局匹配审核核心')
        assert '原有匹配补充要求' in text and '仅返回JSON' in text and 'correct|incorrect|uncertain' in text
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"judgment":"correct","reason":"符合查询"}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        assert await process_one(s.sessions,client)
    assert len(seen)==1
    await s.db.refresh(s.models[1]);await s.db.refresh(job)
    assert s.models[1].revision==1 and s.models[1].chat_prompts=={'match_review':'全局匹配审核核心'}
    assert job.model==original_snapshot
