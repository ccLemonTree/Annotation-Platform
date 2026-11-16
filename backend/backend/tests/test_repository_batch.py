"""Batch variants use isolated storage and never execute TensorRT."""
import hashlib
import json
import pytest
from tests.test_model_repository import repository_tables,send,run_record,source_fixture


def upload(client,headers,profile,**values):
    return send(client,headers,'tensorrt',gpu_model='Tesla T4',tensorrt_version='10.14.1',batch_profile=profile,**values)


def test_batch_variants_coexist_and_duplicates_are_rejected(client,auth_headers):
    rows=[]
    for profile in ('fixed:1','fixed:8','dynamic:1:4:8'):
        response=upload(client,auth_headers,profile);assert response.status_code==201,response.text
        rows.append(response.json());assert rows[-1]['batch_profile']==profile
    assert upload(client,auth_headers,'fixed:08').status_code==409
    assert client.get('/api/model-repository/groups',headers=auth_headers).json()['items'][0]['version_count']==3
    for row in rows:assert client.get('/api/model-repository/'+row['id']+'/download',headers=auth_headers).content==b'weights'


@pytest.mark.parametrize('profile',['fixed:0','fixed:1025','dynamic:8:4:1','dynamic:1:9:8','dynamic:1:4','other:8','fixed:true'])
def test_invalid_batch_specs_are_rejected(client,auth_headers,profile):
    assert upload(client,auth_headers,profile).status_code==422
    assert send(client,auth_headers,'pt',batch_profile='fixed:8').status_code==422


def test_batch_is_immutable_and_rename_collision_uses_batch(client,auth_headers):
    one=upload(client,auth_headers,'fixed:1').json()
    eight=upload(client,auth_headers,'fixed:8',name='second').json();url='/api/model-repository/'+eight['id']
    response=client.patch(url,headers=auth_headers,json=dict(name=one['name'],version='v1',description='batch eight'))
    assert response.status_code==200 and response.json()['batch_profile']=='fixed:8'
    assert client.patch(url,headers=auth_headers,json=dict(name=one['name'],version='v1',description='',batch_profile='fixed:1')).status_code==422
    duplicate=upload(client,auth_headers,'fixed:8',name='third').json()
    assert client.patch('/api/model-repository/'+duplicate['id'],headers=auth_headers,json=dict(name=one['name'],version='v1',description='')).status_code==409


@pytest.mark.asyncio
async def test_training_import_uses_verified_batch_metadata(client,auth_headers,test_db,tmp_path,monkeypatch):
    from services import training_runs
    run=await run_record(test_db,'model_export');source_fixture(monkeypatch,tmp_path,{'model.engine':b'engine'},format='tensorrt',gpu='Tesla T4')
    original=training_runs.remote
    async def remote(db,run,action,**values):
        result=await original(db,run,action,**values)
        if action=='repository_metadata':result['batch_profile']='dynamic:1:4:8'
        return result
    monkeypatch.setattr(training_runs,'remote',remote)
    values=dict(name='trained',version='v1',format='tensorrt',run_id=str(run.id),artifact_path='model.engine')
    response=client.post('/api/model-repository/from-training',headers=auth_headers,json=values)
    assert response.status_code==201 and response.json()['batch_profile']=='dynamic:1:4:8'
    assert client.post('/api/model-repository/from-training',headers=auth_headers,json=values|{'batch_profile':'fixed:8'}).status_code==422


@pytest.mark.parametrize('options,expected',[
    ({'dynamic_batch':False,'batch':8},'fixed:8'),
    ({'dynamic_batch':True,'min_batch':1,'opt_batch':4,'max_batch':8},'dynamic:1:4:8'),
    ({},''),
])
def test_executor_records_batch_from_export_manifest(tmp_path,options,expected):
    from services.training_executor import repository_metadata
    root=tmp_path.resolve();(root/'model.engine').write_bytes(b'engine')
    (root/'result.json').write_text(json.dumps(dict(format='tensorrt',gpu_name='Tesla T4',versions={'tensorrt_runtime':'10.14.1'},options=options,
        files=[dict(path='model.engine',bytes=6,sha256=hashlib.sha256(b'engine').hexdigest())])))
    assert repository_metadata(root,'model.engine')['batch_profile']==expected
