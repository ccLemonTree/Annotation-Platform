from uuid import uuid4
from io import BytesIO
import pytest
from sqlalchemy import select
from models import User, Tenant, Project, Asset, Annotation, LibraryImage
from services.auth import create_access_token


@pytest.fixture
async def permissions(client, test_db, test_project, test_asset, auth_headers, test_engine, mock_image_data):
    from models import Base, ModelService, WorkActivityInterval
    from models.project_work import ProjectWorkJob, ProjectWorkItem, MatchQuery
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda sync:Base.metadata.create_all(sync,tables=[ModelService.__table__,
            ProjectWorkJob.__table__,ProjectWorkItem.__table__,MatchQuery.__table__,WorkActivityInterval.__table__]))
    test_db.add(Tenant(id=2, tenant_name='foreign'))
    await test_db.flush()
    users=[User(id=id,tenant_id=tenant,username=f'member{id}',email=f'm{id}@example.test',
        password_hash='unused',role=role,is_active=active) for id,tenant,role,active in
        [(2,1,'user',True),(3,1,'annotator',True),(4,2,'user',True),(5,1,'user',False)]]
    hidden=Project(tenant_id=1,project_name='unassigned',task_type='detection')
    foreign=Project(tenant_id=2,project_name='foreign',task_type='detection')
    test_db.add_all([*users,hidden,foreign]);await test_db.flush()
    image=LibraryImage(tenant_id=1,original_name='hidden.jpg',blob_path='hidden.jpg',content_hash='hidden',
        file_size=1,source_type='upload',width=64,height=48)
    test_db.add(image);await test_db.flush()
    hidden_asset=Asset(project_id=hidden.id,image_id=image.id,original_name='hidden.jpg',blob_path='hidden.jpg',
        content_hash='hidden',file_size=1,source_type='upload',width=64,height=48,format='JPEG')
    test_db.add(hidden_asset);await test_db.flush()
    data={'boxes':[dict(x=1,y=1,width=4,height=4,label='person')]}
    annotations=[Annotation(asset_id=asset.id,annotation_data=data,version=1,is_latest=True,review_status='pending')
        for asset in [test_asset,hidden_asset]]
    test_db.add_all(annotations);await test_db.commit()
    from services.storage import get_storage
    for asset in (test_asset,hidden_asset):
        await get_storage().save_file(BytesIO(mock_image_data.getvalue()),asset.blob_path)
    return dict(admin=auth_headers,user={'Authorization':'Bearer '+create_access_token({'user_id':2})},
        peer={'Authorization':'Bearer '+create_access_token({'user_id':3})},project=test_project,
        asset=test_asset,hidden=hidden,hidden_asset=hidden_asset,foreign=foreign,annotations=annotations)


def assignments(client,p,ids):
    return client.put(f"/api/projects/{p['project'].id}/assignments",headers=p['admin'],json={'user_ids':ids})


def test_assignments_validate_atomically_and_empty_means_admin_only(client,permissions):
    p=permissions;url=f"/api/projects/{p['project'].id}/assignments"
    assert client.get(url,headers=p['admin']).json()=={'user_ids':[]}
    response=assignments(client,p,[3,2,2]);assert response.status_code==200,response.text
    assert response.json()=={'user_ids':[2,3]}
    for ids in ([4],[5],[1],[999]):
        assert assignments(client,p,ids).status_code==400
        assert client.get(url,headers=p['admin']).json()=={'user_ids':[2,3]}
    assert client.put(url,headers=p['admin'],json={'user_ids':['2']}).status_code==422
    assert client.get(url,headers=p['user']).status_code==403
    assert client.put(url,headers=p['user'],json={'user_ids':[2]}).status_code==403
    assert client.put(f"/api/projects/{p['foreign'].id}/assignments",headers=p['admin'],json={'user_ids':[2]}).status_code==404
    assert assignments(client,p,[]).status_code==200
    assert client.get('/api/projects',headers=p['user']).json()['total']==0
    assert client.get(f"/api/projects/{p['project'].id}",headers=p['admin']).status_code==200


@pytest.mark.parametrize('method,path',[
    ('POST','/api/projects'),('PUT','/api/projects/{project}'),('DELETE','/api/projects/{project}'),
    ('POST','/api/assets/upload'),('POST','/api/assets/batch-upload'),('DELETE','/api/assets/{asset}'),
    ('POST','/api/batch/assets/delete'),('POST','/api/batch/assets/update-status'),
    ('GET','/api/model-repository'),('GET','/api/model-repository/{random}/download'),
    ('GET','/api/stats/dashboard'),('GET','/api/analytics'),('GET','/api/users'),
    ('POST','/api/automation/jobs'),('GET','/api/automation/jobs'),('GET','/api/automation/review-queue'),
    ('POST','/api/projects/{project}/work/jobs'),('GET','/api/projects/{project}/work/jobs'),
    ('POST','/api/projects/{project}/matches/review-jobs'),('POST','/api/image-semantics/jobs'),
    ('GET','/api/image-semantics/jobs'),('POST','/api/image-semantics/search'),
    ('POST','/api/images/batch-upload'),('DELETE','/api/annotations/{annotation}'),
    ('POST','/api/batch/annotations/delete'),('GET','/api/model-previews/config'),
])
def test_ordinary_user_management_api_default_deny(client,permissions,method,path):
    p=permissions
    path=path.format(project=p['project'].id,asset=p['asset'].id,random=uuid4(),annotation=p['annotations'][0].id)
    assert client.request(method,path,headers=p['user'],json={}).status_code==403


def test_member_reads_saves_reviews_and_revocation_is_immediate(client,permissions):
    p=permissions;assert assignments(client,p,[2,3]).status_code==200
    project=str(p['project'].id);asset=str(p['asset'].id);ann=str(p['annotations'][0].id)
    assert [row['id'] for row in client.get('/api/projects',headers=p['user']).json()['items']]==[project]
    for suffix in ('', '/stats?scope=annotation'):
        assert client.get('/api/projects/'+project+suffix,headers=p['user']).status_code==200
    for suffix in ('','/neighbors','/content'):
        status=client.get('/api/assets/'+asset+suffix,headers=p['user']).status_code
        assert status==200
    for query in ('', '?project_id='+project,'?asset_id='+asset):
        rows=client.get('/api/annotations'+query,headers=p['user']).json()
        assert rows['total']==1 and rows['items'][0]['id']==ann
    saved=client.put('/api/annotations/'+ann,headers=p['user'],json={
        'base_version':1,'annotation_data':{'boxes':[dict(x=2,y=2,width=4,height=4,label='person')]}})
    assert saved.status_code==200,saved.text
    path='/api/annotations/'+saved.json()['id']
    assert client.post(path+'/review',headers=p['peer'],json={'approved':True}).status_code==200
    assert assignments(client,p,[]).status_code==200
    for url in ('/api/projects/'+project,'/api/assets/'+asset,'/api/assets/'+asset+'/content',path,
                f'/api/assets?project_id={project}',f'/api/assets/selection?project_id={project}'):
        assert client.get(url,headers=p['user']).status_code==404,url
    assert client.post(path+'/review',headers=p['user'],json={'approved':False}).status_code==404
    assert client.post('/api/annotations',headers=p['user'],json={
        'asset_id':asset,'base_version':2,'annotation_data':{'boxes':[]}}).status_code==404


@pytest.mark.asyncio
async def test_unassigned_objects_and_mixed_batch_cannot_be_accessed(client,permissions,test_db):
    p=permissions;assert assignments(client,p,[2]).status_code==200
    ann=p['annotations'][1]
    for url in (f"/api/projects/{p['hidden'].id}",f"/api/assets/{p['hidden_asset'].id}/content",
                f'/api/annotations/{ann.id}',f"/api/projects/{p['hidden'].id}/matches",
                f"/api/projects/{p['hidden'].id}/matches/smart-search/platforms"):
        assert client.get(url,headers=p['user']).status_code==404,url
    for action in ('approve','reject'):
        response=client.post('/api/batch/annotations/'+action,headers=p['user'],json=[str(a.id) for a in p['annotations']])
        assert response.status_code==404,response.text
        await test_db.refresh(p['annotations'][0]);assert p['annotations'][0].review_status=='pending'
        await test_db.commit()
    assert client.post('/api/batch/annotations/approve',headers=p['user'],json=[str(p['annotations'][0].id)]).status_code==200
    assert client.get('/api/images',headers=p['user']).json()['total']==0
    assert client.get(f"/api/images/{p['hidden_asset'].image_id}/content",headers=p['user']).status_code==404
    assert client.get(f"/api/images/{p['hidden_asset'].image_id}/content",headers=p['admin']).status_code==200
    assert client.get('/api/images',headers=p['user'],params={'source_project_id':str(p['hidden'].id)}).status_code==404


def test_member_negative_review_still_needs_admin(client,permissions):
    p=permissions;assert assignments(client,p,[2]).status_code==200
    result=client.put('/api/annotations/'+str(p['annotations'][0].id),headers=p['user'],json={
        'base_version':1,'annotation_data':{'boxes':[]}})
    assert result.status_code==200
    path='/api/annotations/'+result.json()['id']+'/review'
    assert client.post(path,headers=p['user'],json={'approved':True}).status_code==403
    assert client.post(path,headers=p['admin'],json={'approved':True}).status_code==200


def test_migration_can_deploy_without_unrelated_device_or_model_changes():
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    script=ScriptDirectory.from_config(Config('alembic.ini'))
    assert len(script.get_heads())==1
    assert script.get_revision('036').down_revision=='035'
    assert script.get_revision('034').down_revision=='031'
    assert {revision.revision for revision in script.walk_revisions('031','034')}=={'034','031'}
    assert set(script.get_revision('035').down_revision)=={'033','034'}


@pytest.mark.asyncio
async def test_project_models_and_vector_judgment_are_scoped(client,permissions,test_db):
    from models import ModelService
    from models.project_work import ProjectWorkJob, ProjectWorkItem, MatchQuery
    p=permissions;assert assignments(client,p,[2,3]).status_code==200
    p['project'].task_type='vector'
    model=ModelService(tenant_id=1,key='embedding',name='Index model',kind='embedding',protocol='openai_embedding',
        base_url='http://private-model.test',endpoint='/embeddings',enabled=True,revision=2)
    stale=ModelService(tenant_id=1,key='stale',name='Changed model',kind='embedding',protocol='openai_embedding',
        base_url='http://private-model.test',endpoint='/embeddings',enabled=True,revision=2)
    test_db.add_all([model,stale]);await test_db.flush()
    for row in (model,stale):
        job=ProjectWorkJob(project_id=p['project'].id,user_id=1,operation='index',options={},
            model={'id':str(row.id),'revision':2 if row is model else 1})
        test_db.add(job);await test_db.flush()
        test_db.add(ProjectWorkItem(job_id=job.id,asset_id=p['asset'].id,content_hash='sample',status='completed'))
    query=MatchQuery(project_id=p['project'].id,query={'text':'query'},space={},hits=[{
        'asset_id':str(p['asset'].id),'content_hash':'sample','judgment':'unknown'}])
    test_db.add(query);await test_db.commit()
    base=f"/api/projects/{p['project'].id}"
    response=client.get(base+'/annotation-models',headers=p['user'])
    assert response.status_code==200,response.text
    assert response.json()==[dict(id=str(model.id),name=model.name,revision=2,kind='embedding',enabled=True,connection_check=None)]
    assert 'private-model' not in response.text
    assert client.get(f"/api/projects/{p['hidden'].id}/annotation-models",headers=p['user']).status_code==404
    match=base+'/matches/'+str(query.id)
    response=client.put(match,headers=p['user'],json={'asset_id':str(p['asset'].id),'judgment':'correct','base_version':1})
    assert response.status_code==200,response.text
    assert client.post(match+'/review',headers=p['peer'],json={
        'asset_id':str(p['asset'].id),'approved':True,'base_version':2}).status_code==200
    assert assignments(client,p,[]).status_code==200
    assert client.get(base+'/matches',headers=p['user']).status_code==404
    assert client.put(match,headers=p['user'],json={
        'asset_id':str(p['asset'].id),'judgment':'incorrect','base_version':3}).status_code==404


@pytest.mark.asyncio
async def test_revoke_during_vector_search_prevents_saving_results(client,permissions,test_db,monkeypatch):
    from models import ModelService
    from models.project_work import ProjectWorkJob,ProjectWorkItem,MatchQuery
    from services import project_work
    from services.milvus import space_key
    from services.image_semantics import model_snapshot
    p=permissions;assert assignments(client,p,[2]).status_code==200
    p['project'].task_type='vector'
    model=ModelService(tenant_id=1,key='embedding',name='Index',kind='embedding',protocol='openai_embedding',
        base_url='http://private-model.test',endpoint='/embeddings',model_name='embedding',enabled=True,revision=1)
    test_db.add(model);await test_db.commit()
    snapshot=await model_snapshot(test_db,1,model.id,'embedding')
    job=ProjectWorkJob(project_id=p['project'].id,user_id=1,operation='index',options={},model=snapshot)
    test_db.add(job);await test_db.flush()
    test_db.add(ProjectWorkItem(job_id=job.id,asset_id=p['asset'].id,content_hash='sample',status='completed',
        result={'space':space_key(snapshot),'vector_id':'vector-1'}))
    await test_db.commit()
    async def embedding(*args,**kwargs):
        import asyncio
        assert (await asyncio.to_thread(assignments,client,p,[])).status_code==200
        return [1.0]*2048
    async def search(*args,**kwargs):return []
    monkeypatch.setattr(project_work,'embedding',embedding)
    monkeypatch.setattr(project_work.Milvus,'search',search)
    result=client.post(f"/api/projects/{p['project'].id}/matches/search",headers=p['user'],json={
        'model_id':str(model.id),'text':'query'})
    assert result.status_code==404,result.text
    assert not (await test_db.scalars(select(MatchQuery))).all()


@pytest.mark.asyncio
async def test_member_image_query_only_uses_direct_project_references(client,permissions,test_db):
    p=permissions;assert assignments(client,p,[2]).status_code==200
    image_id=p['hidden_asset'].image_id
    p['annotations'][1].review_status='approved'
    p['asset'].device_info={'parent_image_id':str(image_id)}
    await test_db.commit()
    assert client.get('/api/images',headers=p['user']).json()['total']==0
    assert client.get(f'/api/images/{image_id}/content',headers=p['user']).status_code==404
    p['asset'].image_id=image_id
    await test_db.commit()
    assert [row['id'] for row in client.get('/api/images',headers=p['user']).json()['items']]==[str(image_id)]
    assert client.get(f'/api/images/{image_id}/content',headers=p['user']).status_code==200
    assert assignments(client,p,[]).status_code==200
    assert client.get(f'/api/images/{image_id}/content',headers=p['user']).status_code==404


@pytest.mark.asyncio
async def test_member_create_activity_scope_and_membership_foreign_keys(client,permissions,test_db):
    from datetime import datetime,timedelta,timezone
    from models import ProjectMember
    p=permissions;assert assignments(client,p,[2]).status_code==200
    asset=Asset(project_id=p['project'].id,original_name='new.jpg',blob_path='new.jpg',content_hash='new',
        file_size=1,source_type='upload',width=64,height=48)
    test_db.add(asset);await test_db.commit()
    result=client.post('/api/annotations',headers=p['user'],json={
        'asset_id':str(asset.id),'base_version':0,'annotation_data':{'boxes':[dict(x=1,y=1,width=2,height=2,label='person')]}})
    assert result.status_code==201,result.text
    now=datetime.now(timezone.utc)
    data=dict(project_id=str(p['project'].id),asset_id=str(asset.id),session_id=str(uuid4()),sequence=1,
        activity='annotation',started_at=(now-timedelta(seconds=5)).isoformat(),ended_at=now.isoformat())
    assert client.post('/api/analytics/activity',headers=p['user'],json=data).status_code==200
    data.update(project_id=str(p['hidden'].id),asset_id=str(p['hidden_asset'].id),sequence=2)
    assert client.post('/api/analytics/activity',headers=p['user'],json=data).status_code==404
    data.update(project_id=str(p['project'].id))
    assert client.post('/api/analytics/activity',headers=p['user'],json=data).status_code==404
    peer=await test_db.get(User,3)
    test_db.add(ProjectMember(project_id=p['hidden'].id,user_id=3));await test_db.commit()
    await test_db.delete(peer);await test_db.commit()
    assert (await test_db.scalars(select(ProjectMember).where(ProjectMember.user_id==3))).all()==[]
    await test_db.delete(p['project']);await test_db.commit()
    assert (await test_db.scalars(select(ProjectMember).where(ProjectMember.project_id==p['project'].id))).all()==[]


@pytest.mark.asyncio
@pytest.mark.parametrize('operation',['save','review'])
async def test_shared_write_rechecks_membership_after_asset_lookup(client,permissions,test_db,operation):
    from fastapi import HTTPException
    from services.annotations import save_annotation,review
    p=permissions;assert assignments(client,p,[2]).status_code==200
    assert assignments(client,p,[]).status_code==200
    test_db.info['current_user']={'id':2,'tenant_id':1,'role':'user'}
    with pytest.raises(HTTPException) as error:
        if operation=='save':
            await save_annotation(test_db,p['asset'],p['annotations'][0].annotation_data,1,2)
        else:
            await review(test_db,p['annotations'][0],p['asset'],True,None,2)
    assert error.value.status_code==404
