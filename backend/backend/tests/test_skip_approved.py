"""Reviewed unknown images stay skipped and never become training negatives."""
import hashlib
import json
import zipfile
from io import BytesIO
from types import SimpleNamespace
from uuid import UUID

import pytest
import pytest_asyncio
from PIL import Image
from sqlalchemy import select

from models import Annotation, AnnotationEvent, Asset, ImageCollection, ImageCollectionMember, LibraryImage, Project, ProjectMember, User
from models.project_work import MatchQuery
from services.auth import create_access_token
from services.storage import get_storage


SKIPPED={'labels':[],'description':'','skipped':True}
SCHEMA={'labels':['car'],'object_attributes':{'target_label':'car','fields':[
    {'key':'color','name':'颜色','instruction':'仅判断可见颜色','options':['黑','白']} ]}}
VALID={'labels':['car'],'description':'清晰可见的黑色车辆','attribute_schema':SCHEMA['object_attributes'],
    'attributes':{'color':{'value':'黑','reason':'车身清晰可见'}}}


@pytest_asyncio.fixture
async def skip_env(client,test_db,test_engine,test_project,test_asset,test_user,auth_headers):
    async with test_engine.begin() as conn:await conn.run_sync(lambda c:MatchQuery.__table__.create(c,checkfirst=True))
    test_project.task_type='caption';test_project.label_schema=SCHEMA
    member=User(id=2,tenant_id=test_user.tenant_id,username='assigned',email='assigned@test.invalid',password_hash='unused',role='user',is_active=True)
    test_db.add(member);await test_db.flush()
    test_db.add(ProjectMember(project_id=test_project.id,user_id=member.id));await test_db.commit()
    return SimpleNamespace(client=client,db=test_db,project=test_project,asset=test_asset,
        admin=auth_headers,member={'Authorization':'Bearer '+create_access_token({'user_id':member.id})})


def expected_skipped(task_type: str):
    return {'labels':[],'skipped':True} if task_type=='classification' else {'description':'','skipped':True}


def save_and_approve_skip(s):
    saved=s.client.post('/api/annotations',headers=s.member,json={'asset_id':str(s.asset.id),'base_version':0,'annotation_data':SKIPPED})
    assert saved.status_code==201,saved.text
    response=s.client.post('/api/annotations/'+saved.json()['id']+'/review',headers=s.member,json={'approved':True})
    assert response.status_code==200,response.text
    assert response.json()['annotation_data']==expected_skipped(s.project.task_type) and response.json()['review_status']=='approved'
    assert response.json()['reviewer_id']==2
    return response.json()


async def valid_asset(s,color='black'):
    raw=BytesIO();Image.new('RGB',(64,48),color).save(raw,'PNG');content=raw.getvalue()
    digest=hashlib.sha256(content).hexdigest();path=f'fixture/{digest}.png'
    await get_storage().save_file(BytesIO(content),path)
    asset=Asset(project_id=s.project.id,original_name=color+'.png',blob_path=path,content_hash=digest,
        file_size=len(content),width=64,height=48,format='PNG',source_type='upload',annotation_status='reviewed')
    s.db.add(asset);await s.db.flush()
    s.db.add(Annotation(asset_id=asset.id,version=1,is_latest=True,annotation_source='manual',annotator_id=1,reviewer_id=1,
        review_status='approved',annotation_data=VALID))
    return asset


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['classification','caption','vector'])
async def test_assigned_member_can_approve_whole_image_skip_with_audit_and_counts(skip_env,kind):
    s=skip_env;s.project.task_type=kind;await s.db.commit()
    saved=save_and_approve_skip(s)
    await s.db.refresh(s.asset);await s.db.refresh(s.project)
    assert s.asset.annotation_status=='reviewed'
    assert s.project.annotated_count==1 and s.project.reviewed_count==1
    latest=await s.db.get(Annotation,UUID(saved['id']))
    assert latest.annotation_data==expected_skipped(kind) and latest.version==1 and latest.is_latest
    approved=(await s.db.scalars(select(AnnotationEvent).where(AnnotationEvent.asset_id==s.asset.id,AnnotationEvent.action=='approve'))).all()
    assert len(approved)==1 and approved[0].user_id==2
    assert approved[0].detail['annotation_id']==saved['id'] and approved[0].detail['source']=='manual'


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['classification','caption','vector'])
async def test_approved_skips_are_excluded_from_training_exports_and_freezes(skip_env,kind):
    s=skip_env;s.project.task_type=kind;good=await valid_asset(s);await s.db.commit()
    save_and_approve_skip(s)
    path=f'/api/export/projects/{s.project.id}'
    exported=s.client.get(path+'/export?format=annotation',headers=s.admin)
    assert exported.status_code==200,exported.text
    with zipfile.ZipFile(BytesIO(exported.content)) as archive:
        rows=[json.loads(line) for line in archive.read('annotations.jsonl').decode().splitlines()]
        assert [row['asset_id'] for row in rows]==[str(good.id)]
        assert all(str(s.asset.id) not in name for name in archive.namelist())
    frozen=s.client.post(path+'/annotation-snapshots',headers=s.admin,json={'dataset_format':'swift_sft'})
    assert frozen.status_code==201,frozen.text
    assert [row['asset_id'] for row in frozen.json()['manifest']['assets']]==[str(good.id)]


@pytest.mark.asyncio
async def test_approved_skip_stays_private_but_historical_references_and_valid_reuse_survive(skip_env):
    s=skip_env
    skipped_image=LibraryImage(tenant_id=1,original_name='unknown.png',blob_path=s.asset.blob_path,content_hash=s.asset.content_hash,
        width=64,height=48,file_size=10,source_type='upload')
    s.db.add(skipped_image);await s.db.flush();s.asset.image_id=skipped_image.id
    group=ImageCollection(tenant_id=1,name='only skipped');s.db.add(group);await s.db.flush()
    s.db.add(ImageCollectionMember(collection_id=group.id,image_id=skipped_image.id))
    old=Project(tenant_id=1,project_name='historical',task_type='caption',label_schema=SCHEMA)
    target=Project(tenant_id=1,project_name='new target',task_type='caption',label_schema=SCHEMA)
    s.db.add_all([old,target]);await s.db.flush()
    reference=Asset(project_id=old.id,image_id=skipped_image.id,original_name='historical.png',blob_path=s.asset.blob_path,
        content_hash=s.asset.content_hash,file_size=10,width=64,height=48,source_type='upload')
    s.db.add(reference);await s.db.flush()
    history=Annotation(asset_id=reference.id,version=1,is_latest=True,review_status='pending',annotation_data={'description':'保留的历史描述'})
    s.db.add(history)
    good=await valid_asset(s)
    good_image=LibraryImage(tenant_id=1,original_name=good.original_name,blob_path=good.blob_path,content_hash=good.content_hash,
        width=64,height=48,file_size=good.file_size,format='PNG',source_type='upload')
    s.db.add(good_image);await s.db.flush();good.image_id=good_image.id
    mixed=ImageCollection(tenant_id=1,name='approved and skipped')
    duplicate=Asset(project_id=old.id,image_id=good_image.id,original_name=good.original_name,blob_path=good.blob_path,
        content_hash=good.content_hash,file_size=good.file_size,width=64,height=48,source_type='upload')
    s.db.add_all([mixed,duplicate]);await s.db.flush()
    s.db.add_all([ImageCollectionMember(collection_id=mixed.id,image_id=id) for id in (good_image.id,skipped_image.id)])
    s.db.add(Annotation(asset_id=duplicate.id,version=1,is_latest=True,review_status='approved',annotation_data=VALID))
    await s.db.commit();save_and_approve_skip(s)
    listing=s.client.get('/api/images',headers=s.admin,params={'review_status':'approved'}).json()['items']
    assert {row['id'] for row in listing}=={str(good_image.id)}
    selected=s.client.get('/api/images/selection',headers=s.admin,params={'review_status':'approved'}).json()['image_ids']
    assert set(selected)=={str(good_image.id)}
    groups=s.client.get('/api/images/collections',headers=s.admin).json()
    assert next(row for row in groups if row['id']==str(group.id))['image_count']==1
    assert next(row for row in groups if row['id']==str(mixed.id))['image_count']==2
    skipped_reuse=s.client.post('/api/images/associate',headers=s.admin,json={
        'project_id':str(target.id),'image_ids':[str(skipped_image.id)],
        'source_project_id':str(s.project.id),'include_source_annotations':True})
    assert skipped_reuse.status_code==200 and skipped_reuse.json()['reused_annotations']==0 and skipped_reuse.json()['without_annotations']==1
    existing=s.client.post('/api/images/associate',headers=s.admin,json={'project_id':str(old.id),'image_ids':[str(skipped_image.id)]})
    assert existing.status_code==200 and existing.json()['added_count']==0
    copied=s.client.post('/api/images/associate',headers=s.admin,json={'project_id':str(target.id),'image_ids':[str(good_image.id)],
        'source_project_id':str(s.project.id),'include_source_annotations':True})
    assert copied.status_code==200,copied.text
    assert copied.json()['reused_annotations']==1
    await s.db.refresh(history)
    assert history.annotation_data=={'description':'保留的历史描述'} and history.is_latest
    assert await s.db.get(Asset,reference.id) is not None


@pytest.mark.asyncio
@pytest.mark.parametrize('judgment',['correct','incorrect'])
async def test_vector_matches_cannot_reintroduce_approved_skips_as_training_pairs(skip_env,judgment):
    s=skip_env;s.project.task_type='vector'
    good=await valid_asset(s)
    raw=BytesIO();Image.new('RGB',(64,48),'red').save(raw,'PNG');content=raw.getvalue()
    s.asset.content_hash=hashlib.sha256(content).hexdigest();s.asset.file_size=len(content);s.asset.format='PNG'
    await get_storage().save_file(BytesIO(content),s.asset.blob_path)
    hit=lambda asset,verdict:dict(asset_id=str(asset.id),content_hash=asset.content_hash,judgment=verdict,score=.5,
        review_status='approved',reviewer_id=1,user_id=1)
    s.db.add(MatchQuery(project_id=s.project.id,query={'text':'黑色车辆'},space={},hits=[hit(good,'correct'),hit(s.asset,judgment)]))
    await s.db.commit();save_and_approve_skip(s)
    from services.annotation_export import training_package
    stream,manifest=await training_package(s.db,s.project,dataset_format='contrastive',sample_source='matches')
    try:
        assert [row['asset_id'] for row in manifest['assets']]==[str(good.id)]
        with zipfile.ZipFile(stream) as archive:
            pairs=[json.loads(line) for line in archive.read('queries.jsonl').decode().splitlines()]
            assert pairs[0]['negatives']==[]
            assert [entry['asset_id'] for entry in pairs[0]['positives']]==[str(good.id)]
            training=[json.loads(line) for line in archive.read('train.jsonl').decode().splitlines()]
            assert len(training)==1 and training[0]['label']==1
            assert str(s.asset.id) not in archive.read('train.jsonl').decode()
    finally:stream.close()
