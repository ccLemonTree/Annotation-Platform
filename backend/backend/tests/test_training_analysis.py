"""Metric evidence and LLM reports: isolated files/database, no training launched."""
import json
from uuid import uuid4,UUID
from datetime import datetime,timedelta
import pytest
import pytest_asyncio


def test_training_analysis_entrypoint_exists():
    from importlib.util import find_spec
    assert find_spec('services.training_analysis'), '训练效果分析尚未接入'


def test_analysis_request_limits():
    from services.training_analysis import AnalysisRequest
    from pydantic import ValidationError
    for body in ({'compare_ids':[uuid4() for _ in range(4)]},{'compare_ids':[UUID(int=1),UUID(int=1)]},{'prompt':'   '}):
        with pytest.raises(ValidationError):AnalysisRequest(model_id=uuid4(),**body)


def test_sparse_csv_keeps_regular_and_ema_separate(tmp_path):
    from services.training_metrics import collect_metrics
    (tmp_path/'metrics.csv').write_text('epoch,step,train/loss,val/mAP_50_95,val/ema_mAP_50_95\n0,1,2,,\n0,2,,0.4,0.5\n1,3,1,,\n1,4,,0.3,0.6\n2,5,nan,inf,\n')
    result=collect_metrics(tmp_path)
    regular=next(m for m in result['metrics'] if m['name']=='val/mAP_50_95')
    ema=next(m for m in result['metrics'] if m['name']=='val/ema_mAP_50_95')
    assert regular['last']==.3 and regular['best']==.4 and regular['best_epoch']==0
    assert ema['last']==.6 and ema['best_epoch']==1
    assert next(m for m in result['metrics'] if m['name']=='train/loss')['best']==1
    assert result['files'][0]['sha256'] and result['warnings']


@pytest.mark.parametrize('file,body,name',[
    ('results.csv',' epoch, metrics/mAP50(B), val/box_loss\n0,0.4,1.2\n1,0.5,1.0\n','metrics/mAP50(B)'),
    ('checkpoint-20/trainer_state.json',json.dumps({'log_history':[{'step':10,'loss':2},{'step':20,'eval_loss':1.1}]}),'eval_loss'),
    ('logging.jsonl','{"global_step":1,"mean_pos":0.6,"mean_neg":0.3}\n{"global_step":2,"mean_pos":0.7,"mean_neg":0.2}\n','mean_pos'),
    ('log.txt','{"epoch":0,"test_coco_eval_bbox":[0.4,0.6,0.5]}\n','test_coco_eval_bbox/AP50:95'),
    ('metrics.jsonl','{"step":1,"metrics":{"val/map":0.3}}\n','metrics/val/map'),
])
def test_all_trainer_metric_formats(tmp_path,file,body,name):
    from services.training_metrics import collect_metrics
    path=tmp_path/file;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(body)
    result=collect_metrics(tmp_path)
    assert any(m['name']==name for m in result['metrics']),result


def test_bounded_reader_excludes_symlinks_weights_and_duplicates(tmp_path):
    from services.training_metrics import collect_metrics
    (tmp_path/'results.csv').write_text('epoch,loss\n0,1\n')
    (tmp_path/'weights.pth').write_bytes(b'not a pickle')
    nested=tmp_path/'checkpoint-1';nested.mkdir()
    (nested/'trainer_state.json').write_text('{"log_history":[{"step":1,"loss":3}]}')
    nested2=tmp_path/'checkpoint-2';nested2.mkdir()
    (nested2/'trainer_state.json').write_text('{"log_history":[{"step":1,"loss":3},{"step":2,"loss":2}]}')
    result=collect_metrics(tmp_path)
    assert len([m for m in result['metrics'] if m['name']=='loss'])==2 # distinct sources, never merge runs/files
    assert len([f for f in result['files'] if f['path'].endswith('trainer_state.json')])==1
    (tmp_path/'results.csv').write_text('x'* (4*1024*1024+1))
    assert any('大小' in w for w in collect_metrics(tmp_path)['warnings'])


def test_comparison_warns_for_remote_paths_and_different_labels():
    from services.training_analysis import compare_evidence
    def sample(id,labels):return {'id':id,'kind':'rfdetr_medium','source':{'kind':'remote','path':'/same/path'},'labels':labels,'parameters':{},'metrics':[{'name':'val/mAP','last':.4,'best':.5,'direction':'higher','file':'metrics.csv'}]}
    result=compare_evidence(sample('a',['fire']),[sample('b',['smoke'])])
    assert any('类别' in w for w in result[0]['warnings'])
    assert any('路径' in w for w in result[0]['warnings'])
    assert result[0]['metrics'][0]['last_delta']==0
    assert result[0]['comparable'] is False


def test_embedding_comparison_does_not_require_detection_classes():
    from services.training_analysis import compare_evidence
    current={'id':'a','kind':'wemm','source':{'kind':'snapshot','sha256':'a'*64},'labels':[],
        'parameters':{'loss_type':'infonce'},'image_digest':'sha256:identical','metrics':[
            {'name':'eval_loss','file':'logging.jsonl','last':.2,'best':.1,'direction':'lower'}]}
    previous={**current,'id':'b'}
    comparison=compare_evidence(current,[previous])[0]
    assert comparison['comparable'] is True
    assert not any('类别' in warning for warning in comparison['warnings'] if '一致' not in warning)


@pytest_asyncio.fixture
async def tables(test_engine):
    from models.training_run import TrainingRun,TrainingGpuHold,TrainingAnalysis
    from models.model_service import ModelService
    async with test_engine.begin() as conn:
        for table in (TrainingRun,TrainingGpuHold,TrainingAnalysis,ModelService):
            await conn.run_sync(lambda c,t=table:t.__table__.create(c,checkfirst=True))


@pytest.mark.asyncio
async def test_report_is_persisted_scoped_and_uses_only_sanitized_evidence(tables,client,auth_headers,test_db,test_user,monkeypatch):
    from models.training_run import TrainingRun
    from models.model_service import ModelService
    from services import training_analysis
    rows=[]
    for index in range(2):
        row=TrainingRun(tenant_id=1,user_id=1,name=f'训练{index}',kind='wemm',status='completed',
            config={'parameters':{'loss_type':'infonce'},'environment':{'SECRET':'do-not-send'},'model_path':'/models/wemm'},
            source={'kind':'remote','path':'/data'},server={'id':'default','name':'server'},output_dir='/output',created_at=datetime.now()+timedelta(seconds=index))
        test_db.add(row);rows.append(row)
    model=ModelService(tenant_id=1,key='qwen',name='Qwen',kind='caption',protocol='openai_chat',base_url='http://127.0.0.1:8001/v1',endpoint='/chat/completions',model_name='qwen',enabled=True,timeout_seconds=60,confidence=.25,iou=.45,labels=[],label_mapping={},api_key_env='',chat_prompts={})
    test_db.add(model);await test_db.commit()
    async def remote(db,run):return {'metrics':[{'name':'eval_loss','last':.2,'best':.1,'direction':'lower','file':'metrics.csv','series':[]}],'files':[],'warnings':[]}
    captured=[]
    async def llm(config,evidence,prompt):
        captured.append(evidence)
        return {'summary':'验证损失较低，仍需独立验证。','findings':['eval_loss=0.2'],'recommendations':['固定验证集对比。'],'limitations':['没有检索评测。']}
    monkeypatch.setattr(training_analysis,'read_metrics',remote);monkeypatch.setattr(training_analysis,'ask_model',llm)
    base='/api/training/runs/'+str(rows[1].id)
    body={'model_id':str(model.id),'compare_ids':[str(rows[0].id)],'prompt':'按证据分析'}
    result=client.post(base+'/analysis',headers=auth_headers,json=body)
    assert result.status_code==200,result.text
    assert 'do-not-send' not in json.dumps(captured)
    report=result.json();assert report['analysis']['summary'].startswith('验证损失')
    assert client.get(base+'/analysis',headers=auth_headers).json()[0]['id']==report['id']
    assert client.get(base+'/analysis/'+report['id'],headers=auth_headers).json()['analysis']==report['analysis']
    assert client.get(base+'/comparison-candidates',headers=auth_headers).json()['items'][0]['id']==str(rows[0].id)
    # Tenant boundary checked before remote or model access (use nonexistent UUID).
    bad=client.post(base+'/analysis',headers=auth_headers,json={**body,'compare_ids':[str(uuid4())]})
    assert bad.status_code==404
    duplicate=client.post(base+'/analysis',headers=auth_headers,json={**body,'compare_ids':[str(rows[1].id)]})
    assert duplicate.status_code in (400,422)


@pytest.mark.asyncio
async def test_model_response_schema_and_failure_no_fabricated_report():
    from services.training_analysis import ask_model
    from schemas.model_service import ModelServiceConfig
    from fastapi import HTTPException
    import httpx
    config=ModelServiceConfig(key='q',name='q',kind='caption',protocol='openai_chat',base_url='http://127.0.0.1:8001/v1',endpoint='/chat/completions',model_name='q')
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request:httpx.Response(200,json={'choices':[{'message':{'content':'{"summary":"made up"}'}}]}))) as session:
        with pytest.raises(HTTPException,match='502'):
            await ask_model(config,{'current':{'metrics':[]},'previous':[],'comparisons':[]},'test',client=session)

@pytest.mark.asyncio
@pytest.mark.parametrize('live', [False, True])
async def test_missing_metrics_use_saved_evidence_with_provenance(tables,test_db,test_user,monkeypatch,live):
    from models.training_run import TrainingRun,TrainingAnalysis
    from services import training_analysis
    runs=[]
    for index in range(2):
        run=TrainingRun(tenant_id=1,user_id=1,name=f'train-{index}',kind='rfdetr_2xlarge',status='completed',
            config={'parameters':{}},source={'kind':'remote','path':'/dataset'},server={},output_dir='/output',
            inspection={},created_at=datetime.now()+timedelta(seconds=index))
        test_db.add(run);runs.append(run)
    await test_db.flush()
    metric=dict(name='val/mAP',file='metrics.csv',last=.6,best=.7,direction='higher',series=[])
    archived=dict(id=str(runs[0].id),name=runs[0].name,kind=runs[0].kind,status='completed',
        source={'kind':'remote','path':'/original-dataset'},labels=['fire','smoke'],parameters={},
        metrics=[metric],files=[],warnings=[],image_digest='sha256:old')
    report=TrainingAnalysis(tenant_id=1,user_id=1,run_id=runs[0].id,evidence={'current':archived},model={},prompt='p',analysis={})
    test_db.add(report);await test_db.commit()
    async def remote(db,run):
        return dict(metrics=[{**metric,'last':.8}] if live or run.id==runs[1].id else [],files=[],
            warnings=[] if live or run.id==runs[1].id else ['训练产物目录不存在'])
    monkeypatch.setattr(training_analysis,'read_metrics',remote)
    bundle=await training_analysis.evidence(test_db,test_user,runs[1].id,training_analysis.EvidenceRequest(compare_ids=[runs[0].id]))
    previous=bundle['previous'][0]
    assert previous['metrics'][0]['last']==(.8 if live else .6)
    if not live:
        assert previous['labels']==['fire','smoke'] and previous['source']==archived['source']
        assert previous['metrics_origin']=='saved_report' and previous['metrics_report_id']==str(report.id)
        assert any('已保存' in text for text in previous['warnings'])
        assert bundle['comparisons'][0]['metrics'][0]['last_delta']==pytest.approx(.2)

@pytest.mark.asyncio
async def test_analysis_retries_transient_gateway_failure_once_without_retrying_auth():
    from services.training_analysis import ask_model
    from schemas.model_service import ModelServiceConfig
    from fastapi import HTTPException
    import httpx
    config=ModelServiceConfig(key='q',name='q',kind='caption',protocol='openai_chat',base_url='http://127.0.0.1:8001/v1',endpoint='/chat/completions',model_name='q')
    evidence={'current':{'metrics':[]},'previous':[],'comparisons':[]}
    verdict={'summary':'分析完成','findings':['无指标'],'recommendations':[],'limitations':['未独立评测']}
    seen=[]
    def response(request):
        seen.append(request)
        return httpx.Response(502) if len(seen)==1 else httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(verdict)}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as session:
        assert (await ask_model(config,evidence,'p',session))['summary']=='分析完成'
    assert len(seen)==2
    for status in (401,403,502):
        seen.clear()
        def failure(request):
            seen.append(request)
            return httpx.Response(status,text='secret-must-not-leak')
        async with httpx.AsyncClient(transport=httpx.MockTransport(failure)) as session:
            with pytest.raises(HTTPException) as error:await ask_model(config,evidence,'p',session)
        assert len(seen)==(2 if status==502 else 1)
        assert str(status) in error.value.detail and 'secret-must-not-leak' not in error.value.detail


def test_reader_records_actual_rfdetr_classes_without_exposing_config(tmp_path):
    from services.training_metrics import collect_metrics
    (tmp_path/'training_config.json').write_text(json.dumps({'class_names':['fire','smoke','star'],'secret':'never-copy'}))
    result=collect_metrics(tmp_path)
    assert result['recorded_labels']==['fire','smoke','star']
    assert 'never-copy' not in json.dumps(result)


@pytest.mark.asyncio
async def test_chat_request_bounds_metrics_and_preserves_exact_values():
    from services.training_analysis import ask_model
    from schemas.model_service import ModelServiceConfig
    import httpx
    captured=[]
    def response(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'summary':'结果','findings':['loss=0.2'],'recommendations':[],'limitations':['未独立评测']})}}]})
    metrics=[dict(name=f'val/loss_{i}',file='metrics.csv',direction='lower',last=.2,best=.1,series=[{'value':1}]*80) for i in range(80)]
    config=ModelServiceConfig(key='q',name='q',kind='caption',protocol='openai_chat',base_url='http://127.0.0.1:8001/v1',endpoint='/chat/completions',model_name='q')
    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as session:
        await ask_model(config,{'current':{'metrics':metrics,'warnings':[]},'previous':[],'comparisons':[]},'提示词',session)
    content=json.loads(captured[0]['messages'][1]['content'])
    assert len(content['current']['metrics'])==16
    assert all('series' not in metric for metric in content['current']['metrics'])
    assert content['current']['metrics'][0]['last']==.2 and len(metrics[0]['series'])==80


@pytest.mark.asyncio
async def test_tenant_and_role_boundaries_before_remote_calls(tables,client,auth_headers,test_db,test_user,monkeypatch):
    from models import Tenant
    from models.training_run import TrainingRun
    from services import training_analysis
    test_db.add(Tenant(id=2,tenant_name='other'));await test_db.commit()
    foreign=TrainingRun(tenant_id=2,user_id=1,name='other',kind='yolo26',status='completed',config={},source={},server={},output_dir='/output')
    test_db.add(foreign);await test_db.commit()
    async def no_remote(*args):raise AssertionError('Tenant check must precede SSH')
    monkeypatch.setattr(training_analysis,'read_metrics',no_remote)
    url='/api/training/runs/'+str(foreign.id)
    for suffix in ('/analysis','/comparison-candidates'):
        assert client.get(url+suffix,headers=auth_headers).status_code==404
    assert client.post(url+'/analysis/evidence',headers=auth_headers,json={'compare_ids':[]}).status_code==404
    test_user.role='annotator';await test_db.commit()
    assert client.post(url+'/analysis',headers=auth_headers,json={'model_id':str(uuid4())}).status_code==403
