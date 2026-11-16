import httpx
import pytest
from sqlalchemy import delete, select

from models import Annotation, Asset
from models.project_work import ProjectWorkItem, ProjectWorkJob
from tests.test_project_work import work_env


@pytest.mark.asyncio
async def test_history_reads_saved_workflow_configuration(client,auth_headers,work_env):
    env=work_env
    url=f'/api/projects/{env.project.id}/work/workflows'
    schema={'target_label':'person','fields':[{'key':'action','name':'动作','instruction':'可见动作','options':[]}]}
    run_ids=[]
    for confidence in (.6,.9):
        created=client.post(url,headers=auth_headers,json={'name':'同名流程','attribute_schema':schema,
            'steps':[{'operation':'crop','model_id':str(env.models[0].id),'confidence':confidence,'labels':['person']},
                {'operation':'attributes','model_id':str(env.models[1].id),'attribute_prompt':'读取动作'}]})
        assert created.status_code==201,created.text
        run_ids.append(created.json()['id'])

    for run_id,confidence in zip(run_ids,(.6,.9)):
        response=client.get(f'{url}/{run_id}',headers=auth_headers)
        assert response.status_code==200,response.text
        config=response.json()
        assert config['image_kind']=='originals' and config['source_job_id'] is None
        crop,attributes=config['steps']
        assert crop['confidence']==confidence and crop['labels']==['person']
        assert attributes['attribute_prompt']=='读取动作' and attributes['attribute_schema']==schema
        assert 'base_url' not in response.text and 'api_key_env' not in response.text
        assert client.get(f"{url}/{attributes['id']}",headers=auth_headers).status_code==404

    target=f'{url}/{run_ids[0]}'
    assert client.delete(target,headers=auth_headers).status_code==409
    assert client.post(target+'/cancel',headers=auth_headers).status_code==200
    from uuid import UUID
    async with env.sessions() as db:
        crop=Asset(project_id=env.project.id,original_name='history-crop.png',source_type='model_crop',
            blob_path=env.asset.blob_path,content_hash='history-crop-test',file_size=1)
        db.add(crop)
        await db.flush()
        crop_id=str(crop.id)
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==UUID(run_ids[0])))
        item.status='completed'
        item.result={'asset_ids':[crop_id]}
        await db.commit()
    assert client.delete(target,headers=auth_headers).status_code==204
    assert client.get(target,headers=auth_headers).status_code==404
    assert [run['id'] for run in client.get(url,headers=auth_headers).json()]==[run_ids[1]]
    jobs_url=url.removesuffix('workflows')+'jobs'
    details=client.get(f'{jobs_url}/{run_ids[0]}',headers=auth_headers)
    assert details.status_code==200 and details.json()['items'][0]['result']['asset_ids']==[crop_id]
    assert client.get(f'/api/assets/{crop_id}',headers=auth_headers).status_code==200
    assert client.post(f'{jobs_url}/{run_ids[0]}/retry',headers=auth_headers).status_code==409


@pytest.mark.asyncio
async def test_freeze_uses_only_confirmed_workflow_results(client,auth_headers,work_env):
    from tests.test_p2_exports import populate

    env=work_env
    images=populate(client,auth_headers,env.project.id)
    job=ProjectWorkJob(project_id=env.project.id,user_id=env.user.id,operation='detect',model={},options={'workflow_id':'test'})
    env.db.add(job)
    await env.db.flush()
    from uuid import UUID
    image=await env.db.get(Asset,UUID(images[0][0]))
    env.db.add(ProjectWorkItem(job_id=job.id,asset_id=image.id,content_hash=image.content_hash,
        status='completed',result={'passed':True}))
    await env.db.commit()
    root=f'/api/export/projects/{env.project.id}'
    body={'work_job_id':str(job.id),'approved_only':False,'selection':{'mode':'none'}}
    response=client.post(root+'/splits/preview',headers=auth_headers,json=body)
    assert response.status_code==200,response.text
    preview=response.json()
    assert preview['images_by_split']=={'all':1}
    frozen=client.post(root+'/snapshots',headers=auth_headers,json={**body,'preview_token':preview['preview_token']})
    assert frozen.status_code==201,frozen.text
    assert [item['asset_id'] for item in frozen.json()['manifest']['assets']]==[images[0][0]]
    annotation=images[0][1]
    assert client.put('/api/annotations/'+annotation['id'],headers=auth_headers,
        json={'base_version':annotation['version'],'annotation_data':{'boxes':[]}}).status_code==200
    changed=client.post(root+'/snapshots',headers=auth_headers,json={**body,'preview_token':preview['preview_token']})
    assert changed.status_code==409,changed.text
    await env.db.refresh(env.project)
    env.project.task_type='caption';await env.db.commit()
    for asset_id,_ in images:
        latest=client.get('/api/annotations',headers=auth_headers,params={'asset_id':asset_id}).json()['items'][0]
        saved=client.put('/api/annotations/'+latest['id'],headers=auth_headers,json={
            'base_version':latest['version'],'kind':'image','annotation_data':{'description':'批次描述'}})
        assert saved.status_code==200,saved.text
    sft={key:value for key,value in body.items() if key!='selection'}
    sft['dataset_format']='swift_sft'
    preview=client.post(root+'/annotation-snapshots/preview',headers=auth_headers,json=sft)
    assert preview.status_code==200 and preview.json()['asset_count']==1,preview.text
    frozen=client.post(root+'/annotation-snapshots',headers=auth_headers,json={**sft,'preview_token':preview.json()['preview_token']})
    assert frozen.status_code==201,frozen.text
    assert [item['asset_id'] for item in frozen.json()['manifest']['assets']]==[images[0][0]]


@pytest.mark.asyncio
@pytest.mark.parametrize('task_type,valid,wrong',[
    ('detection',{'boxes':[],'negative':True},{'description':'描述'}),
    ('caption',{'description':'一辆汽车'},{'labels':['person']}),
    ('classification',{'labels':['person']},{'description':'描述'}),
])
async def test_project_annotation_format_keeps_legacy_versions(client,auth_headers,work_env,task_type,valid,wrong):
    env=work_env
    env.project.task_type=task_type
    env.project.label_schema={'labels':['person']}
    legacy=Annotation(project_id=env.project.id,asset_id=env.asset.id,version=1,is_latest=True,review_status='pending',
        annotation_data={'boxes':[],'negative':True,'description':'历史描述','labels':['person']})
    env.db.add(legacy)
    await env.db.commit()
    url=f'/api/annotations/{legacy.id}'
    assert client.put(url,headers=auth_headers,json={'base_version':1,'annotation_data':wrong}).status_code==400
    response=client.put(url,headers=auth_headers,json={'base_version':1,'annotation_data':valid})
    assert response.status_code==200,response.text
    assert response.json()['annotation_data']==valid
    history=client.get(url,headers=auth_headers).json()
    assert history['annotation_data']==legacy.annotation_data
    assert not history['is_latest']


@pytest.mark.asyncio
async def test_workflow_template_saves_steps_and_attribute_snapshot(client,auth_headers,work_env,test_engine):
    from models import Project, Tenant, User
    from models.project_work import ProjectWorkflowTemplate
    from services.auth import create_access_token
    env=work_env
    async with test_engine.begin() as db:await db.run_sync(ProjectWorkflowTemplate.__table__.create)
    definition={'target_label':'person','fields':[{'key':'action','name':'动作','instruction':'可见动作','options':[]}]}
    env.project.label_schema={'object_attributes':definition}
    target=Project(tenant_id=env.project.tenant_id,project_name='目标项目',task_type='caption')
    other_admin=User(id=2,tenant_id=2,username='other_admin',email='other@example.com',password_hash='fixture',role='admin')
    env.db.add(Tenant(id=2,tenant_name='other'))
    await env.db.flush()
    env.db.add_all([target,other_admin])
    await env.db.commit()
    url='/api/workflow-templates'
    payload=dict(name='人员分析',attribute_schema=definition,steps=[dict(operation='crop',model_id=str(env.models[0].id),labels=['person']),
        dict(operation='attributes',model_id=str(env.models[1].id),attribute_prompt='判断动作')])
    created=client.post(url,headers=auth_headers,json=payload)
    assert created.status_code==201,created.text
    template=created.json()
    assert template['attribute_schema']==definition and template['steps'][1]['attribute_prompt']=='判断动作'
    assert client.get(url,headers=auth_headers).json()==[template]
    target_url=f'/api/projects/{target.id}/work'
    launched=client.post(f'{target_url}/workflows',headers=auth_headers,json={
        **{key:template[key] for key in ('name','steps','attribute_schema')},'image_kind':'uploads','follow_uploads':True})
    assert launched.status_code==201,launched.text
    await env.db.refresh(target)
    assert target.label_schema is None
    await env.db.commit()
    other_token=create_access_token({'user_id':other_admin.id,'tenant_id':other_admin.tenant_id,'username':other_admin.username})
    other_headers={'Authorization':f'Bearer {other_token}'}
    detail=f"{url}/{template['id']}"
    assert client.get(url,headers=other_headers).json()==[]
    assert client.put(detail,headers=other_headers,json=payload).status_code==404
    assert client.delete(detail,headers=other_headers).status_code==404
    duplicate=client.post(url,headers=auth_headers,json=payload)
    assert duplicate.status_code==409
    await env.db.execute(delete(Project).where(Project.id==env.project.id))
    await env.db.commit()
    assert client.get(url,headers=auth_headers).json()==[template]
    payload['steps'][1]['attribute_prompt']='只判断可见动作'
    updated=client.put(detail,headers=auth_headers,json=payload)
    assert updated.status_code==200,updated.text
    assert updated.json()['steps'][1]['attribute_prompt']=='只判断可见动作'
    assert client.delete(detail,headers=auth_headers).status_code==204


@pytest.mark.asyncio
@pytest.mark.parametrize('passed,save_input_boxes',[(True,None),(False,None),(True,False),(True,True)])
async def test_detection_project_can_chain_crop_filter_and_description(work_env, monkeypatch, passed, save_input_boxes):
    from schemas.project_work import WorkflowCreate
    from services import image_quality, project_work, project_workflow
    env = work_env
    env.sessions.configure(autoflush=False)
    env.project.task_type = 'detection'
    env.project.label_schema={'annotation_scope':'crops','labels':[]}
    await env.db.commit()
    async def detect(*args, **kwargs):
        return {'boxes': [dict(x=0, y=0, width=5, height=5, label='person')]}
    async def filter_image(*args, **kwargs):
        return {'passed': passed, 'reason': 'visible evidence'}
    async def describe(*args, **kwargs):
        return 'a matching person'
    monkeypatch.setattr(project_work, 'infer', detect)
    monkeypatch.setattr(project_work, 'caption', describe)
    monkeypatch.setattr(image_quality, 'analyze', filter_image)
    flow = await project_workflow.create(env.db, env.project, WorkflowCreate(steps=[
        dict(operation='crop', model_id=env.models[0].id, labels=['person'],save_input_boxes=save_input_boxes),
        dict(operation='detect', model_id=env.models[0].id, labels=['person']),
        dict(operation='filter', model_id=env.models[1].id, prompt='Smoking'),
        dict(operation='caption', model_id=env.models[1].id),
    ]), env.user.id)
    async with httpx.AsyncClient() as client:
        for index in range(len(flow)):
            assert await project_work.process_one(env.sessions, client)==(index<3 or passed)
    async with env.sessions() as db:
        assert len((await db.scalars(select(Asset.id))).all()) == 2
        original=await db.scalar(select(Annotation).where(Annotation.asset_id==env.asset.id))
        if save_input_boxes is not True:
            assert original is None
        else:
            assert original.review_status=='pending'
            assert original.annotation_data['boxes']==[dict(x=0,y=0,width=5,height=5,label='person')]
        assert (await db.get(type(env.project),env.project.id)).label_schema['labels']==['person']
        result = await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id == flow[-1].id))
        if passed:
            assert result.result['description'] == 'a matching person'
            assert result.asset_id != env.asset.id
            crop=await db.get(Asset,result.asset_id)
            assert crop.device_info['parent_image_id']==str(env.asset.id)
            rows=(await db.scalars(select(Annotation).where(Annotation.project_id==env.project.id,Annotation.asset_id!=env.asset.id))).all()
            assert not rows
        else:
            assert result is None


@pytest.mark.asyncio
@pytest.mark.parametrize('score,expected',[(.96,'approved'),(.9,'pending'),(None,'pending')])
async def test_workflow_preannotation_feeds_ai_review(client,auth_headers,work_env,test_engine,monkeypatch,score,expected):
    from models.automation import AutomationItem, AutomationJob
    from schemas.project_work import WorkflowCreate
    from services import project_work, project_workflow
    env=work_env
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda db:[table.create(db,checkfirst=True) for table in (AutomationJob.__table__,AutomationItem.__table__)])
    env.project.task_type='detection'
    env.project.label_schema={'labels':[]}
    await env.db.commit()
    boxes=[] if score is None else [dict(label='truck',x=1,y=2,width=4,height=5,score=score)]
    async def detect(*args,**kwargs):return {'boxes':boxes}
    calls=[]
    async def audit(config,content,detected,labels,*args):
        calls.append(detected)
        assert labels==['truck']
        return dict(boxes=[dict(index=0,verdict='pass',reason='正确')],completeness='pass',negative='uncertain',reason='完整')
    monkeypatch.setattr(project_work,'infer',detect)
    monkeypatch.setattr(project_work,'audit_image',audit)
    flow=await project_workflow.create(env.db,env.project,WorkflowCreate(steps=[
        dict(operation='preannotate',model_id=env.models[0].id),
        dict(operation='ai_review',model_id=env.models[1].id),
    ],follow_uploads=True),env.user.id)
    async with httpx.AsyncClient() as worker_client:
        assert await project_work.process_one(env.sessions,worker_client)
        assert await project_work.process_one(env.sessions,worker_client)
    async with env.sessions() as db:
        annotation=await db.scalar(select(Annotation).where(Annotation.asset_id==env.asset.id))
        assert annotation.annotation_data=={'boxes':boxes,'negative':not boxes}
        assert annotation.review_status==expected and annotation.version==1
        result=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==flow[-1].id))
        assert result.status=='completed'
        assert result.result.get('approved',False)==(expected=='approved')
    assert calls==([boxes] if boxes else [])
    queue=client.get('/api/annotations',headers=auth_headers,
        params={'project_id':str(env.project.id),'kind':'detection','ai_pending':True})
    assert queue.status_code==200,queue.text
    assert queue.json()['total']==int(score==.9)
    if score==.9:
        assert client.post(f'/api/annotations/{annotation.id}/review',headers=auth_headers,
            json={'kind':'detection','approved':True}).status_code==200
        queue=client.get('/api/annotations',headers=auth_headers,
            params={'project_id':str(env.project.id),'kind':'detection','ai_pending':True})
        assert queue.json()['total']==0


@pytest.mark.asyncio
async def test_crop_preserves_existing_detection_annotations(work_env):
    from services.project_work import save_crop_source_boxes
    env=work_env
    existing=Annotation(asset_id=env.asset.id,project_id=env.project.id,version=1,review_status='approved',
        annotation_data={'boxes':[],'negative':True},annotation_source='manual')
    env.db.add(existing);await env.db.commit()
    result=await save_crop_source_boxes(env.db,env.asset,[dict(label='person',x=1,y=1,width=4,height=4)],env.user.id)
    assert result=={'source_annotation_preserved':True}
    rows=(await env.db.scalars(select(Annotation).where(Annotation.asset_id==env.asset.id))).all()
    assert rows==[existing] and existing.annotation_data['negative'] and existing.review_status=='approved'


@pytest.mark.asyncio
async def test_grouped_cut_uses_all_internal_batch_results(work_env):
    from uuid import uuid4
    from services import project_work,project_workflow
    env=work_env
    root=uuid4()
    jobs=[ProjectWorkJob(id=id,project_id=env.project.id,user_id=env.user.id,operation='crop',
        model={'id':str(env.models[0].id),'revision':1},options={'batch_root':str(root)}) for id in (root,uuid4())]
    env.db.add_all(jobs)
    await env.db.flush()
    outputs=[str(uuid4()),str(uuid4())]
    env.db.add_all([ProjectWorkItem(job_id=job.id,asset_id=env.asset.id,content_hash=env.asset.content_hash,
        status='completed',result={'asset_ids':[output]}) for job,output in zip(jobs,outputs)])
    await env.db.commit()
    assert set((await env.db.scalars(project_workflow.output_ids(env.db,jobs[0]))).all())=={id.replace('-','') for id in outputs}
    assert (await project_work.summary(env.db,jobs[0]))['total']==2


@pytest.mark.asyncio
async def test_original_workflow_adds_uploads_and_keeps_its_detection_threshold(work_env):
    from schemas.project_work import WorkflowCreate
    from services import project_workflow,upload_follow
    from services.image_semantics import validate_snapshots

    env=work_env
    model=env.models[0]
    model.confidence=.25
    await env.db.commit()
    root,=await project_workflow.create(env.db,env.project,WorkflowCreate(image_kind='originals',follow_uploads=True,
        steps=[dict(operation='detect',model_id=model.id)]),env.user.id)
    assert root.options['confidence']==.85
    model.confidence=.85
    await env.db.commit()
    await validate_snapshots(env.db,env.project.tenant_id,[root.model])
    assert root.model['config']['confidence']==.25
    asset=Asset(project_id=env.project.id,source_type='upload',blob_path='new.jpg',content_hash='new',
        original_name='new.jpg',file_size=12)
    crop=Asset(project_id=env.project.id,source_type='model_crop',blob_path='crop.jpg',content_hash='new-crop',
        original_name='crop.jpg',file_size=12)
    env.db.add_all([asset,crop])
    await env.db.flush()
    assert await upload_follow.enqueue_uploads(env.db,root,[asset,crop])==(1,None)
    assert await upload_follow.enqueue_uploads(env.db,root,[asset])==(0,None)
    queued=await env.db.scalars(select(ProjectWorkItem.asset_id).where(ProjectWorkItem.job_id==root.id))
    assert set(queued)=={env.asset.id,asset.id}


@pytest.mark.asyncio
@pytest.mark.parametrize('operations,entry',[
    (['crop','quality','attributes'],1),
    (['crop','caption','attributes'],1),
    (['caption','crop','crop','attributes'],3),
    (['crop','crop','crop','attributes'],2),
])
async def test_workflow_can_route_uploaded_crops_to_selected_step(client,auth_headers,work_env,operations,entry):
    from schemas.project_work import WorkflowCreate
    from services import project_workflow,upload_follow

    env=work_env
    env.project.task_type='caption'
    await env.db.commit()
    template=dict(id='vehicle',name='车辆',config=dict(target_label=['car','truck'],fields=[dict(key='color',name='颜色')]))
    steps=[]
    for operation in operations:
        step=dict(operation=operation,model_id=env.models[0 if operation=='crop' else 1].id)
        if operation=='quality':step['prompt']='主体清晰'
        if operation=='attributes':step['attribute_templates']=[template]
        steps.append(step)
    jobs=await project_workflow.create(env.db,env.project,WorkflowCreate(follow_uploads=True,steps=steps),env.user.id)
    root,destination=jobs[0],jobs[entry]
    path=f'/api/projects/{env.project.id}/work/jobs/{root.id}/follow-uploads'
    response=client.put(path,headers=auth_headers,json={'enabled':True,'uploaded_crop_job_id':str(destination.id)})
    assert response.status_code==200,response.text
    async with env.sessions() as db:
        crop=Asset(project_id=env.project.id,source_type='model_crop',blob_path='crop.png',content_hash='uploaded-crop',
            original_name='crop.png',file_size=1,device_info={'uploaded_crop':True})
        original=Asset(project_id=env.project.id,source_type='upload',blob_path='original.png',content_hash='uploaded-original',
            original_name='original.png',file_size=1)
        db.add_all([crop,original]);await db.flush()
        following=await upload_follow.lock_follow_job(db,env.project.id)
        assert await upload_follow.enqueue_uploads(db,following,[crop,original])==(2,None)
        assert await upload_follow.enqueue_uploads(db,following,[crop,original])==(0,None)
        assert await db.scalar(select(ProjectWorkItem.job_id).where(ProjectWorkItem.asset_id==crop.id))==destination.id
        assert await db.scalar(select(ProjectWorkItem.job_id).where(ProjectWorkItem.asset_id==original.id))==root.id
        await db.commit()
    response=client.post(f'/api/projects/{env.project.id}/work/jobs/{destination.id}/cancel',headers=auth_headers)
    assert response.status_code==200,response.text
    async with env.sessions() as db:
        crop=Asset(project_id=env.project.id,source_type='model_crop',blob_path='later.png',content_hash='later',
            original_name='later.png',file_size=1,device_info={'uploaded_crop':True})
        db.add(crop);await db.flush()
        following=await upload_follow.lock_follow_job(db,env.project.id)
        count,warning=await upload_follow.enqueue_uploads(db,following,[crop])
        assert count==0 and warning


@pytest.mark.asyncio
async def test_paused_legacy_workflow_can_configure_uploaded_crop_entry(client,auth_headers,work_env):
    from schemas.project_work import WorkflowCreate
    from services import project_workflow

    env=work_env
    root,next_step=await project_workflow.create(env.db,env.project,WorkflowCreate(follow_uploads=True,steps=[
        dict(operation='crop',model_id=env.models[0].id),
        dict(operation='caption',model_id=env.models[1].id),
    ]),env.user.id)
    root.options={key:value for key,value in root.options.items() if key not in ('workflow_can_follow','workflow_image_kind')}
    root.follow_uploads=False
    await env.db.commit()
    path=f'/api/projects/{env.project.id}/work/workflows'
    before=client.get(path,headers=auth_headers).json()[0]['steps'][0]
    assert before['workflow_can_route_uploaded_crops'] and not before['follow_uploads']
    configured=client.put(f'/api/projects/{env.project.id}/work/jobs/{root.id}/follow-uploads',headers=auth_headers,
        json={'enabled':False,'uploaded_crop_job_id':str(next_step.id)})
    assert configured.status_code==200,configured.text
    after=client.get(path,headers=auth_headers).json()[0]['steps'][0]
    assert after['uploaded_crop_job_id']==str(next_step.id) and not after['follow_uploads']


def test_unlabeled_uploaded_crop_uses_only_attribute_template():
    from types import SimpleNamespace
    from services.project_work import crop_attribute_template

    template={'id':'vehicle','config':{'target_label':['car','truck'],'fields':[]}}
    crop=SimpleNamespace(source_type='model_crop',device_info={'uploaded_crop':True})
    assert crop_attribute_template(crop,[template])==(None,'vehicle',template['config'])
    assert crop_attribute_template(crop,[template,{**template,'id':'person'}])==(None,None,None)


@pytest.mark.asyncio
async def test_follow_workflow_streams_while_more_inputs_can_arrive(work_env,monkeypatch):
    from schemas.project_work import WorkflowCreate
    from services import image_quality,project_work,project_workflow,upload_follow
    env=work_env
    flow=await project_workflow.create(env.db,env.project,WorkflowCreate(image_kind='uploads',follow_uploads=True,steps=[
        dict(operation='filter',model_id=env.models[1].id,prompt='keep the target'),
        dict(operation='caption',model_id=env.models[1].id)]),env.user.id)
    async def filter_image(*args,**kwargs):
        return {'passed':True,'reason':'match'}
    async def describe(*args,**kwargs):
        return 'arrived before upload was closed'
    monkeypatch.setattr(image_quality,'analyze',filter_image)
    monkeypatch.setattr(project_work,'caption',describe)
    async with env.sessions() as db:
        assert not await db.scalar(select(ProjectWorkItem.id).where(ProjectWorkItem.job_id==flow[0].id))
        asset=await db.get(Asset,env.asset.id)
        asset.source_type='upload'
        await db.flush()
        root=await upload_follow.lock_follow_job(db,env.project.id)
        assert root.id==flow[0].id
        assert await upload_follow.enqueue_uploads(db,root,[asset])==(1,None)
        assert await upload_follow.enqueue_uploads(db,root,[asset])==(0,None)
        await db.commit()
    async with httpx.AsyncClient() as client:
        assert await project_work.process_one(env.sessions,client)
        assert await project_work.process_one(env.sessions,client)
    async with env.sessions() as db:
        root=await db.get(ProjectWorkJob,flow[0].id)
        result=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==flow[1].id))
        assert root.follow_uploads
        assert result.result['description']=='arrived before upload was closed'


@pytest.mark.asyncio
@pytest.mark.parametrize('task_type', ['detection','caption'])
async def test_workflow_reuses_project_attribute_analysis(work_env, monkeypatch, task_type):
    from models import Annotation
    from schemas.project_work import WorkflowCreate
    from services import project_work,project_workflow
    env=work_env
    definition={'target_label':'person','fields':[{'key':'smoking','name':'抽烟','instruction':'依据可见证据','options':['是','否']}]}
    env.project.task_type=task_type
    env.project.label_schema={'object_attributes':definition} if task_type=='detection' else {}
    await env.db.commit()
    core='仅检查人物手部与嘴部' if task_type=='detection' else ''
    async def analyze(config,content,schema,client,**kwargs):
        assert schema==definition
        assert kwargs['prompt']=='只判断人物'
        assert kwargs['attribute_prompt']==core
        return {'smoking':{'value':'是','reason':'手持香烟'}}
    monkeypatch.setattr(project_work,'analyze',analyze)
    jobs=await project_workflow.create(env.db,env.project,WorkflowCreate(attribute_schema=definition if task_type=='caption' else None,steps=[
        dict(operation='attributes',model_id=env.models[1].id,prompt='只判断人物',attribute_prompt=core)]),env.user.id)
    async with httpx.AsyncClient() as client:assert await project_work.process_one(env.sessions,client)
    async with env.sessions() as db:
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==jobs[0].id))
        assert item.result['attributes']['smoking']['value']=='是'
        annotation=await db.scalar(select(Annotation).where(Annotation.asset_id==env.asset.id))
        if task_type=='detection':assert annotation is None
        else:
            assert annotation.review_status=='pending'
            assert annotation.annotation_data['attributes']['smoking']['value']=='是'


@pytest.mark.asyncio
@pytest.mark.parametrize('streaming,single',[(False,False),(True,False),(False,True)])
async def test_workflow_routes_detected_crop_labels_to_attribute_templates(work_env,monkeypatch,streaming,single):
    from schemas.project_work import WorkflowCreate
    from services import project_work,project_workflow

    env=work_env
    env.project.task_type='caption';await env.db.commit()
    labels=['sam3_person','sam3_car','sam3_bag']
    async def detect(*args,**kwargs):
        return {'boxes':[dict(label=label,x=0,y=0,width=5,height=5) for label in labels]}

    calls=[]
    async def analyze(config,content,schema,client,**kwargs):
        field=schema['fields'][0]['key']
        calls.append((schema['target_label'],field))
        return {field:{'value':'可见','reason':'测试'}}

    monkeypatch.setattr(project_work,'infer',detect)
    monkeypatch.setattr(project_work,'analyze',analyze)
    templates=[dict(id=label,name=label,config=dict(target_label=label,fields=[dict(key=field,name=field)]))
        for label,field in [('person','clothing'),('car','plate')]]
    request=WorkflowCreate(follow_uploads=streaming,attribute_schema=templates[1]['config'] if single else None,steps=[
        dict(operation='crop',model_id=env.models[0].id,save_input_boxes=False),
        dict(operation='attributes',model_id=env.models[1].id,attribute_templates=None if single else templates),
    ])
    jobs=await project_workflow.create(env.db,env.project,request,env.user.id)
    async with httpx.AsyncClient() as client:
        assert await project_work.process_one(env.sessions,client)
        for _ in labels:assert await project_work.process_one(env.sessions,client)
    assert sorted(calls)==([('car','plate')] if single else [('car','plate'),('person','clothing')])
    async with env.sessions() as db:
        crops=(await db.scalars(select(Asset).where(Asset.source_type=='model_crop'))).all()
        assert {crop.device_info['crop']['box']['label'] for crop in crops}==set(labels)
        anns=(await db.scalars(select(Annotation))).all()
        assert {next(iter(ann.annotation_data['attributes'])) for ann in anns}==({'plate'} if single else {'clothing','plate'})
        job=await db.get(ProjectWorkJob,jobs[1].id)
        details=await project_work.summary(db,job,True)
        assert details['attribute_skipped_count']==(2 if single else 1)
        assert any(item['result'].get('attribute_skipped') for item in details['items'])
        assert len((await db.scalars(project_workflow.output_ids(db,job))).all())==(1 if single else 2)


@pytest.mark.asyncio
async def test_routed_attribute_templates_survive_workflow_template_round_trip(client,auth_headers,work_env,test_engine):
    from models.project_work import ProjectWorkflowTemplate
    env=work_env
    async with test_engine.begin() as conn:await conn.run_sync(ProjectWorkflowTemplate.__table__.create)
    templates=[dict(id='person',name='人员',config=dict(target_label='person',fields=[dict(key='clothing',name='服饰')]))]
    payload=dict(name='按类别分析',steps=[
        dict(operation='crop',model_id=str(env.models[0].id)),
        dict(operation='attributes',model_id=str(env.models[1].id),attribute_templates=templates)])
    response=client.post('/api/workflow-templates',headers=auth_headers,json=payload)
    assert response.status_code==201,response.text
    saved=response.json()
    assert saved['steps'][1]['attribute_templates'][0]['config']['fields'][0]['key']=='clothing'
    loaded=client.get('/api/workflow-templates',headers=auth_headers).json()
    assert any(item==saved for item in loaded)
    duplicate={**templates[0],'id':'duplicate','name':'重复模板'}
    payload['steps'][1]['attribute_templates']=[templates[0],duplicate]
    assert client.post('/api/workflow-templates',headers=auth_headers,json=payload).status_code==422


def test_crop_template_match_prefers_exact_label_and_skips_unlabeled_images():
    from types import SimpleNamespace
    from services.project_work import crop_attribute_template
    templates=[dict(id=label,name=label,config=dict(target_label=label,fields=[dict(key='value',name='属性')]))
        for label in ['person','sam3_person']]
    asset=SimpleNamespace(source_type='model_crop',device_info={'crop':{'box':{'label':'sam3_person'}}})
    assert crop_attribute_template(asset,templates)[1]=='sam3_person'
    asset.source_type='upload'
    assert crop_attribute_template(asset,templates)==(None,None,None)


@pytest.mark.asyncio
@pytest.mark.parametrize('resume_existing',[False,True])
async def test_fixed_workflow_forwards_first_result_before_upstream_finishes(work_env,monkeypatch,resume_existing):
    from io import BytesIO
    from hashlib import sha256
    from PIL import Image
    from schemas.project_work import WorkflowCreate
    from services import project_work,project_workflow
    from services.storage import get_storage

    env=work_env
    output=BytesIO()
    Image.new('RGB',(64,48),'blue').save(output,'PNG')
    raw=output.getvalue()
    await get_storage().save_file(BytesIO(raw),'second.png')
    env.db.add(Asset(project_id=env.project.id,original_name='second.png',blob_path='second.png',
        content_hash=sha256(raw).hexdigest(),file_size=len(raw),width=64,height=48,format='PNG',source_type='upload'))
    await env.db.commit()

    async def infer(*args,**kwargs):
        return {'boxes':[dict(x=1,y=1,width=5,height=5,label='person')]}
    async def caption(*args,**kwargs):return '裁剪结果'
    monkeypatch.setattr(project_work,'infer',infer)
    monkeypatch.setattr(project_work,'caption',caption)
    crop,description=await project_workflow.create(env.db,env.project,WorkflowCreate(steps=[
        dict(operation='crop',model_id=env.models[0].id,save_input_boxes=False),
        dict(operation='caption',model_id=env.models[1].id),
    ]),env.user.id)
    if resume_existing:
        crop.options={key:value for key,value in crop.options.items() if key!='workflow_next'}|{'workflow_streaming':False}
        description.options={**description.options,'workflow_streaming':False,'workflow_started':False}
        await env.db.commit()

    async with httpx.AsyncClient() as client:
        assert await project_work.process_one(env.sessions,client)
        if resume_existing:
            from sqlalchemy import update
            async with env.sessions() as db:
                await db.execute(update(ProjectWorkItem).where(ProjectWorkItem.job_id==crop.id,
                    ProjectWorkItem.status=='pending').values(status='running',attempt=1))
                await db.commit()
            await project_workflow.resume_waiting_workflows(env.sessions)
            await project_workflow.resume_waiting_workflows(env.sessions)
        assert await project_work.process_one(env.sessions,client)
    async with env.sessions() as db:
        upstream=(await db.scalars(select(ProjectWorkItem.status).where(ProjectWorkItem.job_id==crop.id))).all()
        downstream=(await db.scalars(select(ProjectWorkItem.status).where(ProjectWorkItem.job_id==description.id))).all()
        assert sorted(upstream)==['completed','pending']
        assert downstream==['completed']


@pytest.mark.asyncio
async def test_downstream_requests_leave_capacity_for_upstream(work_env,monkeypatch):
    from schemas.project_work import WorkflowCreate
    from services import project_work,project_workflow
    env=work_env
    schema={'target_label':'person','fields':[{'key':'color','name':'颜色'}]}
    jobs=await project_workflow.create(env.db,env.project,WorkflowCreate(attribute_schema=schema,steps=[
        dict(operation='crop',model_id=env.models[0].id),
        dict(operation='attributes',model_id=env.models[1].id),
    ]),env.user.id)
    pending=Asset(project_id=env.project.id,original_name='next.png',blob_path=env.asset.blob_path,
        content_hash=env.asset.content_hash+'next',file_size=1,width=64,height=48,source_type='upload')
    env.db.add(pending);await env.db.flush()
    env.db.add_all([
        ProjectWorkItem(job_id=jobs[1].id,asset_id=env.asset.id,content_hash=env.asset.content_hash,status='running'),
        ProjectWorkItem(job_id=jobs[1].id,asset_id=pending.id,content_hash=pending.content_hash,status='pending'),
    ])
    await env.db.commit()
    calls=[]
    async def detect(*args,**kwargs):
        calls.append('crop');return {'boxes':[]}
    async def analyze(*args,**kwargs):
        calls.append('attributes');return {'color':{'value':'白','reason':''}}
    monkeypatch.setattr(project_work,'infer',detect)
    monkeypatch.setattr(project_work,'analyze',analyze)
    async with httpx.AsyncClient() as client:
        assert await project_work.process_one(env.sessions,client)
    assert calls==['crop']


@pytest.mark.asyncio
async def test_cancel_entire_workflow_preserves_results(client,auth_headers,work_env):
    from schemas.project_work import WorkflowCreate
    from services import project_workflow

    env=work_env
    root,child=await project_workflow.create(env.db,env.project,WorkflowCreate(follow_uploads=True,steps=[
        dict(operation='crop',model_id=env.models[0].id),
        dict(operation='caption',model_id=env.models[1].id),
    ]),env.user.id)
    saved=ProjectWorkItem(job_id=child.id,asset_id=env.asset.id,content_hash=env.asset.content_hash,
        status='completed',result={'description':'已完成'})
    running=ProjectWorkItem(job_id=child.id,content_hash='running',status='running',attempt=1)
    env.db.add_all([saved,running]);await env.db.commit()
    url=f'/api/projects/{env.project.id}/work/workflows'
    assert client.post(f'{url}/{child.id}/cancel',headers=auth_headers).status_code==404
    response=client.post(f'{url}/{root.id}/cancel',headers=auth_headers)
    assert response.status_code==200,response.text
    assert response.json()=={'changed_count':2}
    assert client.post(f'{url}/{root.id}/cancel',headers=auth_headers).json()=={'changed_count':0}
    async with env.sessions() as db:
        for job_id in (root.id,child.id):
            job=await db.get(ProjectWorkJob,job_id)
            assert not job.follow_uploads and job.options['workflow_cancelled']
        assert (await db.get(ProjectWorkItem,saved.id)).result=={'description':'已完成'}
        assert (await db.get(ProjectWorkItem,running.id)).status=='cancelled'
        states=(await db.scalars(select(ProjectWorkItem.status).where(ProjectWorkItem.job_id==root.id))).all()
        assert states==['cancelled']


@pytest.mark.asyncio
async def test_cancelled_workflow_step_keeps_results_and_rejects_late_inputs(work_env,monkeypatch):
    from schemas.project_work import WorkflowCreate
    from services import project_work,project_workflow

    env=work_env
    async def detect(*args,**kwargs):
        return {'boxes':[dict(label='person',x=0,y=0,width=8,height=8)]}
    monkeypatch.setattr(project_work,'infer',detect)
    _,downstream=await project_workflow.create(env.db,env.project,WorkflowCreate(steps=[
        dict(operation='crop',model_id=env.models[0].id),
        dict(operation='caption',model_id=env.models[1].id),
    ]),env.user.id)
    saved=ProjectWorkItem(job_id=downstream.id,asset_id=env.asset.id,content_hash=env.asset.content_hash,
        status='completed',result={'description':'已完成'})
    running=ProjectWorkItem(job_id=downstream.id,content_hash='running',status='running',attempt=2)
    env.db.add_all([saved,running]);await env.db.commit()
    assert await project_work.action_job(env.db,env.project,downstream.id,'cancel')=={'changed_count':1}
    async with httpx.AsyncClient() as client:
        assert await project_work.process_one(env.sessions,client)
    async with env.sessions() as db:
        assert (await db.get(ProjectWorkItem,saved.id)).result=={'description':'已完成'}
        item=await db.get(ProjectWorkItem,running.id)
        assert (item.status,item.attempt)==('cancelled',3)
        items=(await db.scalars(select(ProjectWorkItem.id).where(ProjectWorkItem.job_id==downstream.id))).all()
        assert len(items)==2
        assert await db.scalar(select(Asset.id).where(Asset.source_type=='model_crop'))


@pytest.mark.asyncio
@pytest.mark.parametrize('mode',['success','cancel','interrupted','permanent','database'])
async def test_workflow_automatically_retries_failed_images(work_env,monkeypatch,mode):
    from fastapi import HTTPException
    from sqlalchemy.exc import DBAPIError
    from schemas.project_work import WorkflowCreate
    from services import project_work,project_workflow

    env=work_env
    now=1000
    calls=0
    monkeypatch.setattr(project_work,'time',lambda:now,raising=False)

    async def detect(*args,**kwargs):
        nonlocal calls
        calls+=1
        if mode=='permanent':raise HTTPException(400,'模型配置无效')
        if calls<3 and mode in ('success','cancel'):raise HTTPException(504,'模型服务超时，请稍后重试')
        return {'boxes':[]}

    monkeypatch.setattr(project_work,'infer',detect)
    if mode=='database':
        validate=project_work.validate_snapshots

        async def validate_after_deadlock(*args,lock=True):
            if lock and calls==1:
                cause=RuntimeError('deadlock detected')
                cause.sqlstate='40P01'
                raise DBAPIError(None,None,cause)
            return await validate(*args,lock=lock)

        monkeypatch.setattr(project_work,'validate_snapshots',validate_after_deadlock)
    job,=await project_workflow.create(env.db,env.project,WorkflowCreate(steps=[
        dict(operation='crop',model_id=env.models[0].id),
    ]),env.user.id)
    if mode=='interrupted':
        item=await env.db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
        item.status='running'
        await env.db.commit()
        await project_work.recover_running(env.sessions)

    async with httpx.AsyncClient() as client:
        assert await project_work.process_one(env.sessions,client)
        if mode not in ('interrupted','permanent'):
            async with env.sessions() as db:
                summary=await project_work.summary(db,job,details=True)
                assert summary['retrying_count']==1
                assert summary['items'][0]['auto_retry']
            assert not await project_work.process_one(env.sessions,client)
        if mode=='cancel':
            assert await project_workflow.cancel(env.db,env.project,job.id)=={'changed_count':1}
        if mode in ('success','database'):
            now+=60
            assert await project_work.process_one(env.sessions,client)
            assert not await project_work.process_one(env.sessions,client)
        if mode=='success':
            now+=60
            assert await project_work.process_one(env.sessions,client)
        now+=600
        assert not await project_work.process_one(env.sessions,client)
    async with env.sessions() as db:
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id))
        assert item.status=={'cancel':'cancelled','permanent':'failed'}.get(mode,'completed')
    assert calls=={'success':3,'database':2}.get(mode,1)


@pytest.mark.asyncio
async def test_worker_refills_available_slot_while_another_image_is_running(monkeypatch):
    import asyncio
    from services import project_work

    entered=0
    next_started=asyncio.Event()
    unblock=asyncio.Event()
    async def process(*args):
        nonlocal entered
        entered+=1
        if entered==1:await unblock.wait()
        elif entered==3:
            next_started.set()
            await unblock.wait()
        return True
    monkeypatch.setattr(project_work.settings,'PROJECT_WORK_CONCURRENCY',2)
    monkeypatch.setattr(project_work,'process_one',process)
    worker=asyncio.create_task(project_work.run_worker(None))
    try:
        await asyncio.wait_for(next_started.wait(),timeout=2)
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):await worker
