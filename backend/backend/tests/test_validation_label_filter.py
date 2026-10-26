from uuid import UUID,uuid4
import pytest
from sqlalchemy import select
from tests.test_validation import validation_tables,vclient,ready,create_run


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['standard','target'])
async def test_label_filter_matches_the_actual_missing_or_false_positive_class(vclient,auth_headers,ready,test_db,mode):
    from models.validation import ValidationRun,ValidationItem,ValidationSet
    run=create_run(vclient,auth_headers,ready)
    row=await test_db.get(ValidationRun,UUID(run['id']))
    dataset=await test_db.get(ValidationSet,row.set_id);dataset.labels=['person','car']
    original=await test_db.scalar(select(ValidationItem).where(ValidationItem.run_id==row.id))
    original.status='completed';original.truth=[dict(label='person',x=1,y=2,width=10,height=20),dict(label='car',x=30,y=2,width=10,height=20)]
    original.predictions=[dict(label='car',x=30,y=2,width=10,height=20,score=.9)]
    extra=ValidationItem(run_id=row.id,asset_id=uuid4(),original_name='false-positive.jpg',width=64,height=48,status='completed',
        truth=[],predictions=[dict(label='person',x=1,y=2,width=10,height=20,score=.9)],evaluation={})
    test_db.add(extra);await test_db.flush()
    low=ValidationItem(run_id=row.id,asset_id=uuid4(),original_name='low-score.jpg',width=64,height=48,status='completed',
        truth=[dict(label='car',x=1,y=2,width=10,height=20)],predictions=[dict(label='car',x=1,y=2,width=10,height=20,score=.2)],evaluation={})
    test_db.add(low);row.status='completed';await test_db.commit()
    path='/api/validation/runs/'+run['id']+'/items'
    for error,label,expected in [('false_negative','person',[str(original.id)]),('false_negative','car',[str(low.id)]),
        ('false_positive','person',[str(extra.id)]),('false_positive','car',[])]:
        response=vclient.get(path,headers=auth_headers,params=dict(mode=mode,label=label,error_type=error,page_size=1))
        assert response.status_code==200,response.text
        assert response.json()['total']==len(expected)
        assert [i['id'] for i in response.json()['items']]==expected
    detail=vclient.get('/api/validation/runs/'+run['id'],headers=auth_headers).json()
    assert detail['labels']==['person','car']
    assert vclient.get(path,headers=auth_headers,params={'mode':mode,'label':'unknown'}).json()['total']==0
    await test_db.refresh(original)
    assert original.predictions[0]['label']=='car' and len(original.truth)==2
