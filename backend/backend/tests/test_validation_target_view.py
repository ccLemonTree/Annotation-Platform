from uuid import UUID
import pytest
from tests.test_validation import validation_tables,vclient,ready,create_run


@pytest.mark.asyncio
async def test_target_view_recalculates_existing_results_without_changing_frozen_run(vclient,auth_headers,ready,test_db):
    from sqlalchemy import select
    from models.validation import ValidationRun,ValidationItem
    run=create_run(vclient,auth_headers,ready)
    row=await test_db.get(ValidationRun,UUID(run['id']))
    item=await test_db.scalar(select(ValidationItem).where(ValidationItem.run_id==row.id))
    row.status=item.status='completed';row.processed=1
    item.predictions=[dict(label='person',x=1,y=2,width=6,height=12,score=.9),dict(label='person',x=1,y=2,width=5.5,height=11,score=.8)]
    item.evaluation=dict(tp=0,fp=2,fn=1,errors=['false_positive','false_negative'])
    await test_db.commit()
    params=dict(mode='target',target_overlap=.2)
    detail=vclient.get('/api/validation/runs/'+run['id'],headers=auth_headers,params=params)
    assert detail.status_code==200,detail.text
    assert detail.json()['target_metrics']['tp']==1
    assert detail.json()['target_metrics']['merged_predictions']==1
    assert detail.json()['config']['match_iou']==.5
    for error,count in [('correct',1),('false_positive',0),('false_negative',0)]:
        response=vclient.get('/api/validation/runs/'+run['id']+'/items',headers=auth_headers,params={**params,'error_type':error})
        assert response.status_code==200 and response.json()['total']==count,response.text
    report=vclient.get('/api/validation/runs/'+run['id']+'/report',headers=auth_headers,params=params).json()
    assert report['items'][0]['evaluation']['fp']==0
    assert report['items'][0]['standard_evaluation']['fp']==2
    assert report['run']['evaluation_view']==dict(mode='target',target_overlap=.2)
    assert report['run']['target_metrics']['method']['overlap_metric']=='intersection_over_min_area'
    assert report['items'][0]['evaluation']['matches'][0]['overlap']==1
    await test_db.refresh(item);await test_db.refresh(row)
    assert item.evaluation['fp']==2 and row.config['match_iou']==.5
    for threshold in [0,1.01,'nan']:
        assert vclient.get('/api/validation/runs/'+run['id'],headers=auth_headers,params={'mode':'target','target_overlap':threshold}).status_code==422

    item.predictions=[dict(label='person',x=9,y=2,width=10,height=20,score=.9)]
    await test_db.commit()
    for threshold,tp in [(.2,1),(.21,0)]:
        response=vclient.get('/api/validation/runs/'+run['id'],headers=auth_headers,params=dict(mode='target',target_overlap=threshold))
        assert response.json()['target_metrics']['tp']==tp


@pytest.mark.asyncio
async def test_saved_overlap_is_default_for_report_items_and_export(vclient,auth_headers,ready,test_db):
    from models.validation import ValidationItem
    from tests.test_validation import create_set
    from sqlalchemy import select
    dataset=create_set(vclient,auth_headers,ready)
    response=vclient.post('/api/validation/runs',headers=auth_headers,json=dict(
        set_id=dataset['id'],model_id=str(ready['model'].id),config=dict(target_overlap=.4)))
    assert response.status_code==201,response.text
    run=response.json();assert run['config']['target_overlap']==.4
    item=await test_db.scalar(select(ValidationItem).where(ValidationItem.run_id==UUID(run['id'])))
    item.status='completed'
    item.predictions=[dict(label='person',x=8,y=2,width=10,height=20,score=.9)]
    await test_db.commit()
    root='/api/validation/runs/'+run['id']
    for params,tp,threshold in [(dict(mode='target'),0,.4),(dict(mode='target',target_overlap=.2),1,.2)]:
        detail=vclient.get(root,headers=auth_headers,params=params).json()
        assert detail['target_metrics']['tp']==tp
        assert detail['evaluation_view']['target_overlap']==threshold
        page=vclient.get(root+'/items',headers=auth_headers,params={**params,'error_type':'correct'}).json()
        assert page['total']==tp
        report=vclient.get(root+'/report',headers=auth_headers,params=params).json()
        assert report['items'][0]['evaluation']['tp']==tp
        assert report['run']['evaluation_view']['target_overlap']==threshold
    assert vclient.get(root,headers=auth_headers).json()['config']['target_overlap']==.4
    from models.validation import ValidationRun
    row=await test_db.get(ValidationRun,UUID(run['id']))
    row.config={key:value for key,value in row.config.items() if key!='target_overlap'}
    await test_db.commit()
    legacy=vclient.get(root,headers=auth_headers,params=dict(mode='target')).json()
    assert legacy['evaluation_view']['target_overlap']==.2 and legacy['target_metrics']['tp']==1
    assert 'target_overlap' not in legacy['config']
