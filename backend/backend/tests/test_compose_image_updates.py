"""Compose file and container changes use temporary files and command fixtures."""
import json
from uuid import uuid4
import pytest
from services import image_update_executor as service


SOURCE='''version: "3.9"
services:
  one:
    image: registry/app:v1 # keep this comment
    environment:
      TOKEN: fixture-secret
  two:
    image: registry/app:v1
'''


def test_image_edit_preserves_other_services_comments_and_credentials():
    updated,old=service.compose_image(SOURCE,'one','registry/app:v2 # keep this comment')
    assert old=='registry/app:v1 # keep this comment'
    assert updated==SOURCE.replace('registry/app:v1 #','registry/app:v2 #')
    assert service.compose_image(updated,'one',old)[0]==SOURCE


@pytest.mark.parametrize('text',["services: {one: {image: old:v1}}",'services:\n  one:\n    extends: other\n'])
def test_unsupported_image_layout_is_rejected_before_writing(text):
    with pytest.raises(ValueError):service.compose_image(text,'one','new:v2')


def fixture(tmp_path,monkeypatch):
    project=tmp_path/'docker-compose.yml';project.write_text(SOURCE)
    stage=tmp_path/'job';stage.mkdir();calls=[]
    def command(args,timeout=60):
        calls.append(args)
        if args[-1].startswith('--hash='):
            # Per-service hash changes only for that service; another service may change independently.
            return 'service '+service.digest(service.compose_image(project.read_text(),args[-1][7:])[1])
        if args[-1]=='config':return project.read_text()
        return ''
    monkeypatch.setattr(service,'command',command)
    info=lambda name:dict(prefix=['docker-compose','-f',str(project)],service=name,version='3.9')
    return project,stage,calls,info


def test_shared_compose_activation_and_reverse_rollback_restore_exact_original(tmp_path,monkeypatch):
    project,stage,calls,info=fixture(tmp_path,monkeypatch);job=str(uuid4())
    for name in ('one','two'):service.compose_apply(info(name),stage,name,job,'registry/app:v2',False)
    assert project.read_text().count('registry/app:v2')==2
    assert list(stage.glob('compose-*.backup'))[0].read_text()==SOURCE
    for name in ('two','one'):service.compose_apply(info(name),stage,name,job,'registry/app:v1',True)
    assert project.read_text()==SOURCE
    assert all('sha256:' not in json.dumps(call) for call in calls)
    labels=json.loads((stage/'one-labels.json').read_text())
    assert 'image' not in labels['services']['one']


def test_compose_failure_keeps_backup_and_allows_retry(tmp_path,monkeypatch):
    project,stage,calls,info=fixture(tmp_path,monkeypatch);job=str(uuid4());command=service.command
    def fail(args,timeout=60):
        if 'up' in args:raise ValueError('fixture recreate failed')
        return command(args,timeout)
    monkeypatch.setattr(service,'command',fail)
    with pytest.raises(ValueError):service.compose_apply(info('one'),stage,'one',job,'registry/app:v2',False)
    assert 'registry/app:v2' in project.read_text() and list(stage.glob('compose-*.backup'))
    monkeypatch.setattr(service,'command',command)
    service.compose_apply(info('one'),stage,'one',job,'registry/app:v2',False)
    service.compose_apply(info('one'),stage,'one',job,'registry/app:v1',True)
    assert project.read_text()==SOURCE


def test_external_service_change_refuses_rollback(tmp_path,monkeypatch):
    project,stage,calls,info=fixture(tmp_path,monkeypatch);job=str(uuid4())
    service.compose_apply(info('one'),stage,'one',job,'registry/app:v2',False)
    project.write_text(project.read_text().replace('app:v2','app:v3'))
    before=len(calls)
    with pytest.raises(ValueError):service.compose_apply(info('one'),stage,'one',job,'registry/app:v1',True)
    assert not any('up' in call for call in calls[before:])


def test_failed_rollback_validation_restores_active_files_and_labels(tmp_path,monkeypatch):
    project,stage,calls,info=fixture(tmp_path,monkeypatch);job=str(uuid4())
    service.compose_apply(info('one'),stage,'one',job,'registry/app:v2',False)
    active=project.read_bytes();labels=(stage/'one-labels.json').read_bytes();command=service.command
    def invalid(args,timeout=60):
        if args[-1]=='config':return SOURCE.replace('app:v1','other:v7')
        return command(args,timeout)
    monkeypatch.setattr(service,'command',invalid)
    with pytest.raises(ValueError):service.compose_apply(info('one'),stage,'one',job,'registry/app:v1',True)
    assert project.read_bytes()==active and (stage/'one-labels.json').read_bytes()==labels
    monkeypatch.setattr(service,'command',command)
    service.compose_apply(info('one'),stage,'one',job,'registry/app:v1',True)
    assert project.read_text()==SOURCE


def test_legacy_image_overrides_are_not_carried_into_new_compose_prefix(tmp_path,monkeypatch):
    monkeypatch.setattr(service.Path,'home',lambda:tmp_path)
    generated=tmp_path/'.local/share/data-platform/image-updates'/str(uuid4())/'one-image.json'
    prefix=['docker-compose','-f','/project/compose.yml','-f',str(generated)]
    assert service.compose_prefix(prefix)==['docker-compose','-f','/project/compose.yml']


def test_image_reference_checks_tag_identity_and_keeps_name(monkeypatch):
    monkeypatch.setattr(service,'command',lambda args,timeout=60:json.dumps([{'Id':'sha256:expected','RepoTags':['registry/app:v2']}]))
    assert service.image_reference('registry/app:v2','sha256:expected')=='registry/app:v2'
    assert service.image_reference('sha256:expected','sha256:expected')=='registry/app:v2'
    with pytest.raises(ValueError):service.image_reference('registry/app:v2','sha256:different')


def test_full_compose_update_and_rollback_keep_named_images(tmp_path,monkeypatch):
    project,stage,calls,info=fixture(tmp_path,monkeypatch);job=str(uuid4());original_command=service.command
    live={};instances=[]
    for name in ('one','two'):
        row=dict(Id=name+'-old',Name='/'+name,Image='sha256:old',Config={'Image':'registry/app:v1','Labels':{}},State={'Running':True},HostConfig={},Mounts=[])
        live[name]=row
        (stage/(name+'.json')).write_text(json.dumps(row))
        instances.append(dict(name=name,uid=row['Id'],fingerprint=service.docker_summary(row)['fingerprint'],compose=info(name)))
    def command(args,timeout=60):
        if args[:3]==['docker','image','inspect']:
            return json.dumps([{'Id':'sha256:new' if args[-1]=='registry/app:v2' else 'sha256:old'}])
        if 'up' in args:
            name=args[-1];image=service.compose_image(project.read_text(),name)[1].split(' #')[0]
            live[name]=dict(live[name],Id=name+'-'+image[-2:],Image='sha256:new' if image.endswith('v2') else 'sha256:old',Config={'Image':image,'Labels':{'data-platform.image-update':job+(':activate' if image.endswith('v2') else ':rollback')}})
        return original_command(args,timeout)
    monkeypatch.setattr(service,'command',command);monkeypatch.setattr(service,'folder',lambda job:stage)
    monkeypatch.setattr(service,'existing_container',lambda name:live[name]);monkeypatch.setattr(service,'docker_inspect',lambda name:live[name])
    monkeypatch.setattr(service,'compose',lambda row:info(row['Name'][1:]))
    receipt=dict(image='registry/app:v2',image_id='sha256:new')
    for instance in instances:service.docker_apply({},instance,job,receipt,False)
    assert all(row['Config']['Image']=='registry/app:v2' and row['Image']=='sha256:new' for row in live.values())
    for instance in reversed(instances):service.docker_apply({},instance,job,receipt,True)
    assert project.read_text()==SOURCE and all(row['Config']['Image']=='registry/app:v1' for row in live.values())
