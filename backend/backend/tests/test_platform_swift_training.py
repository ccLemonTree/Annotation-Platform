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
async def test_automatic_infonce_spreads_negatives_across_workflow_template_categories(
        client,auth_headers,swift_project,test_db,test_engine,monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from models import Annotation,Asset,ProjectWorkJob
    from services import embedding_recommendation_jobs,project_work

    project,_=swift_project
    project.task_type='caption'
    monkeypatch.setattr(embedding_recommendation_jobs,'BATCH_SIZE',2)
    field={'key':'load','name':'载荷','instruction':'','options':[]}
    templates=[{'id':'lift','name':'叉车','config':{'target_label':['forklift','fork_lift'],'fields':[field]}},
        {'id':'crane','name':'吊车','config':{'target_label':['crane'],'fields':[field]}}]
    workflow_id=UUID('aa000000-0000-4000-8000-000000000100')
    test_db.add(ProjectWorkJob(id=workflow_id,project_id=project.id,user_id=1,operation='attributes',model={},
        options={'workflow_id':str(workflow_id),'workflow_step':0,'attribute_templates':templates}))
    for index,target in enumerate(('forklift','fork_lift','forklift','fork_lift','crane','crane','crane','crane'),1):
        asset=Asset(id=UUID(f'aa000000-0000-4000-8000-{index:012x}'),project_id=project.id,source_type='model_crop',original_name=f'{index}.jpg',
            blob_path=f'{index}.jpg',content_hash=str(index),file_size=1)
        test_db.add(asset)
        await test_db.flush()
        test_db.add(Annotation(asset_id=asset.id,version=1,is_latest=True,review_status='pending',
            annotation_data={'attribute_schema':{'target_label':target,'fields':[field]},
                'attributes':{'load':{'value':'有货','reason':''}}}))
    await test_db.commit()
    base=f'/api/export/projects/{project.id}'
    url=base+'/embedding-negative-recommendations/jobs'
    options={'source':'attribute_descriptions','automatic':True,'image_kind':'crops','approved_only':False}
    assert client.post(url,headers=auth_headers,json=options).status_code==202
    sessions=async_sessionmaker(test_engine,expire_on_commit=False)
    for _ in range(4):
        assert await project_work.process_one(sessions,None)
    ready=client.post(url+'/status',headers=auth_headers,json=options).json()
    assert ready['state']=='ready' and ready['recommended']==8 and ready['candidates']==24
    by_anchor={UUID(row['asset_id']).int&0xffff:{UUID(candidate['asset_id']).int&0xffff for candidate in row['candidates']}
        for row in ready['sample']}
    assert all(len(ids)==3 and all((anchor<=4)!=(id<=4) for id in ids) for anchor,ids in by_anchor.items())
    assert len(set.union(*by_anchor.values()))==8
    assert by_anchor[1]!=by_anchor[2]


@pytest.mark.asyncio
@pytest.mark.parametrize('include_pending',[False,True])
async def test_automatic_infonce_export_uses_attributes_in_review_scope_without_manual_confirmation(
        client,auth_headers,swift_project,test_db,test_engine,include_pending):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from models import Annotation
    from services import project_work

    project,crops=swift_project
    project.task_type='caption'
    schema={'target_label':['car'],'fields':[{'key':'body_color','name':'车身颜色','instruction':'','options':[]}]}
    for asset,color in zip(crops,['白色','白色','红色','黑色']):
        row=await test_db.scalar(select(Annotation).where(Annotation.asset_id==asset.id))
        if row is None:
            row=Annotation(asset_id=asset.id,version=1,is_latest=True)
            test_db.add(row)
        row.review_status='rejected' if color=='黑色' else 'pending' if color=='红色' and include_pending else 'approved'
        row.annotation_data={'attribute_schema':schema,'attributes':{'body_color':{'value':color,'reason':''}}}
    await test_db.commit()
    base=f'/api/export/projects/{project.id}'
    jobs=base+'/embedding-negative-recommendations/jobs'
    options={'source':'attribute_descriptions','image_kind':'crops','automatic':True,'approved_only':not include_pending}
    started=client.post(jobs,headers=auth_headers,json=options)
    assert started.status_code==202,started.text
    export={'dataset_format':'infonce','sample_source':options['source'],'image_kind':'crops','approved_only':not include_pending,
        'automatic_negative_job_id':started.json()['id']}
    preview=base+'/annotation-snapshots/preview'
    assert client.post(preview,headers=auth_headers,json=export).status_code==409
    assert await project_work.process_one(async_sessionmaker(test_engine,expire_on_commit=False),None)
    ready=client.post(jobs+'/status',headers=auth_headers,json=options).json()
    assert ready['state']=='ready' and ready['scanned']==ready['recommended']==3
    planned=client.post(preview,headers=auth_headers,json=export)
    assert planned.status_code==200 and planned.json()['sample_count']==3,planned.text
    zip_response=client.get(base+'/export',headers=auth_headers,params={'format':'annotation',**export})
    assert zip_response.status_code==200,zip_response.text
    with zipfile.ZipFile(BytesIO(zip_response.content)) as archive:
        rows=[json.loads(line) for line in archive.read('train.jsonl').splitlines()]
        manifest=json.loads(archive.read('manifest.json'))
    assert manifest['training']['negative_origin']=='automatic'
    assert {row['asset_id'] for row in manifest['assets']}=={str(asset.id) for asset in crops[:3]}
    assert all(row['negative_images'] for row in rows)
    assert [len(row['negative_images']) for row in rows if '白色' in row['messages'][0]['content']]==[1,1]
    manual={key:value for key,value in export.items() if key!='automatic_negative_job_id'}
    assert client.post(preview,headers=auth_headers,json={**manual,'explicit_negatives':True}).json()['sample_count']==0
    frozen=client.post(base+'/annotation-snapshots',headers=auth_headers,json={**export,'preview_token':planned.json()['preview_token']})
    assert frozen.status_code==201,frozen.text
    changed=await test_db.scalar(select(Annotation).where(Annotation.asset_id==crops[2].id))
    changed.annotation_data={'attribute_schema':schema,'attributes':{'body_color':{'value':'白色','reason':''}}}
    await test_db.commit()
    assert client.post(preview,headers=auth_headers,json=export).json()['sample_count']==0


@pytest.mark.asyncio
async def test_recommendation_job_scans_all_batches_then_confirms(client,auth_headers,swift_project,test_db,test_engine,monkeypatch):
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from models import Annotation,ProjectWorkItem
    from services import embedding_recommendation_jobs,project_work
    monkeypatch.setattr(embedding_recommendation_jobs,'BATCH_SIZE',2)
    sessions=async_sessionmaker(test_engine,expire_on_commit=False)
    project,crops=swift_project
    project.task_type='caption'
    for index,(asset,color) in enumerate(zip(crops,['白色','白色','红色','黑色'])):
        row=await test_db.scalar(select(Annotation).where(Annotation.asset_id==asset.id))
        if row is None:
            row=Annotation(asset_id=asset.id,version=1,is_latest=True,review_status='approved')
            test_db.add(row)
        row.annotation_data={'description':f'{color}轿车，编号{index}'}
    await test_db.commit()
    base=f'/api/export/projects/{project.id}'
    url=base+'/embedding-negative-recommendations/jobs'
    options={'source':'descriptions','image_kind':'crops'}
    job=client.post(url,headers=auth_headers,json=options)
    assert job.status_code==202,job.text
    job_id=job.json()['id']
    assert client.post(url,headers=auth_headers,json=options).json()['id']==job_id
    async def step():
        assert await project_work.process_one(sessions,None)
    def state():
        return client.post(url+'/status',headers=auth_headers,json=options).json()
    await step()
    assert state()['scanned']==2 and state()['state']=='pending'
    async with sessions() as db:
        checkpoint=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==UUID(job_id),ProjectWorkItem.content_hash=='checkpoint'))
        checkpoint.status='running'
        await db.commit()
    await project_work.recover_running(sessions)
    await step()
    ready=state()
    assert ready['state']=='ready' and ready['scanned']==ready['total']==ready['recommended']==4
    excluded=ready['sample'][0]
    result=client.post(f'{url}/{job_id}/confirm',headers=auth_headers,json={'excluded':[
        {'asset_id':excluded['asset_id'],'negative_id':excluded['candidates'][0]['asset_id']}]})
    assert result.status_code==202,result.text
    preview=base+'/annotation-snapshots/preview'
    freeze={'dataset_format':'infonce','sample_source':'descriptions','explicit_negatives':True,'image_kind':'crops'}
    assert client.post(preview,headers=auth_headers,json=freeze).status_code==409
    await step()
    await step()
    confirmed=state()
    assert confirmed['state']=='confirmed' and confirmed['confirmed']==ready['candidates']-1
    assert client.post(preview,headers=auth_headers,json=freeze).json()['sample_count']==4


@pytest.mark.asyncio
@pytest.mark.parametrize('source',['descriptions','attributes'])
async def test_recommend_only_clear_mismatches_and_batch_confirm(client,auth_headers,swift_project,test_db,source):
    from models import Annotation
    project,crops=swift_project
    project.task_type='caption'
    schema={'target_label':['car'],'fields':[{'key':'body_color','name':'车身颜色','instruction':'','options':[]}]}
    for index,(asset,color) in enumerate(zip(crops,['白色','白色','红色','黑色'])):
        row=await test_db.scalar(select(Annotation).where(Annotation.asset_id==asset.id))
        if row is None:
            row=Annotation(asset_id=asset.id,version=1,is_latest=True)
            test_db.add(row)
        row.review_status='pending' if index==3 else 'approved'
        row.annotation_data={'description':f'图中有一辆{color}轿车，编号{index}',
            'attribute_schema':schema,'attributes':{'body_color':{'value':color,'reason':''}}}
    await test_db.commit()
    base=f'/api/export/projects/{project.id}'
    params={'source':source,'approved_only':source=='descriptions','image_kind':'crops'}
    url=base+'/embedding-negative-recommendations'
    response=client.post(url,headers=auth_headers,json=params)
    assert response.status_code==200,response.text
    items=response.json()['items']
    first=next(row for row in items if row['asset_id']==str(crops[0].id))
    assert first['candidates']==[{'asset_id':str(crops[2].id),'name':'white.png','reason':'车身颜色不同（白色 / 红色）'}]
    assert str(crops[3].id) not in {row['asset_id'] for row in items}
    options={'dataset_format':'infonce','sample_source':source,'explicit_negatives':True}
    preview=base+'/annotation-snapshots/preview'
    assert client.post(preview,headers=auth_headers,json=options).json()['sample_count']==0
    def confirm(rows):
        return client.post(url+'/confirm',headers=auth_headers,json={**params,'items':rows})
    selected=[{key:row[key] for key in ('asset_id','text','version')}|{'asset_ids':[hit['asset_id'] for hit in row['candidates']]}
        for row in items]
    assert confirm([{**selected[0],'text':'已变更'}]).status_code==409
    assert confirm([{**selected[0],'asset_ids':[selected[0]['asset_id']]}]).status_code==409
    result=confirm(selected)
    assert result.status_code==200 and result.json()['confirmed_count']==sum(len(row['asset_ids']) for row in selected),result.text
    assert client.post(preview,headers=auth_headers,json=options).json()['sample_count']==len(selected)


@pytest.mark.asyncio
async def test_caption_infonce_manual_negatives_freeze_and_train(client,auth_headers,swift_project,test_db):
    from models import Annotation,ExportSnapshot
    from services.storage import get_storage
    project,crops=swift_project
    project.task_type='caption'
    test_db.add(Annotation(asset_id=crops[1].id,version=1,is_latest=True,review_status='approved',
        annotation_data={'description':'穿黑衣服的人'}))
    await test_db.commit()
    base=f'/api/export/projects/{project.id}'
    choice=f'{base}/embedding-negatives/{crops[0].id}?source=descriptions'
    request=client.get(choice,headers=auth_headers).json()
    assert request['text']=='穿黑衣服的人'
    rejected=client.put(choice,headers=auth_headers,json={**request,'asset_ids':[str(crops[1].id)]})
    assert rejected.status_code==422
    saved=client.put(choice,headers=auth_headers,json={**request,'asset_ids':[str(crops[2].id),str(crops[3].id)]})
    assert saved.status_code==200,saved.text
    assert client.put(choice,headers=auth_headers,json=request).status_code==409
    options={'dataset_format':'infonce','sample_source':'descriptions','explicit_negatives':True}
    preview=client.post(base+'/annotation-snapshots/preview',headers=auth_headers,json=options).json()
    assert preview['sample_count']==1 and preview['negative_image_count']==2 and preview['asset_count']==3
    freeze=client.post(base+'/annotation-snapshots',headers=auth_headers,json={**options,'preview_token':preview['preview_token']})
    assert freeze.status_code==201,freeze.text
    frozen=freeze.json()
    assert frozen['manifest']['training']['negative_strategy']=='explicit'
    listing=client.get(base+'/snapshots',params={'training_only':True},headers=auth_headers)
    assert listing.status_code==200,listing.text
    assert listing.json()==[{**frozen,'manifest':{'training':frozen['manifest']['training']}}]
    snapshot=await test_db.get(ExportSnapshot,UUID(frozen['id']))
    from services.snapshot_manifests import read_manifest
    assert snapshot.manifest is None and snapshot.manifest_path
    assert await read_manifest(snapshot.manifest_path)==frozen['manifest']
    assert snapshot.list_metadata==dict(training=frozen['manifest']['training'],
        version_label=None,asset_count=len(frozen['manifest']['assets']))
    with await get_storage().get_file(snapshot.blob_path) as stream, zipfile.ZipFile(stream) as package:
        sample=json.loads(package.read('train.jsonl'))
        assert sample['negative_messages']==[[{'role':'user','content':'<image>'}]]*2
        assert sample['negative_images']==[[f'images/{asset.id}.png'] for asset in crops[2:4]]
        assert all(package.read(paths[0]) for paths in sample['negative_images'])
    await test_db.rollback()
    values=spec('wemm',snapshot_id=frozen['id'],dataset_path='',parameters={'loss_type':'infonce'}).model_dump(mode='json')
    result=client.post('/api/training/runs',headers=auth_headers,json=values)
    assert result.status_code==201,result.text
    assert result.json()['config']['environment']['INFONCE_USE_BATCH']=='false'
    assert 'INFONCE_HARD_NEGATIVES' not in result.json()['config']['environment']


@pytest.mark.asyncio
@pytest.mark.parametrize('format',['infonce','contrastive','online_contrastive','cosine_similarity'])
@pytest.mark.parametrize('source',['descriptions','attributes'])
async def test_caption_pairs_preview_freeze_and_create_training(client,auth_headers,swift_project,test_db,format,source):
    from models import Annotation,ExportSnapshot
    from services.k8s_training_recipes import build_assets
    from services.storage import get_storage

    project,crops=swift_project
    project.task_type='caption'
    schema={'target_label':['person'],'fields':[
        {'key':'top','name':'上衣颜色','instruction':'','options':[]},
        {'key':'hat','name':'帽子','instruction':'','options':[]}]}
    for index,asset in enumerate(crops[:3]):
        row=await test_db.scalar(select(Annotation).where(Annotation.asset_id==asset.id))
        if row is None:
            row=Annotation(asset_id=asset.id,version=1,is_latest=True,review_status='approved')
            test_db.add(row)
        color=['黑色','蓝色',None][index]
        row.annotation_data={'description':f'{color}上衣' if color else '   ',
            'attribute_schema':schema,'attributes':{'top':{'value':color,'reason':''},
            'hat':{'value':None,'reason':'看不清'}}}
    await test_db.commit()

    url=f'/api/export/projects/{project.id}/annotation-snapshots'
    options={'dataset_format':format,'sample_source':source}
    preview=client.post(url+'/preview',headers=auth_headers,json=options)
    assert preview.status_code==200 and preview.json()['asset_count']==2
    response=client.post(url,headers=auth_headers,json={**options,'preview_token':preview.json()['preview_token']})
    assert response.status_code==201,response.text
    frozen=response.json()
    assert frozen['manifest']['training']['samples']==2
    snapshot=await test_db.get(ExportSnapshot,UUID(frozen['id']))
    with await get_storage().get_file(snapshot.blob_path) as stream, zipfile.ZipFile(stream) as package:
        samples=[json.loads(line) for line in package.read('train.jsonl').decode().splitlines()]
        expected={'黑色上衣','蓝色上衣'} if source=='descriptions' else {'person；上衣颜色：黑色','person；上衣颜色：蓝色'}
        assert {sample['messages'][0]['content'] for sample in samples}==expected
        for sample in samples:
            assert sample['positive_messages']==[[{'role':'user','content':'<image>'}]]
            assert package.read(sample['positive_images'][0][0])
            assert 'negative_messages' not in sample
            assert 'label' not in sample if format=='infonce' else sample['label']==1
    await test_db.rollback()
    values=spec('wemm',snapshot_id=frozen['id'],dataset_path='',parameters={'loss_type':format}).model_dump(mode='json')
    run=client.post('/api/training/runs',headers=auth_headers,json=values)
    assert run.status_code==201,run.text
    config=run.json()['config']
    if format=='infonce':
        assert config['environment']['INFONCE_USE_BATCH']=='true'
        assert 'INFONCE_HARD_NEGATIVES' not in config['environment']
        assert config['parameters']['per_device_train_batch_size']>=2
        distributed=json.loads(build_assets(config)['train.yaml'])
        assert 'INFONCE_HARD_NEGATIVES' not in distributed['ENV']


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
