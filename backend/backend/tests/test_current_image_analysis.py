import hashlib
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select,func,update,delete
from sqlalchemy.ext.asyncio import async_sessionmaker


@pytest.fixture
async def analysis_env(client,test_engine,test_db,test_user,test_project,test_asset,auth_headers):
    from models import Base,ModelService,User,ProjectMember,Tenant
    from services.auth import create_access_token
    from services.storage import get_storage
    async with test_engine.begin() as conn:await conn.run_sync(lambda c:Base.metadata.create_all(c,tables=[ModelService.__table__]))
    schema={'target_label':'car','fields':[{'key':'plate','name':'车牌','instruction':'清晰可见的车牌','options':[]},
        {'key':'color','name':'颜色','instruction':'车身颜色','options':['白','黑']}]}
    test_project.task_type='caption';test_project.label_schema={'object_attributes':schema}
    image=BytesIO();Image.new('RGB',(64,48),'red').save(image,'PNG');raw=image.getvalue()
    test_asset.content_hash=hashlib.sha256(raw).hexdigest();test_asset.file_size=len(raw);test_asset.format='PNG'
    await get_storage().save_file(BytesIO(raw),test_asset.blob_path)
    test_db.add(Tenant(id=2,tenant_name='other'))
    member=User(id=2,tenant_id=1,username='member',email='member@test.invalid',password_hash='unused',role='user',is_active=True)
    test_db.add(member);await test_db.flush();test_db.add(ProjectMember(project_id=test_project.id,user_id=2))
    models=[]
    for key,protocol,kind,tenant,enabled,offline in [('qwen','openai_chat','caption',1,True,False),
        ('offline','openai_chat','caption',1,True,True),('disabled','openai_chat','caption',1,False,False),
        ('foreign','openai_chat','caption',2,True,False),('detect','model_service','detection',1,True,False),
        ('jev','jev_decide','caption',1,True,False),('caption','model_service','caption',1,True,False)]:
        row=ModelService(key=key,name=key,protocol=protocol,kind=kind,tenant_id=tenant,enabled=enabled,
            base_url='http://model.test',endpoint='/infer',model_name='test',timeout_seconds=30,revision=1,
            connection_check={'status':'offline' if offline else 'online','checked_revision':1})
        test_db.add(row);models.append(row)
    await test_db.commit()
    return SimpleNamespace(client=client,db=test_db,sessions=async_sessionmaker(test_engine,expire_on_commit=False),
        project=test_project,asset=test_asset,models={m.key:m for m in models},schema=schema,raw=raw,admin=auth_headers,
        member={'Authorization':'Bearer '+create_access_token({'user_id':2})})


def url(s,suffix='analyze'):return f'/api/annotations/assets/{s.asset.id}/{suffix}'
def request(s,key='qwen',**changes):return dict(model_id=str(s.models[key].id),model_revision=1,**changes)
def values():return {'plate':{'value':'苏L12345','reason':'清晰可见'},'color':{'value':'黑','reason':'车身可见'}}


def test_model_list_is_scoped_and_does_not_expose_model_configuration(analysis_env):
    s=analysis_env;response=s.client.get(url(s,'analysis-models'),headers=s.member)
    assert response.status_code==200,response.text
    rows={m['name']:m for m in response.json()}
    assert set(rows)=={'qwen','offline'}
    assert rows['qwen']['unavailable_reason'] is None
    assert rows['offline']['unavailable_reason']
    assert all(set(row)=={'id','name','revision','unavailable_reason','prompt','supports_prompt'} for row in rows.values())


@pytest.mark.asyncio
async def test_analysis_returns_current_image_attributes_without_saving_or_reviewing(analysis_env,monkeypatch):
    from services import object_attributes
    from models import Annotation,AnnotationEvent
    s=analysis_env;calls=[]
    original={'attribute_schema':s.schema,'attributes':{**values(),'plate':{'value':'原车牌','reason':'人工填写'}},'description':'保留原标注'}
    existing=Annotation(asset_id=s.asset.id,annotation_data=original,version=1,is_latest=True,review_status='approved')
    s.db.add(existing);await s.db.commit()
    async def analyze(config,content,schema,client,prompt='',attribute_prompt=''):
        calls.append(content);assert config.timeout_seconds==30 and schema==s.schema
        return values()
    monkeypatch.setattr(object_attributes,'analyze',analyze)
    response=s.client.post(url(s),headers=s.member,json=request(s))
    assert response.status_code==200,response.text
    assert response.json()['asset_id']==str(s.asset.id) and response.json()['attributes']==values()
    assert response.json()['attribute_schema']==s.schema and response.json()['model_revision']==1
    assert calls==[s.raw]
    assert await s.db.scalar(select(func.count()).select_from(Annotation))==1
    saved=await s.db.get(Annotation,existing.id,populate_existing=True)
    assert saved.version==1 and saved.review_status=='approved' and saved.annotation_data==original
    assert await s.db.scalar(select(func.count()).select_from(AnnotationEvent))==0


@pytest.mark.parametrize('key,status',[('offline',400),('disabled',400),('foreign',404),('detect',400),('caption',400),('jev',400)])
def test_rejects_unavailable_or_incompatible_models(analysis_env,key,status):
    s=analysis_env;response=s.client.post(url(s),headers=s.member,json=request(s,key))
    assert response.status_code==status,response.text


def test_rejects_stale_revision_and_invalid_input(analysis_env):
    s=analysis_env
    assert s.client.post(url(s),headers=s.member,json={'model_id':str(s.models['qwen'].id),'model_revision':2}).status_code==409
    for revision in [0,True,1.5]:
        assert s.client.post(url(s),headers=s.member,json={'model_id':str(s.models['qwen'].id),'model_revision':revision}).status_code==422
    assert s.client.post(url(s),headers=s.member,json={'model_id':str(uuid4()),'model_revision':1}).status_code==404


@pytest.mark.asyncio
async def test_member_must_still_have_project_access(analysis_env):
    from models import ProjectMember
    s=analysis_env;await s.db.execute(delete(ProjectMember));await s.db.commit()
    assert s.client.get(url(s,'analysis-models'),headers=s.member).status_code==404
    assert s.client.post(url(s),headers=s.member,json=request(s)).status_code==404
    assert s.client.post(url(s),json=request(s)).status_code==401


@pytest.mark.asyncio
@pytest.mark.parametrize('change,status',[('project',409),('asset',409),('model',409),('membership',404)])
async def test_rechecks_context_after_inference(analysis_env,monkeypatch,change,status):
    from services import object_attributes
    from models import Project,Asset,ModelService,ProjectMember
    s=analysis_env
    async def analyze(*args,**kwargs):
        async with s.sessions() as db:
            if change=='project':await db.execute(update(Project).where(Project.id==s.project.id).values(label_schema={}))
            elif change=='asset':await db.execute(update(Asset).where(Asset.id==s.asset.id).values(content_hash='changed'))
            elif change=='model':await db.execute(update(ModelService).where(ModelService.id==s.models['qwen'].id).values(revision=2))
            else:await db.execute(delete(ProjectMember))
            await db.commit()
        return values()
    monkeypatch.setattr(object_attributes,'analyze',analyze)
    response=s.client.post(url(s),headers=s.member,json=request(s))
    assert response.status_code==status,response.text


def test_model_failure_keeps_existing_annotations_unchanged(analysis_env,monkeypatch):
    from services import object_attributes
    s=analysis_env
    async def fail(*args,**kwargs):raise HTTPException(504,'模型服务超时，请稍后重试')
    monkeypatch.setattr(object_attributes,'analyze',fail)
    response=s.client.post(url(s),headers=s.member,json=request(s))
    assert response.status_code==504 and response.json()['detail']=='模型服务超时，请稍后重试'


@pytest.mark.asyncio
async def test_without_attribute_configuration_returns_description(analysis_env,monkeypatch):
    from services import image_semantics
    s=analysis_env;s.project.label_schema={};await s.db.commit()
    async def caption(config,content,prompt,client):assert content==s.raw;return '一辆黑色汽车'
    monkeypatch.setattr(image_semantics,'caption',caption)
    response=s.client.post(url(s),headers=s.member,json=request(s))
    assert response.status_code==200,response.text
    assert response.json()['description']=='一辆黑色汽车' and 'attributes' not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize('task_type',['detection','classification','caption','vector'])
async def test_explicit_description_mode_works_for_all_workspaces(analysis_env,monkeypatch,task_type):
    from services import image_semantics
    s=analysis_env;s.project.task_type=task_type;await s.db.commit()
    async def caption(config,content,prompt,client):return '当前图片的描述'
    monkeypatch.setattr(image_semantics,'caption',caption)
    listing=s.client.get(url(s,'analysis-models'),params={'mode':'description'},headers=s.member)
    assert {row['name'] for row in listing.json()}=={'qwen','offline','caption'}
    response=s.client.post(url(s),headers=s.member,json={**request(s),'mode':'description'})
    assert response.status_code==200,response.text
    assert response.json()['description']=='当前图片的描述' and 'attributes' not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize('description',[None,123,[], 'x'*20001],ids=['null','number','array','too_long'])
async def test_detection_description_rejects_invalid_values(analysis_env,description):
    s=analysis_env;s.project.task_type='detection';s.project.label_schema={'labels':['car']};await s.db.commit()
    response=s.client.post('/api/annotations',headers=s.member,json={'asset_id':str(s.asset.id),'base_version':0,
        'annotation_data':{'boxes':[{'x':1,'y':1,'width':10,'height':10,'label':'car'}],'description':description}})
    assert response.status_code==400,response.text


@pytest.mark.asyncio
async def test_detection_description_is_versioned_with_boxes_and_metadata(analysis_env):
    s=analysis_env;s.project.task_type='detection';s.project.label_schema={'labels':['car']};await s.db.commit()
    box={'x':1,'y':2,'width':10,'height':20,'label':'car'}
    data={'boxes':[box],'description':'核对后的图片描述','extra_metadata':'保留'}
    first=s.client.post('/api/annotations',headers=s.member,json={'asset_id':str(s.asset.id),'base_version':0,'annotation_data':data})
    assert first.status_code==201,first.text
    second=s.client.put('/api/annotations/'+first.json()['id'],headers=s.member,
        json={'base_version':1,'annotation_data':{**data,'boxes':[{**box,'x':3}]}})
    assert second.status_code==200,second.text
    assert second.json()['annotation_data']=={**data,'boxes':[{**box,'x':3}]}
    assert second.json()['version']==2 and second.json()['review_status']=='pending'
    assert s.client.get('/api/annotations/'+first.json()['id'],headers=s.member).json()['annotation_data']==data
