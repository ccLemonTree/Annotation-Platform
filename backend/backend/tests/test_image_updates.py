"""Image changes use command fixtures only; no production containers are touched."""
import importlib
import json
from uuid import uuid4
import pytest
from types import SimpleNamespace


def executor():
    try:return importlib.import_module('services.image_update_executor')
    except ModuleNotFoundError:pytest.fail('镜像更新执行器尚未实现')


def test_raw_docker_clone_keeps_gpu_ports_env_mounts_and_networks():
    module=executor()
    current={'Config':{'Image':'old:v1','Env':['TOKEN=private'],'Cmd':['serve'],'Labels':{}},
        'HostConfig':{'DeviceRequests':[{'Count':1,'Capabilities':[['gpu']]}],'PortBindings':{'8000/tcp':[{'HostPort':'8000'}]},'Binds':['/data/models:/models:ro'],'NetworkMode':'bridge'},
        'Mounts':[{'Type':'bind','Source':'/data/models','Destination':'/models','RW':False},{'Type':'volume','Name':'keep-volume','Destination':'/cache','RW':True}],
        'NetworkSettings':{'Networks':{'bridge':{'IPAMConfig':None,'Aliases':None}}}}
    result=module.clone_config(current,'sha256:new','job')
    assert result['Image']=='sha256:new' and result['Env']==['TOKEN=private']
    assert result['HostConfig']['DeviceRequests']==current['HostConfig']['DeviceRequests']
    assert result['HostConfig']['PortBindings']==current['HostConfig']['PortBindings']
    assert 'keep-volume:/cache:rw' in result['HostConfig']['Binds']
    assert result['Cmd']==['serve'] and current['Config']['Image']=='old:v1'


def test_k8s_update_changes_only_selected_container_with_uid_and_resource_version_guard(monkeypatch,tmp_path):
    module=executor();calls=[]
    workload={'metadata':{'uid':'controller','resourceVersion':'5','generation':1},'spec':{'replicas':1,'updateStrategy':{'type':'RollingUpdate'},
        'template':{'metadata':{},'spec':{'containers':[{'name':'nexusai','image':'old:v1'},{'name':'triton','image':'triton:v1'}]}}},'status':{}}
    target=dict(environment='k8s',namespace='ai',workload_kind='StatefulSet',workload='workload',container='nexusai')
    def command(args,timeout=60):
        calls.append(args)
        if args[1]=='get':return json.dumps(workload)
        if args[1]=='patch':return '{}'
        raise AssertionError(args)
    monkeypatch.setattr(module,'command',command)
    monkeypatch.setattr(module,'folder',lambda job:tmp_path)
    module.k8s_apply(target,dict(uid='controller',image='old:v1',template_hash=module.template_hash(workload)),'new:v2',str(uuid4()),False)
    patch=json.loads(next(c for c in calls if c[1]=='patch')[-1])
    assert {'op':'test','path':'/metadata/uid','value':'controller'} in patch
    assert {'op':'test','path':'/metadata/resourceVersion','value':'5'} in patch
    assert {'op':'replace','path':'/spec/template/spec/containers/0/image','value':'new:v2'} in patch
    assert {'op':'add','path':'/spec/template/spec/containers/0/imagePullPolicy','value':'Never'} in patch
    assert not any('containers/1' in op['path'] for op in patch)


def test_stale_k8s_update_refuses_to_replace_unrelated_change(monkeypatch):
    module=executor()
    workload={'metadata':{'uid':'different','resourceVersion':'6'},'spec':{'template':{'metadata':{},'spec':{'containers':[{'name':'nexusai','image':'other:v3'}]}}}}
    monkeypatch.setattr(module,'command',lambda args,timeout=60:json.dumps(workload))
    with pytest.raises(ValueError):module.k8s_apply(dict(namespace='ai',workload_kind='StatefulSet',workload='w',container='nexusai'),
        dict(uid='controller',image='old:v1',template_hash='hash'),'new:v2',str(uuid4()),False)


def test_no_healthcheck_is_reported_explicitly_not_as_healthy():
    module=executor()
    result=module.docker_summary({'Id':'uid','Name':'/worker','Image':'sha256:id','Config':{'Image':'worker:v1'},'State':{'Running':True}})
    assert result['health']=='not_configured' and result['ready'] is True


def test_k8s_inventory_requires_every_replica_ready_and_revision_converged(monkeypatch):
    module=executor()
    workload={'metadata':{'uid':'c','generation':4},'spec':{'replicas':2,'updateStrategy':{'type':'RollingUpdate'},'template':{'metadata':{},'spec':{'containers':[{'name':'nexusai','image':'new:v2'}]}}},
        'status':{'observedGeneration':4,'readyReplicas':2,'updatedReplicas':1,'currentRevision':'a','updateRevision':'b'}}
    pods=[{'metadata':{'name':'one','uid':'p','ownerReferences':[{'uid':'c'}]},'spec':{'containers':[{'name':'nexusai','image':'new:v2'}]},
        'status':{'phase':'Running','conditions':[{'type':'Ready','status':'True'}],'containerStatuses':[{'name':'nexusai','ready':True,'imageID':'repo@sha256:d'}]}}]
    monkeypatch.setattr(module,'command',lambda args,timeout=60:json.dumps({'items':pods}) if args[2]=='pods' else json.dumps(workload))
    result=module.inventory(dict(environment='k8s',namespace='ai',workload_kind='StatefulSet',workload='w',container='nexusai'))
    assert result['desired']==2 and len(result['instances'])==1 and not result['rollout_complete']


def test_raw_rollback_can_recover_after_name_was_moved_but_new_container_not_created(tmp_path,monkeypatch):
    module=executor();job=str(uuid4());stage=tmp_path/'job';stage.mkdir()
    saved={'Id':'old-id','Name':'/vector','Image':'sha256:old','Config':{'Image':'old:v1','Labels':{}},'State':{'Running':True},
        'HostConfig':{'NetworkMode':'host','RestartPolicy':{'Name':'always'}},'NetworkSettings':{'Networks':{}},'Mounts':[]}
    (stage/'vector.json').write_text(json.dumps(saved));(stage/'vector-backup.json').write_text(json.dumps({'uid':'old-id','name':'vector-old'}))
    monkeypatch.setattr(module,'folder',lambda job:stage)
    calls=[]
    def inspect(name):
        if name=='vector':raise ValueError('missing container')
        return saved
    monkeypatch.setattr(module,'docker_inspect',inspect)
    monkeypatch.setattr(module,'command',lambda args,timeout=60:calls.append(args) or '')
    monkeypatch.setattr(module,'engine',lambda method,path,body=None:calls.append((method,path,body)) or {})
    module.docker_apply({},dict(name='vector',uid='old-id'),job,{'image_id':'sha256:new'},True)
    assert any(isinstance(call,tuple) and '/old-id/rename?name=vector' in call[1] for call in calls)


@pytest.mark.asyncio
async def test_image_prepare_does_not_switch_services_then_activation_checks_every_image_id(monkeypatch):
    from services import image_updates as service,model_deployments
    row=SimpleNamespace(id=uuid4(),source={'image':'service:v2'},target={'environment':'docker'},inspection={},status='transferring')
    calls=[];state={'updated':False}
    original=dict(instances=[dict(name='one',uid='old',ready=True,image='service:v1',image_id='old-id')],desired=1,controller=None)
    async def validate(row):pass
    async def save(db,row,message,status=None,**inspection):
        row.inspection.update(inspection)
        if status:row.status=status
    async def remote(db,row,action,**values):
        calls.append(action)
        if action=='inventory':return dict(instances=[dict(name='one',uid='new',ready=True,image='sha256:new',image_id='new-id')],desired=1,controller=None) if state['updated'] else original
        if action=='prepare':return dict(prepared=True,image_id='new-id')
        if action=='apply':state['updated']=True;return dict(ok=True)
        raise AssertionError(action)
    monkeypatch.setattr(model_deployments,'validate_connections',validate);monkeypatch.setattr(model_deployments,'save',save);monkeypatch.setattr(service,'remote',remote)
    await service.prepare(None,row)
    assert row.status=='staged' and 'apply' not in calls
    await service.activate(None,row)
    assert row.status=='active' and row.inspection['instances'][0]['image_id']=='new-id'


def test_failed_image_pull_can_be_retried_without_applying_any_container(tmp_path,monkeypatch):
    module=executor();stage=tmp_path/'job';stage.mkdir();job=str(uuid4())
    (stage/'receipt.json').write_text(json.dumps({'image':'service:v2','pending':True,'prepared':False}))
    (stage/'pull-result.json').write_text(json.dumps({'code':1}))
    monkeypatch.setattr(module,'folder',lambda job:stage)
    started=[]
    class Process:pid=999999
    monkeypatch.setattr(module.subprocess,'Popen',lambda *args,**kwargs:started.append(args) or Process())
    result=module.prepare({'environment':'docker'},job,'service:v2',{'instances':[]},retry=True)
    assert result['pending'] and len(started)==1 and not (stage/'pull-result.json').exists()


def test_raw_rollback_retains_replaced_container_instead_of_deleting_its_writable_layer(tmp_path,monkeypatch):
    module=executor();job=str(uuid4());stage=tmp_path/'job';stage.mkdir()
    saved={'Id':'old-id','Name':'/vector','Image':'sha256:old','Config':{'Image':'old:v1','Labels':{}},'State':{'Running':False},
        'HostConfig':{'NetworkMode':'host','RestartPolicy':{'Name':'always'}},'NetworkSettings':{'Networks':{}},'Mounts':[]}
    updated=dict(saved,Id='new-id',Image='sha256:new',Config={'Image':'sha256:new','Labels':{'data-platform.image-update':job+':activate'}})
    (stage/'vector.json').write_text(json.dumps(saved));(stage/'vector-backup.json').write_text(json.dumps({'uid':'old-id','name':'vector-old'}))
    monkeypatch.setattr(module,'folder',lambda job:stage)
    monkeypatch.setattr(module,'docker_inspect',lambda name:saved if name=='old-id' else updated)
    monkeypatch.setattr(module,'command',lambda args,timeout=60:'new-id' if args[1]=='ps' else '')
    operations=[]
    monkeypatch.setattr(module,'engine',lambda method,path,body=None:operations.append((method,path)) or {})
    module.docker_apply({},dict(name='vector',uid='old-id'),job,{'image_id':'sha256:new'},True)
    assert not any(method=='DELETE' for method,path in operations)
    assert any('/new-id/rename?' in path for method,path in operations)


def test_reading_missing_receipt_does_not_create_remote_directories(tmp_path,monkeypatch):
    module=executor();monkeypatch.setattr(module.Path,'home',lambda:tmp_path)
    assert module.dispatch(dict(action='receipt',target={},job_id=str(uuid4())))=={}
    assert not (tmp_path/'.local').exists()


def test_k8s_rollback_does_not_overwrite_a_later_image_even_if_annotation_was_kept(monkeypatch):
    module=executor();job=str(uuid4())
    workload={'metadata':{'uid':'c','resourceVersion':'8'},'spec':{'template':{'metadata':{'annotations':{'data-platform/image-update':job+':activate'}},
        'spec':{'containers':[{'name':'nexusai','image':'unrelated:v3'}]}}}}
    monkeypatch.setattr(module,'command',lambda args,timeout=60:json.dumps(workload))
    with pytest.raises(ValueError):module.k8s_apply(dict(namespace='ai',workload_kind='StatefulSet',workload='w',container='nexusai'),
        dict(uid='c',image='old:v1',template_hash='hash'),'old:v1',job,True,expected_image='wanted:v2')
