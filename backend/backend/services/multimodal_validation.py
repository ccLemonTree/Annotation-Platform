"""Frozen caption references and task-local retrieval, without business index writes."""
import asyncio
from copy import deepcopy
import math
from uuid import UUID
import httpx
from fastapi import HTTPException
from sqlalchemy import select,or_
from models import Asset,Annotation
from models.image import LibraryImage
from models.project_work import MatchQuery
from models.validation import ValidationSet,ValidationItem
from services.annotation_scope import eligible_assets
from services.model_inference import response_json,headers,image_content


def task_type(run):return run.model_snapshot.get('task_type','detection')


def kind_filter(column,kind):
    value=column['task_type'].as_string()
    return or_(value.is_(None),value=='detection') if kind=='detection' else value==kind


async def model_snapshot(db,model_id,tenant_id,kind):
    from services.image_semantics import model_snapshot as existing
    return await existing(db,tenant_id,model_id,kind)


def vector(value):
    if not isinstance(value,list) or not 0<len(value)<=16384 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in value):
        raise ValueError('模型必须返回完整有限数值向量，最多16384维')
    norm=math.hypot(*value)
    if not math.isfinite(norm) or not norm:raise ValueError('模型返回零向量或无效范数')
    return [v/norm for v in value]


def retrieval(rows,queries):
    import numpy as np
    result={'queries':[]}
    matrix=np.asarray([row['vector'] for row in rows],dtype=float) if rows and len({len(row['vector']) for row in rows})==1 else None
    for query in queries:
        public={k:v for k,v in query.items() if k not in ('vector','blob_path')}
        q=query.get('vector');reason=None
        if not q:reason='查询向量尚未生成或生成失败'
        elif not rows:reason='没有成功生成向量的候选图片'
        elif any(len(row['vector'])!=len(q) for row in rows):reason='查询与候选向量维度不一致'
        scores=matrix@np.asarray(q) if not reason else []
        hits=[]
        if not reason:
            order=sorted(range(len(rows)),key=lambda i:(-float(scores[i]),rows[i]['asset_id']))
            for i in order:
                row=rows[i]
                if query.get('kind')=='image' and row['content_hash']==query.get('content_hash'):continue
                id=row['asset_id']
                hits.append(dict(asset_id=id,item_id=row.get('item_id'),original_name=row.get('original_name',id),score=max(-1,min(1,float(scores[i]))),judgment='correct' if id in query.get('positives',[]) else 'incorrect' if id in query.get('negatives',[]) else 'unknown'))
        eligible={row['asset_id'] for row in rows if not (query.get('kind')=='image' and row['content_hash']==query.get('content_hash'))}
        positives=set(query.get('positives',[]))
        if query.get('kind')=='image':positives-={row['asset_id'] for row in rows if row['content_hash']==query.get('content_hash')}
        if not reason and not positives:reason='没有已审核的非自身正样本，仅展示检索结果'
        if not reason and not positives<=eligible:reason='部分正样本向量生成失败，不参与完整查询指标'
        rank=next((i+1 for i,h in enumerate(hits) if h['asset_id'] in positives),None)
        result['queries'].append({**public,'hits':hits[:10],'first_positive_rank':rank,'positive_count':len(positives),
            'excluded_reason':reason,**{f'recall_at_{k}':sum(h['asset_id'] in positives for h in hits[:k])/len(positives) if positives and not reason else None for k in (1,5,10)}})
    for kind in ('text','image'):
        selected=[q for q in result['queries'] if q['kind']==kind and not q['excluded_reason']]
        count=len(selected)
        result[kind]=dict(evaluated_queries=count,mrr=sum(1/q['first_positive_rank'] if q['first_positive_rank'] else 0 for q in selected)/count if count else None,
            **{f'hit_at_{k}':sum(bool(q['first_positive_rank'] and q['first_positive_rank']<=k) for q in selected)/count if count else None for k in (1,5,10)},
            **{f'recall_at_{k}':sum(q[f'recall_at_{k}'] for q in selected)/count if count else None for k in (1,5,10)})
    return result


async def create_set(db,request,user,project):
    if request.snapshot_id:raise HTTPException(400,'描述/向量验证集请使用项目当前人工审核数据；创建后真值会独立冻结')
    kind='embedding' if project.task_type=='vector' else 'caption'
    selected=set(request.asset_ids or [])
    query=select(Asset,Annotation).outerjoin(Annotation,(Annotation.asset_id==Asset.id)&Annotation.is_latest.is_(True)).where(Asset.project_id==project.id,eligible_assets(project))
    if selected:query=query.where(Asset.id.in_(selected))
    if kind=='caption':query=query.where(Annotation.review_status=='approved',Annotation.reviewer_id.is_not(None))
    rows=(await db.execute(query.order_by(Asset.id).limit(5001))).all()
    if len(rows)>5000:raise HTTPException(400,'单验证集最多5000张，请缩小范围')
    if selected and {a.id for a,_ in rows}!=selected:raise HTTPException(400,'部分图片不属于项目或缺少人工审核真值')
    samples=[]
    for asset,ann in rows:
        data=ann.annotation_data if ann else {}
        if data.get('skipped') or (kind=='caption' and not data.get('description','').strip()):continue
        samples.append(dict(asset_id=str(asset.id),original_name=asset.original_name,blob_path=asset.blob_path,content_hash=asset.content_hash,
            width=asset.width,height=asset.height,truth=[{'description':data['description']}] if kind=='caption' else [],candidate=True,
            annotation_id=str(ann.id) if ann else None,annotation_version=ann.version if ann else None,reviewer_id=ann.reviewer_id if ann else None))
    queries=[]
    if kind=='embedding':
        records=(await db.scalars(select(MatchQuery).where(MatchQuery.project_id==project.id).order_by(MatchQuery.id).limit(201))).all()
        if len(records)>200:raise HTTPException(400,'单验证集最多200条检索查询，请分项目验证')
        by_id={s['asset_id']:s for s in samples}
        image_ids={UUID(r.query['image_id']) for r in records if r.query.get('image_id')}
        images={str(i.id):i for i in (await db.scalars(select(LibraryImage).where(LibraryImage.id.in_(image_ids),LibraryImage.tenant_id==user.tenant_id))).all()} if image_ids else {}
        for record in records:
            hits=[h for h in record.hits if h.get('review_status')=='approved' and h.get('reviewer_id') is not None and h.get('judgment') in ('correct','incorrect') and h.get('asset_id') in by_id and h.get('content_hash')==by_id[h['asset_id']]['content_hash']]
            if not hits:continue
            q=dict(id=str(record.id),kind='text' if record.query.get('text') else 'image',text=record.query.get('text',''),version=record.version,
                positives=[h['asset_id'] for h in hits if h['judgment']=='correct'],negatives=[h['asset_id'] for h in hits if h['judgment']=='incorrect'])
            if q['kind']=='image':
                original=record.query
                same=next((s for s in samples if s['content_hash']==original.get('content_hash')),None)
                if same:q.update(asset_id=same['asset_id'],content_hash=same['content_hash'])
                else:
                    if original.get('query_asset_id'):
                        from services.match_review import query_reference
                        image=await query_reference(db,project,original)
                    else:image=images.get(original.get('image_id'))
                    if image is None or image.content_hash!=original.get('content_hash'):raise HTTPException(409,'查询原图已删除或变化，请重新核对图片查询')
                    sample=dict(asset_id=str(image.id),original_name=image.original_name,blob_path=image.blob_path,content_hash=image.content_hash,width=image.width,height=image.height,truth=[],candidate=False)
                    samples.append(sample);q.update(asset_id=sample['asset_id'],content_hash=sample['content_hash'])
            queries.append(q)
        if not queries:raise HTTPException(400,'没有已人工审核的检索匹配关系，请在向量项目中判定并审核文搜图或图搜图结果')
    samples=list({s['asset_id']:s for s in samples}.values())
    if not samples or len(samples)>5000:raise HTTPException(400,'需要1至5000张具有有效真值的图片')
    if queries:samples[0]['query_texts']={q['id']:q['text'] for q in queries if q['kind']=='text'}
    dataset=ValidationSet(tenant_id=user.tenant_id,user_id=user.id,project_id=project.id,name=request.name,labels=[],samples=samples,
        source=dict(kind='project',task_type=kind,project_name=project.project_name,independent=False,queries=queries),total=len(samples))
    db.add(dataset);await db.commit();await db.refresh(dataset)
    return dataset


async def service_infer(config,content,sample,policy):
    async with httpx.AsyncClient() as client:
        async def call(content=None,text=None):
            kwargs=dict(headers=headers(config),timeout=config.timeout_seconds)
            if config.protocol=='model_service':kwargs['files']={'image':('image.bin',content,'application/octet-stream')}
            else:
                prompt=text or (policy.prompt or config.chat_prompts.caption if config.kind=='caption' else '表示这张图片的可见内容。')
                kwargs['json']=dict(model=config.model_name,messages=[dict(role='user',content=image_content(content,prompt) if content is not None else [{'type':'text','text':text}])])
                if config.kind=='caption':kwargs['json'].update(temperature=0,max_tokens=policy.max_tokens,chat_template_kwargs={'enable_thinking':False})
                else:kwargs['json']['encoding_format']='float'
            data=await response_json(client,'POST',config.base_url+config.endpoint,**kwargs)
            if config.kind=='caption':
                value=data['caption'] if config.protocol=='model_service' else data['choices'][0]['message']['content']
                if not isinstance(value,str) or not value.strip() or len(value)>20000:raise ValueError('描述输出无效')
                return value
            return vector(data['data'][0]['embedding'])
        if config.kind=='caption':return dict(caption=await call(content=content))
        result=dict(vector=await call(content=content),queries={})
        for id,text in sample.get('query_texts',{}).items():result['queries'][id]=await call(text=text)
        return result


def clean_result(result,kind):
    if kind=='caption':
        text=result.get('caption')
        if not isinstance(text,str) or not text.strip() or len(text)>20000:raise ValueError('描述输出无效')
        return dict(caption=text)
    values=vector(result.get('vector'));queries=result.get('queries',{})
    if not isinstance(queries,dict) or len(queries)>200:raise ValueError('查询向量无效')
    return dict(vector=values,dimensions=len(values),queries={str(UUID(k)):vector(v) for k,v in queries.items()})


async def metrics(db,run,items=None):
    if items is None and task_type(run)=='embedding' and run.status in ('pending','running'):
        return dict(task_type='embedding',coverage=(run.metrics or {}).get('coverage',{}),queries=[])
    if items is None and run.status in ('completed','failed','canceled') and (run.metrics or {}).get('task_type')=='embedding':return run.metrics
    if items is None:items=(await db.scalars(select(ValidationItem).where(ValidationItem.run_id==run.id))).all()
    done=[i for i in items if i.status=='completed']
    coverage=dict(total=run.total,succeeded=len(done),failed=sum(i.status=='failed' for i in items),canceled=sum(i.status=='canceled' for i in items),pending=sum(i.status in ('running','pending') for i in items),complete=len(done)==run.total)
    if task_type(run)=='caption':
        verdicts=[(i.evaluation or {}).get('review',{}).get('verdict') for i in done]
        correct=verdicts.count('correct');incorrect=verdicts.count('incorrect');reviewed=sum(v is not None for v in verdicts)
        return dict(task_type='caption',coverage=coverage,reviewed=reviewed,correct=correct,incorrect=incorrect,uncertain=verdicts.count('uncertain'),pass_rate=correct/(correct+incorrect) if correct+incorrect else None)
    dataset=await db.get(ValidationSet,run.set_id)
    if run.status in ('pending','running'):return dict(task_type='embedding',coverage=coverage,queries=[])
    query_vectors={k:v for i in done for k,v in (i.evaluation or {}).get('queries',{}).items()}
    by_id={str(i.asset_id):i for i in done};by_sample={s['asset_id']:s for s in dataset.samples}
    queries=deepcopy(dataset.source.get('queries',[]))
    for q in queries:
        q['vector']=query_vectors.get(q['id']) if q['kind']=='text' else (by_id[q['asset_id']].evaluation or {}).get('vector') if q.get('asset_id') in by_id else None
    rows=[dict(asset_id=id,item_id=str(i.id),original_name=i.original_name,content_hash=by_sample[id]['content_hash'],vector=i.evaluation['vector']) for id,i in by_id.items() if by_sample[id].get('candidate',True)]
    data=await asyncio.to_thread(retrieval,rows,queries)
    return dict(task_type='embedding',coverage=coverage,**data)
