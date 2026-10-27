from datetime import datetime
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select
from models import Asset, Project, Tenant


@pytest.mark.asyncio
async def test_review_filter_latest_versions_intersects_labels_and_preserves_navigation(client,auth_headers,test_db,test_project):
    from models import Annotation
    ids=[UUID(f'cccccccc-0000-4000-8000-{i+1:012x}') for i in range(6)]
    for id in ids:
        test_db.add(Asset(id=id,project_id=test_project.id,original_name=f'{id}.jpg',blob_path='fixture.jpg',content_hash=str(id),
            file_size=1,width=64,height=48,source_type='upload',created_at=datetime(2026,10,15)))
    await test_db.flush()
    for index,status,label in [(0,'pending','person'),(1,'approved','person'),(2,'rejected','person'),(3,'pending','bike'),(5,'approved',None)]:
        test_db.add(Annotation(asset_id=ids[index],version=2 if index==3 else 1,is_latest=True,review_status=status,
            annotation_data={'boxes':[] if label is None else [dict(label=label,x=1,y=2,width=3,height=4)]}))
    test_db.add(Annotation(asset_id=ids[3],version=1,is_latest=False,review_status='approved',annotation_data={'boxes':[{'label':'person'}]}))
    await test_db.commit()
    query=dict(project_id=str(test_project.id),scope='annotation',review_status='pending',page_size=1)
    response=client.get('/api/assets',headers=auth_headers,params=query)
    assert response.status_code==200 and response.headers.get('X-Annotation-Review-Filter')=='1'
    assert response.json()['total']==2 and response.json()['items'][0]['id']==str(ids[3])
    assert client.get('/api/assets',headers=auth_headers,params={**query,'page':2}).json()['items'][0]['id']==str(ids[0])
    selected=client.get('/api/assets/selection',headers=auth_headers,params=query)
    assert selected.headers.get('X-Annotation-Review-Filter')=='1' and selected.json()['asset_ids']==[str(ids[3]),str(ids[0])]
    matches=client.get('/api/assets',headers=auth_headers,params={**query,'label':'person'}).json()
    assert matches['total']==1 and matches['items'][0]['id']==str(ids[0])
    assert client.get('/api/assets',headers=auth_headers,params={**query,'review_status':'approved'}).json()['total']==2
    url='/api/assets/'+str(ids[3])+'/neighbors'
    neighbor=client.get(url,headers=auth_headers,params={'review_status':'pending'})
    assert neighbor.headers.get('X-Annotation-Review-Filter')=='1'
    assert neighbor.json()==dict(position=1,total=2,previous_id=None,next_id=str(ids[0]),matched=True)
    outside=client.get('/api/assets/'+str(ids[1])+'/neighbors',headers=auth_headers,params={'review_status':'pending'}).json()
    assert outside==dict(position=0,total=2,previous_id=str(ids[3]),next_id=str(ids[0]),matched=False)
    empty=client.get(url,headers=auth_headers,params={'review_status':'pending','label':'absent'}).json()
    assert empty==dict(position=0,total=0,previous_id=None,next_id=None,matched=False)
    for endpoint,extra in [('/api/assets',{'project_id':str(test_project.id)}),('/api/assets/selection',{'project_id':str(test_project.id)}),(url,{})]:
        assert client.get(endpoint,headers=auth_headers,params={**extra,'review_status':'invalid'}).status_code==422


@pytest.mark.asyncio
async def test_review_filter_respects_crop_scope_and_current_review_changes(client,auth_headers,test_db,test_project,test_asset):
    from models import Annotation
    test_project.label_schema={'annotation_scope':'crops','labels':['person']}
    crop=Asset(project_id=test_project.id,original_name='crop.jpg',blob_path='crop.jpg',content_hash='crop',file_size=1,width=64,height=48,source_type='model_crop')
    test_db.add(crop);await test_db.flush()
    rows=[]
    for asset in (test_asset,crop):
        row=Annotation(asset_id=asset.id,version=1,is_latest=True,review_status='pending',annotation_data={'boxes':[]})
        test_db.add(row);rows.append(row)
    await test_db.commit()
    query=dict(project_id=str(test_project.id),scope='annotation',review_status='pending')
    page=client.get('/api/assets',headers=auth_headers,params=query).json()
    assert page['total']==1 and page['items'][0]['id']==str(crop.id)
    assert client.get('/api/assets/'+str(test_asset.id)+'/neighbors',headers=auth_headers,params={'review_status':'pending'}).status_code==409
    assert client.post('/api/annotations/'+str(rows[1].id)+'/review',headers=auth_headers,json={'approved':True}).status_code==200
    neighbor=client.get('/api/assets/'+str(crop.id)+'/neighbors',headers=auth_headers,params={'review_status':'pending'}).json()
    assert neighbor==dict(position=0,total=0,previous_id=None,next_id=None,matched=False)


@pytest.mark.asyncio
async def test_filename_search_filters_all_pages_selection_and_source(client,auth_headers,test_db,test_project):
    names=['Fire_cam_01.JPG','fire_cam_02.jpg','fireXcam_03.jpg','烟火%_现场.jpg','烟火XX现场.jpg']
    ids=[]
    for index,name in enumerate(names):
        asset=Asset(project_id=test_project.id,original_name=name,blob_path='test.jpg',content_hash=str(index),
            file_size=1,source_type='model_crop' if index==1 else 'upload',created_at=datetime(2026,10,15))
        test_db.add(asset);await test_db.flush();ids.append(str(asset.id))
    other=Project(tenant_id=test_project.tenant_id,project_name='other',task_type='detection')
    test_db.add(other);await test_db.flush()
    test_db.add(Asset(project_id=other.id,original_name=names[0],blob_path='other.jpg',content_hash='other',file_size=1,source_type='upload'))
    await test_db.commit()
    query=dict(project_id=str(test_project.id),q=' FIRE_CAM_ ',page_size=1)
    response=client.get('/api/assets',headers=auth_headers,params=query)
    assert response.status_code==200,response.text
    assert response.headers['X-Asset-Filename-Filter']=='1'
    assert response.json()['total']==2
    first=response.json()['items'][0]['id']
    second=client.get('/api/assets',headers=auth_headers,params={**query,'page':2}).json()['items'][0]['id']
    assert {first,second}==set(ids[:2])
    selected=client.get('/api/assets/selection',headers=auth_headers,params=query)
    assert selected.headers['X-Asset-Filename-Filter']=='1'
    assert selected.json()['asset_ids']==[first,second]
    for kind,expected in [('originals',[ids[0]]),('crops',[ids[1]])]:
        page=client.get('/api/assets',headers=auth_headers,params={**query,'image_kind':kind}).json()
        assert page['total']==1 and [row['id'] for row in page['items']]==expected
    literal=client.get('/api/assets',headers=auth_headers,params={**query,'q':'%_'}).json()
    assert literal['total']==1 and literal['items'][0]['id']==ids[3]
    assert client.get('/api/assets',headers=auth_headers,params={**query,'q':'not-found'}).json()['total']==0
    assert client.get('/api/assets',headers=auth_headers,params={**query,'q':'  '}).json()['total']==5
    assert client.get('/api/assets',headers=auth_headers,params={**query,'q':'x'*256}).status_code==422


@pytest.mark.asyncio
async def test_label_filter_uses_latest_annotation_for_list_selection_and_neighbors(client,auth_headers,test_db,test_project):
    from models import Annotation
    ids=[UUID(f'bbbbbbbb-0000-4000-8000-{i+1:012x}') for i in range(5)]
    test_project.label_schema={'labels':['fire','smoke','star']}
    for id in ids:
        test_db.add(Asset(id=id,project_id=test_project.id,original_name=f'{id}.jpg',blob_path='test.jpg',
            content_hash=str(id),file_size=1,source_type='upload',created_at=datetime(2026,10,15)))
    await test_db.flush()
    for index,label in [(0,'fire'),(1,'smoke'),(2,'fire')]:
        test_db.add(Annotation(asset_id=ids[index],annotation_data={'boxes':[{'label':label}]},version=1,is_latest=True))
    test_db.add_all([Annotation(asset_id=ids[3],annotation_data={'boxes':[{'label':'fire'}]},version=1,is_latest=False),
        Annotation(asset_id=ids[3],annotation_data={'boxes':[{'label':'smoke'}]},version=2,is_latest=True),
        Annotation(asset_id=ids[4],annotation_data={'boxes':[]},version=1,is_latest=True)])
    await test_db.commit()
    query=dict(project_id=str(test_project.id),scope='annotation',label='fire',page_size=1)
    response=client.get('/api/assets',headers=auth_headers,params=query)
    assert response.headers['X-Annotation-Label-Filter']=='1'
    page=response.json()
    assert page['total']==2 and page['items'][0]['id']==str(ids[2]),page
    page2=client.get('/api/assets',headers=auth_headers,params={**query,'page':2}).json()
    assert page2['items'][0]['id']==str(ids[0])
    selected=client.get('/api/assets/selection',headers=auth_headers,params=query).json()
    assert selected['asset_ids']==[str(ids[2]),str(ids[0])]
    neighbors=client.get('/api/assets/'+str(ids[2])+'/neighbors',headers=auth_headers,params={'label':'fire'}).json()
    assert neighbors==dict(position=1,total=2,previous_id=None,next_id=str(ids[0]))
    outside=client.get('/api/assets/'+str(ids[1])+'/neighbors',headers=auth_headers,params={'label':'fire'})
    assert outside.status_code==200,outside.text
    assert outside.json()==dict(position=0,total=2,previous_id=str(ids[2]),next_id=str(ids[0]),matched=False)
    empty=client.get('/api/assets/'+str(ids[1])+'/neighbors',headers=auth_headers,params={'label':'star'})
    assert empty.status_code==200 and empty.json()==dict(position=0,total=0,previous_id=None,next_id=None,matched=False)
    assert client.get('/api/assets',headers=auth_headers,params={**query,'label':'star'}).json()['total']==0
    assert client.get('/api/assets',headers=auth_headers,params={k:v for k,v in query.items() if k!='label'}).json()['total']==5
    latest=await test_db.scalar(select(Annotation).where(Annotation.asset_id==ids[2],Annotation.is_latest.is_(True)))
    latest.is_latest=False
    test_db.add(Annotation(asset_id=ids[2],annotation_data={'boxes':[]},version=2,is_latest=True))
    await test_db.commit()
    after_save=client.get('/api/assets/'+str(ids[2])+'/neighbors',headers=auth_headers,params={'label':'fire'})
    assert after_save.status_code==200 and after_save.json()==dict(position=0,total=1,previous_id=None,next_id=str(ids[0]),matched=False)


@pytest.mark.asyncio
async def test_tied_asset_paging_and_neighbors(client,auth_headers,test_db,test_project):
    ids=[UUID(f'aaaaaaaa-0000-4000-8000-{i+1:012x}') for i in range(205)]
    for id in ids:
        test_db.add(Asset(id=id,project_id=test_project.id,original_name=f'{id}.jpg',blob_path='test.jpg',
            content_hash=str(id),file_size=1,source_type='upload',created_at=datetime(2026,10,15)))
    test_db.add(Tenant(id=99,tenant_name='foreign'));await test_db.flush()
    foreign=Project(tenant_id=99,project_name='foreign',task_type='detection');test_db.add(foreign);await test_db.flush()
    foreign_asset=Asset(project_id=foreign.id,original_name='foreign.jpg',blob_path='foreign.jpg',
        content_hash='foreign',file_size=1,source_type='upload');test_db.add(foreign_asset);await test_db.flush()
    foreign_id=str(foreign_asset.id);await test_db.commit()
    actual=[]
    for page in range(1,4):
        result=client.get('/api/assets',headers=auth_headers,params={'project_id':str(test_project.id),'page':page,'page_size':100})
        assert result.status_code==200,result.text
        actual.extend(item['id'] for item in result.json()['items'])
    assert actual==list(map(str,reversed(ids)))
    for index in (0,99,100,204):
        response=client.get(f'/api/assets/{actual[index]}/neighbors',headers=auth_headers)
        assert response.status_code==200,response.text
        assert response.json()=={'position':index+1,'total':205,
            'previous_id':actual[index-1] if index else None,'next_id':actual[index+1] if index<204 else None}
    assert client.get(f'/api/assets/{actual[0]}/neighbors').status_code==401
    for id in (str(uuid4()),foreign_id):
        assert client.get(f'/api/assets/{id}/neighbors',headers=auth_headers).status_code==404
