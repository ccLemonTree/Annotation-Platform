from datetime import datetime
from uuid import UUID
import pytest
import pytest_asyncio
from models import Asset,Annotation,Project


@pytest_asyncio.fixture
async def attribute_assets(test_db,test_project):
    test_project.task_type='caption'
    test_project.label_schema={'annotation_scope':'crops','labels':['car'],'object_attributes':{'target_label':'car',
        'fields':[{'key':'color','name':'颜色','instruction':'','options':['白','黑']}]}}
    ids=[UUID(f'dddddddd-0000-4000-8000-{i+1:012x}') for i in range(8)]
    for i,id in enumerate(ids):
        test_db.add(Asset(id=id,project_id=test_project.id,original_name=f'{i}.png',blob_path='fixture.png',content_hash=str(id),
            file_size=1,width=64,height=48,source_type='upload' if i==7 else 'model_crop',created_at=datetime(2026,11,4)))
    await test_db.flush();annotations={}
    for index,value in [(0,'白'),(1,'黑'),(2,None),(3,'missing'),(5,'白'),(6,'黑'),(7,'白')]:
        data={'labels':['car'],'attribute_schema':test_project.label_schema['object_attributes'],'attributes':{} if value=='missing' else {'color':{'value':value,'reason':'遮挡' if value is None else '可见'}}}
        row=Annotation(asset_id=ids[index],version=2 if index==6 else 1,is_latest=True,review_status='approved' if index==5 else 'pending',annotation_data=data)
        test_db.add(row);annotations[index]=row
    test_db.add(Annotation(asset_id=ids[6],version=1,is_latest=False,review_status='pending',annotation_data={
        'attribute_schema':test_project.label_schema['object_attributes'],'attributes':{'color':{'value':'白','reason':'历史'}}}))
    other=Project(tenant_id=test_project.tenant_id,project_name='other',task_type='caption');test_db.add(other);await test_db.flush()
    test_db.add(Asset(project_id=other.id,original_name='other.png',blob_path='fixture.png',content_hash='other',file_size=1,source_type='model_crop'))
    await test_db.commit()
    return ids,annotations


@pytest.mark.asyncio
@pytest.mark.parametrize('state,indices',[
    ('all',[6,5,4,3,2,1,0]),('analyzed',[6,5,2,1,0]),('known',[6,5,1,0]),('unknown',[2]),('missing',[4,3])])
async def test_attribute_states_filter_latest_annotation(client,auth_headers,test_project,attribute_assets,state,indices):
    ids,_=attribute_assets
    query={'project_id':str(test_project.id),'scope':'annotation','attribute_key':'color','attribute_state':state,'page_size':1}
    response=client.get('/api/assets',headers=auth_headers,params=query)
    assert response.status_code==200,response.text
    assert response.headers.get('X-Asset-Attribute-Filter')=='1'
    assert response.json()['total']==len(indices)
    assert response.json()['items'][0]['id']==str(ids[indices[0]])
    selected=client.get('/api/assets/selection',headers=auth_headers,params=query)
    assert selected.headers.get('X-Asset-Attribute-Filter')=='1'
    assert selected.json()['asset_ids']==[str(ids[i]) for i in indices]


@pytest.mark.asyncio
async def test_attribute_value_navigation_and_review_intersection(client,auth_headers,test_project,attribute_assets,test_db):
    ids,annotations=attribute_assets
    query={'project_id':str(test_project.id),'scope':'annotation','attribute_key':'color','attribute_value':'白','page_size':1}
    first=client.get('/api/assets',headers=auth_headers,params=query)
    assert first.json()['total']==2 and first.json()['items'][0]['id']==str(ids[5])
    assert client.get('/api/assets',headers=auth_headers,params={**query,'page':2}).json()['items'][0]['id']==str(ids[0])
    assert client.get('/api/assets',headers=auth_headers,params={**query,'attribute_value':'白色'}).json()['total']==0
    pending=client.get('/api/assets',headers=auth_headers,params={**query,'label':'car','review_status':'pending'})
    assert pending.json()['total']==1 and pending.json()['items'][0]['id']==str(ids[0])
    filters={'attribute_key':'color','attribute_value':'白'}
    response=client.get(f'/api/assets/{ids[5]}/neighbors',headers=auth_headers,params=filters)
    assert response.headers.get('X-Asset-Attribute-Filter')=='1'
    assert response.json()==dict(position=1,total=2,previous_id=None,next_id=str(ids[0]),matched=True)
    annotations[5].annotation_data={**annotations[5].annotation_data,'attributes':{'color':{'value':'黑','reason':'修正'}}};await test_db.commit()
    outside=client.get(f'/api/assets/{ids[5]}/neighbors',headers=auth_headers,params=filters)
    assert outside.json()==dict(position=0,total=1,previous_id=None,next_id=str(ids[0]),matched=False)
    empty=client.get(f'/api/assets/{ids[5]}/neighbors',headers=auth_headers,params={**filters,'attribute_value':'蓝'})
    assert empty.json()==dict(position=0,total=0,previous_id=None,next_id=None,matched=False)
    assert client.get(f'/api/assets/{ids[7]}/neighbors',headers=auth_headers,params=filters).status_code==409


@pytest.mark.asyncio
@pytest.mark.parametrize('filters,status',[
    ({'attribute_value':'白'},400),({'attribute_state':'unknown'},400),
    ({'attribute_key':'../x'},422),({'attribute_key':'color','attribute_state':'invalid'},422),
    ({'attribute_key':'color','attribute_value':'x'*501},422)])
async def test_invalid_attribute_navigation_filters_rejected(client,auth_headers,test_project,attribute_assets,filters,status):
    ids,_=attribute_assets
    for path,extra in [('/api/assets',{'project_id':str(test_project.id)}),('/api/assets/selection',{'project_id':str(test_project.id)}),(f'/api/assets/{ids[0]}/neighbors',{})]:
        response=client.get(path,headers=auth_headers,params={**extra,**filters})
        assert response.status_code==status,(path,response.text)
