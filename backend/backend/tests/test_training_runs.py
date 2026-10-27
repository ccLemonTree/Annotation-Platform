"""Training contracts; no real GPU, Docker daemon or business data is touched."""
import json
from uuid import UUID,uuid4

import pytest
from pydantic import ValidationError
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest_asyncio.fixture(autouse=True)
async def training_tables(test_engine):
    from models.training_run import TrainingRun,TrainingGpuHold
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda c:TrainingRun.__table__.create(c,checkfirst=True))
        await conn.run_sync(lambda c:TrainingGpuHold.__table__.create(c,checkfirst=True))


def spec(kind='yolo26', **values):
    from schemas.training_run import TrainingCreate
    return TrainingCreate(**dict(dict(name='训练测试',kind=kind,gpu_uuids=['GPU-test-1'],
        dataset_path='/data/dataset/data.yaml',model_path='/data/weights/model.pt',output_root='/data/runs'),**values))


def test_labels_and_timestamp_output_are_part_of_saved_config():
    from services.training_config import effective_config,build_script
    from schemas.training_run import TrainingCreate
    values=spec(snapshot_id=uuid4(),dataset_path='').model_dump();values.pop('output_root');values['selected_labels']=['smoke']
    config=effective_config(TrainingCreate(**values))
    assert config['output_root'].startswith('/ai/chen/training/runs/')
    assert config['selected_labels']==['smoke']
    assert 'prepare_detection(' in build_script(config)
    with pytest.raises(ValidationError):spec('wemm',selected_labels=['smoke'])


@pytest.mark.parametrize('kind',['yolo26','rfdetr_medium','rfdetr_2xlarge'])
def test_server_training_uses_original_data_config_without_preparation(kind,monkeypatch,tmp_path):
    import sys,types
    from services.training_config import effective_config,build_script
    source=tmp_path/'existing.yaml';source.write_text('path: /existing/mount\nnames: [smoke]\n')
    config=effective_config(spec(kind))
    config['dataset_path']=str(source if kind=='yolo26' else tmp_path)
    calls=[]
    class Model:
        @staticmethod
        def _detect_num_classes_for_training(dataset):return 1
        def __init__(self,*args,**kwargs):pass
        def train(self,**kwargs):calls.append(kwargs)
    monkeypatch.setitem(sys.modules,'ultralytics',types.SimpleNamespace(YOLO=Model))
    monkeypatch.setitem(sys.modules,'rfdetr',types.SimpleNamespace(RFDETR=Model,RFDETRMedium=Model,RFDETR2XLarge=Model))
    code=build_script(config)
    assert 'prepare_detection' not in code
    exec(compile(code,'train.py','exec'),{'__name__':'__main__'})
    assert calls[0]['data' if kind=='yolo26' else 'dataset_dir']==config['dataset_path']
    assert source.read_text()=='path: /existing/mount\nnames: [smoke]\n'
    assert not (tmp_path/'prepared-dataset').exists()


def test_only_platform_sources_allow_class_selection():
    with pytest.raises(ValidationError,match='平台'):spec(selected_labels=['smoke'])
    assert spec(snapshot_id=uuid4(),dataset_path='',selected_labels=['smoke']).selected_labels==['smoke']


@pytest.mark.parametrize('kind',['rfdetr_medium','rfdetr_2xlarge'])
def test_server_rfdetr_yaml_uses_same_directory_in_precheck_and_training(kind,monkeypatch,tmp_path):
    import sys,types
    from services.training_config import effective_config,build_script
    dataset=tmp_path/'data.yml';dataset.write_text('names: [smoke]')
    config=effective_config(spec(kind));config['dataset_path']=str(dataset)
    calls=[]
    class Model:
        @staticmethod
        def _detect_num_classes_for_training(dataset):return 1
        def __init__(self,**kwargs):pass
        def train(self,**kwargs):calls.append(kwargs)
    monkeypatch.setitem(sys.modules,'rfdetr',types.SimpleNamespace(RFDETR=Model,RFDETRMedium=Model,RFDETR2XLarge=Model))
    exec(compile(build_script(config),'train.py','exec'),{'__name__':'__main__'})
    assert calls[0]['dataset_dir']==str(tmp_path)
    assert dataset.read_text()=='names: [smoke]'


@pytest.mark.parametrize('kind',['rfdetr_medium','rfdetr_2xlarge'])
@pytest.mark.parametrize('source',['directory','yaml','filtered_snapshot'])
def test_rfdetr_initializes_with_actual_training_dataset_classes(kind,source,monkeypatch,tmp_path):
    import sys,types,shutil,yaml
    from pathlib import Path
    from services.training_config import effective_config,build_script
    root=tmp_path/'dataset';root.mkdir()
    names=['fire','smoke','star']
    (root/'data.yaml').write_text(json.dumps(dict(names=names,nc=3,train='train/images',val='valid/images')))
    for split in ('train','valid'):
        (root/split/'images').mkdir(parents=True);(root/split/'labels').mkdir()
        (root/split/'images/a.jpg').write_bytes(b'image')
        (root/split/'labels/a.txt').write_text('1 0.5 0.5 0.2 0.2\n')
    snapshot=source=='filtered_snapshot'
    config=effective_config(spec(kind,dataset_path='' if snapshot else '/data/dataset',
        snapshot_id=uuid4() if snapshot else None,selected_labels=['smoke'] if snapshot else None))
    if snapshot:config['runtime_dataset']=str(root)
    else:config['dataset_path']=str(root/'data.yaml' if source=='yaml' else root)
    events=[]
    class Model:
        @staticmethod
        def _detect_num_classes_for_training(dataset):
            events.append(('detect',dataset))
            return len(yaml.safe_load((Path(dataset)/'data.yaml').read_text())['names'])
        def __init__(self,**kwargs):events.append(('init',kwargs))
        def train(self,**kwargs):events.append(('train',kwargs))
    monkeypatch.setitem(sys.modules,'rfdetr',types.SimpleNamespace(RFDETR=Model,RFDETRMedium=Model,RFDETR2XLarge=Model))
    monkeypatch.setattr(Path,'symlink_to',lambda self,target:shutil.copyfile(target,self))
    prepared=tmp_path/'prepared'
    monkeypatch.delenv('PLATFORM_PREPARED_DETECTION',raising=False)
    monkeypatch.delenv('LOCAL_RANK',raising=False)
    code=build_script(config).replace('"/output/prepared-dataset"',repr(str(prepared)))
    exec(compile(code,'train.py','exec'),{'__name__':'__main__'})
    assert [event[0] for event in events]==['detect','init','train']
    assert events[0][1]==str(prepared if snapshot else root)
    assert events[1][1]['num_classes']==(1 if snapshot else 3)
    assert events[1][1]['pretrain_weights']==config['model_path']
    assert 'num_classes' not in events[2][1]
    assert json.loads((root/'data.yaml').read_text())['names']==names
    if snapshot:
        before={str(p): (p.read_bytes(),p.stat().st_mtime_ns) for p in prepared.rglob('*') if p.is_file()}
        monkeypatch.setenv('LOCAL_RANK','1')
        exec(compile(code,'train.py','exec'),{'__name__':'__main__'})
        assert [event[0] for event in events]==['detect','init','train']*2
        assert before=={str(p): (p.read_bytes(),p.stat().st_mtime_ns) for p in prepared.rglob('*') if p.is_file()}


@pytest.mark.parametrize('detected',[0,'unreadable'])
def test_rfdetr_invalid_dataset_classes_stop_before_model_loading(detected,monkeypatch):
    import sys,types
    from services.training_config import effective_config,build_script
    events=[]
    class Model:
        @staticmethod
        def _detect_num_classes_for_training(dataset):
            if detected=='unreadable':raise ValueError('无法读取类别')
            return detected
        def __init__(self,**kwargs):events.append('init')
        def train(self,**kwargs):events.append('train')
    monkeypatch.setitem(sys.modules,'rfdetr',types.SimpleNamespace(RFDETR=Model,RFDETR2XLarge=Model))
    with pytest.raises(ValueError):exec(compile(build_script(effective_config(spec('rfdetr_2xlarge'))),'train.py','exec'),{'__name__':'__main__'})
    assert events==[]


@pytest.mark.asyncio
async def test_edit_draft_keeps_identity_and_resets_precheck(client,auth_headers,test_db,monkeypatch):
    from services import training_runs
    from models.training_run import TrainingRun
    async def no_remote(*args,**kwargs):raise AssertionError('Editing cannot contact GPU server')
    monkeypatch.setattr(training_runs,'remote',no_remote)
    payload=spec().model_dump(mode='json')
    row=client.post('/api/training/runs',headers=auth_headers,json=payload).json()
    stored=await test_db.get(TrainingRun,UUID(row['id']))
    stored.inspection=dict(ok=True,checks=[]);stored.error='old';await test_db.commit()
    payload.update(name='修改配置',parameters=dict(epochs=17),output_root='/data/new-runs')
    response=client.put('/api/training/runs/'+row['id'],headers=auth_headers,json=payload)
    assert response.status_code==200,response.text
    changed=response.json()
    assert changed['id']==row['id'] and changed['status']=='draft'
    assert changed['config']['parameters']['epochs']==17 and changed['name']=='修改配置'
    assert changed['inspection']=={} and changed['error'] is None
    assert changed['output_dir']=='/data/new-runs/'+row['id']
    assert client.get('/api/training/runs',headers=auth_headers).json()['total']==1


@pytest.mark.asyncio
async def test_create_copy_has_new_identity_directory_and_requires_precheck(client,auth_headers,test_db,monkeypatch):
    from services import training_runs
    from models.training_run import TrainingRun,TrainingGpuHold
    from sqlalchemy import select,func
    async def no_remote(*args,**kwargs):raise AssertionError('Copying cannot start or inspect a GPU server')
    monkeypatch.setattr(training_runs,'remote',no_remote)
    payload=spec().model_dump(mode='json')
    original=client.post('/api/training/runs',headers=auth_headers,json=payload).json()
    stored=await test_db.get(TrainingRun,UUID(original['id']));stored.status='canceled';await test_db.commit()
    response=client.post('/api/training/runs',headers=auth_headers,json={**payload,'name':payload['name']+' 副本'})
    assert response.status_code==201,response.text
    copied=response.json()
    assert copied['id']!=original['id'] and copied['output_dir']!=original['output_dir']
    assert copied['output_dir']==payload['output_root']+'/'+copied['id']
    assert copied['status']=='draft' and copied['inspection']=={} and copied['error'] is None
    assert copied['source']==original['source']
    for key in ('gpu_uuids','parameters','model_path','mounts','environment'):
        assert copied['config'][key]==original['config'][key]
    assert client.get('/api/training/runs/'+original['id'],headers=auth_headers).json()['status']=='canceled'
    assert await test_db.scalar(select(func.count()).select_from(TrainingGpuHold))==0


@pytest.mark.asyncio
@pytest.mark.parametrize('state',['starting','running','stopping','unknown','completed','failed','canceled'])
async def test_edit_refuses_submitted_tasks(client,auth_headers,test_db,state):
    from models.training_run import TrainingRun
    payload=spec().model_dump(mode='json')
    row=client.post('/api/training/runs',headers=auth_headers,json=payload).json()
    stored=await test_db.get(TrainingRun,UUID(row['id']));stored.status=state;await test_db.commit()
    response=client.put('/api/training/runs/'+row['id'],headers=auth_headers,json=dict(payload,name='不得写入'))
    assert response.status_code==409,response.text
    assert client.get('/api/training/runs/'+row['id'],headers=auth_headers).json()['name']==payload['name']


@pytest.mark.asyncio
async def test_edit_requires_admin_and_scoped_tenant(client,auth_headers,test_db,test_user):
    from models import Tenant
    payload=spec().model_dump(mode='json')
    row=client.post('/api/training/runs',headers=auth_headers,json=payload).json()
    url='/api/training/runs/'+row['id']
    assert client.put(url,json=payload).status_code==401
    test_user.role='annotator';await test_db.commit()
    assert client.put(url,headers=auth_headers,json=payload).status_code==403
    test_db.add(Tenant(id=2,tenant_name='other'));await test_db.flush()
    test_user.role='admin';test_user.tenant_id=2;await test_db.commit()
    assert client.put(url,headers=auth_headers,json=payload).status_code==404


@pytest.mark.asyncio
async def test_weight_defaults_follow_saved_recipe_and_tenant(client,auth_headers,test_db,test_user):
    base='/api/training/defaults?server_id=default&kind='
    response=client.get(base+'yolo26',headers=auth_headers)
    assert response.status_code==200,response.text
    assert response.json()['model_path']==''
    client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json'))
    assert client.get(base+'yolo26',headers=auth_headers).json()['model_path']=='/data/weights/model.pt'
    assert client.get(base+'wemm',headers=auth_headers).json()['model_path']==''
    from models import Tenant
    test_db.add(Tenant(id=2,tenant_name='other'));await test_db.flush();test_user.tenant_id=2;await test_db.commit()
    assert client.get(base+'yolo26',headers=auth_headers).json()['model_path']==''


@pytest.mark.asyncio
async def test_dataset_inspection_is_admin_only_and_does_not_create_training(client,auth_headers,monkeypatch,test_user,test_db):
    from services import training_runs
    async def remote(db,run,action,**values):
        assert action=='dataset' and run.config['dataset_path']=='/data/data.yaml'
        return dict(labels=['fire','smoke'],splits=dict(train=2,val=1),format='yolo')
    monkeypatch.setattr(training_runs,'remote',remote)
    payload=dict(kind='yolo26',dataset_path='/data/data.yaml')
    response=client.post('/api/training/dataset',headers=auth_headers,json=payload)
    assert response.status_code==200 and response.json()['labels']==['fire','smoke']
    assert client.get('/api/training/runs',headers=auth_headers).json()['total']==0
    assert client.post('/api/training/dataset',json=payload).status_code==401
    assert client.get('/api/training/defaults?kind=yolo26&server_id=bad',headers=auth_headers).status_code==400
    test_user.role='annotator';await test_db.commit()
    assert client.post('/api/training/dataset',headers=auth_headers,json=payload).status_code==403
    assert client.get('/api/training/defaults?kind=yolo26',headers=auth_headers).status_code==403


def test_timestamp_output_directory_is_created_under_existing_writable_parent(tmp_path):
    from services.training_executor import run_directory
    root=tmp_path/'runs/20261016-150000'
    path,_=run_directory(dict(run_id=str(uuid4()),config=dict(output_root=str(root))))
    assert path.is_dir() and (path/'owner.json').is_file()


@pytest.mark.parametrize('kind', ['yolo26','rfdetr_medium','rfdetr_2xlarge','swift_sft','wemm','dinov3_vits16_ltdetr'])
def test_every_template_has_typed_defaults_and_builds_a_script(kind):
    from services.training_config import build_script, effective_config, catalog
    request=spec(kind)
    config=effective_config(request)
    code=build_script(config)
    compile(code,'train.py','exec')
    assert kind in catalog()
    assert config['parameters']
    assert config['environment']['CUDA_VISIBLE_DEVICES']=='0'
    assert 'if __name__' in code


def test_rfdetr_initializers_are_separate_and_license_is_not_silently_accepted():
    from services.training_config import build_script,effective_config
    medium=effective_config(spec('rfdetr_medium',parameters={'epochs':17,'batch_size':4,'grad_accum_steps':4,'lr':.0001,'devices':'auto'}))
    code=build_script(medium)
    assert 'RFDETRMedium' in code and 'pretrain_weights' in code
    assert 'accept_platform_model_license' not in code
    xl=effective_config(spec('rfdetr_2xlarge'))
    assert not xl['accept_platform_model_license']
    assert 'RFDETR2XLarge' in build_script(xl)


def test_yolo_global_batch_must_be_divisible_by_requested_devices():
    with pytest.raises(ValidationError):spec(gpu_uuids=['GPU-card-'+str(i) for i in range(7)])


def test_supplied_swift_parameters_and_environment_survive_without_loss():
    from services.training_config import effective_config,build_script
    config=effective_config(spec('wemm',parameters={'lora_rank':16,'freeze_vit':False,'freeze_aligner':True},
        environment={'MAX_PIXELS':'100352','INFONCE_USE_BATCH':'false','INFONCE_HARD_NEGATIVES':'4','NPROC_PER_NODE':'1'}))
    assert config['parameters']['lora_rank']==16
    assert config['parameters']['freeze_aligner'] is True
    assert config['parameters']['loss_type']=='infonce'
    assert config['environment']['INFONCE_HARD_NEGATIVES']=='4'
    assert '--freeze_vit' in build_script(config)


@pytest.mark.parametrize('values', [
    {'parameters':{'epochs':0}}, {'parameters':{'epochs':True}}, {'parameters':{'unknown':1}},
    {'environment':{'PYTHONPATH':'/tmp/evil'}}, {'output_root':'/data/../etc'},
    {'mounts':[{'source':'/','target':'/host','read_only':False}]},
    {'environment':{'NPROC_PER_NODE':'7'}}, {'parameters':{'device':'1'}},
])
def test_untrusted_training_values_are_rejected(values):
    with pytest.raises(ValidationError):spec(**values)


def test_snapshot_source_requires_detection_and_rejects_unsplit_or_wrong_format():
    from services.training_config import validate_snapshot
    from fastapi import HTTPException
    with pytest.raises(HTTPException):validate_snapshot('wemm','yolo',{'assets':[]})
    with pytest.raises(HTTPException):validate_snapshot('yolo26','coco',{'assets':[]})
    with pytest.raises(HTTPException):validate_snapshot('yolo26','yolo',{'assets':[{'split':'all'}]})
    validate_snapshot('rfdetr_medium','coco',{'assets':[{'split':'train'},{'split':'val'}],'approved_only':True})


def test_archive_extraction_rejects_traversal_and_symlinks(tmp_path):
    import zipfile
    from services.training_executor import unpack_snapshot
    archive=tmp_path/'input.zip'
    with zipfile.ZipFile(archive,'w') as data:data.writestr('../escape.txt','bad')
    with pytest.raises(ValueError):unpack_snapshot(archive,tmp_path/'dataset','yolo26')
    assert not (tmp_path/'escape.txt').exists()
    with zipfile.ZipFile(archive,'w') as data:
        member=zipfile.ZipInfo('link');member.external_attr=0o120777<<16;data.writestr(member,'/etc/passwd')
    with pytest.raises(ValueError):unpack_snapshot(archive,tmp_path/'dataset','yolo26')


@pytest.mark.parametrize('kind,format',[('yolo26','yolo'),('rfdetr_medium','yolo'),('rfdetr_2xlarge','coco'),('dinov3_vits16_ltdetr','yolo'),('dinov3_vits16_ltdetr','coco')])
def test_frozen_package_is_verified_and_adapted_without_using_test_for_validation(tmp_path,kind,format):
    import zipfile,hashlib
    from services.training_executor import unpack_snapshot
    archive=tmp_path/'input.zip';assets=[]
    with zipfile.ZipFile(archive,'w') as data:
        for split in ('train','val','test'):
            raw=(split+'-picture').encode();path='images/'+split+'/image.jpg'
            assets.append(dict(split=split,image_path=path,content_hash=hashlib.sha256(raw).hexdigest()))
            data.writestr(path,raw);data.writestr('labels/'+split+'/image.txt','0 0.5 0.5 0.2 0.2')
            data.writestr('annotations/instances_'+split+'.json',json.dumps(dict(images=[dict(id=1,file_name=path)],categories=[dict(id=1,name='person')],annotations=[dict(id=1,image_id=1,category_id=1)])))
        data.writestr('manifest.json',json.dumps(dict(format=format,labels=['person'],assets=assets)))
    target=tmp_path/'dataset';unpack_snapshot(archive,target,kind)
    if kind=='yolo26':
        config=json.loads((target/'data.yaml').read_text());assert config['val']=='images/val' and config['test']=='images/test'
    elif format=='yolo':
        assert (target/'rfdetr/valid/images/image.jpg').read_bytes()==b'val-picture'
        assert (target/'rfdetr/test/images/image.jpg').read_bytes()==b'test-picture'
    else:
        value=json.loads((target/'rfdetr/valid/_annotations.coco.json').read_text())
        assert value['images'][0]['file_name']=='image.jpg' and value['annotations'][0]['category_id']==0


@pytest.mark.parametrize('action',['start','stop','status','logs'])
def test_remote_actions_refuse_foreign_container(monkeypatch,tmp_path,action):
    import contextlib
    from types import SimpleNamespace
    from services import training_executor as executor
    run_id=str(uuid4())
    def fake(args,**kwargs):
        return SimpleNamespace(stdout='id' if args[0]=='ps' else json.dumps([{'Config':{'Labels':{}},'State':{'Status':'running'}}]))
    monkeypatch.setattr(executor,'docker',fake)
    monkeypatch.setattr(executor,'run_lock',lambda path:contextlib.nullcontext())
    with pytest.raises(RuntimeError,match='不是本平台'):
        executor.execute(dict(action=action,run_id=run_id,config=dict(output_root=str(tmp_path))))


@pytest.mark.parametrize('values',[{'model_path':'/etc/passwd'},{'dataset_path':'/data.yaml'},{'mounts':[dict(source='/data',target='/output/train',read_only=True)]}])
def test_system_and_output_paths_cannot_be_mounted(values):
    with pytest.raises(ValidationError):spec(**values)


def test_storage_maintenance_retains_training_snapshot_reference():
    from services.storage_cleanup import _reference_queries
    queries=dict(_reference_queries(False,False,True))
    assert 'training_run' in queries


def test_remote_dispatch_never_treats_docker_connection_failure_as_missing(monkeypatch):
    from services import training_executor as executor
    def fail(*args,**kwargs):raise RuntimeError('Docker daemon unavailable')
    monkeypatch.setattr(executor,'docker',fail)
    with pytest.raises(RuntimeError):executor.container_state(str(uuid4()))


def test_precheck_rejects_weight_directory_for_yolo_and_wrong_rfdetr_layout(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from services import training_executor as executor
    from services.training_config import effective_config
    monkeypatch.setattr(executor,'docker',lambda *a,**k:SimpleNamespace(returncode=0,stdout='[{"Id":"sha256:test"}]'))
    def probe(args,**kwargs):
        return SimpleNamespace(returncode=0,stdout='0, GPU-test-1, A100, 40000, Disabled' if '--query-gpu=' in args[1] else '')
    monkeypatch.setattr(executor.subprocess,'run',probe)
    dataset=tmp_path/'data.yaml';dataset.write_text('names: [person]')
    config=effective_config(spec());config.update(dataset_path=str(dataset),model_path=str(tmp_path),output_root=str(tmp_path))
    assert not executor.precheck(dict(config=config))['ok']
    weights=tmp_path/'model.pth';weights.write_bytes(b'fixture')
    config.update(kind='rfdetr_medium',dataset_path=str(tmp_path),model_path=str(weights))
    assert not executor.precheck(dict(config=config))['ok']


def test_precheck_never_allocates_a_physical_gpu_in_mig_mode(monkeypatch,tmp_path):
    from types import SimpleNamespace
    from services import training_executor as executor
    from services.training_config import effective_config
    monkeypatch.setattr(executor,'docker',lambda *a,**k:SimpleNamespace(returncode=0,stdout='[{"Id":"sha256:test"}]'))
    def probe(args,**kwargs):
        data='0, GPU-test-1, A100, 40000'
        if 'mig.mode.current' in args[1]:data+=', Enabled'
        return SimpleNamespace(returncode=0,stdout=data if '--query-gpu=' in args[1] else '')
    monkeypatch.setattr(executor.subprocess,'run',probe)
    data=tmp_path/'data.yaml';data.write_text('names: [person]')
    weights=tmp_path/'model.pt';weights.write_bytes(b'fixture')
    config=effective_config(spec());config.update(dataset_path=str(data),model_path=str(weights),output_root=str(tmp_path))
    assert not executor.precheck(dict(config=config))['ok']


def test_remote_start_is_idempotent_and_stop_blocks_late_start(monkeypatch,tmp_path):
    import contextlib
    from types import SimpleNamespace
    from services import training_executor as executor
    from services.training_config import effective_config,build_script
    config=effective_config(spec());config['output_root']=str(tmp_path)
    run_id=str(uuid4());state={};calls=[]
    def fake(args,**kwargs):
        calls.append(args[0])
        if args[0]=='ps':return SimpleNamespace(stdout='owned' if state else '')
        if args[0]=='inspect':return SimpleNamespace(stdout=json.dumps([dict(Config=dict(Labels={'platform.training.id':run_id}),State=dict(Status=state['status'],Running=state['status']=='running',ExitCode=0))]))
        if args[0]=='create':state['status']='created'
        if args[0]=='start':state['status']='running'
        if args[0]=='stop':state['status']='exited'
        return SimpleNamespace(stdout='')
    monkeypatch.setattr(executor,'docker',fake)
    monkeypatch.setattr(executor,'run_lock',lambda p:contextlib.nullcontext())
    monkeypatch.setattr(executor,'precheck',lambda p:dict(ok=True,image_digest='sha256:fixture'))
    payload=dict(run_id=run_id,config=config)
    executor.execute(dict(payload,action='stage',script=build_script(config)))
    assert executor.execute(dict(payload,action='start'))['status']=='running'
    assert executor.execute(dict(payload,action='start'))['status']=='running'
    assert calls.count('create')==calls.count('start')==1
    assert executor.execute(dict(payload,action='stop'))['status']=='canceled'
    assert executor.execute(dict(payload,action='start'))['status']=='canceled'
    assert calls.count('create')==1


@pytest.mark.asyncio
async def test_training_create_read_permissions_and_immutable_source(client,auth_headers,test_db,test_user,test_project,test_engine):
    from models.training_run import TrainingRun,TrainingGpuHold
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda c:TrainingRun.__table__.create(c,checkfirst=True))
        await conn.run_sync(lambda c:TrainingGpuHold.__table__.create(c,checkfirst=True))
    payload=dict(name='只保存不启动',kind='yolo26',gpu_uuids=['GPU-test-1'],dataset_path='/data/data.yaml',model_path='/data/model.pt',output_root='/data/runs')
    response=client.post('/api/training/runs',headers=auth_headers,json=payload)
    assert response.status_code==201,response.text
    row=response.json();assert row['status']=='draft'
    assert row['config']['parameters']['batch']==48
    assert row['config']['parameters']['epochs']==300
    assert client.get('/api/training/runs',headers=auth_headers).json()['total']==1
    assert client.get('/api/training/runs').status_code==401
    test_user.role='annotator';await test_db.commit()
    assert client.post('/api/training/runs',headers=auth_headers,json=payload).status_code==403
    assert client.get('/api/training/runs/'+row['id'],headers=auth_headers).status_code==200


@pytest.mark.asyncio
async def test_start_recheck_holds_and_stop_release(client,auth_headers,monkeypatch,test_db):
    from sqlalchemy import select,func
    from services import training_runs
    from models.training_run import TrainingGpuHold
    calls=[]
    async def fake(db,run,action,**values):
        calls.append(action)
        if action=='precheck':return dict(ok=True,checks=[])
        return dict(status='canceled' if action=='stop' else 'running')
    monkeypatch.setattr(training_runs,'remote',fake)
    payload=spec().model_dump(mode='json')
    first=client.post('/api/training/runs',headers=auth_headers,json=payload).json()
    second=client.post('/api/training/runs',headers=auth_headers,json=payload).json()
    assert client.post('/api/training/runs/'+first['id']+'/start',headers=auth_headers).json()['status']=='starting'
    await training_runs.process_start(UUID(first['id']),async_sessionmaker(test_db.bind,expire_on_commit=False))
    assert client.post('/api/training/runs/'+first['id']+'/start',headers=auth_headers).status_code==409
    assert client.post('/api/training/runs/'+second['id']+'/start',headers=auth_headers).status_code==409
    assert await test_db.scalar(select(func.count()).select_from(TrainingGpuHold))==1
    await test_db.rollback()
    assert client.post('/api/training/runs/'+first['id']+'/stop',headers=auth_headers).json()['status']=='canceled'
    assert await test_db.scalar(select(func.count()).select_from(TrainingGpuHold))==0
    assert calls.count('start')==1


@pytest.mark.asyncio
async def test_ssh_uncertainty_retains_gpu_holds_and_never_relaunches(client,auth_headers,monkeypatch,test_db):
    from sqlalchemy import select,func
    from fastapi import HTTPException
    from services import training_runs
    from models.training_run import TrainingGpuHold
    calls=[]
    async def fake(db,run,action,**values):
        calls.append(action)
        if action=='precheck':return dict(ok=True,checks=[])
        if action=='stage':return dict(directory=run.output_dir)
        raise HTTPException(503,'SSH已断开')
    monkeypatch.setattr(training_runs,'remote',fake)
    row=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    assert client.post('/api/training/runs/'+row['id']+'/start',headers=auth_headers).status_code==202
    await training_runs.process_start(UUID(row['id']),async_sessionmaker(test_db.bind,expire_on_commit=False))
    assert client.get('/api/training/runs/'+row['id'],headers=auth_headers).json()['status']=='unknown'
    assert await test_db.scalar(select(func.count()).select_from(TrainingGpuHold))==1
    client.post('/api/training/runs/'+row['id']+'/refresh',headers=auth_headers)
    assert calls.count('start')==1 and await test_db.scalar(select(func.count()).select_from(TrainingGpuHold))==1


@pytest.mark.asyncio
async def test_failed_precheck_releases_holds_without_staging(client,auth_headers,monkeypatch,test_db):
    from sqlalchemy import select,func
    from services import training_runs
    from models.training_run import TrainingGpuHold
    calls=[]
    async def fake(db,run,action,**values):
        calls.append(action);return dict(ok=False,checks=[dict(name='GPU',ok=False,detail='busy')])
    monkeypatch.setattr(training_runs,'remote',fake)
    row=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    result=client.post('/api/training/runs/'+row['id']+'/start',headers=auth_headers)
    assert result.status_code==202 and calls==[]
    await training_runs.process_start(UUID(row['id']),async_sessionmaker(test_db.bind,expire_on_commit=False))
    assert calls==['precheck']
    assert await test_db.scalar(select(func.count()).select_from(TrainingGpuHold))==0
    assert client.get('/api/training/runs/'+row['id'],headers=auth_headers).json()['status']=='draft'


@pytest.mark.asyncio
async def test_other_tenant_cannot_read_operate_or_see_training(client,auth_headers,test_db,test_user):
    from models import Tenant
    row=client.post('/api/training/runs',headers=auth_headers,json=spec().model_dump(mode='json')).json()
    test_db.add(Tenant(id=2,tenant_name='other'));await test_db.flush()
    test_user.tenant_id=2;await test_db.commit()
    assert client.get('/api/training/runs',headers=auth_headers).json()['total']==0
    for suffix in ('','/logs','/artifacts'):
        assert client.get('/api/training/runs/'+row['id']+suffix,headers=auth_headers).status_code==404
    assert client.post('/api/training/runs/'+row['id']+'/start',headers=auth_headers).status_code==404


@pytest.mark.asyncio
async def test_snapshot_file_copy_does_not_block_api_event_loop(monkeypatch,tmp_path):
    import hashlib,threading
    from io import BytesIO
    from types import SimpleNamespace
    from config.settings import settings
    from services import training_runs
    main_thread=threading.get_ident();raw=b'fixture archive'
    class GuardedFile(BytesIO):
        def read(self,*args):
            assert threading.get_ident()!=main_thread,'Large archive reads must run off the API event loop'
            return super().read(*args)
    class Storage:
        async def get_file(self,key):return GuardedFile(raw)
    monkeypatch.setattr(training_runs,'get_storage',lambda:Storage())
    monkeypatch.setattr(training_runs,'ROOT',tmp_path)
    monkeypatch.setattr(training_runs,'bridge',lambda *a,**k:dict(success=True))
    async def remote(*args,**kwargs):return dict(ok=True,hit=False)
    monkeypatch.setattr(training_runs,'remote',remote)
    run=SimpleNamespace(id=uuid4(),tenant_id=1,source=dict(kind='snapshot',blob_path='fixture.zip',sha256=hashlib.sha256(raw).hexdigest()),
        server=dict(id='default',name=settings.GPU_SERVER_NAME),output_dir='/tmp/fixture')
    async def progress(*args,**kwargs):pass
    monkeypatch.setattr(training_runs,'startup_progress',progress)
    monkeypatch.setattr(training_runs,'ensure_starting',progress)
    class DB:
        async def scalars(self,query):return SimpleNamespace(all=lambda:[])
    await training_runs.transfer_snapshot(DB(),run)
