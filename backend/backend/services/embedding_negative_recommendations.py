"""Suggest negatives from explicit, comparable image descriptions or reviewed attributes."""
import re

from fastapi import HTTPException
from sqlalchemy import func, select

from models import Annotation, Asset
from models.project_work import MatchQuery
from services.annotation_scope import eligible_assets
from services.dataset_splits import source_metadata
from services.embedding_negatives import candidate_rows, checked_hits, eligible_negative, query_text, selection_id, write_selection
from services.swift_export import ATTRIBUTE_SOURCES, TARGET_FAMILIES, text_messages


VEHICLES={'轿车':'轿车','小轿车':'轿车','sedan':'轿车','SUV':'SUV','卡车':'卡车','货车':'卡车','truck':'卡车',
    '公交车':'公交车','巴士':'公交车','bus':'公交车','皮卡':'皮卡','面包车':'面包车','摩托车':'摩托车','自行车':'自行车'}
VEHICLE_PATTERN='|'.join(sorted((key for key in VEHICLES if not key.isascii() or key=='SUV'),key=len,reverse=True))
COLORS=r'(?<![黑白红蓝绿黄紫橙粉灰棕银浅深])(?:浅|深)?([黑白红蓝绿黄紫橙粉灰棕银])(?:色)?'
TARGET=re.compile(r'(?P<vehicle>'+VEHICLE_PATTERN+r'|汽车|车辆)|(?P<person>行人|男子|女子|男孩|女孩|人员|人)|(?P<boat>船只|轮船|货船|游艇|船)',re.I)
UNCERTAIN=re.compile(r'没有|未见|不确定|看不清|疑似|可能|无法判断|多人|两人|[多两三]辆|另一')
FIELDS={'vehicle_type':'车辆类型','body_color':'车身颜色','upper_color':'上衣颜色','lower_color':'下装颜色'}
FAMILY_NAMES={'vehicle':'车辆','person':'人员','boat':'船只'}
TYPE_NAMES={key.casefold():value for key,value in VEHICLES.items()}
PAGE_SIZE=12


def evidence(annotation,source,*,allow_pending_attributes=False,template_families=None):
    text=query_text(annotation,source)
    if not text:return None
    if source in ATTRIBUTE_SOURCES:
        if annotation.status_for('image') not in (('approved','pending') if allow_pending_attributes else ('approved',)):return None
        data=annotation.annotation_data
        targets=data.get('attribute_schema',{}).get('target_label',[])
        if isinstance(targets,str):targets=[targets]
        aliases=[target.casefold().removeprefix('sam3_') for target in targets]
        families={(template_families or {}).get(alias) or TARGET_FAMILIES.get(alias) for alias in aliases}
        if len(families)!=1 or None in families:return None
        family=families.pop()
        values={key:item.get('value') for key,item in data.get('attributes',{}).items()}
        fields={}
        for key in ('body_color',) if family=='vehicle' else ('upper_color','lower_color') if family=='person' else ():
            color=re.fullmatch(COLORS,values.get(key) or '')
            if color:fields[key]=color[1]+'色'
        if family=='vehicle' and (vehicle_type:=TYPE_NAMES.get((values.get('vehicle_type') or '').casefold())):
            fields['vehicle_type']=vehicle_type
        return family,fields
    if UNCERTAIN.search(text):return None
    targets=list(TARGET.finditer(text))
    if len(targets)!=1:return None
    target=targets[0]
    family=target.lastgroup
    fields={}
    if family=='vehicle':
        vehicle_type=TYPE_NAMES.get(target.group().casefold())
        if vehicle_type:fields['vehicle_type']=vehicle_type
        color=re.search(COLORS+r'(?:的)?$',text[:target.start()])
        if color:fields['body_color']=color[1]+'色'
    elif family=='person':
        for key,garments in [('upper_color','上衣|衬衫|夹克|T恤'),('lower_color','裤子|长裤|短裤|裙子')]:
            colors=set(re.findall(COLORS+r'(?:的)?(?:'+garments+')',text,re.I))
            if len(colors)==1:fields[key]=colors.pop()+'色'
    return family,fields


def mismatch(anchor,candidate):
    if not anchor or not candidate:return ''
    family,fields=anchor
    other,other_fields=candidate
    if family!=other:return f'目标类别不同（{FAMILY_NAMES.get(family,family)} / {FAMILY_NAMES.get(other,other)}）'
    for key,label in FIELDS.items():
        if key in fields and key in other_fields and fields[key]!=other_fields[key]:
            return f'{label}不同（{fields[key]} / {other_fields[key]}）'
    return ''


def allowed(annotation,source,approved_only):
    return annotation and annotation.status_for('image') in (
        ('approved',) if approved_only or source in ATTRIBUTE_SOURCES else ('approved','pending'))


def recommendation_rows(project,source,approved_only,*,automatic=False):
    statuses=('approved',) if approved_only or (source in ATTRIBUTE_SOURCES and not automatic) else ('approved','pending')
    field='description' if source=='descriptions' else 'attributes'
    return select(Asset,Annotation).join(Annotation,Annotation.asset_id==Asset.id).where(
        Annotation.project_id==project.id,Asset.project_id==project.id,Annotation.is_latest.is_(True),
        Annotation.status_for_query('image').in_(statuses),eligible_assets(project),
        func.trim(Annotation.annotation_data[field].as_string())!='',
        Annotation.annotation_data['skipped'].as_boolean().is_not(True))


def original_id(asset):
    return source_metadata(asset).get('parent_image_id') or str(asset.image_id or asset.id)


async def recommend(db,project,source,approved_only,scope,after,limit=PAGE_SIZE,*,automatic=False,template_families=None,usage=None):
    if project.task_type!='caption':raise HTTPException(400,'自动推荐适用于描述与属性项目')
    base=recommendation_rows(project,source,approved_only,automatic=automatic)
    allow_pending_attributes=automatic and not approved_only
    anchors=(await db.execute(base.where(*scope,*([Asset.id>after] if after else [])).order_by(Asset.id).limit(limit+1))).all()
    anchors,next_cursor=anchors[:limit],anchors[limit-1][0].id if len(anchors)>limit else None
    if not anchors:return dict(items=[],next_cursor=None,scanned=0)
    # Bound the candidate scan per page. Vary the slice across pages so later images can be recommended.
    if automatic:base=base.where(*scope)
    pool=(await db.execute(base.where(Asset.id>=anchors[0][0].id).order_by(Asset.id).limit(384))).all()
    if len(pool)<384:
        pool+= (await db.execute(base.where(Asset.id<anchors[0][0].id).order_by(Asset.id).limit(384-len(pool)))).all()
    pool=[(image,ann,signal,original_id(image) if automatic else None,str(image.id)) for image,ann in pool
        if (signal:=evidence(ann,source,allow_pending_attributes=allow_pending_attributes,template_families=template_families))]
    if automatic:
        if usage is None:usage={}
        # Consecutive candidate windows overlap; keep their counts without growing each checkpoint with the dataset.
        for id in usage.keys()-{entry[4] for entry in pool}:del usage[id]
    keys={asset.id:selection_id(project,asset.id,source) for asset,_ in anchors} if not automatic else {}
    existing={row.id:row for row in (await db.scalars(select(MatchQuery).where(MatchQuery.id.in_(keys.values())))).all()} if keys else {}
    items=[]
    for asset,ann in anchors:
        text=query_text(ann,source)
        signal=evidence(ann,source,allow_pending_attributes=allow_pending_attributes,template_families=template_families)
        if not signal:continue
        row=existing.get(keys.get(asset.id))
        saved=row.hits if row and row.query['text']==text and row.query['content_hash']==asset.content_hash else []
        hashes={asset.content_hash,*(hit['content_hash'] for hit in saved)}
        root=original_id(asset) if automatic else None
        choices=[]
        candidates=sorted(pool,key=lambda entry:usage.get(entry[4],0)) if automatic else pool
        for image,other_ann,other_signal,other_root,image_id in candidates:
            if len(choices)>=min(3,20-len(saved)):break
            if image.content_hash in hashes or (automatic and root==other_root):continue
            reason=mismatch(signal,other_signal)
            if reason and eligible_negative(asset,image,other_ann,source,text):
                choices.append(dict(asset_id=image.id,name=image.original_name,reason=reason))
                hashes.add(image.content_hash)
                if automatic:usage[image_id]=usage.get(image_id,0)+1
        if choices:
            items.append(dict(asset_id=asset.id,name=asset.original_name,text=text,version=row.version if row else 0,candidates=choices))
    return dict(items=items,next_cursor=next_cursor,scanned=len(anchors))


async def confirm(db,project,source,approved_only,scope,items,user_id,*,skip_stale=False):
    if project.task_type!='caption':raise HTTPException(400,'自动推荐适用于描述与属性项目')
    anchor_ids={item.asset_id for item in items}
    if len(anchor_ids)!=len(items):raise HTTPException(422,'正样本重复')
    in_scope=(await db.scalars(select(Asset.id).where(Asset.project_id==project.id,Asset.id.in_(anchor_ids),
        eligible_assets(project),*scope))).all()
    if len(in_scope)!=len(items) and not skip_stale:raise HTTPException(409,'图片范围已变化，请重新推荐')
    scope_ids=set(in_scope)
    keys={id:selection_id(project,id,source) for id in scope_ids}
    selection_query=select(MatchQuery).where(MatchQuery.id.in_(keys.values()))
    ids=anchor_ids|{id for item in items for id in item.asset_ids}
    existing=(await db.scalars(selection_query)).all()
    ids.update(UUID(hit['asset_id']) for row in existing for hit in row.hits)
    await db.scalars(select(Asset.id).where(Asset.project_id==project.id,Asset.id.in_(ids)).order_by(Asset.id).with_for_update())
    existing={row.id:row for row in (await db.scalars(selection_query.execution_options(populate_existing=True))).all()}
    candidates={image.id:(image,ann) for image,ann in await candidate_rows(db,project,ids)}
    count=skipped=0
    for item in items:
        asset,ann=candidates.get(item.asset_id,(None,None))
        key=keys.get(item.asset_id)
        row=existing.get(key)
        if not key or not allowed(ann,source,approved_only) or query_text(ann,source)!=item.text or item.version!=(row.version if row else 0):
            if skip_stale:
                skipped+=len(item.asset_ids);continue
            raise HTTPException(409,'描述或属性已变化，请重新推荐')
        signal=evidence(ann,source)
        saved=row.hits if row and row.query['text']==item.text and row.query['content_hash']==asset.content_hash else []
        saved_ids={UUID(hit['asset_id']) for hit in saved}
        new_ids=[]
        for id in dict.fromkeys(id for id in item.asset_ids if id not in saved_ids):
            negative=candidates.get(id)
            if not negative or not allowed(negative[1],source,approved_only) or not mismatch(signal,evidence(negative[1],source)):
                if skip_stale:
                    skipped+=1;continue
                raise HTTPException(409,'负样本的描述、属性或审核状态已变化，请重新推荐')
            new_ids.append(id)
        if len(saved)+len(new_ids)>20:raise HTTPException(422,'每条查询最多保存 20 张负图')
        if new_ids:
            try:
                text_messages(item.text)
                hits=checked_hits(asset,source,item.text,[*(UUID(hit['asset_id']) for hit in saved),*new_ids],candidates,user_id)
            except HTTPException:
                if skip_stale:
                    skipped+=len(new_ids);continue
                raise
            write_selection(db,key,row,project,asset,source,item.text,hits)
            count+=len(new_ids)
    if not skip_stale:await db.commit()
    return dict(confirmed_count=count,skipped_count=skipped)
