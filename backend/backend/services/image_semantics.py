"""Single-process durable semantics; full vectors stay server-side."""
import asyncio
import logging
import math
from uuid import UUID
import httpx
from fastapi import HTTPException
from sqlalchemy import select, update, func
from models.image import LibraryImage
from models.image_collection import ImageCollection, ImageCollectionMember
from models.model_service import ModelService
from models.image_semantic import ImageSemanticJob, ImageSemanticItem, ImageSemanticVector
from schemas.model_service import ModelServiceConfig
from services.model_inference import headers, response_json, image_content
from services.storage import get_storage

PREPROCESSING = 'rgb-jpeg90-thumbnail1024-image-prompt-v1'
IMAGE_PROMPT = '表示这张图片的可见内容。'
MAX_INDEX_RECORDS = 5000
_worker = None
logger = logging.getLogger(__name__)


async def image_scope(db, tenant_id, collection_id=None):
    conditions = [LibraryImage.tenant_id == tenant_id,LibraryImage.source_type!='model_crop']
    if collection_id:
        if not await db.scalar(select(ImageCollection.id).where(ImageCollection.id == collection_id, ImageCollection.tenant_id == tenant_id)):
            raise HTTPException(404, '图片集不存在')
        conditions.append(LibraryImage.id.in_(select(ImageCollectionMember.image_id).where(ImageCollectionMember.collection_id == collection_id)))
    return conditions


async def model_snapshot(db, tenant_id, model_id, kind):
    row = await db.scalar(select(ModelService).where(ModelService.id == model_id, ModelService.tenant_id == tenant_id))
    if row is None:
        raise HTTPException(404, '模型配置不存在')
    if not row.enabled or row.kind != kind:
        raise HTTPException(400, '请选择已启用且用途匹配的模型')
    return dict(id=str(row.id), revision=row.revision, config=ModelServiceConfig.model_validate(row).model_dump())


async def validate_snapshots(db, tenant_id, snapshots, lock=True):
    # Lock model rows against configuration edits until the write commits.
    for snapshot in sorted((value for value in snapshots if value), key=lambda value: value['id']):
        statement=select(ModelService).where(ModelService.id == UUID(snapshot['id']), ModelService.tenant_id == tenant_id)
        if lock:statement=statement.with_for_update()
        row = await db.scalar(statement.execution_options(populate_existing=True))
        if row is None or row.revision != snapshot['revision'] or ModelServiceConfig.model_validate(row).model_dump() != ModelServiceConfig.model_validate(snapshot['config']).model_dump():
            raise HTTPException(409, '模型配置已变更，请按当前配置新建批次')


async def create_job(db, tenant_id, request):
    conditions = await image_scope(db, tenant_id, request.collection_id)
    if request.image_ids:
        conditions.append(LibraryImage.id.in_(set(request.image_ids)))
    images = (await db.scalars(select(LibraryImage).where(*conditions).order_by(LibraryImage.id).limit(MAX_INDEX_RECORDS + 1).with_for_update())).all()
    if len(images) > MAX_INDEX_RECORDS:
        raise HTTPException(409, '单批最多5000张图片，请缩小选择范围')
    if not images or (request.image_ids and len(images) != len(set(request.image_ids))):
        raise HTTPException(404, '部分图片不存在或不属于所选图片集')
    caption = await model_snapshot(db, tenant_id, request.caption_model_id, 'caption') if request.caption_model_id else None
    embedding = await model_snapshot(db, tenant_id, request.embedding_model_id, 'embedding') if request.embedding_model_id else None
    job = ImageSemanticJob(tenant_id=tenant_id, collection_id=request.collection_id, caption_model=caption,
                           embedding_model=embedding, dimensions=request.dimensions, preprocessing=PREPROCESSING, prompt=request.prompt)
    db.add(job); await db.flush()
    db.add_all([ImageSemanticItem(job_id=job.id, image_id=image.id, original_name=image.original_name, content_hash=image.content_hash) for image in images])
    await db.commit()
    return job


async def scoped_job(db, tenant_id, job_id, lock=False):
    query = select(ImageSemanticJob).where(ImageSemanticJob.id == job_id, ImageSemanticJob.tenant_id == tenant_id)
    if lock: query = query.with_for_update()
    job = await db.scalar(query)
    if job is None: raise HTTPException(404, '语义批次不存在')
    return job


async def job_summary(db, job, include_items=False, page=1, page_size=50):
    counts = dict((await db.execute(select(ImageSemanticItem.status, func.count()).where(ImageSemanticItem.job_id == job.id)
                                   .group_by(ImageSemanticItem.status))).all())
    result = dict(id=str(job.id), collection_id=str(job.collection_id) if job.collection_id else None,
                  caption_model_id=job.caption_model['id'] if job.caption_model else None,
                  embedding_model_id=job.embedding_model['id'] if job.embedding_model else None,
                  dimensions=job.dimensions, preprocessing=job.preprocessing, created_at=job.created_at,
                  counts=counts, total=sum(counts.values()))
    if include_items:
        items = (await db.scalars(select(ImageSemanticItem).where(ImageSemanticItem.job_id == job.id).order_by(ImageSemanticItem.id)
                                  .offset((page-1)*page_size).limit(page_size))).all()
        result.update(page=page, page_size=page_size, items=[dict(id=str(item.id), image_id=str(item.image_id) if item.image_id else None,
            original_name=item.original_name, status=item.status, attempt=item.attempt, caption=item.caption, error=item.error) for item in items])
    return result


async def action_job(db, tenant_id, job_id, action, item_ids=None):
    job = await scoped_job(db, tenant_id, job_id, lock=True)
    conditions = [ImageSemanticItem.job_id == job.id]
    if item_ids: conditions.append(ImageSemanticItem.id.in_(set(item_ids)))
    items = (await db.scalars(select(ImageSemanticItem).where(*conditions).order_by(ImageSemanticItem.id).with_for_update())).all()
    if item_ids and len(items) != len(set(item_ids)): raise HTTPException(404, '批次项目不存在')
    if action == 'retry':
        await validate_snapshots(db, tenant_id, [job.caption_model, job.embedding_model], lock=False)
    changed = 0
    for item in items:
        if action == 'cancel' and item.status in ('pending', 'running'):
            item.status='cancelled'; item.attempt+=1; item.error='已取消'; changed+=1
        elif action == 'retry' and item.status in ('failed', 'cancelled') and item.image_id:
            conditions = await image_scope(db, tenant_id, job.collection_id)
            if not await db.scalar(select(LibraryImage.id).where(*conditions, LibraryImage.id == item.image_id, LibraryImage.content_hash == item.content_hash)):
                raise HTTPException(409, '图片已删除、变更或移出图片集，请新建批次')
            item.status='pending'; item.attempt+=1; item.error=None; changed+=1
    await db.commit()
    return dict(changed_count=changed)


def full_vector(value, dimensions=2048):
    if not isinstance(value, list) or len(value) != dimensions or any(isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v) for v in value):
        raise HTTPException(502, f'Embedding必须返回完整{dimensions}维有限数值向量')
    norm = math.hypot(*value)
    if not math.isfinite(norm) or norm == 0: raise HTTPException(502, 'Embedding返回零向量或无效范数')
    return value


async def embedding(config, *, content=None, text=None, client):
    multimodal = image_content(content, IMAGE_PROMPT) if content is not None else [{'type':'text','text':text}]
    data = await response_json(client, 'POST', config.base_url + config.endpoint, headers=headers(config),
                               timeout=config.timeout_seconds, json=dict(model=config.model_name,
                               messages=[dict(role='user',content=multimodal)], encoding_format='float'))
    try: return full_vector(data['data'][0]['embedding'])
    except (KeyError, IndexError, TypeError): raise HTTPException(502, 'Embedding响应格式无效')


async def caption(config, content, prompt, client):
    kwargs = dict(headers=headers(config), timeout=config.timeout_seconds)
    if config.protocol == 'model_service':
        kwargs['files'] = {'image':('image.bin',content,'application/octet-stream')}
    else:
        kwargs['json'] = dict(model=config.model_name,messages=[dict(role='user',content=image_content(content,prompt or config.chat_prompts.caption))],
                              temperature=0,max_tokens=512,chat_template_kwargs={'enable_thinking':False})
    data = await response_json(client,'POST',config.base_url+config.endpoint,**kwargs)
    try: value=data['caption'] if config.protocol == 'model_service' else data['choices'][0]['message']['content']
    except (KeyError,IndexError,TypeError): raise HTTPException(502,'描述响应格式无效')
    if not isinstance(value,str) or not value.strip() or len(value)>20000: raise HTTPException(502,'描述文本无效')
    return value.strip()


async def finish_item(sessions, work, description, vector, error):
    async with sessions() as db:
        image = await db.scalar(select(LibraryImage).where(LibraryImage.id == work['image_id'], LibraryImage.tenant_id == work['tenant_id']).with_for_update())
        try:
            if image is None: raise HTTPException(409,'图片已删除')
            if image.content_hash!=work['content_hash']: raise HTTPException(409,'图片内容已变更，请新建批次')
            conditions = await image_scope(db,work['tenant_id'],work['collection_id'])
            if not await db.scalar(select(LibraryImage.id).where(*conditions,LibraryImage.id==image.id)): raise HTTPException(409,'图片已移出图片集')
            if work['collection_id']:
                member=await db.scalar(select(ImageCollectionMember).where(ImageCollectionMember.collection_id==work['collection_id'],
                    ImageCollectionMember.image_id==image.id).with_for_update())
                if member is None: raise HTTPException(409,'图片已移出图片集')
            await validate_snapshots(db,work['tenant_id'],[work['caption_model'],work['embedding_model']])
        except HTTPException as exc: error=exc.detail
        await db.scalar(select(ImageSemanticJob).where(ImageSemanticJob.id==work['job_id']).with_for_update())
        item = await db.scalar(select(ImageSemanticItem).where(ImageSemanticItem.id==work['item_id']).with_for_update())
        if item is None or item.status!='running' or item.attempt!=work['attempt']:
            return
        if error:
            item.status='failed';item.error=error
        else:
            item.caption=description;item.status='completed';item.error=None
            if vector is not None:
                snapshot=work['embedding_model']
                row=await db.scalar(select(ImageSemanticVector).where(ImageSemanticVector.image_id==image.id,
                    ImageSemanticVector.model_id==UUID(snapshot['id']),ImageSemanticVector.revision==snapshot['revision'],
                    ImageSemanticVector.dimensions==work['dimensions'],ImageSemanticVector.preprocessing==work['preprocessing']))
                if row is None:
                    row=ImageSemanticVector(tenant_id=work['tenant_id'],image_id=image.id,model_id=UUID(snapshot['id']),
                        revision=snapshot['revision'],dimensions=work['dimensions'],preprocessing=work['preprocessing'])
                    db.add(row)
                row.vector=vector;row.caption=description;row.content_hash=work['content_hash']
        await db.commit()


async def process_one(sessions, client):
    async with sessions() as db:
        item=await db.scalar(select(ImageSemanticItem).join(ImageSemanticJob).where(ImageSemanticItem.status=='pending')
            .order_by(ImageSemanticJob.created_at,ImageSemanticItem.id).with_for_update(of=ImageSemanticItem,skip_locked=True).limit(1))
        if item is None:return False
        job=await db.get(ImageSemanticJob,item.job_id)
        item.status='running';item.attempt+=1
        image=await db.get(LibraryImage,item.image_id) if item.image_id else None
        work=dict(item_id=item.id,job_id=job.id,image_id=item.image_id,tenant_id=job.tenant_id,collection_id=job.collection_id,
            attempt=item.attempt,caption_model=job.caption_model,embedding_model=job.embedding_model,
            dimensions=job.dimensions,preprocessing=job.preprocessing,prompt=job.prompt,content_hash=item.content_hash,
            path=image.blob_path if image and image.content_hash==item.content_hash else None)
        await db.commit()
    description=vector=error=None
    try:
        if not work['path']:raise HTTPException(409,'图片已删除或内容已变更，请新建批次')
        async with sessions() as db:await validate_snapshots(db,work['tenant_id'],[work['caption_model'],work['embedding_model']])
        with await get_storage().get_file(work['path']) as file:content=file.read()
        if work['caption_model']:description=await caption(ModelServiceConfig(**work['caption_model']['config']),content,work['prompt'],client)
        if work['embedding_model']:vector=await embedding(ModelServiceConfig(**work['embedding_model']['config']),content=content,client=client)
    except HTTPException as exc:error=str(exc.detail)[:500]
    except (OSError, ValueError):error='图片无法读取或模型结果无效，原图已保留，可重试'
    await finish_item(sessions,work,description,vector,error)
    return True


async def search(db,tenant_id,request,client):
    snapshot=await model_snapshot(db,tenant_id,request.embedding_model_id,'embedding')
    conditions=await image_scope(db,tenant_id,request.collection_id)
    rows=(await db.execute(select(ImageSemanticVector,LibraryImage).join(LibraryImage,LibraryImage.id==ImageSemanticVector.image_id)
        .where(*conditions,ImageSemanticVector.content_hash==LibraryImage.content_hash,ImageSemanticVector.tenant_id==tenant_id,ImageSemanticVector.model_id==request.embedding_model_id,
               ImageSemanticVector.revision==snapshot['revision'],ImageSemanticVector.dimensions==request.dimensions,
               ImageSemanticVector.preprocessing==PREPROCESSING).limit(MAX_INDEX_RECORDS+1))).all()
    if len(rows)>MAX_INDEX_RECORDS:raise HTTPException(409,'当前索引超过5000条，无法执行精确检索，请选择更小的图片集；未截断结果')
    content=None
    if request.image_id:
        image=await db.scalar(select(LibraryImage).where(*conditions,LibraryImage.id==request.image_id))
        if image is None:raise HTTPException(404,'查询图片不存在或不属于所选图片集')
        with await get_storage().get_file(image.blob_path) as file:content=file.read()
    candidates=[dict(image_id=image.id,content_hash=row.content_hash,original_name=image.original_name,caption=row.caption,vector=row.vector) for row,image in rows]
    # Release the read transaction during remote inference; recheck config before responding.
    await db.rollback()
    return await search_vectors(db,tenant_id,request,client,snapshot,candidates,content)


async def search_vectors(db,tenant_id,request,client,snapshot,rows,content):
    query=await embedding(ModelServiceConfig(**snapshot['config']),content=content,text=request.text,client=client)
    await validate_snapshots(db,tenant_id,[snapshot])
    conditions=await image_scope(db,tenant_id,request.collection_id)
    allowed=dict((await db.execute(select(LibraryImage.id,LibraryImage.content_hash).where(*conditions,LibraryImage.id.in_([row['image_id'] for row in rows])))).all())
    if request.image_id and request.image_id not in set((await db.scalars(select(LibraryImage.id).where(*conditions,LibraryImage.id==request.image_id))).all()):
        raise HTTPException(409,'查询期间图片已删除或已移出图片集')
    norm=math.hypot(*query)
    hits=[]
    for row in rows:
        if allowed.get(row['image_id'])!=row['content_hash']:continue
        vector=full_vector(row['vector'],request.dimensions);vector_norm=math.hypot(*vector)
        score=math.fsum((a/norm)*(b/vector_norm) for a,b in zip(query,vector))
        hits.append(dict(image_id=str(row['image_id']),original_name=row['original_name'],caption=row['caption'],score=max(-1,min(1,score))))
    hits.sort(key=lambda hit:(-hit['score'],hit['image_id']))
    return dict(items=hits[:request.top_k],indexed_count=len(rows),limit=MAX_INDEX_RECORDS,
        space=dict(model_id=snapshot['id'],revision=snapshot['revision'],dimensions=request.dimensions,preprocessing=PREPROCESSING),
        warning='相似度仅用于检索，不代表标注正确性')


async def recover_running(sessions):
    async with sessions() as db:
        await db.execute(update(ImageSemanticItem).where(ImageSemanticItem.status=='running').values(status='failed',
            attempt=ImageSemanticItem.attempt+1,error='服务重启导致推理中断，原图已保留，请重试'))
        await db.commit()


async def run_worker(sessions):
    async with httpx.AsyncClient(follow_redirects=False) as client:
        while True:
            try:
                if not await process_one(sessions,client):await asyncio.sleep(1)
            except asyncio.CancelledError:raise
            except Exception as exc:
                logger.error('Semantic worker failed: %s',type(exc).__name__)
                await asyncio.sleep(1)


async def start_worker():
    global _worker
    if _worker and not _worker.done():return
    from database import AsyncSessionLocal
    # shortcut: single backend process only; use leases before deploying multiple instances.
    await recover_running(AsyncSessionLocal)
    _worker=asyncio.create_task(run_worker(AsyncSessionLocal))


async def stop_worker():
    global _worker
    if _worker:
        _worker.cancel()
        try:await _worker
        except asyncio.CancelledError:pass
        _worker=None
