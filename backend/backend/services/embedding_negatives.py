"""Manually confirmed image negatives for a particular caption or attribute query."""
from uuid import NAMESPACE_URL, UUID, uuid5

from fastapi import HTTPException
from sqlalchemy import select

from models import Annotation, Asset
from models.project_work import MatchQuery
from services.annotation_scope import eligible_assets
from services.swift_export import sample_texts, text_messages


def query_text(annotation, source):
    if not annotation or annotation.annotation_data.get('skipped') or annotation.status_for('image')=='rejected':
        return ''
    return next(iter(sample_texts(annotation.annotation_data,'infonce',source)), '').strip()


def selection_id(project,asset_id,source):
    return uuid5(NAMESPACE_URL,f'embedding-negatives/{project.id}/{asset_id}/{source}')


async def configured(db, project, asset, source):
    if project.task_type!='caption':
        raise HTTPException(400,'人工负样本配置适用于描述与属性项目')
    annotation=await db.scalar(select(Annotation).where(Annotation.asset_id==asset.id,Annotation.is_latest.is_(True)))
    text=query_text(annotation,source)
    id=selection_id(project,asset.id,source)
    row=await db.get(MatchQuery,id,populate_existing=True)
    return id,row,text


async def candidate_rows(db,project,ids):
    rows=[]
    ids=list(ids)
    for offset in range(0,len(ids),4096):
        rows.extend((await db.execute(select(Asset,Annotation).outerjoin(Annotation,
            (Annotation.asset_id==Asset.id)&Annotation.is_latest.is_(True)).where(
            Asset.project_id==project.id,Asset.id.in_(ids[offset:offset+4096]),eligible_assets(project)))).all())
    return rows


def eligible_negative(positive, negative, annotation, source, text):
    return negative.content_hash!=positive.content_hash and not (
        annotation and (annotation.annotation_data.get('skipped') or annotation.status_for('image')=='rejected'
            or query_text(annotation,source)==text))


async def save(db,project,asset,source,request,user_id):
    id,row,text=await configured(db,project,asset,source)
    if not text or text!=request.text or request.version!=(row.version if row else 0):
        raise HTTPException(409,'描述、属性或负样本配置已变化，请重新读取')
    text_messages(text)
    candidates={image.id:(image,ann) for image,ann in await candidate_rows(db,project,request.asset_ids)}
    hits=checked_hits(asset,source,text,request.asset_ids,candidates,user_id)
    row=write_selection(db,id,row,project,asset,source,text,hits)
    await db.commit()
    return dict(text=text,version=row.version,asset_ids=request.asset_ids)


def checked_hits(asset,source,text,ids,candidates,user_id):
    hashes={asset.content_hash}
    hits=[]
    for image_id in ids:
        candidate=candidates.get(image_id)
        if not candidate or candidate[0].content_hash in hashes or not eligible_negative(asset,*candidate,source,text):
            raise HTTPException(422,'负样本必须是本项目中不同内容、与当前描述或属性不匹配的有效图片')
        image,_=candidate
        hashes.add(image.content_hash)
        hits.append(dict(asset_id=str(image.id),content_hash=image.content_hash,user_id=user_id))
    return hits


def write_selection(db,id,row,project,asset,source,text,hits):
    if row is None:
        row=MatchQuery(id=id,project_id=project.id,version=0,space={})
        db.add(row)
    row.query=dict(embedding_source=source,asset_id=str(asset.id),content_hash=asset.content_hash,text=text)
    row.hits=hits
    row.version+=1
    return row


async def resolve(db,project,rows,source,queries):
    candidates={str(asset.id):(asset,annotation) for asset,annotation in await candidate_rows(
        db,project,{UUID(hit['asset_id']) for row in queries for hit in row.hits})}
    by_anchor={row.query['asset_id']:row for row in queries}
    selected=[]
    assets={}
    negatives={}
    for asset,annotation in rows:
        row=by_anchor.get(str(asset.id))
        text=query_text(annotation,source)
        if not row or row.query['text']!=text or row.query['content_hash']!=asset.content_hash:continue
        valid=[]
        for hit in row.hits:
            candidate=candidates.get(hit['asset_id'])
            if not candidate or candidate[0].content_hash!=hit['content_hash'] or not eligible_negative(asset,*candidate,source,text):
                continue
            valid.append(candidate[0])
        if valid:
            selected.append((asset,annotation))
            negatives[str(asset.id)]=[str(image.id) for image in valid]
            assets.update({image.id:image for image in valid})
    return selected,assets,negatives
