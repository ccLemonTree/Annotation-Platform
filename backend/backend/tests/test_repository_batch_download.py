"""Batch downloads are authorized, verified ZIPs built only from isolated artifacts."""
import io
import json
from uuid import UUID,uuid4
import zipfile

import pytest

from tests.test_model_repository import repository_tables,send
from tests.test_repository_python_groups import upload_python

URL='/api/model-repository/download-batch'


def unpack(response):
    assert response.status_code==200,response.text[:500]
    assert response.headers['content-type'].startswith('application/zip')
    return zipfile.ZipFile(io.BytesIO(response.content))


def test_selected_versions_keep_same_names_in_distinct_directories(client,auth_headers,tmp_path):
    first=send(client,auth_headers,name='first',files=[('files',('best.pt',b'first'))]).json()
    second=send(client,auth_headers,name='second',files=[('files',('best.pt',b'second'))]).json()
    with unpack(client.post(URL,headers=auth_headers,json={'ids':[first['id'],second['id']]})) as archive:
        manifest=json.loads(archive.read('manifest.json'))
        assert [m['id'] for m in manifest['models']]==[first['id'],second['id']]
        for row,content in [(first,b'first'),(second,b'second')]:
            item=next(m for m in manifest['models'] if m['id']==row['id'])
            assert archive.read(item['directory']+'/best.pt')==content
            assert item['files']==row['files'] and item['name']==row['name']
            assert item['entry_file']==row['entry_file'] and item['file_size']==row['file_size']
    assert not list((tmp_path/'models').rglob('download-*'))


def test_python_directory_and_empty_auxiliary_files_survive_batch(client,auth_headers):
    row=upload_python(client,auth_headers).json()
    with unpack(client.post(URL,headers=auth_headers,json={'ids':[row['id']]})) as archive:
        manifest=json.loads(archive.read('manifest.json'));item=manifest['models'][0]
        assert item['group_name']=='sam3' and item['format']=='python'
        assert archive.read(item['directory']+'/sam3_pipeline/1/__init__.py')==b''
        assert b'uploaded code must not run' in archive.read(item['directory']+'/sam3_pipeline/1/model.py')


def test_filtered_download_includes_matching_versions_beyond_first_page(client,auth_headers):
    ids=[]
    for n in range(23):
        response=send(client,auth_headers,name='component-'+str(n),group_name='family')
        assert response.status_code==201,response.text
        ids.append(response.json()['id'])
    assert send(client,auth_headers,name='other',group_name='other').status_code==201
    response=client.post(URL,headers=auth_headers,json={'filters':{'group_name':'family','format':'pt'}})
    with unpack(response) as archive:
        assert {m['id'] for m in json.loads(archive.read('manifest.json'))['models']}==set(ids)


def test_download_filters_combine_keyword_format_and_gpu(client,auth_headers):
    first=send(client,auth_headers,'tensorrt',name='encoder',group_name='sam3',gpu_model='Tesla T4').json()
    assert send(client,auth_headers,'tensorrt',name='encoder',group_name='sam3',gpu_model='A100').status_code==201
    assert send(client,auth_headers,name='encoder',group_name='sam3').status_code==201
    with unpack(client.post(URL,headers=auth_headers,json={'filters':{'q':'sam3','format':'tensorrt','gpu_model':'Tesla T4'}})) as archive:
        assert [m['id'] for m in json.loads(archive.read('manifest.json'))['models']]==[first['id']]


@pytest.mark.asyncio
async def test_filter_skips_unready_and_explicit_selection_rejects_it(client,auth_headers,test_db):
    from models.model_repository import ModelRepositoryItem
    first=send(client,auth_headers,name='ready').json();second=send(client,auth_headers,name='failed').json()
    record=await test_db.get(ModelRepositoryItem,UUID(second['id']));record.status='failed';await test_db.commit()
    with unpack(client.post(URL,headers=auth_headers,json={'filters':{}})) as archive:
        assert [m['id'] for m in json.loads(archive.read('manifest.json'))['models']]==[first['id']]
    assert client.post(URL,headers=auth_headers,json={'ids':[first['id'],second['id']]}).status_code==409


@pytest.mark.asyncio
async def test_batch_download_is_tenant_scoped_and_available_to_readers(client,auth_headers,test_db,test_user):
    from models import Tenant
    own=send(client,auth_headers).json()
    test_db.add(Tenant(id=2,tenant_name='other'));test_user.tenant_id=2;await test_db.commit()
    other=send(client,auth_headers).json();test_user.tenant_id=1;test_user.role='annotator';await test_db.commit()
    assert client.post(URL,headers=auth_headers,json={'ids':[own['id']]}).status_code==403
    test_user.role='admin';await test_db.commit()
    assert client.post(URL,headers=auth_headers,json={'ids':[own['id'],other['id']]}).status_code==404
    with unpack(client.post(URL,headers=auth_headers,json={'filters':{}})) as archive:
        assert [m['id'] for m in json.loads(archive.read('manifest.json'))['models']]==[own['id']]
    assert client.post(URL,json={'ids':[own['id']]}).status_code==401


@pytest.mark.parametrize('payload',[{}, {'ids':[]},{'ids':['bad']},{'ids':[str(uuid4())]*101},
    {'ids':[str(uuid4())],'filters':{}},{'filters':{'status':'all'}},{'filters':{'format':'bad'}}])
def test_invalid_batch_requests_are_rejected(client,auth_headers,payload):
    assert client.post(URL,headers=auth_headers,json=payload).status_code==422


def test_duplicate_ids_and_no_matches_are_explicit(client,auth_headers):
    row=send(client,auth_headers).json()
    assert client.post(URL,headers=auth_headers,json={'ids':[row['id'],row['id']]}).status_code==422
    assert client.post(URL,headers=auth_headers,json={'ids':[str(uuid4())]}).status_code==404
    assert client.post(URL,headers=auth_headers,json={'filters':{'group_name':'missing'}}).status_code==404


def test_corruption_aborts_entire_archive_and_cleans_temporary_files(client,auth_headers,tmp_path):
    first=send(client,auth_headers,name='first').json();second=send(client,auth_headers,name='second').json()
    (tmp_path/'models/repository/1'/second['id']/'files/best.pt').write_bytes(b'CORRUPT')
    assert client.post(URL,headers=auth_headers,json={'ids':[first['id'],second['id']]}).status_code==409
    assert not list((tmp_path/'models').rglob('download-*'))


@pytest.mark.asyncio
async def test_filter_limit_requires_narrower_filters(client,auth_headers,test_db):
    from models.model_repository import ModelRepositoryItem
    row=send(client,auth_headers).json()
    original=await test_db.get(ModelRepositoryItem,UUID(row['id']))
    for n in range(100):
        test_db.add(ModelRepositoryItem(tenant_id=1,user_id=1,name='extra-'+str(n),version='v1',format='pt',
            entry_file='best.pt',files=original.files,file_size=7,source={'kind':'upload'},status='ready'))
    await test_db.commit()
    response=client.post(URL,headers=auth_headers,json={'filters':{}})
    assert response.status_code==422,response.text[:500]


def test_disk_space_failure_creates_no_archive(client,auth_headers,monkeypatch,tmp_path):
    from fastapi import HTTPException
    from services import model_repository as service
    row=send(client,auth_headers).json()
    def unavailable(*args):raise HTTPException(409,'模型仓库磁盘空间不足')
    monkeypatch.setattr(service,'check_space',unavailable)
    assert client.post(URL,headers=auth_headers,json={'ids':[row['id']]}).status_code==409
    assert not list((tmp_path/'models').rglob('download-*'))


@pytest.mark.asyncio
async def test_cancelled_pack_waits_for_writer_and_cleans_temp(client,auth_headers,test_db,monkeypatch,tmp_path):
    import asyncio,threading
    from services import model_repository as service
    from schemas.model_repository import RepositoryBatchDownload
    row=send(client,auth_headers).json();started=threading.Event();resume=threading.Event();original=zipfile.ZipFile
    def delayed(*args,**kwargs):
        started.set();assert resume.wait(10);return original(*args,**kwargs)
    monkeypatch.setattr(service.zipfile,'ZipFile',delayed)
    task=asyncio.create_task(service.download_batch(test_db,1,RepositoryBatchDownload(ids=[row['id']])))
    try:
        assert await asyncio.to_thread(started.wait,10)
        task.cancel();resume.set()
        with pytest.raises(asyncio.CancelledError):await task
    finally:resume.set()
    assert not list((tmp_path/'models').rglob('download-*'))
