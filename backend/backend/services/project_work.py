"""Serial, durable project inference; immutable inputs and fenced results."""
import asyncio
import hashlib
import json
import logging
import math
from copy import deepcopy
from datetime import datetime, timezone
from io import BytesIO
from uuid import UUID
import httpx
from PIL import Image,PngImagePlugin
from fastapi import HTTPException
from sqlalchemy import select, update, func
from models import Asset, Project, Annotation, User, LibraryImage
from models.project_work import ProjectWorkJob, ProjectWorkItem, MatchQuery
from schemas.model_service import ModelServiceConfig
from services.annotations import save_annotation, record_event
from services.image_semantics import model_snapshot, validate_snapshots, caption, embedding, PREPROCESSING
from services.annotation_scope import eligible_assets,require_annotation_asset
from services.model_inference import infer
from services.milvus import Milvus, space_key
from services.storage import get_storage
from services.automation import review_state

_worker=None
logger=logging.getLogger(__name__)


def crop_region(box,width,height,expansion):
    x,y,w,h=(box[key] for key in ('x','y','width','height'))
    return (max(0,math.floor(x-w*expansion)),max(0,math.floor(y-h*expansion)),
        min(width,math.ceil(x+w*(1+expansion))),min(height,math.ceil(y+h*(1+expansion))))


async def create_job(db,project,request,user_id):
    if request.operation=='caption' and project.task_type not in ('caption','vector'):
        raise HTTPException(400,'描述生成适用于描述或向量项目')
    if request.operation=='index' and project.task_type!='vector':
        raise HTTPException(400,'请在向量标注项目建立索引')
    images=(await db.scalars(select(Asset).where(Asset.project_id==project.id,Asset.id.in_(set(request.asset_ids)))
        .order_by(Asset.id).with_for_update())).all()
    if len(images)!=len(set(request.asset_ids)):raise HTTPException(404,'部分项目图片不存在')
    kind={'crop':'detection','caption':'caption','index':'embedding'}[request.operation]
    snapshot=await model_snapshot(db,project.tenant_id,request.model_id,kind)
    project=await db.scalar(select(Project).where(Project.id==project.id).with_for_update().execution_options(populate_existing=True))
    if request.operation!='crop':
        for image in images:require_annotation_asset(project,image)
    if request.operation=='crop':
        project.label_schema={**(project.label_schema or {}),'annotation_scope':'crops' if request.crops_only else 'all'}
    latest={a.asset_id:a for a in (await db.scalars(select(Annotation).where(
        Annotation.asset_id.in_([a.id for a in images]),Annotation.is_latest.is_(True)))).all()}
    job=ProjectWorkJob(project_id=project.id,user_id=user_id,operation=request.operation,model=snapshot,
        options=request.model_dump(mode='json',exclude={'operation','model_id','asset_ids'}))
    db.add(job);await db.flush()
    db.add_all([ProjectWorkItem(job_id=job.id,asset_id=a.id,content_hash=a.content_hash,
        base_version=latest[a.id].version if a.id in latest else 0,result={'review_state':review_state(latest.get(a.id))}) for a in images])
    await db.commit()
    return job


async def scoped_job(db,project,job_id):
    job=await db.scalar(select(ProjectWorkJob).where(ProjectWorkJob.id==job_id,ProjectWorkJob.project_id==project.id).with_for_update())
    if not job:raise HTTPException(404,'处理批次不存在')
    return job


async def summary(db,job,details=False,page=1):
    counts=dict((await db.execute(select(ProjectWorkItem.status,func.count()).where(ProjectWorkItem.job_id==job.id)
        .group_by(ProjectWorkItem.status))).all())
    result=dict(id=str(job.id),project_id=str(job.project_id),operation=job.operation,model_id=job.model['id'],
        revision=job.model['revision'],created_at=job.created_at,total=sum(counts.values()),counts=counts)
    if details:
        items=(await db.scalars(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id)
            .order_by(ProjectWorkItem.id).offset((page-1)*20).limit(20))).all()
        result.update(page=page,items=[dict(id=str(i.id),asset_id=str(i.asset_id) if i.asset_id else None,
            status=i.status,attempt=i.attempt,result=i.result,error=i.error) for i in items])
    return result


async def action_job(db,project,job_id,action):
    job=await scoped_job(db,project,job_id)
    items=(await db.scalars(select(ProjectWorkItem).where(ProjectWorkItem.job_id==job.id)
        .order_by(ProjectWorkItem.id).with_for_update())).all()
    if action=='retry':await validate_snapshots(db,project.tenant_id,[job.model],lock=False)
    count=0
    for item in items:
        if action=='cancel' and item.status in ('pending','running'):
            item.status='cancelled';item.attempt+=1;item.error='已取消';count+=1
        if action=='retry' and item.status in ('failed','cancelled') and item.asset_id:
            item.status='pending';item.attempt+=1;item.error=None;count+=1
    await db.commit()
    return {'changed_count':count}


async def read_content(asset):
    with await get_storage().get_file(asset.blob_path) as file:content=file.read()
    if hashlib.sha256(content).hexdigest()!=asset.content_hash:
        raise HTTPException(409,'图片内容与记录不一致，请重新导入')
    with Image.open(BytesIO(content)) as image:
        if image.size!=(asset.width,asset.height):raise HTTPException(409,'图片尺寸已变更')
    return content


async def finish(sessions,work,result,error):
    async with sessions() as db:
        asset=await db.scalar(select(Asset).where(Asset.id==work['asset_id']).with_for_update())
        project=await db.get(Project,work['project_id'])
        if not project:return
        item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.id==work['item_id']).with_for_update())
        if not item or item.status!='running' or item.attempt!=work['attempt']:return
        try:
            if not asset or asset.project_id!=project.id or asset.content_hash!=work['content_hash']:
                raise HTTPException(409,'图片已删除或变更')
            await validate_snapshots(db,project.tenant_id,[work['model']])
            actor=await db.get(User,work['user_id'])
            if not actor or not actor.is_active or actor.tenant_id!=project.tenant_id:
                raise HTTPException(403,'任务创建人已失去访问权限')
            if error:raise HTTPException(502,error)
            if work['operation']!='crop':require_annotation_asset(project,asset)
            if work['operation']=='caption':
                previous=await db.scalar(select(Annotation).where(Annotation.asset_id==asset.id,Annotation.is_latest.is_(True)))
                if review_state(previous)!=work['review_state']:
                    raise HTTPException(409,'人工审核结论已修改，请新建描述批次')
                data=deepcopy(previous.annotation_data) if previous else {'labels':[]}
                data.pop('skipped',None);data['description']=result['description']
                annotation=await save_annotation(db,asset,data,work['base_version'],work['user_id'],source='auto')
                result['annotation_id']=str(annotation.id)
            elif work['operation']=='crop':
                ids=[]
                root=(asset.device_info or {}).get('parent_image_id') or str(asset.image_id or asset.id)
                for crop in result['crops']:
                    raw=crop.pop('content');digest=hashlib.sha256(raw).hexdigest()
                    path=f'project-crops/{project.id}/{digest[:2]}/{digest}.png'
                    await get_storage().save_file(BytesIO(raw),path)
                    info={**(asset.device_info or {}),'parent_image_id':root,'crop':dict(
                        parent_asset_id=str(asset.id),parent_image_id=str(asset.image_id) if asset.image_id else None,
                        parent_blob_path=asset.blob_path,parent_content_hash=asset.content_hash,
                        region=crop['region'],box=crop['box'],expansion=work['options']['expansion'],
                        model_id=work['model']['id'],revision=work['model']['revision'])}
                    await db.scalar(select(Project).where(Project.id==project.id).with_for_update())
                    derived=await db.scalar(select(Asset).where(Asset.project_id==project.id,Asset.content_hash==digest))
                    if derived is None:
                        derived=Asset(project_id=project.id,image_id=None,blob_path=path,content_hash=digest,
                            original_name=f'{asset.id}_{digest[:12]}.png',file_size=len(raw),width=crop['width'],height=crop['height'],
                            format='PNG',source_type='model_crop',device_info=info,annotation_status='pending')
                        db.add(derived);await db.flush()
                        await db.execute(update(Project).where(Project.id==project.id).values(total_assets=Project.total_assets+1))
                    ids.append(str(derived.id))
                result=dict(asset_ids=list(dict.fromkeys(ids)),detected_count=len(ids),
                    warning='未检测到目标；仅标注裁剪图时，此原图不进入标注队列' if not ids else '')
                record_event(db,asset,work['user_id'],'crop',result)
            item.result=result;item.status='completed';item.error=None
            await db.commit()
        except HTTPException as exc:
            await db.rollback()
            # Rollback can expire ORM state. Recheck the fence in a new transaction.
            item=await db.scalar(select(ProjectWorkItem).where(ProjectWorkItem.id==work['item_id']).with_for_update())
            if item and item.status=='running' and item.attempt==work['attempt']:
                item.status='failed';item.error=str(exc.detail)[:500];await db.commit()


async def process_one(sessions,client):
    async with sessions() as db:
        item=await db.scalar(select(ProjectWorkItem).join(ProjectWorkJob).where(ProjectWorkItem.status=='pending')
            .order_by(ProjectWorkJob.created_at,ProjectWorkItem.id).with_for_update(of=ProjectWorkItem,skip_locked=True).limit(1))
        if not item:return False
        job=await db.get(ProjectWorkJob,item.job_id);project=await db.get(Project,job.project_id)
        item.status='running';item.attempt+=1
        work=dict(item_id=item.id,asset_id=item.asset_id,attempt=item.attempt,project_id=job.project_id,
            tenant_id=project.tenant_id,operation=job.operation,model=deepcopy(job.model),options=deepcopy(job.options),
            content_hash=item.content_hash,base_version=item.base_version,user_id=job.user_id)
        work['review_state']=(item.result or {}).get('review_state',{})
        work['match_baseline']=deepcopy((item.result or {}).get('match_baseline',{}))
        await db.commit()
    if work['operation']=='match_review':
        from services.match_review import process
        return await process(sessions,work,client)
    result=error=None
    try:
        async with sessions() as db:
            asset=await db.get(Asset,work['asset_id']) if work['asset_id'] else None
            if not asset or asset.content_hash!=work['content_hash']:raise HTTPException(409,'图片已删除或变更')
            if work['operation']!='crop':require_annotation_asset(await db.get(Project,work['project_id']),asset)
            content=await read_content(asset);width,height=asset.width,asset.height
            await validate_snapshots(db,work['tenant_id'],[work['model']],lock=False)
        config=ModelServiceConfig(**work['model']['config']);options=work['options']
        if work['operation']=='caption':
            result={'description':await caption(config,content,options['prompt'],client)}
        elif work['operation']=='index':
            vector=await embedding(config,content=content,client=client)
            key=f'{work["item_id"]}_{work["attempt"]}'
            await Milvus(client).upsert(key,vector,work['tenant_id'],work['project_id'],work['asset_id'],work['model'],work['content_hash'])
            result={'vector_id':key,'space':space_key(work['model'])}
        else:
            config=config.model_copy(update={'confidence':options['confidence'],'labels':options['labels'] or config.labels})
            detection=await infer(config,content,width,height,client=client)
            crops=[]
            with Image.open(BytesIO(content)) as image:
                image=image.convert('RGB')
                for box in detection['boxes']:
                    if options['labels'] and box['label'] not in {config.label_mapping.get(label,label) for label in options['labels']}:continue
                    region=crop_region(box,width,height,options['expansion'])
                    metadata=PngImagePlugin.PngInfo()
                    metadata.add_text('project_crop',json.dumps([str(work['project_id']),str(work['asset_id']),region]))
                    output=BytesIO();image.crop(region).save(output,'PNG',pnginfo=metadata)
                    crops.append(dict(content=output.getvalue(),width=region[2]-region[0],height=region[3]-region[1],region=list(region),box=box))
            result={'crops':crops}
    except HTTPException as exc:error=str(exc.detail)[:500]
    except (OSError,ValueError,Image.DecompressionBombError):error='图片或推理结果无法读取，请检查配置后重试'
    await finish(sessions,work,result,error)
    return True


def query_response(query):
    return dict(id=str(query.id),query=query.query,space=query.space,hits=query.hits,version=query.version,created_at=query.created_at)


async def query_with_content(db,query):
    response=query_response(query)
    project=await db.get(Project,query.project_id)
    allowed={str(id):source for id,source in (await db.execute(select(Asset.id,Asset.source_type).where(Asset.project_id==query.project_id,
        Asset.id.in_([UUID(h['asset_id']) for h in query.hits]),eligible_assets(project)))).all()}
    response['hits']=[{**hit,'source_type':allowed[hit['asset_id']]} for hit in response['hits'] if hit['asset_id'] in allowed]
    annotations=(await db.scalars(select(Annotation).join(Asset).where(Asset.project_id==query.project_id,
        Annotation.asset_id.in_([UUID(h['asset_id']) for h in query.hits]),Annotation.is_latest.is_(True)))).all()
    current={str(a.asset_id):a.annotation_data for a in annotations}
    response['hits']=[{**hit,'description':current.get(hit['asset_id'],{}).get('description',''),
        'labels':current.get(hit['asset_id'],{}).get('labels',[])} for hit in response['hits']]
    return response


async def search(db,project,request,client):
    if project.task_type!='vector':raise HTTPException(400,'请在向量标注项目检索')
    project_id,tenant_id=project.id,project.tenant_id
    snapshot=await model_snapshot(db,tenant_id,request.model_id,'embedding')
    key=space_key(snapshot)
    rows=(await db.execute(select(ProjectWorkItem,Asset).join(ProjectWorkJob,ProjectWorkJob.id==ProjectWorkItem.job_id)
        .join(Asset,Asset.id==ProjectWorkItem.asset_id).where(ProjectWorkJob.project_id==project_id,
            ProjectWorkJob.operation=='index',ProjectWorkItem.status=='completed',Asset.project_id==project_id,
            ProjectWorkItem.content_hash==Asset.content_hash,eligible_assets(project)).order_by(ProjectWorkJob.created_at,ProjectWorkJob.id))).all()
    by_asset={}
    for item,asset in rows:
        if item.result and item.result.get('space')==key:
            by_asset[asset.id]=(item.result['vector_id'],dict(asset_id=str(asset.id),content_hash=asset.content_hash))
    allowed=dict(by_asset.values())
    content=None
    query={'text':request.text} if request.text else {'image_id':str(request.image_id)}
    if request.image_id:
        image=await db.scalar(select(LibraryImage).where(LibraryImage.id==request.image_id,LibraryImage.tenant_id==tenant_id,LibraryImage.source_type!='model_crop'))
        if not image:raise HTTPException(404,'查询图片不存在')
        content=await read_content(image)
        query.update(content_hash=image.content_hash,blob_path=image.blob_path)
    if not allowed:raise HTTPException(409,'当前项目和模型版本尚无可用向量，请先建立索引')
    await db.rollback()
    vector=await embedding(ModelServiceConfig(**snapshot['config']),content=content,text=request.text,client=client)
    raw_hits=await Milvus(client).search(vector,tenant_id,project_id,key,list(allowed),request.top_k)
    await validate_snapshots(db,tenant_id,[snapshot])
    if request.image_id:
        present=await db.scalar(select(LibraryImage.id).where(LibraryImage.id==request.image_id,
            LibraryImage.tenant_id==tenant_id,LibraryImage.content_hash==query['content_hash']))
        if not present:raise HTTPException(409,'查询期间图片已删除或变更')
    assets=(await db.scalars(select(Asset).where(Asset.project_id==project_id,Asset.id.in_(
        [UUID(allowed[h['id']]['asset_id']) for h in raw_hits if h.get('id') in allowed])))).all()
    current={str(a.id):a for a in assets}
    hits=[];seen=set()
    for hit in raw_hits:
        meta=allowed.get(hit.get('id'));asset=current.get(meta['asset_id']) if meta else None
        if not asset or asset.content_hash!=meta['content_hash'] or hit.get('content_hash')!=asset.content_hash or asset.id in seen:continue
        seen.add(asset.id)
        hits.append(dict(asset_id=str(asset.id),image_id=str(asset.image_id) if asset.image_id else None,
            original_name=asset.original_name,content_hash=asset.content_hash,blob_path=asset.blob_path,
            score=hit['distance'],judgment='unknown'))
    saved=MatchQuery(project_id=project_id,query=query,space=dict(model_id=snapshot['id'],revision=snapshot['revision'],
        dimensions=2048,preprocessing=PREPROCESSING),hits=hits)
    db.add(saved);await db.commit()
    return await query_with_content(db,saved)


async def judge(db,project,query_id,asset_id,judgment,base_version,user_id,approved=None):
    query=await db.scalar(select(MatchQuery).where(MatchQuery.id==query_id,MatchQuery.project_id==project.id)
        .with_for_update().execution_options(populate_existing=True))
    if not query:raise HTTPException(404,'检索记录不存在')
    if base_version!=query.version:raise HTTPException(409,'匹配判定已修改，请重新加载')
    asset=await db.scalar(select(Asset).where(Asset.id==asset_id,Asset.project_id==project.id))
    hits=deepcopy(query.hits);hit=next((h for h in hits if h['asset_id']==str(asset_id)),None)
    if not asset or not hit:raise HTTPException(404,'图片不在本次检索结果中')
    require_annotation_asset(project,asset)
    if hit.get('content_hash') and hit['content_hash']!=asset.content_hash:raise HTTPException(409,'图片内容已变更')
    if approved is None:
        hit.pop('ai_review',None)
        hit.update(judgment=judgment,user_id=user_id,judged_at=datetime.now(timezone.utc).isoformat(),
            review_status='pending',reviewer_id=None,reviewed_at=None)
    else:
        if hit['judgment']=='unknown':raise HTTPException(400,'尚未判定的匹配关系不能审核')
        hit.update(review_status='approved' if approved else 'rejected',reviewer_id=user_id,reviewed_at=datetime.now(timezone.utc).isoformat())
        if hit.get('ai_review'):hit['ai_review']={**hit['ai_review'],'status':'resolved'}
    query.hits=hits;query.version+=1
    record_event(db,asset,user_id,'match_judgment' if approved is None else 'match_review',
        dict(query_id=str(query.id),judgment=hit['judgment'],review_status=hit['review_status'],version=query.version))
    await db.commit()
    return await query_with_content(db,query)


async def recover_running(sessions):
    async with sessions() as db:
        await db.execute(update(ProjectWorkItem).where(ProjectWorkItem.status=='running').values(
            status='failed',attempt=ProjectWorkItem.attempt+1,error='服务重启中断，请重试'))
        await db.commit()


async def run_worker(sessions):
    async with httpx.AsyncClient(follow_redirects=False) as client:
        while True:
            try:
                if not await process_one(sessions,client):await asyncio.sleep(1)
            except asyncio.CancelledError:raise
            except Exception as exc:
                logger.error('Project annotation worker failed: %s',type(exc).__name__)
                await recover_running(sessions);await asyncio.sleep(1)


async def start_worker():
    global _worker
    if _worker and not _worker.done():return
    from database import AsyncSessionLocal
    # shortcut: one backend process; use leases before running multiple workers.
    await recover_running(AsyncSessionLocal)
    _worker=asyncio.create_task(run_worker(AsyncSessionLocal))


async def stop_worker():
    global _worker
    if _worker:
        _worker.cancel()
        try:await _worker
        except asyncio.CancelledError:pass
        _worker=None
