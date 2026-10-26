"""Ansible preloading is generated and checked locally; never run on real nodes."""
import importlib
import json
from types import SimpleNamespace
import pytest


def module():
    try:return importlib.import_module('services.image_preload_executor')
    except ModuleNotFoundError:pytest.fail('K8s Ansible预分发尚未接入')


def plays():
    names=['Prepare Triton archive on controller','Inspect Triton image state on GPU nodes','Rsync Triton archive and start import','Wait for Triton import to finish']
    result=[{'name':name,'hosts':'localhost' if i==0 else 'gpu-workers','vars':{'triton_image_ref':'old:v1'},'tasks':[{'name':'pull' if i==0 else 'node task','command':'ctr -n {{ containerd_namespace }} images pull {{ triton_image_ref }}' if i==0 else 'true'}]} for i,name in enumerate(names)]
    return result+[{'name':'Prepare GME archive on controller','hosts':'localhost','tasks':[{'command':'unrelated image pull'}]}]


def test_generated_playbook_reuses_only_selected_image_pipeline_and_limits_copy_concurrency(tmp_path):
    m=module();result=m.build_playbook(plays(),tmp_path,'registry/service:v2')
    assert not any('GME' in play.get('name','') for play in result)
    controller=next(play for play in result if play.get('name')=='Prepare Triton archive on controller')
    assert controller['vars']['triton_image_ref']=='registry/service:v2'
    assert all(task.get('no_log') for play in result for task in play['tasks'])
    assert next(play for play in result if play.get('name')=='Rsync Triton archive and start import')['serial']==4
    pull=next(t for t in controller['tasks'] if t.get('name')=='pull')
    assert '--hosts-dir' in pull['command']['argv']
    assert not any('password' in str(t.get('command','')).lower() for t in controller['tasks'])


def test_inventory_must_cover_every_live_node_and_not_redirect_a_matching_name():
    m=module();nodes=[dict(name='one',uid='uid-one',addresses=['10.0.0.1'])]
    with pytest.raises(ValueError):m.cover_inventory({'_meta':{'hostvars':{'one':{'ansible_host':'10.0.0.2'}}}},nodes)
    result=m.cover_inventory({'_meta':{'hostvars':{'alias':{'ansible_host':'10.0.0.1','ansible_password':'private'}}}},nodes)
    assert result['one']['ansible_password']=='private'


def test_every_node_must_report_exact_digest_and_missing_node_never_passes(tmp_path):
    m=module();nodes=[dict(name='one',uid='u1'),dict(name='two',uid='u2')]
    receipt=dict(image='registry/service:v2',digest='sha256:'+'a'*64,archive_sha256='b'*64)
    import json
    (tmp_path/'one.json').write_text(json.dumps(dict(node='one',uid='u1',digest=receipt['digest'],archive_sha256=receipt['archive_sha256'],ok=True)))
    with pytest.raises(ValueError):m.verify_results(tmp_path,nodes,receipt)
    (tmp_path/'two.json').write_text(json.dumps(dict(node='two',uid='u2',digest='sha256:'+'c'*64,archive_sha256=receipt['archive_sha256'],ok=True)))
    with pytest.raises(ValueError):m.verify_results(tmp_path,nodes,receipt)


def test_pinned_reference_preserves_registry_port():
    m=module();digest='sha256:'+'a'*64
    assert m.pinned('registry:9091/team/image:v2',digest)=='registry:9091/team/image@'+digest


@pytest.mark.parametrize('missing',[False,True])
def test_background_preload_requires_fresh_results_for_every_node(tmp_path,monkeypatch,missing):
    import sys
    m=module();monkeypatch.setitem(sys.modules,'fcntl',SimpleNamespace(flock=lambda *args:None,LOCK_EX=1))
    monkeypatch.setattr(m,'root',lambda:tmp_path)
    nodes=[dict(name='one',uid='u1',addresses=['10.0.0.1']),dict(name='two',uid='u2',addresses=['10.0.0.2'])]
    info=dict(playbook='playbook',inventory='inventory',nodes=nodes)
    key='a'*64;stage=tmp_path/'preloads'/key;stage.mkdir(parents=True)
    (stage/'input.json').write_text(json.dumps(dict(image='registry/service:v2',preflight=info,target={})))
    monkeypatch.setattr(m,'preflight',lambda target:info);monkeypatch.setattr(m,'cluster_nodes',lambda:nodes)
    monkeypatch.setattr(m,'load_plays',lambda path:plays());monkeypatch.setattr(m,'prepare_hosts',lambda *args:None)
    monkeypatch.setattr(m,'command',lambda args,timeout=90:json.dumps({'_meta':{'hostvars':{node['name']:dict(ansible_host=node['addresses'][0]) for node in nodes}}}))
    controller=dict(image='registry/service:v2',digest='sha256:'+'b'*64,archive_sha256='c'*64)
    def execute(args,**kwargs):
        attempt=m.Path(args[3]).parent;(attempt/'controller.json').write_text(json.dumps(controller))
        for node in nodes[:1] if missing else nodes:
            (attempt/'results'/(node['name']+'.json')).write_text(json.dumps(dict(node=node['name'],uid=node['uid'],digest=controller['digest'],archive_sha256=controller['archive_sha256'],ok=True)))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(m.subprocess,'run',execute)
    m.run(key);result=json.loads((stage/'result.json').read_text())
    assert result['prepared'] is not missing
    if not missing:assert result['node_count']==2 and result['pinned_image'].endswith(controller['digest'])


def test_registry_credentials_stay_in_private_hosts_file_not_command_arguments(tmp_path,monkeypatch):
    import base64
    m=module();auth=base64.b64encode(b'fixture-user:fixture-only-password').decode()
    secret=base64.b64encode(json.dumps({'auths':{'registry.example':{'auth':auth}}}).encode()).decode()
    def command(args,timeout=90):
        if args[2]=='secret':return json.dumps({'data':{'.dockerconfigjson':secret}})
        return json.dumps({'spec':{'template':{'spec':{'imagePullSecrets':[{'name':'configured-secret'}]}}}})
    monkeypatch.setattr(m,'command',command)
    m.prepare_hosts(tmp_path,dict(workload_kind='StatefulSet',workload='w',namespace='ai'),'registry.example/team/image:v2')
    content=(tmp_path/'hosts/registry.example/hosts.toml').read_text()
    assert 'Basic '+auth in content
