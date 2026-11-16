import json
from io import BytesIO
from uuid import UUID, uuid4
import pytest
from models import Asset, Project, Tenant, User, TaskType, ProjectStatus
from services.auth import create_access_token, get_password_hash
from services.model_preview import MAX_IMAGES
from models.validation import ValidationSet, ValidationRun
from services.storage import get_storage
from tests.test_model_preview import pclient, detector, validation_tables


def post(client, headers, config, content=None):
    return client.post('/api/model-previews', headers=headers, data={'request':json.dumps(config)}, files={'files':('test.jpg',content,'image/jpeg')} if content is not None else None)

@pytest.mark.asyncio
async def test_project_images_copy_filter_and_delete(pclient, detector, auth_headers, test_db, test_project, test_asset, mock_image_data):
    content=mock_image_data.getvalue();storage=get_storage();await storage.save_file(BytesIO(content),test_asset.blob_path)
    request={'model_id':str(detector.id),'project_id':str(test_project.id),'asset_ids':[str(test_asset.id)]}
    response=pclient.post('/api/model-previews',headers=auth_headers,files={'request':(None,json.dumps(request))});assert response.status_code==201,response.text
    run=response.json();dataset=await test_db.get(ValidationSet,UUID(run['set_id']))
    assert dataset.project_id==test_project.id and dataset.source['kind']=='upload_preview'
    copy=dataset.samples[0]['blob_path'];assert copy!=test_asset.blob_path
    with await storage.get_file(copy) as file:assert file.read()==content
    assert pclient.get('/api/model-previews',headers=auth_headers,params={'project_id':str(test_project.id)}).json()['total']==1
    assert pclient.get('/api/model-previews',headers=auth_headers,params={'project_id':str(uuid4())}).status_code==404
    other=Project(tenant_id=test_project.tenant_id,project_name='other',task_type=TaskType.DETECTION,status=ProjectStatus.ACTIVE)
    test_db.add(other);await test_db.commit()
    other_response=post(pclient,auth_headers,{'model_id':str(detector.id),'project_id':str(other.id)},content)
    assert other_response.status_code==201,other_response.text
    assert pclient.get('/api/model-previews',headers=auth_headers,params={'project_id':str(test_project.id)}).json()['total']==1
    assert pclient.get('/api/model-previews',headers=auth_headers,params={'project_id':str(other.id)}).json()['items'][0]['id']==other_response.json()['id']
    await test_db.commit()
    assert pclient.delete('/api/model-previews/'+run['id'],headers=auth_headers).status_code==204
    assert await storage.file_exists(test_asset.blob_path) and not await storage.file_exists(copy)
    with await storage.get_file(test_asset.blob_path) as file:assert file.read()==content
    response=post(pclient,auth_headers,{'model_id':str(detector.id),'project_id':str(test_project.id)},content)
    assert response.status_code==201,response.text
    saved=await test_db.get(ValidationSet,UUID(response.json()['set_id']));assert saved.project_id==test_project.id

@pytest.mark.asyncio
async def test_project_input_rejects_invalid_and_foreign(pclient,detector,auth_headers,test_db,test_project,test_asset,mock_image_data):
    other=Project(tenant_id=test_project.tenant_id,project_name='other',task_type=TaskType.DETECTION,status=ProjectStatus.ACTIVE)
    test_db.add(other);await test_db.flush()
    foreign=Asset(project_id=other.id,original_name='other.jpg',blob_path='other.jpg',content_hash='x',file_size=1,source_type='upload',width=1,height=1,format='JPEG',annotation_status='pending')
    test_db.add(foreign);await test_db.commit()
    base={'model_id':str(detector.id),'project_id':str(test_project.id),'asset_ids':[str(test_asset.id)]}
    for change in ({'project_id':None},{'asset_ids':[]},{'asset_ids':[str(test_asset.id)]*2},{'asset_ids':[str(uuid4()) for _ in range(MAX_IMAGES+1)]},{'asset_ids':[str(test_asset.id),str(foreign.id)]},{'video_interval':1}):
        assert post(pclient,auth_headers,{**base,**change}).status_code in (400,404,422)
    assert post(pclient,auth_headers,base,mock_image_data.getvalue()).status_code in (400,422)
    test_project.task_type=TaskType.CAPTION;await test_db.commit();assert post(pclient,auth_headers,base).status_code==400
    tenant=Tenant(id=2,tenant_name='foreign');test_db.add(tenant);await test_db.flush()
    foreign_project=Project(tenant_id=2,project_name='foreign',task_type=TaskType.DETECTION,status=ProjectStatus.ACTIVE);test_db.add(foreign_project);await test_db.commit()
    assert post(pclient,auth_headers,{**base,'project_id':str(foreign_project.id)}).status_code==404
    assert pclient.get('/api/validation/runs',headers=auth_headers,params={'project_id':str(foreign_project.id)}).status_code==404

@pytest.mark.asyncio
async def test_validation_runs_project_filter(pclient,detector,auth_headers,test_db,test_project,test_user):
    other=Project(tenant_id=test_user.tenant_id,project_name='other',task_type=TaskType.DETECTION,status=ProjectStatus.ACTIVE);test_db.add(other);await test_db.flush()
    for project in (test_project,other):
        dataset=ValidationSet(tenant_id=test_user.tenant_id,user_id=test_user.id,project_id=project.id,name='set',labels=[],samples=[],source={'kind':'annotation'},total=0);test_db.add(dataset);await test_db.flush()
        test_db.add(ValidationRun(set_id=dataset.id,tenant_id=test_user.tenant_id,user_id=test_user.id,name=str(project.id),model_snapshot={'preview':False,'id':'fixture','revision':1,'name':'fixture'},config={},total=0))
    await test_db.commit()
    result=pclient.get('/api/validation/runs',headers=auth_headers,params={'project_id':str(test_project.id)})
    assert result.status_code==200 and result.json()['total']==1
    assert result.json()['items'][0]['name']==str(test_project.id)




@pytest.mark.asyncio
async def test_project_permissions_reject_unassigned_user(pclient,detector,test_db,test_project,test_asset):
    user=User(id=2,tenant_id=test_project.tenant_id,username='ordinary',email='ordinary@test.invalid',password_hash=get_password_hash('fixture'),role='user',is_active=True)
    test_db.add(user);await test_db.commit()
    headers={'Authorization':'Bearer '+create_access_token({'user_id':user.id,'tenant_id':user.tenant_id,'username':user.username})}
    request={'model_id':str(detector.id),'project_id':str(test_project.id),'asset_ids':[str(test_asset.id)]}
    assert post(pclient,headers,request).status_code==403
    assert pclient.get('/api/model-previews',headers=headers,params={'project_id':str(test_project.id)}).status_code==403
    assert pclient.get('/api/validation/runs',headers=headers,params={'project_id':str(test_project.id)}).status_code==403

@pytest.mark.asyncio
@pytest.mark.parametrize(('kind','project_type'), [('caption',TaskType.CAPTION),('embedding',TaskType.VECTOR)])
async def test_multimodal_project_image_order_and_project_filter(pclient,detector,auth_headers,test_db,test_project,test_asset,mock_image_data,kind,project_type):
    from PIL import Image
    storage=get_storage();first=mock_image_data.getvalue()
    stream=BytesIO();Image.new('RGB',(64,48),'blue').save(stream,format='JPEG');second=stream.getvalue()
    await storage.save_file(BytesIO(first),test_asset.blob_path);await storage.save_file(BytesIO(second),'second.jpg')
    other_asset=Asset(project_id=test_project.id,original_name='second.jpg',blob_path='second.jpg',content_hash='second',file_size=len(second),source_type='upload',width=64,height=48,format='JPEG',annotation_status='pending')
    test_project.task_type=project_type;detector.kind=kind;test_db.add(other_asset)
    if kind=='embedding':detector.protocol='openai_embedding';detector.endpoint='/embeddings';detector.model_name='fixture'
    other_project=Project(tenant_id=test_project.tenant_id,project_name='other multimodal',task_type=project_type,status=ProjectStatus.ACTIVE);test_db.add(other_project)
    await test_db.commit()
    request={'model_id':str(detector.id),'task_type':kind,'project_id':str(test_project.id),'asset_ids':[str(other_asset.id),str(test_asset.id)]}
    if kind=='embedding':request['query_image_index']=1
    response=pclient.post('/api/model-previews',headers=auth_headers,files={'request':(None,json.dumps(request))})
    assert response.status_code==201,response.text
    dataset=await test_db.get(ValidationSet,UUID(response.json()['set_id']))
    assert dataset.project_id==test_project.id
    assert [sample['original_name'] for sample in dataset.samples]==['second.jpg',test_asset.original_name]
    for sample,expected in zip(dataset.samples,[second,first]):
        with await storage.get_file(sample['blob_path']) as image:assert image.read()==expected
    if kind=='embedding':
        query=dataset.source['queries'][0];assert query['asset_id']==dataset.samples[1]['asset_id']
        assert query['content_hash']==dataset.samples[1]['content_hash']
    await test_db.commit()
    other_request={**request,'project_id':str(other_project.id),'asset_ids':None}
    if kind=='embedding':other_request['query_image_index']=0
    other_response=post(pclient,auth_headers,other_request,first)
    assert other_response.status_code==201,other_response.text
    for project,run in ((test_project,response),(other_project,other_response)):
        listing=pclient.get('/api/model-previews',headers=auth_headers,params={'project_id':str(project.id),'task_type':kind})
        assert listing.status_code==200 and [item['id'] for item in listing.json()['items']]==[run.json()['id']]
        wrong=pclient.get('/api/model-previews',headers=auth_headers,params={'project_id':str(project.id),'task_type':'detection'})
        assert wrong.status_code==200 and wrong.json()['total']==0
