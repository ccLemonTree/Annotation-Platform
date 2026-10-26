"""External SmartEye results as private crops and ordinary matching queries."""
import asyncio
import hashlib
import json
import math
from io import BytesIO
from urllib.parse import urljoin, urlsplit

import httpx
from fastapi import HTTPException
from PIL import Image, PngImagePlugin
from sqlalchemy import select, update
from models import Asset, Project
from models.project_work import MatchQuery
from integrations.smarteye.api.db_utils import get_smart_search_platforms
from integrations.smarteye.api import smart_search as remote
from services.storage import get_storage


def image_url(value,base_url):
    url=urljoin(base_url.rstrip('/')+'/',value)
    parsed,base=urlsplit(url),urlsplit(base_url)
    if parsed.scheme not in ('http','https') or parsed.username or parsed.password or (
        parsed.scheme,parsed.hostname,parsed.port)!=(base.scheme,base.hostname,base.port):
        raise ValueError('图片地址不属于当前平台')
    return url


async def download_image(client,url):
    chunks=[];size=0
    async with client.stream('GET',url,timeout=30) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            size+=len(chunk)
            if size>30*1024*1024:raise ValueError('单张图片超过30MB')
            chunks.append(chunk)
    return b''.join(chunks)


def crop_image(content,record,identity):
    try:values=[float(record[key]) for key in ('x1','y1','x2','y2')]
    except (TypeError,KeyError):raise ValueError('目标框坐标缺失') from None
    if not all(math.isfinite(v) and 0<=v<=1 for v in values):raise ValueError('目标框坐标无效')
    x1,y1,x2,y2=values
    if x2<=x1 or y2<=y1:raise ValueError('目标框面积必须大于0')
    with Image.open(BytesIO(content)) as source:
        source.load()
        width,height=source.size
        region=[math.floor(x1*width),math.floor(y1*height),math.ceil(x2*width),math.ceil(y2*height)]
        metadata=PngImagePlugin.PngInfo()
        metadata.add_text('project_crop',json.dumps([identity,hashlib.sha256(content).hexdigest(),region]))
        output=BytesIO();source.convert('RGB').crop(region).save(output,'PNG',pnginfo=metadata)
    return output.getvalue(),region


async def original(db,project,query_id,asset_id):
    query=await db.scalar(select(MatchQuery).where(MatchQuery.id==query_id,MatchQuery.project_id==project.id))
    if not query or not any(h['asset_id']==str(asset_id) for h in query.hits):raise HTTPException(404,'图片不属于此查询')
    asset=await db.scalar(select(Asset).where(Asset.id==asset_id,Asset.project_id==project.id))
    info=(asset.device_info or {}).get('smart_search') if asset else None
    if not info:raise HTTPException(404,'此图片没有智搜原图')
    with await get_storage().get_file(info['original_blob_path']) as file:raw=file.read()
    if hashlib.sha256(raw).hexdigest()!=info['original_content_hash']:raise HTTPException(409,'查询原图已变更')
    return raw,info


async def save_crop(db,project,raw,info,region):
    digest=hashlib.sha256(raw).hexdigest()
    path=f'project-crops/{project.id}/{digest[:2]}/{digest}.png'
    await get_storage().save_file(BytesIO(raw),path)
    asset=await db.scalar(select(Asset).where(Asset.project_id==project.id,Asset.content_hash==digest))
    if asset is None:
        asset=Asset(project_id=project.id,blob_path=path,content_hash=digest,source_type='model_crop',
            original_name=f'smart-search-{digest[:12]}.png',file_size=len(raw),format='PNG',
            width=region[2]-region[0],height=region[3]-region[1],annotation_status='pending',
            device_info={'parent_image_id':info['original_content_hash'],'smart_search':{**info,'region':region}})
        db.add(asset);await db.flush()
        await db.execute(update(Project).where(Project.id==project.id).values(total_assets=Project.total_assets+1))
    return asset


async def platforms():
    rows=await asyncio.to_thread(get_smart_search_platforms,only_active=True)
    return [dict(id=p['plat_id'],name=p['platform_name']) for p in rows]


async def search(db,project,request,client):
    from services.project_work import query_with_content
    if project.task_type!='vector':raise HTTPException(400,'请在向量标注项目使用智搜')
    project_id,tenant_id=project.id,project.tenant_id
    rows=await asyncio.to_thread(get_smart_search_platforms,only_active=True)
    platform=next((p for p in rows if p['plat_id']==request.platform_id),None)
    if not platform:raise HTTPException(404,'平台不存在或已停用')
    params=dict(startTime=request.start_time.strftime('%Y-%m-%d %H:%M:%S'),endTime=request.end_time.strftime('%Y-%m-%d %H:%M:%S'))
    reference=None
    if request.text is not None:params.update(text=request.text,limit=request.limit)
    else:
        content,info=await original(db,project,request.reference_query_id,request.reference_asset_id)
        region=request.region
        if region.x+region.width>info['original_width'] or region.y+region.height>info['original_height']:
            raise HTTPException(422,'框选范围不能超出原图')
        reference_record=dict(x1=region.x/info['original_width'],y1=region.y/info['original_height'],
            x2=(region.x+region.width)/info['original_width'],y2=(region.y+region.height)/info['original_height'])
        raw,box=await asyncio.to_thread(crop_image,content,reference_record,str(project_id))
        reference=(raw,info,box)
        params.update(picUrl=info['image_url'],**region.model_dump())
    # Do not hold database transactions during platform login/search/download.
    await db.rollback()
    login=await asyncio.to_thread(remote.login_platform,platform)
    if not login.get('success'):raise HTTPException(502,login.get('msg') or '智搜平台登录失败')
    result=await asyncio.to_thread(remote.search_platform,platform,login,params)
    if not result.get('success'):raise HTTPException(502,result.get('msg') or '智搜平台搜索失败')
    records=result.get('records')
    if not isinstance(records,list):raise HTTPException(502,'智搜平台未返回图片与框坐标记录')
    semaphore=asyncio.Semaphore(4)
    async def prepare(index,row):
        async with semaphore:
            try:
                url=image_url(row['imageUrl'],platform['base_url'])
                content=await download_image(client,url)
                raw,region=await asyncio.to_thread(crop_image,content,row,str(project_id))
                with Image.open(BytesIO(content)) as image:width,height=image.size
                score=float(row.get('confidence') or 0)
                if not math.isfinite(score):raise ValueError('返回分值无效')
                info=dict(platform_id=platform['plat_id'],platform_name=platform['platform_name'],image_url=url,
                    original_width=width,original_height=height,original_content_hash=hashlib.sha256(content).hexdigest(),
                    record={k:row.get(k) for k in ('x1','y1','x2','y2','confidence','timestamp','deviceId','deviceName','channelNumber','channelName')})
                return dict(raw=raw,content=content,info=info,region=region,score=score)
            except (httpx.HTTPError,OSError,ValueError,TypeError,KeyError,Image.DecompressionBombError) as exc:
                return dict(index=index+1,error='图片下载失败' if isinstance(exc,httpx.HTTPError) else '图片或目标框无法读取')
    prepared=await asyncio.gather(*(prepare(i,row) for i,row in enumerate(records[:request.limit])))
    project=await db.scalar(select(Project).where(Project.id==project_id,Project.tenant_id==tenant_id).with_for_update())
    if not project:raise HTTPException(404,'项目已删除')
    hits=[];failures=[];seen=set()
    for item in prepared:
        if 'error' in item:failures.append(item);continue
        info=item['info']
        info['original_blob_path']=f'project-crops/{project_id}/originals/{info["original_content_hash"]}'
        await get_storage().save_file(BytesIO(item['content']),info['original_blob_path'])
        asset=await save_crop(db,project,item['raw'],info,item['region'])
        if asset.id in seen:continue
        seen.add(asset.id)
        hits.append(dict(asset_id=str(asset.id),image_id=None,original_name=asset.original_name,content_hash=asset.content_hash,
            blob_path=asset.blob_path,score=item['score'],judgment='unknown',smart_search=dict(
                platform_id=info['platform_id'],platform_name=info['platform_name'],region=item['region'],
                original_width=info['original_width'],original_height=info['original_height'],record=info['record'])))
    query={'text':request.text} if request.text is not None else {}
    if reference:
        raw,info,box=reference
        asset=await save_crop(db,project,raw,info,box)
        query.update(query_asset_id=str(asset.id),blob_path=asset.blob_path,content_hash=asset.content_hash,
            reference_query_id=str(request.reference_query_id),reference_asset_id=str(request.reference_asset_id),region=request.region.model_dump())
    query.update(source='smart_search',platform_id=platform['plat_id'],platform_name=platform['platform_name'],
        start_time=params['startTime'],end_time=params['endTime'],limit=request.limit)
    saved=MatchQuery(project_id=project_id,query=query,space=dict(source='smart_search',revision=1,failures=failures),hits=hits)
    db.add(saved);await db.commit()
    response=await query_with_content(db,saved);response['failures']=failures
    return response
