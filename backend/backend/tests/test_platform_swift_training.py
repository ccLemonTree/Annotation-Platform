"""Platform training snapshots; isolated database/storage, no GPU or SSH calls."""
import json
import zipfile
from io import BytesIO
from uuid import UUID

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from tests.test_swift_export_api import swift_project
from tests.test_training_runs import spec, training_tables


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,format,source',[
    ('wemm','infonce','matches'),('wemm','contrastive','matches'),
    ('wemm','online_contrastive','matches'),('wemm','cosine_similarity','matches'),
    ('swift_sft','swift_sft','descriptions'),
])
async def test_freeze_create_edit_and_unpack_platform_training(client,auth_headers,swift_project,test_db,tmp_path,kind,format,source):
    from models import ExportSnapshot
    from services.storage import get_storage
    from services.training_config import build_script
    from services.training_executor import unpack_snapshot
    project,_=swift_project
    response=client.post(f'/api/export/projects/{project.id}/annotation-snapshots',headers=auth_headers,
        json={'dataset_format':format,'sample_source':source})
    assert response.status_code==201,response.text
    frozen=response.json()
    assert frozen['format']==format and frozen['manifest']['training']['samples']>0
    params={'loss_type':format} if kind=='wemm' else {}
    values=spec(kind,snapshot_id=frozen['id'],dataset_path='',parameters=params).model_dump(mode='json')
    response=client.post('/api/training/runs',headers=auth_headers,json=values)
    assert response.status_code==201,response.text
    run=response.json()
    assert run['status']=='draft' and run['source']['id']==frozen['id']
    assert run['config']['runtime_dataset']=='/output/dataset/train.jsonl'
    assert '--dataset' in build_script(run['config']) and '/output/dataset/train.jsonl' in build_script(run['config'])
    values['name']='修改后训练'
    response=client.put('/api/training/runs/'+run['id'],headers=auth_headers,json=values)
    assert response.status_code==200 and response.json()['name']=='修改后训练'
    row=await test_db.scalar(select(ExportSnapshot).where(ExportSnapshot.id==UUID(frozen['id'])))
    with await get_storage().get_file(row.blob_path) as stream:raw=stream.read()
    archive=tmp_path/'input.zip';archive.write_bytes(raw)
    target=tmp_path/'dataset';unpack_snapshot(archive,target,kind)
    samples=[json.loads(line) for line in (target/'train.jsonl').read_text(encoding='utf-8').splitlines()]
    for sample in samples:
        for key in ('images','positive_images','negative_images'):
            groups=[sample.get(key,[])] if key=='images' else sample.get(key,[])
            for group in groups:
                for path in group:
                    assert path.startswith('/output/dataset/')
                    assert (target/path.removeprefix('/output/dataset/')).is_file()
    assert archive.read_bytes()==raw
    with zipfile.ZipFile(BytesIO(raw)) as package:
        assert '"images/' in package.read('train.jsonl').decode()
    await test_db.rollback()
    assert client.delete('/api/training/runs/'+run['id'],headers=auth_headers).status_code==204


@pytest.mark.asyncio
async def test_platform_snapshot_tenant_and_format_boundaries(client,auth_headers,swift_project,test_db):
    from models import Tenant,Project
    from services.training_config import validate_snapshot
    project,_=swift_project
    response=client.post(f'/api/export/projects/{project.id}/annotation-snapshots',headers=auth_headers,
        json={'dataset_format':'infonce','sample_source':'matches'})
    assert response.status_code==201,response.text
    frozen=response.json()
    for kind,params in [('yolo26',{}),('swift_sft',{}),('wemm',{'loss_type':'contrastive'})]:
        values=spec(kind,dataset_path='',snapshot_id=frozen['id'],parameters=params).model_dump(mode='json')
        response=client.post('/api/training/runs',headers=auth_headers,json=values)
        assert response.status_code==400,response.text
    manifest=frozen['manifest'];manifest['training']['samples']=0
    with pytest.raises(HTTPException):validate_snapshot('wemm','infonce',manifest,{'task_type':'embedding','loss_type':'infonce'})
    tenant=Tenant(id=2,tenant_name='other');test_db.add(tenant);await test_db.flush()
    other=Project(tenant_id=2,project_name='other',task_type='vector');test_db.add(other);await test_db.commit()
    assert client.post(f'/api/export/projects/{other.id}/annotation-snapshots',headers=auth_headers,
        json={'dataset_format':'infonce','sample_source':'matches'}).status_code==404
    assert client.post(f'/api/export/projects/{project.id}/annotation-snapshots',json={'dataset_format':'infonce'}).status_code==401
    values=spec('wemm',dataset_path='',snapshot_id=frozen['id']).model_dump(mode='json')
    project.tenant_id=2;await test_db.commit()
    assert client.post('/api/training/runs',headers=auth_headers,json=values).status_code==404


@pytest.mark.asyncio
async def test_conflicting_match_history_cannot_be_frozen(client,auth_headers,swift_project,test_db):
    from models.project_work import MatchQuery
    from models import ExportSnapshot
    project,crops=swift_project
    test_db.add(MatchQuery(project_id=project.id,query={'text':'黑衣服的人'},space={'revision':2},hits=[
        {'asset_id':str(crops[0].id),'content_hash':crops[0].content_hash,'score':.1,'judgment':'incorrect','review_status':'approved'}]))
    await test_db.commit()
    for path,method,values in [
        (f'/api/export/projects/{project.id}/annotation-snapshots','post',{'json':{'dataset_format':'infonce','sample_source':'matches'}}),
        (f'/api/export/projects/{project.id}/export','get',{'params':{'format':'annotation','dataset_format':'infonce','sample_source':'matches'}}),
    ]:
        response=getattr(client,method)(path,headers=auth_headers,**values)
        assert response.status_code==400,response.text
        assert '冲突' in response.json()['detail']
    assert not (await test_db.scalars(select(ExportSnapshot))).all()


@pytest.mark.parametrize('fault',['path','missing','hash'])
def test_swift_unpack_refuses_unsafe_or_changed_images(tmp_path,fault):
    import hashlib
    from services.training_executor import unpack_snapshot
    raw=b'picture';path='../outside.png' if fault=='path' else 'images/a.png'
    manifest={'training':{'format':'infonce','samples':1},'assets':[
        {'image':'images/a.png','content_hash':'wrong' if fault=='hash' else hashlib.sha256(raw).hexdigest()}]}
    row={'messages':[{'role':'user','content':'text'}],
        'positive_messages':[[{'role':'user','content':'<image>'}]],'positive_images':[[path]]}
    archive=tmp_path/'input.zip'
    with zipfile.ZipFile(archive,'w') as package:
        package.writestr('manifest.json',json.dumps(manifest));package.writestr('train.jsonl',json.dumps(row)+'\n')
        if fault!='missing':package.writestr('images/a.png',raw)
    with pytest.raises((ValueError,FileNotFoundError)):unpack_snapshot(archive,tmp_path/'dataset','wemm')
