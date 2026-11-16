import hashlib,json
from io import BytesIO
import pytest
from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from fastapi import HTTPException
from tests.test_automation import ready,automation_tables


async def reference(db,project,user):
    from models import Asset
    from services.annotations import save_annotation
    from services.storage import get_storage
    raw=BytesIO();Image.new('RGB',(64,48),'blue').save(raw,'PNG');content=raw.getvalue()
    asset=Asset(project_id=project.id,original_name='manual-box-example.png',blob_path='box-example.png',content_hash=hashlib.sha256(content).hexdigest(),
        width=64,height=48,file_size=len(content),source_type='upload',format='PNG')
    db.add(asset);await db.flush();await get_storage().save_file(BytesIO(content),asset.blob_path)
    ann=await save_annotation(db,asset,{'boxes':[{'label':'person','x':16,'y':12,'width':32,'height':24}],'negative':False},0,user.id)
    await db.commit();return asset,ann


@pytest.mark.asyncio
async def test_detection_example_drives_llm_boxes_and_stays_pending_human(client,auth_headers,ready,test_db,test_engine,test_project,test_asset,test_user,monkeypatch):
    from services import automation,detection_preannotation
    ref,ann=await reference(test_db,test_project,test_user)
    request={**ready,'detector_id':None,'preannotation_model_id':ready['reviewer_id'],'reviewer_id':None,
        'example_asset_id':str(ref.id),'example_annotation_version':ann.version,'config':{'auto_review':False}}
    response=client.post('/api/automation/jobs',headers=auth_headers,json=request)
    assert response.status_code==201,response.text
    calls=[]
    async def provider(*args,**kwargs):
        messages=kwargs['json']['messages'];calls.append(messages)
        assert [m['role'] for m in messages]==['user','assistant','user']
        answer=json.loads(messages[1]['content'])
        assert answer['boxes'][0]['bbox']==[250,250,750,750]
        return {'choices':[{'message':{'content':json.dumps({'boxes':[{'label':'person','bbox':[0,0,500,1000]}],'uncertain':False,'reason':'可见人员'})}}]}
    monkeypatch.setattr(detection_preannotation,'response_json',provider)
    await automation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    items=client.get('/api/automation/jobs/'+response.json()['id']+'/items',headers=auth_headers).json()['items']
    assert len(calls)==1 and items[0]['status']=='needs_review'
    assert items[0]['result']['boxes']==[{'label':'person','x':0,'y':0,'width':32,'height':48}]
    assert 'score' not in items[0]['result']['boxes'][0]
    rows=client.get('/api/annotations',headers=auth_headers,params={'asset_id':str(test_asset.id)}).json()['items']
    assert rows[0]['review_status']=='pending' and rows[0]['annotation_source']=='model'
    examples=client.get(f'/api/projects/{test_project.id}/preannotation-examples',headers=auth_headers).json()
    assert examples['total']==1 and examples['items'][0]['boxes'][0]['label']=='person'


@pytest.mark.asyncio
async def test_changed_detection_example_blocks_late_model_results(client,auth_headers,ready,test_db,test_engine,test_project,test_user,monkeypatch):
    from services import automation,detection_preannotation
    from services.annotations import save_annotation
    from models import Asset
    ref,ann=await reference(test_db,test_project,test_user)
    response=client.post('/api/automation/jobs',headers=auth_headers,json={**ready,'detector_id':None,
        'preannotation_model_id':ready['reviewer_id'],'example_asset_id':str(ref.id),'example_annotation_version':1,'config':{'auto_review':False}})
    assert response.status_code==201,response.text
    sessions=async_sessionmaker(test_engine,expire_on_commit=False)
    async def provider(*args,**kwargs):
        async with sessions() as db:
            asset=await db.get(Asset,ref.id)
            await save_annotation(db,asset,{'boxes':[],'negative':True},1,test_user.id);await db.commit()
        return {'choices':[{'message':{'content':'{"boxes":[],"uncertain":true,"reason":"看不清"}'}}]}
    monkeypatch.setattr(detection_preannotation,'response_json',provider)
    await automation.process_one(sessions)
    items=client.get('/api/automation/jobs/'+response.json()['id']+'/items',headers=auth_headers).json()['items']
    assert items[0]['status']=='skipped' and '范例' in items[0]['reason']


@pytest.mark.parametrize('box',[
    {'label':'other','bbox':[0,0,500,500]}, {'label':'person','bbox':[-1,0,500,500]},
    {'label':'person','bbox':[0,0,1001,500]}, {'label':'person','bbox':[500,0,100,500]},
    {'label':'person','bbox':[False,0,500,500]}, {'label':'person','bbox':[0,0,'500',500]},
])
def test_invalid_llm_detection_boxes_are_rejected(box):
    from services.detection_preannotation import parse_detection
    with pytest.raises(HTTPException):parse_detection(json.dumps({'boxes':[box],'uncertain':False,'reason':'test'}),['person'],64,48)


@pytest.mark.asyncio
async def test_detection_example_request_cannot_bypass_model_and_snapshot_validation(client,auth_headers,ready,test_db,test_project,test_user):
    ref,ann=await reference(test_db,test_project,test_user)
    base={**ready,'example_asset_id':str(ref.id),'example_annotation_version':1,'config':{'auto_review':False}}
    assert client.post('/api/automation/jobs',headers=auth_headers,json=base).status_code==422
    llm={**base,'detector_id':None,'preannotation_model_id':ready['reviewer_id']}
    assert client.post('/api/automation/jobs',headers=auth_headers,json={**llm,'example_annotation_version':999}).status_code==409
    assert client.post('/api/automation/jobs',headers=auth_headers,json={**llm,'config':{'auto_review':False,'example':{'boxes':[]}}}).status_code==422
    response=client.post('/api/automation/jobs',headers=auth_headers,json={**llm,'reviewer_id':None,'config':{}})
    assert response.status_code==201,response.text
    assert response.json()['config']['auto_review'] is False


@pytest.mark.asyncio
async def test_llm_example_respects_selected_project_labels(client,auth_headers,ready,test_db,test_engine,test_project,test_user,monkeypatch):
    from services import automation,detection_preannotation
    test_project.label_schema={'labels':['person','bike']};await test_db.commit()
    ref,ann=await reference(test_db,test_project,test_user)
    base={**ready,'detector_id':None,'preannotation_model_id':ready['reviewer_id'],'example_asset_id':str(ref.id),'example_annotation_version':ann.version}
    assert client.post('/api/automation/jobs',headers=auth_headers,json={**base,'config':{'labels':['unknown']}}).status_code==400
    response=client.post('/api/automation/jobs',headers=auth_headers,json={**base,'config':{'labels':['person']}})
    assert response.status_code==201,response.text
    async def provider(*args,**kwargs):
        assert 'bike' not in kwargs['json']['messages'][-1]['content'][0]['text']
        return {'choices':[{'message':{'content':'{"boxes":[],"uncertain":true,"reason":"看不清"}'}}]}
    monkeypatch.setattr(detection_preannotation,'response_json',provider)
    await automation.process_one(async_sessionmaker(test_engine,expire_on_commit=False))
    items=client.get('/api/automation/jobs/'+response.json()['id']+'/items',headers=auth_headers).json()['items']
    assert items[0]['status']=='needs_review'
