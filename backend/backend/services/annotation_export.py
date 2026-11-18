"""Default portable image/classification/description/matching training packages."""
import asyncio
import hashlib
import json
import tempfile
import zipfile
import csv
from io import StringIO
from types import SimpleNamespace
from uuid import UUID
from pathlib import PurePosixPath
from fastapi import HTTPException
from sqlalchemy import cast, func, or_, select, true
from starlette.concurrency import run_in_threadpool
from models import Asset, Annotation
from models.project_work import MatchQuery
from services.image_library import iter_id_conditions
from services.storage import get_storage
from services.annotation_scope import eligible_assets
from services.swift_export import ATTRIBUTE_SOURCES,dataset,sample_texts


def _ann_status_for_image(review_status, review_by_kind):
    return (review_by_kind or {}).get('image', review_status)


def _compute_preview_token(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, separators=(',', ':'), ensure_ascii=False).encode()
    ).hexdigest()


def exportable_match(hit,approved_only):
    status=hit.get('review_status','pending')
    return hit['judgment'] in ('correct','incorrect') and status!='rejected' and (
        status=='approved' or (not approved_only and (hit.get('ai_review') or {}).get('status')!='needs_review'))


def prepare_single_attribute_pairs(rows,dataset_format,single_attribute):
    from services.single_attribute_export import attribute_pairs

    limit=single_attribute.get('sample_limit')
    options={key:value for key,value in single_attribute.items() if key!='sample_limit'}
    pairs=[]
    seen={}
    count=0
    for text,anchor,candidates in attribute_pairs(rows,**options):
        if dataset_format!='infonce' and single_attribute['negative_count']:
            ids=seen.setdefault(text,set())
            available=single_attribute['negative_count']-len(ids)
            if limit is not None:available=min(available,limit-count-1)
            if count==0 and available<=0:
                raise HTTPException(400,'此格式至少需要冻结两条记录，才能同时包含正负样本')
            if not ids and available<=0:break
            candidates=[candidate for candidate in candidates if candidate not in ids][:max(available,0)]
            ids.update(candidates)
            count+=len(candidates)
        pairs.append((text,anchor,candidates))
        count+=1
        if limit is not None and count>=limit:break
    member_ids={anchor for _,anchor,_ in pairs}
    negative_ids={candidate for _,_,candidates in pairs for candidate in candidates}
    member_ids.update(negative_ids)
    negative_assets={a.id:a for a,_ in rows if str(a.id) in negative_ids}
    return pairs,[(a,ann) for a,ann in rows if str(a.id) in member_ids],negative_assets


async def training_package(db,project,approved_only=True,dataset_format='portable',sample_source='descriptions',instruction='请描述这张图片。',attribute_filters=None,
                           *,scope=(),source=None,preview=False,preview_token=None,explicit_negatives=False,automatic_negative_job_id=None,single_attribute=None,progress=None):
    negative_queries=[]
    negatives={}
    negative_assets={}
    single=sample_source=='single_attribute'
    if single and (dataset_format not in ('infonce','contrastive','online_contrastive','cosine_similarity') or project.task_type not in ('caption','vector') or single_attribute is None or explicit_negatives or automatic_negative_job_id):
        raise HTTPException(400,'单属性配对需要选择向量格式')
    if single:
        scope=(*scope,or_(Annotation.annotation_data['attributes'].as_string().is_not(None),
                         func.ltrim(Annotation.annotation_data['description'].as_string()).like('{%')))
    with_negatives=explicit_negatives or automatic_negative_job_id is not None
    if with_negatives and (project.task_type!='caption' or dataset_format!='infonce' or sample_source not in ('descriptions',*ATTRIBUTE_SOURCES)):
        raise HTTPException(400,'显式负样本仅适用于描述与属性项目的 InfoNCE 文图配对')
    if explicit_negatives and automatic_negative_job_id:
        raise HTTPException(400,'请选择一种负样本来源')
    if explicit_negatives:
        from models import ProjectWorkItem,ProjectWorkJob
        from services.embedding_recommendation_jobs import OPERATION
        confirming=await db.scalar(select(ProjectWorkItem.id).join(ProjectWorkJob).where(
            ProjectWorkJob.project_id==project.id,ProjectWorkJob.operation==OPERATION,
            ProjectWorkJob.options['source'].as_string()==sample_source,
            ProjectWorkItem.status.in_(('pending','running')),
            ProjectWorkItem.result['phase'].as_string()=='confirm').limit(1))
        if confirming:raise HTTPException(409,'负样本正在统一确认，完成后再预览或冻结')
        negative_scope=(MatchQuery.project_id==project.id,MatchQuery.query['embedding_source'].as_string()==sample_source)
        negative_queries=(await db.scalars(select(MatchQuery).where(*negative_scope).order_by(MatchQuery.id))).all()
        anchor=MatchQuery.query['asset_id'].as_string()
        anchor=cast(anchor,Asset.id.type) if db.bind.dialect.name=='postgresql' else func.replace(anchor,'-','')
        scope=(*scope,Asset.id.in_(select(anchor).where(*negative_scope)))
    if dataset_format!='portable' and sample_source=='matches' and project.task_type!='vector':
        raise HTTPException(400,'检索匹配判定数据仅适用于向量项目')
    lightweight_preview = preview and not with_negatives and attribute_filters is None and not single
    if attribute_filters is not None:
        from services.object_attributes import attribute_query
        rows=(await db.execute(attribute_query(project,**attribute_filters).where(Annotation.id.is_not(None),*scope))).all()
        rows=[(asset,ann) for asset,ann in rows if 'attributes' in ann.annotation_data and ann.status_for('image')!='rejected']
    elif lightweight_preview:
        rows = []
        stream = await db.stream(
            select(
                Asset.id,
                Asset.content_hash,
                Annotation.version,
                Annotation.review_status,
                Annotation.review_by_kind,
                Annotation.annotation_data,
            )
            .join(Annotation, Annotation.asset_id == Asset.id)
            .where(Annotation.project_id == project.id, (Annotation.is_latest == true()), eligible_assets(project), *scope)
            .execution_options(yield_per=2000)
        )
        async for a_id, a_hash, ann_ver, rev_st, rev_by_kind, ann_data in stream:
            if ann_data.get('description') or ann_data.get('labels') or ann_data.get('attributes'):
                stub_asset = SimpleNamespace(id=a_id, content_hash=a_hash)
                stub_ann = SimpleNamespace(
                    version=ann_ver,
                    review_status=rev_st,
                    review_by_kind=rev_by_kind,
                    annotation_data=ann_data,
                    status_for=lambda k, rs=rev_st, rbk=rev_by_kind: (rbk or {}).get(k, rs),
                )
                rows.append((stub_asset, stub_ann))
    else:
        rows = []
        stream = await db.stream(
            select(Asset, Annotation)
            .join(Annotation, Annotation.asset_id == Asset.id)
            .where(Annotation.project_id == project.id, (Annotation.is_latest == true()), eligible_assets(project), *scope)
            .execution_options(yield_per=2000)
        )
        async for asset, ann in stream:
            if ann.annotation_data.get('description') or ann.annotation_data.get('labels') or ann.annotation_data.get('attributes'):
                rows.append((asset, ann))
    allowed=('approved',) if approved_only else ('approved','pending')
    rows=[(asset,ann) for asset,ann in rows if not ann.annotation_data.get('skipped') and ann.status_for('image') in allowed]
    if dataset_format!='portable' and not single:
        rows=[(asset,ann) for asset,ann in rows if sample_texts(ann.annotation_data,dataset_format,sample_source)]
    include_queries=project.task_type=='vector' and attribute_filters is None and (dataset_format=='portable' or sample_source=='matches')
    if include_queries and preview:
        q_rows = (await db.execute(
            select(MatchQuery.id, MatchQuery.version, MatchQuery.hits)
            .where(MatchQuery.project_id == project.id, MatchQuery.is_retrieval_query())
            .order_by(MatchQuery.id)
        )).all()
        queries = [SimpleNamespace(id=qid, version=qver, hits=qhits) for qid, qver, qhits in q_rows]
    else:
        queries=(await db.scalars(select(MatchQuery).where(MatchQuery.project_id==project.id,
            MatchQuery.is_retrieval_query()).order_by(MatchQuery.id))).all() if include_queries else []
    if explicit_negatives:
        from services.embedding_negatives import resolve
        rows,negative_assets,negatives=await resolve(db,project,rows,sample_source,negative_queries)
    elif automatic_negative_job_id:
        from services.embedding_recommendation_jobs import automatic_pairs
        rows,negative_assets,negatives=await automatic_pairs(db,project,automatic_negative_job_id,rows,sample_source,approved_only,source)
    pairs=None
    if single:
        pairs,rows,negative_assets=await run_in_threadpool(prepare_single_attribute_pairs,rows,dataset_format,single_attribute)
    assets={a.id:a for a,_ in rows}
    assets.update(negative_assets)
    query_ids={UUID(h['asset_id']) for q in queries for h in q.hits if exportable_match(h,approved_only)}
    if query_ids:
        skipped=select(Annotation.id).where(Annotation.asset_id==Asset.id,(Annotation.is_latest == true()),
            Annotation.annotation_data['skipped'].as_boolean().is_(True)).exists()
        for cond in iter_id_conditions(db, Asset.id, sorted(query_ids)):
            if preview:
                for q_aid, q_hash in (await db.execute(
                    select(Asset.id, Asset.content_hash).where(Asset.project_id==project.id, cond, eligible_assets(project), ~skipped, *scope)
                )).all():
                    assets.setdefault(q_aid, SimpleNamespace(id=q_aid, content_hash=q_hash))
            else:
                for q_asset in (await db.scalars(select(Asset).where(Asset.project_id==project.id, cond, eligible_assets(project), ~skipped, *scope))).all():
                    assets[q_asset.id] = q_asset
    if preview or preview_token:
        members=sorted((str(a.id),a.content_hash,ann.version,ann.status_for('image')) for a,ann in rows)
        token_payload = [
            source, dataset_format, sample_source, instruction, approved_only, members,
            sorted((str(a.id), a.content_hash) for a in assets.values()),
            [(str(q.id), q.version) for q in queries],
            str(automatic_negative_job_id) if automatic_negative_job_id else explicit_negatives,
            sorted(negatives.items()), single_attribute, pairs,
        ]
        token = await run_in_threadpool(_compute_preview_token, token_payload)
        if preview:return dict(preview_token=token,asset_count=len(assets),**(
            dict(sample_count=(len(pairs) if dataset_format=='infonce' else sum(1+len(candidates) for _,_,candidates in pairs)) if single else len(rows),
                negative_image_count=len(negative_assets)) if with_negatives or single else {}))
        if preview_token!=token:raise HTTPException(409,'图片或标注版本已变化，请重新预览后再冻结')
    if not assets:
        raise HTTPException(400,'所选范围没有可导出的单属性配对' if single else
            '所选范围没有有效正负样本配对' if with_negatives else '没有符合审核范围的标注或已判定的匹配结果')
    if progress:
        await db.commit()
    return await run_in_threadpool(lambda:asyncio.run(build_package(project,rows,assets,queries,approved_only,dataset_format,
        sample_source,instruction,attribute_filters,source,confirmed_negatives=negatives,automatic_negatives=automatic_negative_job_id is not None,
        attribute_pairs=pairs,single_attribute=single_attribute if single else None,progress=progress)))


async def build_package(project,rows,assets,queries,approved_only,dataset_format,sample_source,instruction,attribute_filters=None,source=None,*,confirmed_negatives=None,automatic_negatives=False,attribute_pairs=None,single_attribute=None,progress=None):
    result=tempfile.SpooledTemporaryFile(max_size=8*1024*1024)
    manifest=dict(project_id=str(project.id),task_type=project.task_type,label_schema=project.label_schema,
        approved_only=approved_only,assets=[],queries=[],note='同一parent_image_id必须保留在同一训练/验证划分；本包未自动划分')
    if attribute_filters is not None:manifest['attribute_filters']=attribute_filters
    if source:manifest['input']=source
    annotations={a.id:ann for a,ann in rows};paths={};documents=[]
    async def add_file(archive,path,blob,expected):
        try:
            with await get_storage().get_file(blob) as source,archive.open(path,'w') as target:
                digest=hashlib.sha256()
                for chunk in iter(lambda:source.read(65536),b''):digest.update(chunk);target.write(chunk)
            if digest.hexdigest()!=expected:raise HTTPException(409,'导出图片内容已变更')
        except (OSError,ValueError):raise HTTPException(409,'导出图片不可读')
    try:
        if progress:
            progress(0, len(assets))
        with zipfile.ZipFile(result,'w',zipfile.ZIP_DEFLATED) as archive:
            labels=(project.label_schema or {}).get('labels',[])
            for asset in sorted(assets.values(),key=lambda a:a.id):
                ann=annotations.get(asset.id);data=ann.annotation_data if ann else {}
                extension={'JPEG':'jpg','PNG':'png','WEBP':'webp','BMP':'bmp'}.get(asset.format,'img')
                folder='images'
                if project.task_type=='classification' and data.get('labels') and (project.label_schema or {}).get('classification_mode','single')=='single':
                    folder=f'classes/{labels.index(data["labels"][0]):03d}'
                path=f'{folder}/{asset.id}.{extension}';paths[str(asset.id)]=path
                await add_file(archive,path,asset.blob_path,asset.content_hash)
                source=asset.device_info or {}
                row=dict(asset_id=str(asset.id),image=path,content_hash=asset.content_hash,source=source,
                    labels=data.get('labels',[]),description=data.get('description',''),
                    annotation_version=ann.version if ann else None,review_status=ann.status_for('image') if ann else None)
                if 'attributes' in data:
                    row.update(attributes=data['attributes'],attribute_schema=data['attribute_schema'],annotation_source=ann.annotation_source)
                documents.append(row);manifest['assets'].append(row)
                if progress:
                    progress(len(documents), len(assets))
            paired=[]
            for query in queries:
                positives=[];negatives=[];unknown=[]
                for hit in query.hits:
                    if hit['judgment']=='unknown':unknown.append(hit['asset_id']);continue
                    if not exportable_match(hit,approved_only):continue
                    asset_id=hit['asset_id']
                    asset=assets.get(UUID(asset_id))
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
            if any('attributes' in row for row in documents):
                table=StringIO();writer=csv.writer(table)
                writer.writerow(['asset_id','image','target_label','attribute_key','attribute_name','value','reason','review_status','source','probabilities'])
                for row in documents:
                    definition=row.get('attribute_schema',{})
                    for field in definition.get('fields',[]):
                        item=row['attributes'][field['key']]
                        target_labels=definition['target_label'];target_labels=';'.join(target_labels) if isinstance(target_labels,list) else target_labels
                        cells=[row['asset_id'],row['image'],target_labels,field['key'],field['name'],item['value'] or '',item['reason'],row['review_status'],json.dumps(row['source'],ensure_ascii=False),json.dumps(item['probabilities'],ensure_ascii=False) if item.get('probabilities') else '']
                        writer.writerow(["'"+cell if isinstance(cell,str) and cell.lstrip().startswith(('=','+','-','@')) else cell for cell in cells])
                archive.writestr('attributes.csv',table.getvalue().encode('utf-8-sig'))
            if project.task_type=='vector':archive.writestr('queries.jsonl',''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in paired))
            if dataset_format!='portable':
                explicit={anchor:[{'image':paths[asset_id]} for asset_id in ids]
                    for anchor,ids in (confirmed_negatives or {}).items()}
                if attribute_pairs is None:
                    samples,report=dataset(documents,paired,dataset_format,sample_source,instruction,explicit)
                else:
                    attribute_queries=[dict(query={'text':text},positives=[{'image':paths[anchor]}],
                        negatives=[{'image':paths[item]} for item in candidates]) for text,anchor,candidates in attribute_pairs]
                    samples,report=dataset([],attribute_queries,dataset_format,'matches',instruction)
                archive.writestr('train.jsonl',''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in samples))
                policy=None
                notes=''
                if sample_source=='matches':
                    policy='人工正确=1、错误=0；相似度不作训练标签'
                    notes='仅人工判错的检索图片作为负样本；无法判断不参与。训练时避免将相同查询的其他正样本误作批内负样本。\n'
                elif dataset_format!='swift_sft':
                    policy='标注文本与对应图片组成正样本，不自动生成负样本'
                    if explicit or (attribute_pairs and single_attribute['negative_count']):
                        policy='按所选单属性自动生成正负样本' if attribute_pairs else '使用按描述或所选审核范围内的属性生成的负样本' if automatic_negatives else '仅使用人工确认的不匹配图片作为负样本'
                        if dataset_format=='infonce':notes='InfoNCE 请设置 INFONCE_USE_BATCH=false，并取消设置 INFONCE_HARD_NEGATIVES。\n'
                    elif dataset_format=='infonce':
                        notes='InfoNCE 使用批内负样本，请设置 INFONCE_USE_BATCH=true，并取消设置 INFONCE_HARD_NEGATIVES。\n'
                manifest['training']=dict(format=dataset_format,source=sample_source,**report,
                    label_policy=policy,instruction=instruction if dataset_format=='swift_sft' else None)
                if explicit or (attribute_pairs and single_attribute['negative_count']):manifest['training']['negative_strategy']='explicit'
                if automatic_negatives or (attribute_pairs and single_attribute['negative_count']):manifest['training']['negative_origin']='automatic'
                if single_attribute:manifest['training']['single_attribute']=single_attribute
                archive.writestr('README.txt','本包的 train.jsonl 为 ms-swift 训练数据。先解压完整 ZIP，再进入解压目录运行训练；图片路径相对此目录。\n'
                    'annotations.jsonl、queries.jsonl 为来源数据，勿与 train.jsonl 混合加载。\n'
                    '未自动切分训练/验证集，同一原图的裁剪图必须保持在同一划分。\n'
                    + (policy+'。\n' if policy else '') + notes)
            archive.writestr('manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2))
        result.seek(0)
        return result,manifest
    except BaseException:
        result.close();raise
