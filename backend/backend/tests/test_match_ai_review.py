"""Independent LLM matching verdicts and durable, fenced agreement workflow."""
import json
from uuid import UUID,uuid4
import httpx
import pytest
from sqlalchemy import select
from tests.test_project_work import work_env


async def pending_query(s,human='correct'):
    from models.project_work import MatchQuery
    s.project.task_type='vector'
    query=MatchQuery(project_id=s.project.id,query={'text':'穿黑色衣服的人'},space={'revision':1},hits=[
        dict(asset_id=str(s.asset.id),content_hash=s.asset.content_hash,original_name='sample.png',score=.9,judgment=human)])
    s.db.add(query);await s.db.commit();return query


async def enqueue(s,query):
    from services.match_review import create_review_jobs
    from schemas.project_work import MatchReviewBatch
    return await create_review_jobs(s.db,s.project,MatchReviewBatch(model_id=s.models[1].id,query_ids=[query.id],prompt='只依据可见衣服颜色判断'),s.user.id)


@pytest.mark.asyncio
@pytest.mark.parametrize('human,model,expected',[
    ('correct','correct','approved'),('incorrect','incorrect','approved'),
    ('correct','incorrect','pending'),('incorrect','correct','pending'),('correct','uncertain','pending')])
async def test_model_agreement_including_negatives_and_manual_disagreement(work_env,human,model,expected):
    from services.project_work import process_one
    from models.project_work import MatchQuery,ProjectWorkItem
    s=work_env;query=await pending_query(s,human);jobs=await enqueue(s,query);calls=[]
    def provider(request):
        body=json.loads(request.content);calls.append(body)
        texts=' '.join(message['content'] if isinstance(message['content'],str) else ' '.join(part.get('text','') for part in message['content']) for message in body['messages'])
        assert '穿黑色衣服的人' in texts and '只依据可见衣服颜色判断' in texts
        assert 'human_judgment' not in texts and '0.9' not in texts
        assert len([part for message in body['messages'] if isinstance(message['content'],list) for part in message['content'] if part['type']=='image_url'])==1
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'judgment':model,'reason':'衣服颜色的可见证据'})}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:assert await process_one(s.sessions,client)
    async with s.sessions() as db:
        saved=await db.get(MatchQuery,query.id);hit=saved.hits[0];item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==jobs[0].id))
        assert hit['judgment']==human and hit['review_status']==expected
        assert hit['ai_review']['judgment']==model
        assert hit['ai_review']['status']==('approved' if expected=='approved' else 'needs_review')
        assert item.status=='completed' and item.result['reason']=='衣服颜色的可见证据'
    assert len(calls)==1


@pytest.mark.asyncio
@pytest.mark.parametrize('change',['judgment','manual_review','model','delete','scope','cancel'])
async def test_late_ai_review_cannot_overwrite_changed_context(work_env,change):
    from services import project_work as service
    from models import Asset,Project,ModelService
    from models.project_work import MatchQuery,ProjectWorkItem
    s=work_env;query=await pending_query(s);jobs=await enqueue(s,query)
    async def provider(request):
        async with s.sessions() as db:
            current=await db.get(MatchQuery,query.id);project=await db.get(Project,s.project.id)
            if change in ('judgment','manual_review'):
                await service.judge(db,project,query.id,s.asset.id,'incorrect' if change=='judgment' else None,current.version,s.user.id,
                    False if change=='manual_review' else None)
            elif change=='model':
                row=await db.get(ModelService,s.models[1].id);row.revision+=1;await db.commit()
            elif change=='delete':await db.delete(await db.get(Asset,s.asset.id));await db.commit()
            elif change=='scope':project.label_schema={'annotation_scope':'crops'};await db.commit()
            else:await service.action_job(db,project,jobs[0].id,'cancel')
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"judgment":"correct","reason":"迟到结果"}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        saved=await db.get(MatchQuery,query.id)
        if change=='manual_review':assert saved.hits[0]['review_status']=='rejected'
        else:assert saved.hits[0].get('review_status')!='approved'
        assert not saved.hits[0].get('ai_review')
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==jobs[0].id))
        assert item.status in ('failed','cancelled')


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',['invalid','timeout'])
async def test_invalid_model_output_goes_to_manual_review_and_can_retry(work_env,failure):
    from services import project_work as service
    from models.project_work import MatchQuery,ProjectWorkItem
    s=work_env;query=await pending_query(s);jobs=await enqueue(s,query)
    def provider(request):
        if failure=='timeout':raise httpx.ReadTimeout('owned timeout',request=request)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"judgment":"correct"}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        saved=await db.get(MatchQuery,query.id);assert saved.hits[0]['review_status']=='pending'
        assert saved.hits[0]['ai_review']['status']=='needs_review'
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.job_id==jobs[0].id));assert item.status=='failed'
        project=await db.get(__import__('models').Project,s.project.id);await service.action_job(db,project,jobs[0].id,'retry')
    def success(request):return httpx.Response(200,json={'choices':[{'message':{'content':'{"judgment":"correct","reason":"重试成功"}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(success)) as client:await service.process_one(s.sessions,client)
    async with s.sessions() as db:assert (await db.get(MatchQuery,query.id)).hits[0]['review_status']=='approved'


@pytest.mark.asyncio
async def test_batch_does_not_duplicate_active_or_include_unknown_approved_and_foreign_queries(work_env):
    from services.match_review import create_review_jobs
    from schemas.project_work import MatchReviewBatch
    from fastapi import HTTPException
    s=work_env;query=await pending_query(s)
    await pending_query(s,'unknown');approved=await pending_query(s)
    approved.hits=[{**approved.hits[0],'review_status':'approved'}];await s.db.commit()
    assert len(await create_review_jobs(s.db,s.project,MatchReviewBatch(model_id=s.models[1].id),s.user.id))==1
    with pytest.raises(HTTPException) as exc:await enqueue(s,query)
    assert exc.value.status_code==400
    await s.db.commit()
    with pytest.raises(HTTPException) as exc:
        await create_review_jobs(s.db,s.project,MatchReviewBatch(model_id=s.models[1].id,query_ids=[uuid4()]),s.user.id)
    assert exc.value.status_code==404


@pytest.mark.asyncio
async def test_two_hits_in_one_query_are_reviewed_independently(work_env):
    from services.project_work import process_one
    from models import Asset
    from models.project_work import MatchQuery
    s=work_env;query=await pending_query(s)
    from PIL import Image
    from io import BytesIO
    import hashlib
    from services.storage import get_storage
    raw=BytesIO();Image.new('RGB',(64,48),'blue').save(raw,'PNG');raw=raw.getvalue()
    await get_storage().save_file(BytesIO(raw),'second.png')
    other=Asset(project_id=s.project.id,original_name='second.png',blob_path='second.png',content_hash=hashlib.sha256(raw).hexdigest(),
        source_type='upload',width=s.asset.width,height=s.asset.height,file_size=len(raw),format='PNG',annotation_status='pending')
    s.db.add(other);await s.db.flush()
    query.hits=[*query.hits,{**query.hits[0],'asset_id':str(other.id),'content_hash':other.content_hash}];await s.db.commit();await enqueue(s,query)
    def provider(request):return httpx.Response(200,json={'choices':[{'message':{'content':'{"judgment":"correct","reason":"独立核验"}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        assert await process_one(s.sessions,client);assert await process_one(s.sessions,client)
    async with s.sessions() as db:assert all(hit['review_status']=='approved' for hit in (await db.get(MatchQuery,query.id)).hits)


@pytest.mark.asyncio
async def test_disputed_relationship_cannot_be_exported_until_manual_resolution(work_env):
    from services import project_work as service
    from services.annotation_export import training_package
    from fastapi import HTTPException
    from models.project_work import MatchQuery
    s=work_env;query=await pending_query(s)
    with pytest.raises(HTTPException):await training_package(s.db,s.project,dataset_format='infonce',sample_source='matches')
    await s.db.commit();await enqueue(s,query)
    def provider(request):return httpx.Response(200,json={'choices':[{'message':{'content':'{"judgment":"incorrect","reason":"与人工不同"}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:await service.process_one(s.sessions,client)
    async with s.sessions() as db:
        project=await db.get(__import__('models').Project,s.project.id)
        with pytest.raises(HTTPException):await training_package(db,project,approved_only=False,dataset_format='infonce',sample_source='matches')
        current=await db.get(MatchQuery,query.id)
        await service.judge(db,project,query.id,s.asset.id,None,current.version,s.user.id,True)
        stream,manifest=await training_package(db,project,dataset_format='infonce',sample_source='matches')
        assert manifest['training']['samples']==1;stream.close()


@pytest.mark.asyncio
async def test_image_query_sends_two_independent_images_and_editable_prompt(work_env):
    from services.match_review import audit_match
    from services.project_work import read_content
    from schemas.model_service import ModelServiceConfig
    s=work_env;content=await read_content(s.asset)
    config=ModelServiceConfig.model_validate(s.models[1]).model_copy(update={})
    config.chat_prompts.match_review='仅核对主体服饰颜色，不比较背景。'
    calls=[]
    def provider(request):
        body=json.loads(request.content);calls.append(body)
        assert body['messages'][0]['content'].startswith('仅核对主体服饰颜色')
        parts=body['messages'][1]['content'];assert len([p for p in parts if p['type']=='image_url'])==2
        assert [p['text'] for p in parts if p['type']=='text'][-2:]==['查询参考图','候选图片']
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"judgment":"uncertain","reason":"服饰细节不清楚"}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
        assert (await audit_match(config,content,{'image_id':str(uuid4())},content,client=client))['judgment']=='uncertain'
    assert len(calls)==1


@pytest.mark.asyncio
@pytest.mark.parametrize('delete_reference',[False,True])
async def test_image_query_worker_checks_reference_and_rejects_late_deletion(work_env,delete_reference):
    from models import LibraryImage
    from models.project_work import MatchQuery
    from services.project_work import process_one
    s=work_env;query=await pending_query(s)
    reference=LibraryImage(tenant_id=s.project.tenant_id,blob_path=s.asset.blob_path,content_hash=s.asset.content_hash,
        original_name='reference.png',file_size=s.asset.file_size,width=s.asset.width,height=s.asset.height,format='PNG',source_type='upload')
    s.db.add(reference);await s.db.flush();reference_id=reference.id
    query.query={'image_id':str(reference.id),'blob_path':reference.blob_path,'content_hash':reference.content_hash}
    await s.db.commit();await enqueue(s,query)
    async def provider(request):
        parts=json.loads(request.content)['messages'][1]['content'];assert sum(p['type']=='image_url' for p in parts)==2
        if delete_reference:
            async with s.sessions() as db:await db.delete(await db.get(LibraryImage,reference_id));await db.commit()
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"judgment":"correct","reason":"参考图语义匹配"}'}}]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:await process_one(s.sessions,client)
    async with s.sessions() as db:
        hit=(await db.get(MatchQuery,query.id)).hits[0]
        assert (hit.get('review_status')=='approved') is not delete_reference


@pytest.mark.asyncio
async def test_review_batch_api_requires_admin_and_scoped_queries(client,auth_headers,work_env):
    s=work_env;query=await pending_query(s);url=f'/api/projects/{s.project.id}/matches/review-jobs'
    body={'model_id':str(s.models[1].id),'query_ids':[str(query.id)],'prompt':'检查颜色'}
    assert client.post(url,json=body).status_code==401
    assert client.post(url,headers=auth_headers,json={**body,'query_ids':[str(uuid4())]}).status_code==404
    s.user.role='annotator';await s.db.commit()
    assert client.post(url,headers=auth_headers,json=body).status_code==403
    s.user.role='admin';await s.db.commit()
    result=client.post(url,headers=auth_headers,json=body)
    assert result.status_code==201,result.text
    assert result.json()[0]['operation']=='match_review' and result.json()[0]['total']==1
