"""Deleting a project crop removes live annotations/candidates, preserving its source."""
import pytest
from sqlalchemy import select
from models import Asset,Annotation,Project
from models.project_work import ProjectWorkJob,ProjectWorkItem,MatchQuery
from tests.test_project_work import work_env
from uuid import uuid4


@pytest.mark.asyncio
async def test_delete_crop_preserves_original_and_updates_queue_counts_and_history(client,auth_headers,work_env):
    from services.storage import get_storage
    s=work_env;s.project.task_type='vector';s.project.label_schema={'annotation_scope':'crops'}
    crop=Asset(project_id=s.project.id,original_name='private-crop.png',blob_path=s.asset.blob_path,content_hash='crop-fixture',
        file_size=s.asset.file_size,width=64,height=48,format='PNG',source_type='model_crop',annotation_status='reviewed',
        device_info={'crop':{'parent_asset_id':str(s.asset.id)}})
    s.db.add(crop);await s.db.flush()
    annotation=Annotation(asset_id=crop.id,version=1,is_latest=True,annotation_data={'description':'裁剪图描述'},review_status='approved',reviewer_id=s.user.id)
    job=ProjectWorkJob(project_id=s.project.id,user_id=s.user.id,operation='index',model={'id':str(s.models[2].id),'revision':1},options={})
    s.db.add_all([annotation,job]);await s.db.flush()
    item=ProjectWorkItem(job_id=job.id,asset_id=crop.id,content_hash=crop.content_hash,status='completed',result={'vector_id':'fixture'})
    query=MatchQuery(project_id=s.project.id,query={'text':'人'},space={'revision':1},hits=[{
        'asset_id':str(crop.id),'image_id':None,'original_name':crop.original_name,'content_hash':crop.content_hash,'score':.9,'judgment':'correct'}])
    s.db.add_all([item,query]);s.project.total_assets=2;s.project.annotated_count=1;s.project.reviewed_count=1;await s.db.commit()
    crop_id,annotation_id,item_id=crop.id,annotation.id,item.id
    original_id,original_blob=s.asset.id,s.asset.blob_path
    before=client.get('/api/projects/'+str(s.project.id)+'/matches',headers=auth_headers).json()
    assert before[0]['hits'][0]['source_type']=='model_crop'
    assert client.delete('/api/assets/'+str(crop_id),headers=auth_headers).status_code==204
    assert client.get('/api/assets/'+str(crop_id),headers=auth_headers).status_code==404
    assert client.get('/api/assets/'+str(original_id),headers=auth_headers).status_code==200
    assert client.get('/api/assets',headers=auth_headers,params={'project_id':str(s.project.id),'scope':'annotation'}).json()['total']==0
    stats=client.get('/api/projects/'+str(s.project.id)+'/stats',headers=auth_headers).json()
    assert (stats['total_assets'],stats['annotated_count'],stats['reviewed_count'])==(1,0,0)
    history=client.get('/api/projects/'+str(s.project.id)+'/matches',headers=auth_headers).json()
    assert len(history)==1 and history[0]['hits']==[]
    async with s.sessions() as db:
        assert await db.get(Annotation,annotation_id) is None
        assert (await db.get(ProjectWorkItem,item_id)).asset_id is None
        assert (await db.get(Asset,original_id)).blob_path==original_blob
    assert await get_storage().file_exists(original_blob)


@pytest.mark.asyncio
async def test_batch_crop_delete_updates_counts_once_and_preserves_source(client,auth_headers,work_env):
    from services.storage import get_storage
    s=work_env;s.project.task_type='vector';s.project.label_schema={'annotation_scope':'crops'}
    crops=[]
    for i in range(2):
        crop=Asset(project_id=s.project.id,original_name=f'crop-{i}.png',blob_path=s.asset.blob_path,content_hash=f'crop-{i}',
            file_size=1,width=64,height=48,source_type='model_crop',annotation_status='reviewed' if i==0 else 'pending')
        s.db.add(crop);crops.append(crop)
    await s.db.flush()
    s.db.add(Annotation(asset_id=crops[0].id,version=1,is_latest=True,annotation_data={'description':'已通过裁剪图'},review_status='approved',reviewer_id=s.user.id))
    s.db.add(MatchQuery(project_id=s.project.id,query={'text':'人'},space={'revision':1},hits=[{
        'asset_id':str(crop.id),'original_name':crop.original_name,'score':.9,'judgment':'correct'} for crop in crops]))
    s.project.total_assets=3;s.project.annotated_count=1;s.project.reviewed_count=1;await s.db.commit()
    ids=[str(crop.id) for crop in crops]
    result=client.post('/api/batch/assets/delete',headers=auth_headers,json=[*ids,ids[0]])
    assert result.status_code==200 and result.json()['affected_count']==2
    assert all(client.get('/api/assets/'+id,headers=auth_headers).status_code==404 for id in ids)
    assert client.get('/api/assets/'+str(s.asset.id),headers=auth_headers).status_code==200
    stats=client.get('/api/projects/'+str(s.project.id)+'/stats',headers=auth_headers).json()
    assert (stats['total_assets'],stats['annotated_count'],stats['reviewed_count'])==(1,0,0)
    assert client.get('/api/projects/'+str(s.project.id)+'/matches',headers=auth_headers).json()[0]['hits']==[]
    assert client.get('/api/annotations',headers=auth_headers,params={'project_id':str(s.project.id)}).json()['total']==0
    assert await get_storage().file_exists(s.asset.blob_path)


@pytest.mark.asyncio
async def test_all_asset_selection_includes_every_page_and_respects_source_and_scope(client,auth_headers,work_env):
    s=work_env;s.project.label_schema={'annotation_scope':'crops'}
    for i in range(25):
        s.db.add(Asset(project_id=s.project.id,original_name=f'crop-{i}.png',blob_path=s.asset.blob_path,content_hash=f'crop-all-{i}',
            file_size=1,width=64,height=48,source_type='model_crop',annotation_status='pending'))
    s.project.total_assets=26;await s.db.commit()
    path='/api/assets/selection';params={'project_id':str(s.project.id)}
    result=client.get(path,headers=auth_headers,params=params)
    assert result.status_code==200,result.text
    assert result.json()['total']==len(set(result.json()['asset_ids']))==26
    scoped=client.get(path,headers=auth_headers,params={**params,'scope':'annotation'}).json()
    assert scoped['total']==25 and str(s.asset.id) not in scoped['asset_ids']
    originals=client.get(path,headers=auth_headers,params={**params,'image_kind':'originals'}).json()
    assert originals['asset_ids']==[str(s.asset.id)]
    assert client.get(path,headers=auth_headers,params={**params,'image_kind':'crops'}).json()['total']==25
    assert client.get(path,headers=auth_headers,params={**params,'scope':'annotation','image_kind':'originals'}).json()['total']==0
    assert client.get(path,headers=auth_headers,params={**params,'annotation_status':'reviewed'}).json()['total']==0
    assert client.get(path,headers=auth_headers,params={'project_id':str(uuid4())}).status_code==404
    assert client.get(path,params=params).status_code==401
    deleted=client.post('/api/batch/assets/delete',headers=auth_headers,json=scoped['asset_ids'])
    assert deleted.status_code==200 and deleted.json()['affected_count']==25
    assert client.get('/api/assets',headers=auth_headers,params={**params,'scope':'annotation'}).json()['total']==0
    assert client.get('/api/assets/'+str(s.asset.id),headers=auth_headers).status_code==200
