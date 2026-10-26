"""Durable, bounded scene inference outside image-import transactions."""
import asyncio,base64,json,logging
from datetime import datetime,timezone
from io import BytesIO
import httpx
from PIL import Image
from sqlalchemy import select,update
from models import LibraryImage,ImageCollection
from schemas.image_ingest import ImageIngestConfig
from uuid import UUID
from services.image_ingest import get_config
from services.storage import get_storage
from config.settings import settings

logger=logging.getLogger(__name__)
_worker=None

async def classify_one(sessions,client):
    async with sessions() as db:
        image=await db.scalar(select(LibraryImage).where(LibraryImage.scene_status=='pending')
            .order_by(LibraryImage.created_at,LibraryImage.id).with_for_update(skip_locked=True).limit(1))
        if image is None:return False
        collection_id=UUID(image.scene_config['collection_id']) if image.scene_config else None
        if image.scene_config:
            config=ImageIngestConfig.model_validate(image.scene_config['config']);revision=image.scene_config['revision']
        else:config,revision=await get_config(db,image.tenant_id)
        image.scene_status='processing';image.scene_attempt+=1
        image_id,tenant_id,attempt,path=image.id,image.tenant_id,image.scene_attempt,image.blob_path
        await db.commit()
    labels=None;error=None
    try:
        if not config.classification_enabled:raise ValueError('场景分类已关闭，请开启后重试')
        with await get_storage().get_file(path) as file:
            with Image.open(file) as picture:
                picture=picture.convert('RGB');picture.thumbnail((1024,1024))
                output=BytesIO();picture.save(output,format='JPEG',quality=85)
        prompt=config.scene_prompt+'\n系统候选名称：'+json.dumps(config.scenes,ensure_ascii=False)+\
            '\n输出约束：只返回JSON对象 {"labels":["候选名称"]}；labels只能使用候选名称，没有匹配时为空数组。'
        headers={'Authorization':'Bearer '+settings.QWEN_API_KEY} if settings.QWEN_API_KEY else {}
        response=await client.post(config.base_url+'/chat/completions',headers=headers,json={
            'model':config.model,'messages':[{'role':'user','content':[{'type':'text','text':prompt},
                {'type':'image_url','image_url':{'url':'data:image/jpeg;base64,'+base64.b64encode(output.getvalue()).decode()}}]}],
            'temperature':0,'max_tokens':512,'response_format':{'type':'json_object'},
            'chat_template_kwargs':{'enable_thinking':False}})
        response.raise_for_status()
        content=response.json()['choices'][0]['message']['content']
        if not isinstance(content,str) or len(content)>16384:raise ValueError('模型返回不是有效的场景JSON')
        labels=json.loads(content)['labels']
        if not isinstance(labels,list) or any(not isinstance(label,str) or label not in config.scenes for label in labels):
            raise ValueError('模型返回了自定义场景名单之外的标签')
        labels=list(dict.fromkeys(labels))
    except httpx.TimeoutException:error='场景分类超时，图片已保留，可重试'
    except httpx.HTTPError:error='模型服务请求失败，图片已保留，请检查服务后重试'
    except (OSError,ValueError,KeyError,TypeError,IndexError):error='模型结果无效或图片读取失败，图片已保留，可重试'
    async with sessions() as db:
        if collection_id:
            group=await db.get(ImageCollection,collection_id)
            current_revision=group.ingest_revision if group else None
        else:_,current_revision=await get_config(db,tenant_id)
        if current_revision!=revision:error='分类期间配置已更改，请用新配置重试'
        values={'scene_status':'failed','scene_error':error} if error else {
            'scene_status':'completed','scene_error':None,'scene_labels':labels,
            'scene_metadata':{'model':config.model,'collection_id':str(collection_id),'settings_revision':revision,'classified_at':datetime.now(timezone.utc).isoformat()}}
        await db.execute(update(LibraryImage).where(LibraryImage.id==image_id,
            LibraryImage.scene_attempt==attempt,LibraryImage.scene_status=='processing').values(**values))
        await db.commit()
    return True

async def run_worker(sessions):
    async with httpx.AsyncClient(timeout=30) as client:
        async def consume():
            while True:
                try:
                    if not await classify_one(sessions,client):await asyncio.sleep(1)
                except asyncio.CancelledError:raise
                except Exception as exc:
                    logger.error('Scene worker failed: %s',type(exc).__name__)
                    await asyncio.sleep(1)
        async with asyncio.TaskGroup() as group:
            for _ in range(settings.SCENE_CLASSIFICATION_CONCURRENCY):group.create_task(consume())

async def start_worker():
    global _worker
    from database import AsyncSessionLocal
    # shortcut: single backend process recovery; add leases before multi-instance deployment.
    async with AsyncSessionLocal() as db:
        await db.execute(update(LibraryImage).where(LibraryImage.scene_status=='processing').values(
            scene_status='failed',scene_error='服务重启导致分类中断，图片已保留，请重试'))
        await db.commit()
    _worker=asyncio.create_task(run_worker(AsyncSessionLocal))

async def stop_worker():
    if _worker:
        _worker.cancel()
        try:await _worker
        except asyncio.CancelledError:pass
