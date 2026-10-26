"""Output jobs use isolated fixtures; never export business checkpoints."""
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID,uuid4
import pytest
from pydantic import ValidationError
from tests.test_training_runs import training_tables,spec


def request(**values):
    from schemas.model_output import ModelOutputCreate
    return ModelOutputCreate(**dict(dict(format='onnx',server_id='default',gpu_uuid='GPU-test-1',
        source_path='/data/run/best.pt',output_root='/data/exports'),**values))


@pytest.mark.parametrize('values',[
    {'batch':0},{'dynamic_batch':True,'min_batch':8,'opt_batch':7,'max_batch':8},
    {'dynamic_batch':True,'min_batch':1,'opt_batch':9,'max_batch':8},
    {'source_path':'/data/../secret'},{'source_path':'/best.pt'},
    {'output_root':'/etc/export'},{'gpu_uuid':'0; stop'},
    {'format':'merge','dynamic_batch':True},{'input_name':'a:b'},
])
def test_reject_invalid_output_parameters(values):
    with pytest.raises(ValidationError):request(**values)


def test_output_api_creates_draft_without_remote_work(client,auth_headers,monkeypatch):
    from services import training_runs
    async def forbidden(*args,**kwargs):raise AssertionError('Saving must not invoke SSH')
    monkeypatch.setattr(training_runs,'remote',forbidden)
    parent=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    result=client.post('/api/training/runs/'+parent['id']+'/outputs',headers=auth_headers,json=request(batch=7).model_dump(mode='json'))
    assert result.status_code==201,result.text
    job=result.json()
    assert job['kind']=='model_export' and job['status']=='draft'
    assert job['config']['output']['batch']==7 and job['source']['run_id']==parent['id']
    assert job['config']['model_path']=='/data/run/best.pt'
    assert client.get('/api/training/runs/'+job['id'],headers=auth_headers).status_code==200


@pytest.mark.asyncio
async def test_output_type_and_tenant_and_admin_checks(client,auth_headers,test_db,test_user):
    from models.training_run import TrainingRun
    parent=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    url='/api/training/runs/'+parent['id']+'/outputs'
    assert client.post(url,headers=auth_headers,json=request(format='merge').model_dump(mode='json')).status_code==422
    assert client.post(url,json=request().model_dump(mode='json')).status_code==401
    test_user.role='annotator';await test_db.commit()
    assert client.post(url,headers=auth_headers,json=request().model_dump(mode='json')).status_code==403
    from models import Tenant
    test_db.add(Tenant(id=999,tenant_name='other'));await test_db.commit()
    test_user.role='admin';row=await test_db.get(TrainingRun,UUID(parent['id']));row.tenant_id=999;await test_db.commit()
    assert client.post(url,headers=auth_headers,json=request().model_dump(mode='json')).status_code==404


def test_merge_uses_original_image_and_rejects_full_tuning(client,auth_headers):
    parent=client.post('/api/training/runs',headers=auth_headers,json=spec('wemm',model_path='/data/WeMM',parameters={'tuner_type':'lora'}).model_dump(mode='json')).json()
    result=client.post('/api/training/runs/'+parent['id']+'/outputs',headers=auth_headers,
        json=request(format='merge',source_path='/data/run/checkpoint-40',model_type='wemm_embedding').model_dump(mode='json'))
    assert result.status_code==201,result.text
    job=result.json()
    assert job['kind']=='model_merge' and job['config']['image']==parent['config']['image']
    assert job['config']['model_path']=='/data/WeMM'
    assert job['config']['adapter_path']=='/data/run/checkpoint-40'
    assert job['source']['model_kind']=='wemm'


def test_cross_server_requires_explicit_base_model_path(client,auth_headers,monkeypatch):
    from services import training_runs
    async def snapshot(db,id,tenant):return dict(id=id,name='target',revision=1)
    parent=client.post('/api/training/runs',headers=auth_headers,json=spec('wemm',model_path='/data/WeMM').model_dump(mode='json')).json()
    monkeypatch.setattr(training_runs,'server_snapshot',snapshot)
    result=client.post('/api/training/runs/'+parent['id']+'/outputs',headers=auth_headers,
        json=request(format='merge',source_path='/data/run/checkpoint-1',server_id=str(uuid4())).model_dump(mode='json'))
    assert result.status_code==422,result.text


def test_trtexec_fixed_and_dynamic_use_actual_input_name():
    from services.model_output_runner import trtexec_args
    fixed=trtexec_args('/out/model.onnx','/out/model.engine',request(format='tensorrt',batch=4).model_dump(), 'images',880,880)
    assert '--fp16' in fixed and not any('Shapes=' in item for item in fixed)
    dynamic=trtexec_args('/out/model.onnx','/out/model.engine',request(format='tensorrt',dynamic_batch=True,opt_batch=7,max_batch=8).model_dump(),'actual_input',880,880)
    assert '--minShapes=actual_input:1x3x880x880' in dynamic
    assert '--optShapes=actual_input:7x3x880x880' in dynamic
    assert '--maxShapes=actual_input:8x3x880x880' in dynamic
    assert '--skipInference' in dynamic


def test_swift_merge_argv_is_not_shell_text():
    from services.model_output_runner import swift_args
    args=swift_args(dict(model_path='/data/base model',adapter_path='/data/checkpoint-1',output=request(format='merge',model_type='wemm_embedding').model_dump()),'/output/merged')
    assert args[:2]==['swift','export']
    for flag,value in [('--model','/data/base model'),('--adapters','/data/checkpoint-1'),('--model_type','wemm_embedding'),('--merge_lora','true'),('--torch_dtype','float16'),('--output_dir','/output/merged')]:
        assert args[args.index(flag)+1]==value


def test_output_script_and_remote_payload_have_no_dataset(client,auth_headers,monkeypatch):
    from services import training_runs
    from services.training_config import build_script
    parent=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    job=client.post('/api/training/runs/'+parent['id']+'/outputs',headers=auth_headers,json=request().model_dump(mode='json')).json()
    code=build_script(job['config'])
    compile(code,'export.py','exec')
    assert 'model.train(' not in code and 'prepare_detection(' not in code
    captured=[]
    monkeypatch.setattr(training_runs,'bridge',lambda server,data:captured.append(data) or {'ok':True,'checks':[]})
    result=client.post('/api/training/runs/'+job['id']+'/precheck',headers=auth_headers)
    assert result.status_code==200,result.text
    assert 'dataset_code' not in captured[0]['payload']
    assert 'output_code' in captured[0]['payload']


@pytest.mark.parametrize('dynamic',[False,True])
def test_real_onnx_rename_and_batch_shape(tmp_path,dynamic):
    onnx=pytest.importorskip('onnx')
    from services.model_output_runner import prepare_onnx
    batch='batch' if dynamic else 4
    dims=[batch,3,'height' if dynamic else 640,'width' if dynamic else 640]
    model=onnx.helper.make_model(onnx.helper.make_graph([onnx.helper.make_node('Identity',['images'],['pred'])],
        'test',[onnx.helper.make_tensor_value_info('images',onnx.TensorProto.FLOAT,dims)],
        [onnx.helper.make_tensor_value_info('pred',onnx.TensorProto.FLOAT,dims)]))
    path=tmp_path/'model.onnx';onnx.save(model,path)
    info=prepare_onnx(path,request(batch=4,dynamic_batch=dynamic).model_dump(),yolo=True)
    actual=onnx.load(path);onnx.checker.check_model(actual)
    assert actual.graph.input[0].name=='input' and actual.graph.output[0].name=='output'
    assert list(actual.graph.node[0].input)==['input'] and list(actual.graph.node[0].output)==['output']
    assert info['height']==640 and info['width']==640
    assert actual.graph.input[0].type.tensor_type.shape.dim[0].HasField('dim_param')==dynamic


def test_existing_fixed_onnx_cannot_be_claimed_dynamic(tmp_path):
    onnx=pytest.importorskip('onnx')
    from services.model_output_runner import prepare_onnx
    tensor=lambda name:onnx.helper.make_tensor_value_info(name,onnx.TensorProto.FLOAT,[1,3,880,880])
    graph=onnx.helper.make_model(onnx.helper.make_graph([onnx.helper.make_node('Identity',['input'],['output'])],'t',[tensor('input')],[tensor('output')]))
    path=tmp_path/'model.onnx';onnx.save(graph,path)
    with pytest.raises(ValueError,match='固定batch'):prepare_onnx(path,request(dynamic_batch=True).model_dump())


@pytest.mark.parametrize('resolution',[576,880])
def test_rfdetr_uses_checkpoint_class_resolution_and_trust(monkeypatch,tmp_path,resolution):
    import sys
    from services import model_output_runner as runner
    calls=[]
    class Model:
        model_config=SimpleNamespace(resolution=resolution)
        @staticmethod
        def from_checkpoint(path,**kwargs):calls.append(('load',path,kwargs));return Model()
        def export(self,**kwargs):
            calls.append(('export',kwargs));(Path(kwargs['output_dir'])/'model.onnx').write_bytes(b'fixture')
    monkeypatch.setitem(sys.modules,'rfdetr',SimpleNamespace(RFDETR=Model))
    monkeypatch.setattr(runner,'prepare_onnx',lambda *a,**k:{'input_name':'input'})
    options=request(source_path='/data/model.pth',trust_checkpoint=True,dynamic_batch=True,opt_batch=7,max_batch=8).model_dump()
    runner.export_onnx(dict(model_kind='rfdetr_2xlarge',model_path='/data/model.pth',output=options),tmp_path)
    assert calls[0][2]=={'trust_checkpoint':True}
    assert calls[1][1]['shape']==(resolution,resolution) and calls[1][1]['batch_size']==7 and calls[1][1]['dynamic_batch'] is True
    assert calls[1][1]['fp16'] is True


def test_sources_detect_adapter_change_and_reject_missing_weights(tmp_path):
    from services.model_output_runner import sources
    base=tmp_path/'base';adapter=tmp_path/'adapter';base.mkdir();adapter.mkdir()
    (base/'config.json').write_text('{}');(base/'model.safetensors').write_bytes(b'base')
    (adapter/'adapter_config.json').write_text('{}');(adapter/'adapter_model.safetensors').write_bytes(b'lora')
    config=dict(kind='model_merge',model_path=str(base),adapter_path=str(adapter))
    before=sources(config);(adapter/'adapter_model.safetensors').write_bytes(b'new')
    assert before!=sources(config)
    (adapter/'adapter_model.safetensors').unlink()
    with pytest.raises(ValueError,match='LoRA'):sources(config)


def test_empty_export_without_success_manifest_is_failed(monkeypatch,tmp_path):
    from services import training_executor as executor
    id=str(uuid4());out=tmp_path/id/'result';out.mkdir(parents=True);(out/'model.onnx').write_bytes(b'partial')
    monkeypatch.setattr(executor,'container_state',lambda _:dict(State={'Status':'exited','ExitCode':0}))
    result=executor.execute(dict(action='status',run_id=id,config=dict(kind='model_export',output_root=str(tmp_path),output_subdir='result')))
    assert result['status']=='failed'
    (out/'result.json').write_text('{}')
    assert executor.execute(dict(action='status',run_id=id,config=dict(kind='model_export',output_root=str(tmp_path),output_subdir='result')))['status']=='completed'


@pytest.mark.asyncio
async def test_output_start_reserves_gpu_and_stop_releases_it(client,auth_headers,test_db,monkeypatch):
    from services import training_runs
    from models.training_run import TrainingGpuHold
    from sqlalchemy import select
    parent=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    job=client.post('/api/training/runs/'+parent['id']+'/outputs',headers=auth_headers,json=request().model_dump(mode='json')).json()
    actions=[]
    async def remote(db,run,action,**values):
        actions.append(action)
        if action=='precheck':return dict(ok=True,checks=[])
        if action=='stage':assert 'model.train(' not in values['script'];return {}
        if action=='start':return dict(status='running',image_digest='sha256:test')
        if action=='stop':return dict(status='canceled')
        raise AssertionError(action)
    monkeypatch.setattr(training_runs,'remote',remote)
    url='/api/training/runs/'+job['id']
    result=client.post(url+'/start',headers=auth_headers)
    assert result.status_code==200,result.text
    assert result.json()['status']=='running' and actions==['precheck','stage','start']
    assert await test_db.scalar(select(TrainingGpuHold.gpu_uuid))=='GPU-test-1'
    await test_db.rollback()
    assert client.post(url+'/start',headers=auth_headers).status_code==409
    assert client.post(url+'/stop',headers=auth_headers).json()['status']=='canceled'
    assert await test_db.scalar(select(TrainingGpuHold.gpu_uuid)) is None


@pytest.mark.parametrize('changed',[False,True])
def test_container_runner_checks_source_stability_and_writes_hash_manifest(tmp_path,monkeypatch,changed):
    from services import model_output_runner as runner
    import subprocess
    monkeypatch.setattr(runner,'Path',lambda path:tmp_path if path=='/output' else Path(path))
    monkeypatch.setattr(runner,'check',lambda config:{'sha':'before'})
    monkeypatch.setattr(runner,'sources',lambda config:{'sha':'after' if changed else 'before'})
    def export(config,out):
        (out/'model.onnx').write_bytes(b'model')
        return out/'model.onnx',{'input_name':'input'}
    monkeypatch.setattr(runner,'export_onnx',export)
    monkeypatch.setattr(runner.subprocess,'run',lambda *a,**kw:subprocess.CompletedProcess(a,0,stdout='A100'))
    config=dict(kind='model_export',output_subdir='result',gpu_uuids=['GPU-test'],output=request().model_dump())
    if changed:
        with pytest.raises(ValueError,match='来源权重发生变化'):runner.run(config)
        assert not (tmp_path/'result/result.json').exists()
    else:
        runner.run(config)
        manifest=json.loads((tmp_path/'result/result.json').read_text())
        assert manifest['gpu_name']=='A100' and manifest['files'][0]['sha256']==runner.sha(tmp_path/'result/model.onnx')


def test_merge_does_not_mark_adapter_only_output_successful(tmp_path,monkeypatch):
    from services import model_output_runner as runner
    monkeypatch.setattr(runner,'Path',lambda path:tmp_path if path=='/output' else Path(path))
    monkeypatch.setattr(runner,'check',lambda config:{})
    def merge(*a,**kw):
        output=tmp_path/'result/merged';output.mkdir();(output/'adapter_model.safetensors').write_bytes(b'adapter')
    monkeypatch.setattr(runner.subprocess,'run',merge)
    config=dict(kind='model_merge',model_path='/data/base',adapter_path='/data/adapter',output_subdir='result',output=request(format='merge').model_dump())
    with pytest.raises(ValueError,match='完整模型'):runner.run(config)
    assert not (tmp_path/'result/result.json').exists()


@pytest.mark.parametrize('dynamic',[False,True])
def test_yolo_exports_copy_with_requested_batch_and_preserves_source(tmp_path,monkeypatch,dynamic):
    import sys
    from services import model_output_runner as runner
    source=tmp_path/'best.pt';source.write_bytes(b'original');output=tmp_path/'export';output.mkdir()
    calls=[]
    class Model:
        def __init__(self,path):assert Path(path)!=source and Path(path).read_bytes()==b'original'
        def export(self,**options):calls.append(options);target=output/'source.onnx';target.write_bytes(b'onnx');return str(target)
    monkeypatch.setitem(sys.modules,'ultralytics',SimpleNamespace(YOLO=Model))
    monkeypatch.setattr(runner,'prepare_onnx',lambda *a,**kw:{'input_name':'input','outputs':['output']})
    options=request(batch=4,dynamic_batch=dynamic,opt_batch=7,max_batch=8).model_dump()
    runner.export_onnx(dict(model_path=str(source),model_kind='yolo26',output=options),output)
    assert source.read_bytes()==b'original' and not (output/'source.pt').exists()
    assert calls[0]['batch']==(7 if dynamic else 4) and calls[0]['dynamic']==dynamic
    assert calls[0]['opset']==13 and calls[0]['simplify'] is True and calls[0]['half'] is False


def test_external_onnx_tensors_are_part_of_source_identity(tmp_path):
    onnx=pytest.importorskip('onnx')
    import numpy as np
    from services.model_output_runner import sources
    tensor=onnx.numpy_helper.from_array(np.ones([4],dtype=np.float32),name='weight')
    model=onnx.helper.make_model(onnx.helper.make_graph([onnx.helper.make_node('Identity',['weight'],['output'])],
        'external',[],[onnx.helper.make_tensor_value_info('output',onnx.TensorProto.FLOAT,[4])],[tensor]))
    path=tmp_path/'model.onnx'
    onnx.save_model(model,path,save_as_external_data=True,all_tensors_to_one_file=True,location='model.data',size_threshold=0)
    config=dict(kind='model_export',model_path=str(path))
    before=sources(config);(tmp_path/'model.data').write_bytes(b'changed')
    assert before!=sources(config) and 'model.data' in before['external_data']


def test_cross_server_tensorrt_keeps_checkpoint_on_training_server(client,auth_headers,monkeypatch):
    from services import training_runs
    async def snapshot(db,id,tenant):return dict(id=id,name='target',revision=1)
    parent=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    monkeypatch.setattr(training_runs,'server_snapshot',snapshot)
    target=str(uuid4())
    result=client.post('/api/training/runs/'+parent['id']+'/outputs',headers=auth_headers,
        json=request(format='tensorrt',server_id=target,gpu_uuid='GPU-target',source_gpu_uuid='GPU-source').model_dump(mode='json'))
    assert result.status_code==201,result.text
    job=result.json();source=job['config']['onnx_stage']
    assert source['server']==parent['server'] and source['config']['model_path']=='/data/run/best.pt'
    assert source['config']['output']['format']=='onnx' and source['config']['gpu_uuids']==['GPU-source']
    assert job['server']['id']==target and job['config']['gpu_uuids']==['GPU-target']
    assert job['config']['model_path']==job['output_dir']+'/onnx/model.onnx'
    assert job['config']['output']['source_path']==job['config']['model_path']


def test_onnx_cannot_be_silently_exported_on_another_server(client,auth_headers):
    parent=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    result=client.post('/api/training/runs/'+parent['id']+'/outputs',headers=auth_headers,
        json=request(server_id=str(uuid4())).model_dump(mode='json'))
    assert result.status_code==422 and '训练服务器' in result.text


def test_onnx_bundle_preserves_external_tensors_and_rejects_corruption(tmp_path):
    from services import training_executor as executor
    from services.model_output_runner import sha
    source=tmp_path/'source';source.mkdir()
    (source/'inference.onnx').write_bytes(b'graph');(source/'model.data').write_bytes(b'tensors')
    entries=[dict(path=p.name,bytes=p.stat().st_size,sha256=sha(p)) for p in source.iterdir()]
    (source/'result.json').write_text(json.dumps(dict(files=entries,onnx=dict(input_name='input',height=32,width=32))))
    archive=tmp_path/'input.zip'
    executor.pack_onnx(source,archive)
    target=tmp_path/'target'
    executor.unpack_onnx(archive,target)
    assert (target/'model.onnx').read_bytes()==b'graph' and (target/'model.data').read_bytes()==b'tensors'
    (source/'model.data').write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='校验'):executor.pack_onnx(source,tmp_path/'bad.zip')


def test_onnx_bundle_rejects_traversal_and_bad_checksum(tmp_path):
    import zipfile
    from services import training_executor as executor
    for path,data,manifest in [('../escape',b'bad',[]),('model.onnx',b'bad',[dict(path='model.onnx',bytes=3,sha256='0'*64)])]:
        archive=tmp_path/'input.zip'
        with zipfile.ZipFile(archive,'w') as out:
            out.writestr(path,data);out.writestr('bundle.json',json.dumps(dict(files=manifest)))
        with pytest.raises(ValueError):executor.unpack_onnx(archive,tmp_path/'target')
        assert not (tmp_path/'escape').exists()


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome',['success','source_failed','corrupt','canceled','target_blocked'])
async def test_cross_server_lifecycle_and_transfer(test_db,test_engine,test_user,monkeypatch,tmp_path,outcome):
    import hashlib
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from services import training_runs,model_output,model_output_pipeline as pipeline
    from models.training_run import TrainingGpuHold
    parent=await training_runs.create(test_db,test_user,spec())
    async def snapshot(db,id,tenant):return dict(id=id,name='目标服务器',revision=1)
    async def server(*args,**kwargs):return SimpleNamespace(enabled=True,revision=1)
    monkeypatch.setattr(training_runs,'server_snapshot',snapshot)
    monkeypatch.setattr(training_runs.gpu_servers,'scoped_server',server)
    job=await model_output.create(test_db,test_user,parent.id,
        request(format='tensorrt',server_id=str(uuid4()),gpu_uuid='GPU-target',source_gpu_uuid='GPU-source'))
    source,target=pipeline.stages(job);actions=[];transfers=[]
    data=b'isolated ONNX transfer bundle';digest=hashlib.sha256(data).hexdigest()
    async def direct(db,view,action,**values):
        where='source' if str(view.id)==str(source.id) else 'target';actions.append((where,action))
        if action=='precheck':
            if where=='target':assert values['pending_onnx'] is True
            return dict(ok=True,checks=[])
        if action=='stage':
            assert ('/data/run/best.pt' in values['script'])==(where=='source')
            return {}
        if action=='start':
            if where=='target':
                assert ('target','receive_onnx') in actions
                if outcome=='target_blocked':return dict(status='blocked',inspection={'ok':False})
            return dict(status='running',image_digest='sha256:fixture')
        if action=='status':return dict(status='failed' if outcome=='source_failed' and where=='source' else 'completed')
        if action=='pack_onnx':
            if outcome=='canceled':await training_runs.stop(db,job)
            return dict(path=source.output_dir+'/onnx.zip',bytes=len(data),sha256=digest)
        if action=='receive_onnx':assert values['sha256']==digest;return dict(ok=True)
        if action=='stop':return dict(status='canceled')
        raise AssertionError(action)
    async def conn(db,view):return dict(host=view.server['name'])
    def bridge(server,input):
        transfers.append(input)
        if 'download' in input:Path(input['download']['localPath']).write_bytes(b'corrupt' if outcome=='corrupt' else data)
        else:
            assert input['connection']['host']=='目标服务器'
            assert Path(input['upload']['localPath']).read_bytes()==data
            assert input['upload']['remotePath']==target.output_dir+'/input.zip'
        return {}
    monkeypatch.setattr(training_runs,'remote_direct',direct)
    monkeypatch.setattr(training_runs,'connection',conn)
    monkeypatch.setattr(training_runs,'bridge',bridge)
    monkeypatch.setattr(training_runs,'AsyncSessionLocal',async_sessionmaker(test_engine,expire_on_commit=False))
    monkeypatch.setattr(training_runs,'ROOT',tmp_path)
    await training_runs.launch(test_db,job,test_user)
    assert actions==[('source','precheck'),('target','precheck'),('source','stage'),('target','stage'),('source','start')]
    assert job.status=='running' and job.inspection['output_phase']=='onnx'
    holds=set((await test_db.scalars(select(TrainingGpuHold.gpu_uuid))).all())
    assert holds=={'GPU-source','GPU-target'}
    await training_runs.refresh(test_db,job)
    task=pipeline._advancing.get(str(job.id))
    await test_db.commit()
    if task:await task
    await test_db.refresh(job)
    if outcome=='success':
        assert job.inspection['output_phase']=='tensorrt' and ('target','start') in actions
        assert job.inspection['onnx_transfer']['sha256']==digest
        await training_runs.refresh(test_db,job)
        assert job.status=='completed'
    elif outcome=='source_failed':assert job.status=='failed' and not transfers
    elif outcome=='canceled':assert job.status=='canceled' and ('target','start') not in actions
    elif outcome=='corrupt':
        assert job.status=='unknown' and '校验失败' in job.error
        assert not any('upload' in item for item in transfers) and ('target','start') not in actions
    else:assert job.status=='unknown' and job.inspection['output_phase']=='transferring'
    if job.status in training_runs.ACTIVE:await training_runs.stop(test_db,job)
    assert await test_db.scalar(select(TrainingGpuHold.gpu_uuid)) is None
    assert all(not Path(item['download']['localPath']).exists() for item in transfers if 'download' in item)


def test_real_external_onnx_survives_pack_transfer_and_target_preparation(tmp_path):
    import numpy as np
    onnx=pytest.importorskip('onnx')
    from services import model_output_runner as runner,training_executor as executor
    weights=np.ones([1,3,32,32],dtype=np.float32)
    tensor=lambda name:onnx.helper.make_tensor_value_info(name,onnx.TensorProto.FLOAT,[1,3,32,32])
    graph=onnx.helper.make_model(onnx.helper.make_graph([onnx.helper.make_node('Add',['images','weight'],['output'])],
        'external',[tensor('images')],[tensor('output')],[onnx.numpy_helper.from_array(weights,name='weight')]))
    original=tmp_path/'original';original.mkdir();path=original/'original.onnx'
    onnx.save_model(graph,path,save_as_external_data=True,all_tensors_to_one_file=True,location='weights.bin',size_threshold=0)
    original_hash=runner.sha(path)
    output=tmp_path/'source-result';output.mkdir()
    options=request(format='tensorrt',source_path='/data/model.onnx').model_dump()
    runner.export_onnx(dict(model_path=str(path),model_kind='rfdetr_2xlarge',output=options),output)
    files=[dict(path=p.name,bytes=p.stat().st_size,sha256=runner.sha(p)) for p in output.iterdir()]
    (output/'result.json').write_text(json.dumps(dict(files=files,onnx=dict(input_name='input',height=32,width=32))))
    archive=tmp_path/'input.zip';executor.pack_onnx(output,archive)
    received=tmp_path/'received';executor.unpack_onnx(archive,received)
    result=tmp_path/'target-result';result.mkdir()
    model,details=runner.export_onnx(dict(model_path=str(received/'model.onnx'),model_kind='rfdetr_2xlarge',output=options),result)
    onnx.checker.check_model(str(model))
    actual=onnx.load(model)
    assert details['input_name']=='input' and details['height']==32
    np.testing.assert_array_equal(onnx.numpy_helper.to_array(actual.graph.initializer[0]),weights)
    assert runner.sha(path)==original_hash


@pytest.mark.asyncio
async def test_stop_attempts_both_export_hosts_even_when_one_is_unreachable(monkeypatch):
    from fastapi import HTTPException
    from services import model_output_pipeline as pipeline,training_runs
    calls=[]
    config=dict(output_root='/data/output',gpu_uuids=['GPU-test'],onnx_stage=dict(id=str(uuid4()),
        server=dict(id='default',name='source'),config=dict(output_root='/data/output')))
    run=SimpleNamespace(id=uuid4(),tenant_id=1,server=dict(id='default',name='target'),config=config)
    async def direct(db,view,action,**values):
        calls.append(view.server['name'])
        if view.server['name']=='source':raise HTTPException(503,'source unreachable')
        return dict(status='canceled')
    monkeypatch.setattr(training_runs,'remote_direct',direct)
    with pytest.raises(HTTPException,match='保留GPU占用'):await pipeline.remote(None,run,'stop')
    assert set(calls)=={'source','target'}


def test_pipeline_reserves_same_gpu_only_once():
    from services.training_runs import reserved_gpus
    run=SimpleNamespace(config=dict(gpu_uuids=['GPU-same'],onnx_stage=dict(config=dict(gpu_uuids=['GPU-same']))))
    assert reserved_gpus(run)==['GPU-same']


@pytest.mark.parametrize('corrupt',[False,True])
def test_received_onnx_builds_tensorrt_without_python_onnx(tmp_path,monkeypatch,corrupt):
    import builtins,subprocess
    from services import model_output_runner as runner
    source=tmp_path/'onnx';source.mkdir();(source/'model.onnx').write_bytes(b'validated ONNX')
    row=dict(path='model.onnx',bytes=(source/'model.onnx').stat().st_size,sha256=runner.sha(source/'model.onnx'))
    (source/'bundle.json').write_text(json.dumps(dict(files=[row],onnx=dict(input_name='input',height=880,width=880))))
    if corrupt:(source/'model.onnx').write_bytes(b'corrupt')
    original_import=builtins.__import__
    def no_onnx(name,*args,**kwargs):
        if name=='onnx' or name.startswith('onnx.'):raise AssertionError('TensorRT目标阶段不应导入onnx')
        return original_import(name,*args,**kwargs)
    monkeypatch.setattr(builtins,'__import__',no_onnx)
    monkeypatch.setattr(runner,'Path',lambda path:tmp_path if path=='/output' else Path(path))
    executable=tmp_path/'trtexec';executable.write_bytes(b'fixture');monkeypatch.setattr(runner,'TRTEXEC',str(executable))
    calls=[]
    def execute(args,**kwargs):
        calls.append(args)
        if args[0]==str(executable):Path(next(arg.split('=',1)[1] for arg in args if arg.startswith('--saveEngine='))).write_bytes(b'engine')
        return subprocess.CompletedProcess(args,0,stdout='T4')
    monkeypatch.setattr(runner.subprocess,'run',execute)
    monkeypatch.setattr(runner,'tensorrt_version',lambda:'10.14.1')
    options=request(format='tensorrt',dynamic_batch=True,opt_batch=7,max_batch=8).model_dump()
    config=dict(kind='model_export',model_kind='rfdetr_2xlarge',model_path=str(source/'model.onnx'),received_onnx=True,
        output_subdir='result',gpu_uuids=['GPU-target'],output=options)
    if corrupt:
        with pytest.raises(ValueError,match='校验'):runner.run(config)
        assert not calls
    else:
        runner.run(config)
        assert (tmp_path/'result/model.engine').read_bytes()==b'engine'
        assert (tmp_path/'result/model.onnx').read_bytes()==b'validated ONNX'
        assert '--optShapes=input:7x3x880x880' in calls[0]
        assert json.loads((tmp_path/'result/result.json').read_text())['versions']['tensorrt_runtime']=='10.14.1'
