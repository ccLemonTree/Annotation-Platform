"""Default portable image/classification/description/matching training packages."""
import asyncio
import hashlib
import json
import tempfile
import zipfile
from uuid import UUID
from pathlib import PurePosixPath
from fastapi import HTTPException
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool
from models import Asset, Annotation
from models.project_work import MatchQuery
from services.storage import get_storage
from services.annotation_scope import eligible_assets
from services.swift_export import dataset


def exportable_match(hit,approved_only):
    status=hit.get('review_status','pending')
    return hit['judgment'] in ('correct','incorrect') and status!='rejected' and (
        status=='approved' or (not approved_only and (hit.get('ai_review') or {}).get('status')!='needs_review'))


async def training_package(db,project,approved_only=True,dataset_format='portable',sample_source='descriptions',instruction='请描述这张图片。'):
    if project.task_type not in ('classification','caption','vector'):
        raise HTTPException(400,'此导出适用于分类、描述和向量项目')
    if dataset_format!='portable' and sample_source=='matches' and project.task_type!='vector':
        raise HTTPException(400,'检索匹配判定数据仅适用于向量项目')
    rows=(await db.execute(select(Asset,Annotation).join(Annotation,Annotation.asset_id==Asset.id)
        .where(Asset.project_id==project.id,Annotation.is_latest.is_(True),eligible_assets(project)))).all()
    rows=[(asset,ann) for asset,ann in rows if not ann.annotation_data.get('skipped') and (not approved_only or ann.review_status=='approved')]
    queries=(await db.scalars(select(MatchQuery).where(MatchQuery.project_id==project.id).order_by(MatchQuery.id))).all() if project.task_type=='vector' else []
    assets={a.id:a for a,_ in rows}
    query_ids={UUID(h['asset_id']) for q in queries for h in q.hits if exportable_match(h,approved_only)}
    if query_ids:
        assets.update({a.id:a for a in (await db.scalars(select(Asset).where(Asset.project_id==project.id,Asset.id.in_(query_ids),eligible_assets(project)))).all()})
    if not assets:raise HTTPException(400,'没有符合审核范围的标注或已判定的匹配结果')
    return await run_in_threadpool(lambda:asyncio.run(build_package(project,rows,list(assets.values()),queries,approved_only,dataset_format,sample_source,instruction)))


async def build_package(project,rows,assets,queries,approved_only,dataset_format,sample_source,instruction):
    result=tempfile.SpooledTemporaryFile(max_size=8*1024*1024)
    manifest=dict(project_id=str(project.id),task_type=project.task_type,label_schema=project.label_schema,
        approved_only=approved_only,assets=[],queries=[],note='同一parent_image_id必须保留在同一训练/验证划分；本包未自动划分')
    annotations={a.id:ann for a,ann in rows};paths={};documents=[]
    async def add_file(archive,path,blob,expected):
        try:
            with await get_storage().get_file(blob) as source,archive.open(path,'w') as target:
                digest=hashlib.sha256()
                for chunk in iter(lambda:source.read(65536),b''):digest.update(chunk);target.write(chunk)
            if digest.hexdigest()!=expected:raise HTTPException(409,'导出图片内容已变更')
        except (OSError,ValueError):raise HTTPException(409,'导出图片不可读')
    try:
        with zipfile.ZipFile(result,'w',zipfile.ZIP_DEFLATED) as archive:
            labels=(project.label_schema or {}).get('labels',[])
            for asset in sorted(assets,key=lambda a:a.id):
                ann=annotations.get(asset.id);data=ann.annotation_data if ann else {}
                extension={'JPEG':'jpg','PNG':'png','WEBP':'webp','BMP':'bmp'}.get(asset.format,'img')
                folder='images'
                if project.task_type=='classification' and (project.label_schema or {}).get('classification_mode','single')=='single':
                    folder=f'classes/{labels.index(data["labels"][0]):03d}'
                path=f'{folder}/{asset.id}.{extension}';paths[str(asset.id)]=path
                await add_file(archive,path,asset.blob_path,asset.content_hash)
                source=asset.device_info or {}
                row=dict(asset_id=str(asset.id),image=path,content_hash=asset.content_hash,source=source,
                    labels=data.get('labels',[]),description=data.get('description',''),
                    annotation_version=ann.version if ann else None,review_status=ann.review_status if ann else None)
                documents.append(row);manifest['assets'].append(row)
            paired=[]
            for query in queries:
                positives=[];negatives=[];unknown=[]
                for hit in query.hits:
                    if hit['judgment']=='unknown':unknown.append(hit['asset_id']);continue
                    if not exportable_match(hit,approved_only):continue
                    asset_id=hit['asset_id']
                    asset=next((a for a in assets if str(a.id)==asset_id),None)
                    if not asset or asset.content_hash!=hit['content_hash']:continue
                    entry=dict(image=paths[asset_id],asset_id=asset_id,score=hit['score'],judgment=hit['judgment'],
                        user_id=hit.get('user_id'),judged_at=hit.get('judged_at'),review_status=hit.get('review_status','pending'),
                        reviewer_id=hit.get('reviewer_id'),reviewed_at=hit.get('reviewed_at'),ai_review=hit.get('ai_review'))
                    (positives if hit['judgment']=='correct' else negatives).append(entry)
                if not positives and not negatives:continue
                request=dict(query.query)
                if request.get('image_id') or request.get('query_asset_id'):
                    extension=PurePosixPath(request['blob_path'].replace('\\','/')).suffix.lower()
                    path=f'queries/{query.id}'+(extension if extension in ('.jpg','.jpeg','.png','.webp','.bmp') else '.img')
                    await add_file(archive,path,request.pop('blob_path'),request['content_hash']);request['image']=path
                row=dict(query_id=str(query.id),query=request,space=query.space,version=query.version,
                    positives=positives,negatives=negatives,unknown=unknown)
                paired.append(row);manifest['queries'].append(str(query.id))
            archive.writestr('annotations.jsonl',''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in documents))
            if project.task_type=='vector':archive.writestr('queries.jsonl',''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in paired))
            if dataset_format!='portable':
                samples,report=dataset(documents,paired,dataset_format,sample_source,instruction)
                archive.writestr('train.jsonl',''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in samples))
                manifest['training']=dict(format=dataset_format,source=sample_source,**report,
                    label_policy='人工正确=1、错误=0；相似度不作训练标签',instruction=instruction if dataset_format=='swift_sft' else None)
                archive.writestr('README.txt','本包的 train.jsonl 为 ms-swift 训练数据。先解压完整 ZIP，再进入解压目录运行训练；图片路径相对此目录。\n'
                    'annotations.jsonl、queries.jsonl 为来源数据，勿与 train.jsonl 混合加载。仅人工判错的检索图片作为负样本；无法判断不参与。\n'
                    'InfoNCE 每行一个正样本，同一查询的多个正样本拆行；其他正样本不转为 hard negative。训练时避免将相同查询的其他正样本误作 in-batch negative。\n'
                    'cosine_similarity 当前使用人工正确/错误的 1.0/0.0，不使用检索相似度。loss 与 INFONCE_* 环境变量在训练端设置。\n'
                    '未自动切分训练/验证集，同一原图的裁剪图必须保持在同一划分。\n')
            archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        result.seek(0)
        return result,manifest
    except BaseException:
        result.close();raise
