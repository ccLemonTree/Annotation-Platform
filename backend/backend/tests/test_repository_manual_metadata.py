"""Only manually uploaded TensorRT build metadata can be corrected."""
from uuid import UUID

import pytest

from tests.test_model_repository import repository_tables,send


def test_manual_build_metadata_edit_preserves_artifact(client,auth_headers):
    original=send(client,auth_headers,'tensorrt',gpu_model='NVIDIA T4',tensorrt_version='101401',batch_profile='fixed:1').json()
    url='/api/model-repository/'+original['id']
    response=client.patch(url,headers=auth_headers,json=dict(name=original['name'],version=original['version'],
        description='修正手填信息',gpu_model=' Tesla T4 ',tensorrt_version=' 10.14.1 '))
    assert response.status_code==200,response.text
    edited=response.json()
    assert (edited['gpu_model'],edited['tensorrt_version'])==('Tesla T4','10.14.1')
    for key in ('id','format','run_id','source','entry_file','file_size','files','batch_profile','created_at'):
        assert edited[key]==original[key]
    assert client.get(url+'/download',headers=auth_headers).content==b'weights'
    renamed=client.patch(url,headers=auth_headers,json=dict(name='renamed',version='v2')).json()
    assert renamed['gpu_model']=='Tesla T4' and renamed['tensorrt_version']=='10.14.1'
    assert client.patch(url,headers=auth_headers,json=dict(name='renamed',version='v2',tensorrt_version='')).json()['tensorrt_version']==''


@pytest.mark.asyncio
@pytest.mark.parametrize('source',[{'kind':'training'},{}])
async def test_unknown_and_training_build_metadata_stay_read_only(client,auth_headers,test_db,source):
    from models.model_repository import ModelRepositoryItem
    original=send(client,auth_headers,'tensorrt',gpu_model='Tesla T4',tensorrt_version='10.14.1').json()
    row=await test_db.get(ModelRepositoryItem,UUID(original['id']));row.source=source;await test_db.commit()
    url='/api/model-repository/'+original['id']
    for fields in ({'gpu_model':'A100'},{'tensorrt_version':'10.15.0'}):
        response=client.patch(url,headers=auth_headers,json=dict(name='renamed',version='v2',description='changed')|fields)
        assert response.status_code==422,response.text
        result=client.get(url,headers=auth_headers).json()
        assert (result['name'],result['version'],result['gpu_model'],result['tensorrt_version'])==('detector','v1','Tesla T4','10.14.1')


@pytest.mark.parametrize('fields',[{'gpu_model':''},{'gpu_model':' '},{'gpu_model':None},{'gpu_model':'x'*201},
    {'gpu_model':'bad\nGPU'},{'tensorrt_version':None},{'tensorrt_version':'x'*101},{'tensorrt_version':'bad\x00version'}])
def test_invalid_build_corrections_are_rejected(client,auth_headers,fields):
    original=send(client,auth_headers,'tensorrt',gpu_model='Tesla T4',tensorrt_version='10.14.1').json()
    response=client.patch('/api/model-repository/'+original['id'],headers=auth_headers,
        json=dict(name='detector',version='v1')|fields)
    assert response.status_code==422,response.text


@pytest.mark.parametrize('format',['pt','onnx'])
def test_non_tensorrt_build_fields_are_rejected(client,auth_headers,format):
    original=send(client,auth_headers,format).json()
    for fields in ({'gpu_model':'Tesla T4'},{'tensorrt_version':'10.14.1'}):
        assert client.patch('/api/model-repository/'+original['id'],headers=auth_headers,
            json=dict(name='detector',version='v1')|fields).status_code==422


def test_gpu_correction_collision_rolls_back_all_metadata(client,auth_headers):
    original=send(client,auth_headers,'tensorrt',gpu_model='T4',tensorrt_version='10.14.1',batch_profile='fixed:1').json()
    assert send(client,auth_headers,'tensorrt',gpu_model='A100',batch_profile='fixed:1').status_code==201
    url='/api/model-repository/'+original['id']
    response=client.patch(url,headers=auth_headers,json=dict(name='detector',version='v1',description='must roll back',
        gpu_model='A100',tensorrt_version='10.15.0'))
    assert response.status_code==409,response.text
    after=client.get(url,headers=auth_headers).json()
    assert (after['gpu_model'],after['tensorrt_version'],after['description'])==('T4','10.14.1','')
