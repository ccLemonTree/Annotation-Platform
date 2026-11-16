"""Grouping/config tests use isolated models, SQLite and temporary files only."""
import hashlib
import io
from uuid import UUID
import zipfile
import pytest
from tests.test_model_repository import repository_tables,send

CONFIG='name: "fire"\nplatform: "tensorrt_plan"\nmax_batch_size: 0\n'


def trt(client,headers,config=CONFIG.encode(),**values):
    return send(client,headers,'tensorrt',files=[('files',('model.engine',b'engine')),('files',('config.pbtxt',config))],gpu_model='Tesla T4',tensorrt_version='10.14.1',**values)


def test_grouping_combines_formats_and_paginates_groups(client,auth_headers):
    for kind,name in [('pt','detr1023烟火训练'),('onnx','detr1023烟火训练 · ONNX'),('tensorrt','detr1023烟火训练 · TENSORRT')]:
        assert send(client,auth_headers,kind,name=name,**({'gpu_model':'T4'} if kind=='tensorrt' else {})).status_code==201
    for i in range(21):assert send(client,auth_headers,name='other-'+str(i)).status_code==201
    response=client.get('/api/model-repository/groups?q=detr1023',headers=auth_headers)
    assert response.status_code==200,response.text
    result=response.json();assert result['total']==1 and result['version_total']==3
    group=result['items'][0];assert group['name']=='detr1023烟火训练' and group['version_count']==3 and set(group['formats'])=={'pt','onnx','tensorrt'}
    versions=client.get('/api/model-repository',headers=auth_headers,params={'group_name':group['name'],'page_size':2}).json()
    assert versions['total']==3 and len(versions['items'])==2
    assert client.get('/api/model-repository/groups?page_size=20',headers=auth_headers).json()['total']==22
    assert len(client.get('/api/model-repository/groups?page_size=20&page=2',headers=auth_headers).json()['items'])==2
    filtered=client.get('/api/model-repository/groups?format=tensorrt&gpu_model=T4',headers=auth_headers).json()
    assert filtered['total']==1 and filtered['version_total']==1


def test_tensor_config_upload_preview_and_zip(client,auth_headers):
    response=trt(client,auth_headers);assert response.status_code==201,response.text
    row=response.json();assert len(row['files'])==2 and row['file_size']==6+len(CONFIG.encode())
    url='/api/model-repository/'+row['id']
    result=client.get(url+'/config',headers=auth_headers);assert result.status_code==200,result.text
    assert result.json()['content']==CONFIG and result.json()['sha256']==hashlib.sha256(CONFIG.encode()).hexdigest()
    download=client.get(url+'/download',headers=auth_headers)
    with zipfile.ZipFile(io.BytesIO(download.content)) as archive:
        assert archive.read('model.engine')==b'engine' and archive.read('config.pbtxt').decode()==CONFIG


def test_group_search_escapes_wildcards_and_ignores_suffix_case(client,auth_headers):
    assert send(client,auth_headers,name='worker%_one').status_code==201
    assert send(client,auth_headers,'onnx',name='worker%_one · onnx').status_code==201
    assert send(client,auth_headers,name='workerXXone').status_code==201
    result=client.get('/api/model-repository/groups',headers=auth_headers,params={'q':'%_'}).json()
    assert result['total']==1 and result['items'][0]['name']=='worker%_one' and result['version_total']==2
    assert client.get('/api/model-repository',headers=auth_headers,params={'group_name':'worker%_one'}).json()['total']==2


@pytest.mark.parametrize('config',[b'bad\x00config',b'\xff\xfe',b'x'*(1024**2+1)],ids=['nul','invalid-utf8','oversized'])
def test_config_rejects_binary_or_oversized_and_cleans_upload(client,auth_headers,tmp_path,config):
    assert trt(client,auth_headers,config=config).status_code==422
    assert client.get('/api/model-repository',headers=auth_headers).json()['total']==0
    assert not [p for p in (tmp_path/'models').rglob('*') if p.is_file()]


def test_trt_allows_only_engine_and_optional_root_config(client,auth_headers):
    for extra in ('nested/config.pbtxt','other.engine','random.txt'):
        assert send(client,auth_headers,'tensorrt',gpu_model='T4',files=[('files',('model.engine',b'engine')),('files',(extra,b'text'))]).status_code==422


@pytest.mark.asyncio
async def test_config_guards_tenant_status_and_corruption(client,auth_headers,test_db,test_user,tmp_path):
    from models import Tenant
    from models.model_repository import ModelRepositoryItem
    row=trt(client,auth_headers).json();url='/api/model-repository/'+row['id']+'/config'
    assert client.get(url).status_code==401
    test_user.role='annotator';await test_db.commit();assert client.get(url,headers=auth_headers).status_code==200
    test_db.add(Tenant(id=2,tenant_name='other'));test_user.tenant_id=2;await test_db.commit()
    assert client.get(url,headers=auth_headers).status_code==404
    assert client.get('/api/model-repository/groups',headers=auth_headers).json()['total']==0
    test_user.tenant_id=1;record=await test_db.get(ModelRepositoryItem,UUID(row['id']));record.status='importing';await test_db.commit()
    assert client.get(url,headers=auth_headers).status_code==409
    record.status='ready';await test_db.commit()
    config=tmp_path/'models/repository/1'/row['id']/'files/config.pbtxt';config.write_bytes(b'x'*len(CONFIG.encode()))
    assert client.get(url,headers=auth_headers).status_code==409


def test_config_absence_and_non_trt_are_explicit(client,auth_headers):
    pt=send(client,auth_headers).json()
    assert client.get('/api/model-repository/'+pt['id']+'/config',headers=auth_headers).status_code==422
    engine=send(client,auth_headers,'tensorrt',gpu_model='T4').json()
    assert client.get('/api/model-repository/'+engine['id']+'/config',headers=auth_headers).status_code==404
