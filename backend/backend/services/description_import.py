"""Import reviewed image descriptions from image/conversations JSON."""
import json
from collections import Counter, defaultdict
from pathlib import PurePosixPath

from fastapi import HTTPException
from sqlalchemy import func, select

from api.asset_routes import scoped_project
from config.settings import settings
from models import Annotation, Asset
from services.annotation_scope import eligible_assets


async def import_descriptions(db, project, file, user):
    from services.annotations import review, save_annotation

    if project.task_type!='caption':
        raise HTTPException(400,'描述标注导入仅支持图片描述与属性项目')
    raw=await file.read(settings.MAX_UPLOAD_SIZE+1)
    if len(raw)>settings.MAX_UPLOAD_SIZE:
        raise HTTPException(413,'描述标注文件超过上传大小限制')
    try:
        records=json.loads(raw.decode('utf-8-sig'))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise HTTPException(422,'请选择有效的 UTF-8 JSON 文件') from exc
    if not isinstance(records,list) or not records:
        raise HTTPException(422,'JSON 必须包含非空的图片标注数组')

    parsed=[]
    for index,record in enumerate(records,1):
        path=record.get('image') if isinstance(record,dict) else None
        name=PurePosixPath(path.replace('\\','/')).name if isinstance(path,str) else ''
        if name in ('.','..') or len(name)>255 or '\0' in name:
            name=''
        turns=record.get('conversations') if isinstance(record,dict) else None
        answers=[turn.get('value') for turn in turns if isinstance(turn,dict) and turn.get('from')=='gpt'] if isinstance(turns,list) else []
        answer=answers[0].strip() if len(answers)==1 and isinstance(answers[0],str) else ''
        parsed.append((name or f'第 {index} 条',name.lower(),answer))
    occurrences=Counter(key for _,key,_ in parsed if key)
    matches=defaultdict(list)
    for asset in (await db.scalars(select(Asset).where(Asset.project_id==project.id,eligible_assets(project),
            func.lower(Asset.original_name).in_(list(occurrences))).order_by(Asset.id).with_for_update())).all():
        matches[asset.original_name.lower()].append(asset)
    project=await scoped_project(db,project.id,user.tenant_id,for_update=True)
    if project.task_type!='caption':
        raise HTTPException(409,'项目标注格式已变化，请刷新后重试')
    existing={ann.asset_id:ann for ann in (await db.scalars(select(Annotation).where(
        Annotation.asset_id.in_([asset.id for assets in matches.values() for asset in assets]),
        Annotation.is_latest.is_(True)))).all()}
    items=[]
    for name,key,answer in parsed:
        item={'filename':name}
        items.append(item)
        assets=matches[key]
        if not key or not 1<=len(answer)<=20000 or '\0' in answer:
            item.update(status='invalid',message='需要图片文件名和一条有效的 gpt 回答')
        elif occurrences[key]>1 or len(assets)>1:
            item.update(status='conflict',message='JSON 或项目中存在同名图片')
        elif not assets:
            item.update(status='missing',message='项目标注范围内没有同名图片')
        else:
            asset=assets[0]
            item['asset_id']=asset.id
            previous=existing.get(asset.id)
            if previous is not None:
                if previous.annotation_data.get('description')==answer:
                    item.update(status='unchanged',message='已有相同描述，保留现有审核状态')
                else:
                    item.update(status='conflict',message='已有标注，保留原记录')
                continue
            annotation=await save_annotation(db,asset,{'description':answer},0,user.id,source='import_description',kind='image')
            await review(db,annotation,asset,True,'管理员导入已审核的描述标注',user.id,kind='image')
            item['status']='imported'
    await db.commit()
    return {'items':items}
