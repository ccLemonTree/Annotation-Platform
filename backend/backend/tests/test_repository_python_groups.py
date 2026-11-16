"""Explicit groups and Python backend directories use only isolated storage."""
import io
import zipfile

import pytest

from tests.test_model_repository import repository_tables,send


def python_files(prefix='sam3_pipeline/'):
    return {prefix+'config.pbtxt':b'name: "sam3_pipeline"\nbackend: "python"\n',
            prefix+'1/model.py':b'raise RuntimeError("uploaded code must not run")\n',
            prefix+'1/tokenizer.json':b'{}',prefix+'1/pos_enc_1008.npy':b'fixture-array',
            prefix+'1/__init__.py':b''}


def upload_python(client,headers,files=None,entry='sam3_pipeline/1/model.py',**values):
    data=dict(name='sam3_pipeline',group_name='sam3',version='v1',format='python',entry_file=entry)|values
    files=python_files() if files is None else files
    return client.post('/api/model-repository/upload',headers=headers,data=data,
        files=[('files',(name,content)) for name,content in files.items()])


def test_explicit_group_preserves_names_and_legacy_fallback(client,auth_headers):
    for name in ('sam3-backbone-tensorrt','sam3-text_encoder-tensorrt','sam3-enc_dec-tensorrt'):
        response=send(client,auth_headers,'tensorrt',name=name,group_name=' sam3 ',gpu_model='Tesla T4')
        assert response.status_code==201,response.text
        assert response.json()['name']==name and response.json()['group_name']=='sam3'
    assert send(client,auth_headers,'onnx',name='sam3',group_name='sam3').status_code==201
    assert send(client,auth_headers,name='legacy · PT').status_code==201
    groups=client.get('/api/model-repository/groups',headers=auth_headers).json()
    assert groups['total']==2 and groups['version_total']==5
    assert next(g for g in groups['items'] if g['name']=='sam3')['version_count']==4
    versions=client.get('/api/model-repository?group_name=sam3',headers=auth_headers).json()
    assert versions['total']==4 and len({r['name'] for r in versions['items']})==4
    assert client.get('/api/model-repository?group_name=legacy',headers=auth_headers).json()['total']==1


def test_group_edit_is_optional_and_searchable(client,auth_headers):
    original=send(client,auth_headers,group_name='family').json()
    assert 'id' in original,original
    url='/api/model-repository/'+original['id']
    assert client.get('/api/model-repository/groups?q=family',headers=auth_headers).json()['version_total']==1
    result=client.patch(url,headers=auth_headers,json=dict(name='detector',version='v1',description='note')).json()
    assert result['group_name']=='family'
    result=client.patch(url,headers=auth_headers,json=dict(name='detector',version='v1',group_name='new group')).json()
    assert result['group_name']=='new group' and result['files']==original['files']
    result=client.patch(url,headers=auth_headers,json=dict(name='detector',version='v1',group_name='')).json()
    assert result['group_name']==''
    assert client.get('/api/model-repository/groups',headers=auth_headers).json()['items'][0]['name']=='detector'


@pytest.mark.parametrize('prefix',['','sam3_pipeline/','SAM3_pipeline/'])
def test_python_directory_is_stored_downloaded_and_previewed_without_execution(client,auth_headers,prefix):
    files=python_files(prefix)
    response=upload_python(client,auth_headers,files,entry=prefix+'1/model.py')
    assert response.status_code==201,response.text
    row=response.json();assert row['format']=='python' and row['group_name']=='sam3' and row['gpu_model']==''
    assert {f['path'] for f in row['files']}==set(files)
    assert row['file_size']==sum(map(len,files.values()))
    root='/api/model-repository/'+row['id']
    config=client.get(root+'/config',headers=auth_headers)
    assert config.status_code==200 and config.json()['content']==files[prefix+'config.pbtxt'].decode()
    assert config.json()['path']==prefix+'config.pbtxt'
    download=client.get(root+'/download',headers=auth_headers)
    assert download.status_code==200 and download.headers['content-type'].startswith('application/zip')
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert set(archive.namelist())==set(files)
        for name,content in files.items():assert archive.read(name)==content
    assert client.get('/api/model-repository?format=python',headers=auth_headers).json()['total']==1


@pytest.mark.parametrize('case',['missing_config','missing_entry','empty_entry','bad_version','traversal','outside_root','bad_config','big_config','config_case'])
def test_invalid_python_directories_leave_no_record(client,auth_headers,case,tmp_path):
    files=python_files();entry='sam3_pipeline/1/model.py'
    if case=='missing_config':files.pop('sam3_pipeline/config.pbtxt')
    elif case=='missing_entry':files.pop(entry)
    elif case=='empty_entry':files[entry]=b''
    elif case=='bad_version':files['sam3_pipeline/latest/model.py']=files.pop(entry);entry='sam3_pipeline/latest/model.py'
    elif case=='traversal':files['sam3_pipeline/../escape.py']=b'bad'
    elif case=='outside_root':files['other/config.pbtxt']=b'outside'
    elif case=='bad_config':files['sam3_pipeline/config.pbtxt']=b'\xff\x00'
    elif case=='big_config':files['sam3_pipeline/config.pbtxt']=b'x'*(1024**2+1)
    elif case=='config_case':files['sam3_pipeline/CONFIG.PBTXT']=files.pop('sam3_pipeline/config.pbtxt')
    response=upload_python(client,auth_headers,files,entry=entry)
    assert response.status_code==422,response.text
    assert client.get('/api/model-repository',headers=auth_headers).json()['total']==0
    root=tmp_path/'models/repository/1'
    assert not root.exists() or not list(root.iterdir())


@pytest.mark.parametrize('group',['bad\nname','x'*101])
def test_invalid_group_is_rejected(client,auth_headers,group):
    assert send(client,auth_headers,group_name=group).status_code==422


@pytest.mark.asyncio
async def test_training_import_can_choose_group_without_changing_source(client,auth_headers,test_db,monkeypatch,tmp_path):
    from tests.test_model_repository import run_record,source_fixture
    run=await run_record(test_db)
    source_fixture(monkeypatch,tmp_path)
    response=client.post('/api/model-repository/from-training',headers=auth_headers,
        json=dict(name='trained component',group_name='family',version='v1',format='pt',run_id=str(run.id),artifact_path='best.pt'))
    assert response.status_code==201,response.text
    assert response.json()['group_name']=='family' and response.json()['source']['kind']=='training'
