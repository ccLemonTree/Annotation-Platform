"""Refresh creates a new truth snapshot; deletion never touches project assets."""
from uuid import UUID
import pytest
from sqlalchemy import select
from tests.test_validation import validation_tables,vclient,ready,create_set,create_run


@pytest.mark.asyncio
async def test_refresh_pins_latest_reviewed_truth_without_changing_old_set_or_run(vclient,auth_headers,ready,test_db):
    from models import Annotation
    from models.validation import ValidationSet,ValidationItem
    original=create_set(vclient,auth_headers,ready);run=create_run(vclient,auth_headers,ready,original)
    ready['annotation'].is_latest=False
    ready['project'].label_schema={'labels':['smoke']}
    new=Annotation(asset_id=ready['asset'].id,annotation_data={'boxes':[dict(label='smoke',x=2,y=3,width=8,height=9)]},
        version=2,is_latest=True,review_status='approved',reviewer_id=ready['annotation'].reviewer_id)
    test_db.add(new);await test_db.commit()
    response=vclient.post('/api/validation/sets/'+original['id']+'/refresh',headers=auth_headers)
    assert response.status_code==201,response.text
    updated=response.json();assert updated['id']!=original['id'] and updated['labels']==['smoke']
    assert updated['source']['refreshed_from_set_id']==original['id']
    stored=await test_db.get(ValidationSet,UUID(updated['id']))
    assert stored.samples[0]['annotation_version']==2 and stored.samples[0]['truth'][0]['label']=='smoke'
    assert vclient.get('/api/validation/sets/'+original['id'],headers=auth_headers).json()['labels']==['person','car']
    item=await test_db.scalar(select(ValidationItem).where(ValidationItem.run_id==UUID(run['id'])))
    assert item.truth[0]['label']=='person'


@pytest.mark.asyncio
async def test_refresh_refuses_unreviewed_latest_annotation(vclient,auth_headers,ready,test_db):
    dataset=create_set(vclient,auth_headers,ready)
    ready['annotation'].review_status='pending';await test_db.commit()
    response=vclient.post('/api/validation/sets/'+dataset['id']+'/refresh',headers=auth_headers)
    assert response.status_code==400,response.text
    assert vclient.get('/api/validation/sets',headers=auth_headers).json()['total']==1


@pytest.mark.asyncio
async def test_delete_refuses_active_run_then_removes_history_only(vclient,auth_headers,ready,test_db):
    from models.validation import ValidationRun,ValidationItem
    from models import Asset,Annotation
    dataset=create_set(vclient,auth_headers,ready);run=create_run(vclient,auth_headers,ready,dataset)
    url='/api/validation/sets/'+dataset['id']
    assert vclient.delete(url,headers=auth_headers).status_code==409
    row=await test_db.get(ValidationRun,UUID(run['id']));row.status='completed';await test_db.commit()
    assert vclient.delete(url,headers=auth_headers).status_code==204
    assert vclient.get(url,headers=auth_headers).status_code==404
    assert await test_db.get(Asset,ready['asset'].id) is not None
    assert await test_db.get(Annotation,ready['annotation'].id) is not None
    assert await test_db.scalar(select(ValidationItem.id)) is None


@pytest.mark.asyncio
async def test_set_actions_admin_and_tenant_scoped(vclient,auth_headers,ready,test_db,test_user):
    dataset=create_set(vclient,auth_headers,ready);url='/api/validation/sets/'+dataset['id']
    assert vclient.delete(url).status_code==401
    assert vclient.post(url+'/refresh').status_code==401
    test_user.role='annotator';await test_db.commit()
    assert vclient.delete(url,headers=auth_headers).status_code==403
    assert vclient.post(url+'/refresh',headers=auth_headers).status_code==403
    test_user.role='admin';test_user.tenant_id=999
    from models import Tenant
    test_db.add(Tenant(id=999,tenant_name='other'));await test_db.commit()
    assert vclient.delete(url,headers=auth_headers).status_code==404
    assert vclient.post(url+'/refresh',headers=auth_headers).status_code==404


@pytest.mark.asyncio
async def test_delete_refuses_remote_reservation_even_after_task_completed(vclient,auth_headers,ready,test_db):
    from models.validation import ValidationRun
    dataset=create_set(vclient,auth_headers,ready);run=create_run(vclient,auth_headers,ready,dataset)
    row=await test_db.get(ValidationRun,UUID(run['id']));row.status='completed'
    row.model_snapshot={**row.model_snapshot,'execution':{'active':True}}
    await test_db.commit()
    response=vclient.delete('/api/validation/sets/'+dataset['id'],headers=auth_headers)
    assert response.status_code==409,response.text
    assert vclient.get('/api/validation/sets/'+dataset['id'],headers=auth_headers).status_code==200
