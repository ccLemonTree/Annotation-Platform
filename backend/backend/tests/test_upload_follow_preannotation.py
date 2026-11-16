import hashlib
import json
from io import BytesIO
from uuid import UUID
import httpx
import pytest
from PIL import Image
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from models import (
    Annotation, Asset, AutomationItem, AutomationJob, Base, ImageCollection, ImageUpload,
    ImageUploadItem, LibraryImage, ModelService, ProjectWorkItem, ProjectWorkJob,
)
from tests.test_project_work import work_env


@pytest.mark.parametrize('mode', ['detection', 'detect_attributes', 'attributes', 'caption',
    'crop_detection', 'crop_caption', 'crop_vector', 'index', 'quality', 'review'])
@pytest.mark.asyncio
async def test_only_new_uploads_follow_enabled_job(client, test_db, test_engine, test_project, test_asset, auth_headers, mode):
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[
            ModelService.__table__, ProjectWorkJob.__table__, ProjectWorkItem.__table__]))
    project = test_project
    schema = {'target_label':'person','fields':[{'key':'color','name':'颜色','options':[],'instruction':''}]}
    is_crop = mode.startswith('crop_')
    project.task_type = mode.removeprefix('crop_') if is_crop else (
        'detection' if mode in ('detection','review','quality') else 'vector' if mode=='index' else 'caption')
    project.label_schema = {'labels':['person']} if project.task_type=='detection' else {'object_attributes':schema}
    if is_crop:project.label_schema = {**project.label_schema,'annotation_scope':'crops'}
    model = ModelService(tenant_id=1, key='det', name='det', kind='detection', protocol='model_service',
                         base_url='http://fixture.test', endpoint='/detect')
    llm = ModelService(tenant_id=1, key='llm', name='llm', kind='caption', protocol='openai_chat',
                       base_url='http://fixture.test/v1', endpoint='/chat/completions', model_name='fixture')
    group = ImageCollection(tenant_id=1, name='fixture')
    embedder = ModelService(tenant_id=1,key='embedder',name='embedder',kind='embedding',protocol='openai_embedding',
        base_url='http://fixture.test/v1',endpoint='/embeddings',model_name='fixture')
    test_db.add_all([model,llm,group,embedder])
    await test_db.flush()
    project_path=f'/api/projects/{project.id}/work/jobs'
    body={'asset_ids':[str(test_asset.id)],'follow_uploads':True}
    if mode in ('detection','review'):
        path='/api/automation/jobs'
        if mode=='review':
            test_db.add(Annotation(asset_id=test_asset.id,annotation_data={'boxes':[]},review_status='pending'))
            body.update(project_id=str(project.id),kind='review',reviewer_id=str(llm.id))
        else:
            body.update(project_id=str(project.id),kind='preannotate',detector_id=str(model.id),config={'auto_review':False})
        job_type = AutomationJob
        item_type = AutomationItem
    else:
        path=project_path
        selected_model=model if is_crop or mode=='detect_attributes' else embedder if mode=='index' else llm
        body.update(operation='crop' if is_crop else mode,model_id=str(selected_model.id))
        if mode=='detect_attributes':body['attribute_model_id']=str(llm.id)
        if mode=='quality':body['prompt']='检查图片是否清晰'
        job_type = ProjectWorkJob
        item_type = ProjectWorkItem
    async def add_file(name):
        image = LibraryImage(tenant_id=1, original_name=name+'.jpg', blob_path=name+'.jpg', content_hash=name,
            file_size=20, width=800, height=600, source_type='upload')
        if mode in ('review','quality') and name!='second':
            image.imported_annotation=dict(names=['person'],boxes=[dict(label='person',x=10,y=10,width=20,height=20)],
                imported_by=1,imported_at='2026-11-09T00:00:00')
        receipt = ImageUpload(tenant_id=1, user_id=1, collection_id=group.id, project_id=project.id, status='completed',
            filename=image.original_name, size=20, resume_key=name, chunk_hashes=['a'*64], min_long_edge=0)
        test_db.add_all([image,receipt])
        await test_db.flush()
        test_db.add(ImageUploadItem(upload_id=receipt.id,image_id=image.id))
        return str(image.id),str(receipt.id)
    image,receipt = await add_file('first')
    other,other_receipt = await add_file('second')
    last,last_receipt = await add_file('third')
    await test_db.commit()
    created=client.post(path,headers=auth_headers,json=body)
    assert created.status_code==201,created.text
    assert created.json()['follow_uploads'] is True
    assert client.post(path,headers=auth_headers,json=body).status_code==409
    base=path+'/'+created.json()['id']
    def toggle(enabled):
        response=client.put(base+'/follow-uploads',headers=auth_headers,json={'enabled':enabled})
        assert response.status_code==200,response.text
        assert response.json()['follow_uploads'] is enabled
    def attach(image_id,upload_id=None):
        return client.post('/api/images/associate',headers=auth_headers,json={'project_id':str(project.id),
            'image_ids':[image_id], 'include_imported_annotations':mode in ('review','quality'),
            **({'upload_id':upload_id} if upload_id else {})})
    assert attach(other,receipt).status_code==409
    first=attach(image,receipt)
    assert first.status_code==200,first.text
    assert first.json()['preannotation_queued']==1
    assert attach(image,receipt).json()['preannotation_queued']==0
    if mode in ('review','quality'):
        async with AsyncSession(test_engine) as db:
            item=await db.scalar(select(item_type).where(item_type.asset_id==UUID(first.json()['asset_ids'][0])))
            if mode=='review':
                assert item.annotation_id and item.annotation_version==1 and item.review_baseline['status']=='pending'
            else:
                assert item.base_version==1 and item.result['quality_event_count']>0
    if mode=='crop_detection':
        detection=dict(asset_ids=[str(test_asset.id)],project_id=str(project.id),kind='preannotate',
            detector_id=str(model.id),config={'auto_review':False},follow_uploads=True)
        project.label_schema={'labels':['person']}
        await test_db.commit()
        assert client.post('/api/automation/jobs',headers=auth_headers,json=detection).status_code==409
    if mode=='review':assert attach(other,other_receipt).json()['preannotation_queued']==0
    assert attach(other).status_code==200
    assert attach(other,other_receipt).json()['preannotation_queued']==0
    toggle(False)
    assert attach(last,last_receipt).json()['preannotation_queued']==0
    toggle(True)
    assert await test_db.scalar(select(func.count()).select_from(job_type).where(job_type.project_id==project.id))==1
    assert await test_db.scalar(select(func.count()).select_from(item_type).where(item_type.status=='pending'))==2
    expected_annotations={'review':3,'quality':2}.get(mode,0)
    assert await test_db.scalar(select(func.count()).select_from(Annotation).join(Asset).where(Asset.project_id==project.id))==expected_annotations
    if mode=='crop_detection':
        crop=Asset(project_id=project.id,original_name='derived.png',content_hash='derived',source_type='model_crop',
            blob_path='derived.png',file_size=1)
        test_db.add(crop)
        await test_db.commit()
        later={**body,'asset_ids':[str(crop.id)]}
        rejected=client.post(path,headers=auth_headers,json=later)
        assert rejected.status_code==409 and '从原图开始' in rejected.text
        later['follow_uploads']=False
        result=client.post(path,headers=auth_headers,json=later)
        assert result.status_code==201,result.text
        later_url=path+'/'+result.json()['id']
        assert client.get(later_url,headers=auth_headers).json()['crop_from_originals'] is False
        rejected=client.put(later_url+'/follow-uploads',headers=auth_headers,json={'enabled':True})
        assert rejected.status_code==409 and '从原图开始' in rejected.text


@pytest.mark.asyncio
async def test_uploaded_original_runs_detection_then_attribute_preannotation(client,auth_headers,work_env):
    from services import project_work
    from services.storage import get_storage
    s=work_env
    s.project.task_type='caption'
    s.project.label_schema={'annotation_scope':'crops','object_attributes':{'target_label':'person',
        'fields':[{'key':'color','name':'颜色','options':[],'instruction':''}]}}
    group=ImageCollection(tenant_id=1,name='combined')
    s.db.add(group)
    buffer=BytesIO()
    Image.new('RGB',(64,48),'blue').save(buffer,'PNG')
    content=buffer.getvalue()
    image=LibraryImage(tenant_id=1,original_name='new.png',blob_path='uploaded/new.png',
        content_hash=hashlib.sha256(content).hexdigest(),file_size=len(content),width=64,height=48,
        format='PNG',source_type='upload')
    await get_storage().save_file(BytesIO(content),image.blob_path)
    s.db.add(image)
    await s.db.flush()
    upload=ImageUpload(tenant_id=1,user_id=s.user.id,collection_id=group.id,project_id=s.project.id,status='completed',
        filename=image.original_name,size=len(content),resume_key='combined',chunk_hashes=['a'*64],min_long_edge=0)
    s.db.add(upload)
    await s.db.flush()
    s.db.add(ImageUploadItem(upload_id=upload.id,image_id=image.id))
    await s.db.commit()
    path=f'/api/projects/{s.project.id}/work/jobs'
    created=client.post(path,headers=auth_headers,json={'operation':'detect_attributes','model_id':str(s.models[0].id),
        'attribute_model_id':str(s.models[1].id),'asset_ids':[str(s.asset.id)],'follow_uploads':True})
    assert created.status_code==201,created.text
    added=client.post('/api/images/associate',headers=auth_headers,json={'project_id':str(s.project.id),
        'image_ids':[str(image.id)],'upload_id':str(upload.id)})
    assert added.status_code==200 and added.json()['preannotation_queued']==1
    new_asset_id=UUID(added.json()['asset_ids'][0])
    def provider(request):
        if request.url.path=='/detect':
            return httpx.Response(200,json={'detections':[{'class':'person','bbox':[0,0,32,48],'score':.9}]})
        assert request.url.path=='/chat/completions'
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(
            {'color':{'value':'蓝','reason':'可见'}})}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as model_client:
        for _ in range(4):
            assert await project_work.process_one(s.sessions,model_client)
        assert not await project_work.process_one(s.sessions,model_client)
    async with s.sessions() as db:
        parent=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.asset_id==new_asset_id))
        assert parent.status=='completed' and parent.result['attribute_job_id']
        crop_id,=parent.result['asset_ids']
        annotation=await db.scalar(select(Annotation).where(Annotation.asset_id==UUID(crop_id)))
        assert annotation.review_status=='pending' and annotation.annotation_source=='auto'
        assert annotation.annotation_data['attributes']['color']['value']=='蓝'
