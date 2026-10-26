"""Only isolated files and mocked subprocesses; no SSH or service updates."""
import importlib
import json
from pathlib import Path
from uuid import uuid4
import pytest


def module():return importlib.import_module('services.deployment_logs_executor')


@pytest.mark.parametrize('text,secret',[
    ('password="two words"','words'),("api_key='two words'",'words'),
    ('token=my-secret','my-secret'),('{"password":"very private"}','private'),
    ('https://user:private@registry/path','private'),('Authorization: Bearer private','private'),
])
def test_redaction_covers_quoted_credentials_and_tokens(text,secret):
    assert secret not in module().redact(text)


def test_docker_update_tail_is_bounded_redacted_and_read_only(tmp_path,monkeypatch):
    mod=module();monkeypatch.setattr(Path,'home',lambda:tmp_path)
    job=str(uuid4());folder=tmp_path/'.local/share/data-platform/image-updates'/job
    folder.mkdir(parents=True);file=folder/'pull.log'
    file.write_text('x'*70000+'\nAuthorization: Bearer private-token\npassword=hunter2\n')
    before=file.read_bytes()
    result=mod.dispatch(dict(source='updates',job_id=job,target={'environment':'docker'}))
    assert result['truncated'] and len(result['content'].encode())<=65536
    assert 'private-token' not in result['content'] and 'hunter2' not in result['content']
    assert file.read_bytes()==before


def test_absent_update_log_does_not_create_remote_directories(tmp_path,monkeypatch):
    mod=module();monkeypatch.setattr(Path,'home',lambda:tmp_path)
    assert mod.dispatch(dict(source='updates',job_id=str(uuid4()),target={'environment':'docker'}))['content']==''
    assert not list(tmp_path.iterdir())


def test_plain_file_tail_reports_truncation(tmp_path,monkeypatch):
    mod=module();monkeypatch.setattr(Path,'home',lambda:tmp_path)
    job=str(uuid4());folder=tmp_path/'.local/share/data-platform/image-updates'/job;folder.mkdir(parents=True)
    (folder/'pull.log').write_text('x'*70000)
    result=mod.dispatch(dict(source='updates',job_id=job,target={'environment':'docker'}))
    assert result['truncated'] and len(result['content'])==65536


def test_command_reads_large_metadata_and_bounds_log_output():
    import sys
    mod=module()
    value=mod.command([sys.executable,'-c',"import json;print(json.dumps({'value':'x'*100000}))"])
    assert len(json.loads(value)['value'])==100000
    value=mod.command([sys.executable,'-c',"print('x'*70000+'END')"],limit=mod.LIMIT)
    assert len(value.encode())==mod.LIMIT and value.rstrip('\r\n').endswith('END')
    with pytest.raises(ValueError,match='日志命令读取失败'):
        mod.command([sys.executable,'-c',"import sys;sys.stderr.write('password=private');sys.exit(1)"])


def test_update_log_refuses_symlink_and_invalid_cache_key(tmp_path,monkeypatch):
    mod=module();monkeypatch.setattr(Path,'home',lambda:tmp_path)
    with pytest.raises(ValueError):mod.dispatch(dict(source='updates',job_id='../private',target={'environment':'docker'}))
    with pytest.raises(ValueError):mod.dispatch(dict(source='updates',job_id=str(uuid4()),cache_key='../private',target={'environment':'k8s'}))
    folder=tmp_path/'.local/share/data-platform/image-updates'/str(uuid4());folder.mkdir(parents=True)
    outside=tmp_path/'private.log';outside.write_text('private')
    try:(folder/'pull.log').symlink_to(outside)
    except OSError:pytest.skip('symlink permission unavailable')
    with pytest.raises(ValueError):mod.dispatch(dict(source='updates',job_id=folder.name,target={'environment':'docker'}))


def test_ansible_tail_uses_only_latest_attempt(tmp_path,monkeypatch):
    mod=module();monkeypatch.setattr(Path,'home',lambda:tmp_path)
    cache='a'*64;attempts=tmp_path/'.local/share/data-platform/image-updates/preloads'/cache/'attempts'
    for name in ['old','new']:
        folder=attempts/name;folder.mkdir(parents=True);(folder/'ansible.log').write_text(name)
    import os
    os.utime(attempts/'old',(1,1));os.utime(attempts/'new',(2,2))
    result=mod.dispatch(dict(source='updates',job_id=str(uuid4()),cache_key=cache,target={'environment':'k8s'}))
    assert 'new' in result['content'] and 'old' not in result['content']


def test_service_logs_use_owned_pods_and_fixed_read_only_arguments(monkeypatch):
    mod=module();calls=[]
    def command(args,**kwargs):
        calls.append(args)
        if 'logs' in args:return 'ready\nAuthorization: Bearer hidden'
        if 'StatefulSet' in args:return json.dumps({'metadata':{'uid':'owner'}})
        return json.dumps({'items':[
            {'metadata':{'name':'owned','uid':'pod1','ownerReferences':[{'uid':'owner'}]},'spec':{'containers':[{'name':'triton'}]}},
            {'metadata':{'name':'other','uid':'pod2','ownerReferences':[{'uid':'unrelated'}]},'spec':{'containers':[{'name':'triton'}]}}]})
    monkeypatch.setattr(mod,'command',command)
    target=dict(environment='k8s',namespace='ai',workload_kind='StatefulSet',workload='triton',container='triton')
    result=mod.dispatch(dict(source='service',job_id=str(uuid4()),target=target,instance='owned'))
    assert result['selected_instance']=='owned' and [r['name'] for r in result['instances']]==['owned']
    assert 'hidden' not in result['content'] and '--tail=200' in calls[-1] and '--limit-bytes=65536' in calls[-1]
    with pytest.raises(ValueError):mod.dispatch(dict(source='service',job_id=str(uuid4()),target=target,instance='other'))
    assert all(not any(word in args for word in ['exec','apply','patch','restart','pull']) for args in calls)
