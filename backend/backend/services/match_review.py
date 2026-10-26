"""Independent query/image judgments, using the existing durable project queue."""
import json
from copy import deepcopy
from datetime import datetime,timezone
from io import BytesIO
from uuid import UUID
import httpx
from fastapi import HTTPException
from PIL import Image
from pydantic import BaseModel,ConfigDict,Field
from typing import Literal
from sqlalchemy import select
from models import Asset,Project,User,LibraryImage
from models.project_work import MatchQuery,ProjectWorkJob,ProjectWorkItem
from schemas.model_service import ModelServiceConfig
from services.annotation_scope import eligible_assets,require_annotation_asset
from services.automation_review import picture_content
from services.image_semantics import model_snapshot,validate_snapshots
from services.model_inference import headers,response_json
from services.annotations import record_event


class MatchVerdict(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    judgment:Literal['correct','incorrect','uncertain']
    reason:str=Field(min_length=1,max_length=1000)


def baseline(hit):
    return {key:hit.get(key,'pending' if key=='review_status' else None) for key in
        ('judgment','user_id','judged_at','review_status','reviewer_id','reviewed_at')}


async def audit_match(config,content,query,query_content=None,prompt='',client=None):
    if client is None:
        async with httpx.AsyncClient(follow_redirects=False) as session:
            return await audit_match(config,content,query,query_content,prompt,session)
    if config.protocol!='openai_chat':raise HTTPException(400,'匹配审核需要OpenAI视觉对话模型')
    parts=[{'type':'text','text':'查询数据：'+json.dumps({'text':query['text']} if query.get('text') else {'type':'image'},ensure_ascii=False)}]
    for label,raw in ([('查询参考图',query_content)] if query_content else [])+[('候选图片',content)]:
        with Image.open(BytesIO(raw)) as picture:
            parts.extend([{'type':'text','text':label},picture_content(picture.convert('RGB'),1024)])
    response=await response_json(client,'POST',config.base_url+config.endpoint,headers=headers(config),timeout=config.timeout_seconds,json={
        'model':config.model_name,'messages':[{'role':'system','content':config.chat_prompts.match_review+'\n补充要求：'+prompt+
            '\n仅返回JSON：{"judgment":"correct|incorrect|uncertain","reason":"可见证据理由"}。'},
            {'role':'user','content':parts}],'temperature':0,'max_tokens':512,'response_format':{'type':'json_object'},
        'chat_template_kwargs':{'enable_thinking':False}})
    try:return MatchVerdict.model_validate(json.loads(response['choices'][0]['message']['content'])).model_dump()
    except (ValueError,KeyError,TypeError,IndexError):raise HTTPException(502,'大模型匹配审核输出无效，转人工复核') from None


async def create_review_jobs(db,project,request,user_id):
    if project.task_type!='vector':raise HTTPException(400,'检索匹配审核仅适用于向量项目')
    actor=await db.get(User,user_id)
    if not actor or not actor.is_active or actor.role!='admin' or actor.tenant_id!=project.tenant_id:raise HTTPException(403,'匹配大模型审核需要管理员权限')
    snapshot=await model_snapshot(db,project.tenant_id,request.model_id,'caption')
    if snapshot['config']['protocol']!='openai_chat':raise HTTPException(400,'请选择OpenAI视觉对话审核模型')
    await db.scalar(select(Project).where(Project.id==project.id).with_for_update())
    query=select(MatchQuery).where(MatchQuery.project_id==project.id).order_by(MatchQuery.id)
    if request.query_ids:query=query.where(MatchQuery.id.in_(request.query_ids))
    queries=(await db.scalars(query.limit(1001).with_for_update())).all()
    if request.query_ids and len(queries)!=len(set(request.query_ids)):raise HTTPException(404,'部分查询不属于当前项目')
    if len(queries)>1000:raise HTTPException(400,'单批最多1000条查询，请按查询选择范围')
    assets={str(a.id):a for a in (await db.scalars(select(Asset).where(Asset.project_id==project.id,eligible_assets(project)))).all()}
    active={(str(qid),str(aid)) for qid,aid in (await db.execute(select(ProjectWorkJob.options['query_id'].as_string(),ProjectWorkItem.asset_id)
        .join(ProjectWorkItem,ProjectWorkItem.job_id==ProjectWorkJob.id).where(ProjectWorkJob.project_id==project.id,
            ProjectWorkJob.operation=='match_review',ProjectWorkItem.status.in_(('pending','running'))))).all()}
    jobs=[];total=0
    for query in queries:
        hits=[h for h in query.hits if h['asset_id'] in assets and h['judgment'] in ('correct','incorrect') and
            h.get('review_status','pending')=='pending' and (str(query.id),h['asset_id']) not in active and
            h.get('content_hash')==assets[h['asset_id']].content_hash]
        if not hits:continue
        total+=len(hits)
        if total>5000:raise HTTPException(400,'单批最多5000条匹配关系，请缩小查询范围')
        job=ProjectWorkJob(project_id=project.id,user_id=user_id,operation='match_review',model=snapshot,
            options={'query_id':str(query.id),'query':deepcopy(query.query),'prompt':request.prompt})
        db.add(job);await db.flush();jobs.append(job)
        db.add_all([ProjectWorkItem(job_id=job.id,asset_id=UUID(h['asset_id']),content_hash=h['content_hash'],base_version=0,
            result={'match_baseline':baseline(h)}) for h in hits])
    if not jobs:raise HTTPException(400,'没有可审核的待审匹配关系，或已有批次正在处理；无法判断和已审核关系不重复提交')
    await db.commit();return jobs


async def context(db,work,lock=False):
    project=await db.scalar(select(Project).where(Project.id==work['project_id']).with_for_update()) if lock else await db.get(Project,work['project_id'])
    asset=await db.get(Asset,work['asset_id']) if work['asset_id'] else None
    if not project or not asset or asset.project_id!=project.id or asset.content_hash!=work['content_hash']:
        raise HTTPException(409,'匹配图片已删除或变更')
    require_annotation_asset(project,asset)
    actor=await db.get(User,work['user_id'])
    if not actor or not actor.is_active or actor.tenant_id!=project.tenant_id or actor.role!='admin':raise HTTPException(403,'审核创建人已失去管理员权限')
    statement=select(MatchQuery).where(MatchQuery.project_id==project.id,MatchQuery.id==UUID(work['options']['query_id']))
    if lock:statement=statement.with_for_update()
    query=await db.scalar(statement.execution_options(populate_existing=True))
    hit=next((h for h in query.hits if h['asset_id']==str(asset.id)),None) if query else None
    if not query or query.query!=work['options']['query'] or not hit or baseline(hit)!=work['match_baseline']:
        raise HTTPException(409,'人工判定、审核或查询已修改，旧AI结果不会覆盖')
    reference=await query_reference(db,project,query.query)
    return project,asset,query,hit,reference


async def query_reference(db,project,query):
    reference=None
    if query.get('query_asset_id'):
        reference=await db.scalar(select(Asset).where(Asset.id==UUID(query['query_asset_id']),Asset.project_id==project.id,
            Asset.content_hash==query['content_hash']))
    elif query.get('image_id'):
        reference=await db.scalar(select(LibraryImage).where(LibraryImage.id==UUID(query['image_id']),
            LibraryImage.tenant_id==project.tenant_id,LibraryImage.content_hash==query['content_hash'],LibraryImage.source_type!='model_crop'))
    if (query.get('query_asset_id') or query.get('image_id')) and reference is None:
        raise HTTPException(409,'查询参考图片已删除或变更')
    return reference


async def process(sessions,work,client):
    from services.project_work import read_content
    verdict=None;failure=None
    try:
        async with sessions() as db:
            _,asset,_,_,reference=await context(db,work)
            await validate_snapshots(db,work['tenant_id'],[work['model']],lock=False)
            content=await read_content(asset);reference_content=await read_content(reference) if reference else None
        verdict=await audit_match(ModelServiceConfig(**work['model']['config']),content,work['options']['query'],reference_content,work['options']['prompt'],client)
    except HTTPException as exc:failure=str(exc.detail)[:500]
    except (ValueError,OSError,Image.DecompressionBombError):failure='匹配审核图片无法解码，转人工复核'
    async with sessions() as db:
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.id==work['item_id']).with_for_update())
        if not item or item.status!='running' or item.attempt!=work['attempt']:return True
        try:
            project,asset,query,hit,_=await context(db,work,lock=True)
            await validate_snapshots(db,work['tenant_id'],[work['model']])
        except HTTPException as exc:
            item.status='failed';item.error=str(exc.detail)[:500];await db.commit();return True
        approved=verdict is not None and verdict['judgment']==hit['judgment']
        now=datetime.now(timezone.utc).isoformat()
        evidence=dict(status='approved' if approved else 'needs_review',judgment=verdict['judgment'] if verdict else 'uncertain',
            reason=verdict['reason'] if verdict else failure or '大模型未完成判断，转人工复核',
            job_id=str(item.job_id),model_id=work['model']['id'],model_name=work['model']['config']['name'],revision=work['model']['revision'],
            reviewed_at=now,content_hash=asset.content_hash,template_version=1)
        hits=deepcopy(query.hits);current=next(h for h in hits if h['asset_id']==str(asset.id))
        current.update(review_status='approved' if approved else 'pending',reviewer_id=None,reviewed_at=now,ai_review=evidence)
        query.hits=hits;query.version+=1
        item.result={'match_baseline':{**work['match_baseline'],'reviewed_at':now},**evidence}
        # Retry compares the same human label and the pending review just written by this attempt.
        item.status='failed' if failure else 'completed';item.error=failure
        record_event(db,asset,work['user_id'],'match_ai_review',dict(query_id=str(query.id),version=query.version,**evidence))
        await db.commit()
    return True
