from uuid import UUID,uuid4
import pytest
from fastapi import HTTPException
from sqlalchemy import select,func
from sqlalchemy.ext.asyncio import async_sessionmaker
from tests.test_validation import validation_tables,vclient,ready,create_set,create_run


@pytest.mark.asyncio
@pytest.mark.parametrize('status',['completed','failed','canceled'])
async def test_delete_task_and_results_preserves_set_images_and_other_tasks(vclient,auth_headers,ready,test_db,status):
    from models.validation import ValidationRun,ValidationItem,ValidationSet
    from models import Asset,Annotation
    dataset=create_set(vclient,auth_headers,ready)
    run=create_run(vclient,auth_headers,ready,dataset);other=create_run(vclient,auth_headers,ready,dataset)
    row=await test_db.get(ValidationRun,UUID(run['id']));row.status=status;await test_db.commit()
    response=vclient.delete('/api/validation/runs/'+run['id'],headers=auth_headers)
    assert response.status_code==204,response.text
    assert vclient.get('/api/validation/runs/'+run['id'],headers=auth_headers).status_code==404
    assert await test_db.scalar(select(func.count()).select_from(ValidationItem).where(ValidationItem.run_id==UUID(run['id'])))==0
    assert await test_db.get(ValidationSet,UUID(dataset['id'])) is not None
    assert await test_db.get(ValidationRun,UUID(other['id'])) is not None
    assert await test_db.get(Asset,ready['asset'].id) is not None
    assert await test_db.get(Annotation,ready['annotation'].id) is not None


@pytest.mark.asyncio
async def test_delete_requires_admin_tenant_scope_and_excludes_preview(vclient,auth_headers,ready,test_db,test_user):
    from models.validation import ValidationRun
    run=create_run(vclient,auth_headers,ready);path='/api/validation/runs/'+run['id']
    assert vclient.delete(path).status_code==401
    test_user.role='annotator';await test_db.commit()
    assert vclient.delete(path,headers=auth_headers).status_code==403
    test_user.role='admin';await test_db.commit()
    row=await test_db.get(ValidationRun,UUID(run['id']));row.model_snapshot={**row.model_snapshot,'preview':True};await test_db.commit()
    assert vclient.delete(path,headers=auth_headers).status_code==404
    from models import Tenant
    test_db.add(Tenant(id=999,tenant_name='other tenant'));await test_db.flush()
    row.model_snapshot={**row.model_snapshot,'preview':False};row.tenant_id=999;await test_db.commit()
    assert vclient.delete(path,headers=auth_headers).status_code==404
    assert vclient.delete('/api/validation/runs/'+str(uuid4()),headers=auth_headers).status_code==404


@pytest.mark.asyncio
@pytest.mark.parametrize('stop_fails',[False,True])
async def test_checkpoint_delete_confirms_remote_stop_before_removing_record(vclient,auth_headers,ready,test_db,monkeypatch,stop_fails):
    from models.validation import ValidationRun,ValidationItem
    from services import checkpoint_validation as cp
    run=create_run(vclient,auth_headers,ready)
    row=await test_db.get(ValidationRun,UUID(run['id']));row.status='running'
    row.model_snapshot={**row.model_snapshot,'source':'checkpoint','execution':dict(active=True,id=str(uuid4()))}
    await test_db.commit();calls=[]
    async def stop(db,current):
        calls.append(str(current.id))
        if stop_fails:raise HTTPException(503,'fixture timeout')
        current.model_snapshot={**current.model_snapshot,'execution':{**current.model_snapshot['execution'],'active':False}}
    monkeypatch.setattr(cp,'stop',stop)
    response=vclient.delete('/api/validation/runs/'+run['id'],headers=auth_headers)
    assert response.status_code==(409 if stop_fails else 204),response.text
    assert calls==[run['id']]
    current=await test_db.get(ValidationRun,UUID(run['id']),populate_existing=True)
    if stop_fails:
        assert current.status=='canceled' and current.model_snapshot['execution']['active']
        assert await test_db.scalar(select(func.count()).select_from(ValidationItem).where(ValidationItem.run_id==current.id))==1
    else:
        assert await test_db.scalar(select(func.count()).select_from(ValidationRun).where(ValidationRun.id==UUID(run['id'])))==0


@pytest.mark.asyncio
async def test_delete_discards_late_worker_result(vclient,auth_headers,ready,test_engine,monkeypatch):
    from services import validation
    run=create_run(vclient,auth_headers,ready)
    async def infer(*args,**kwargs):
        assert vclient.delete('/api/validation/runs/'+run['id'],headers=auth_headers).status_code==204
        return dict(boxes=[dict(label='person',x=1,y=2,width=10,height=20,score=.9)])
    monkeypatch.setattr(validation,'infer',infer)
    assert await validation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    assert vclient.get('/api/validation/runs/'+run['id'],headers=auth_headers).status_code==404
