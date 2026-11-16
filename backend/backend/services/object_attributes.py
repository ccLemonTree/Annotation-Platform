"""Editable target attributes; unknown evidence stays unknown."""
import json
import math
from fastapi import HTTPException
from pydantic import ValidationError
from schemas.object_attributes import AttributeSchema,AttributeValue
from schemas.prompts import ATTRIBUTES_PROMPT
from services.model_inference import headers,response_json,image_content
from sqlalchemy import select,or_, true
from models import Asset,Annotation
from services.annotation_scope import eligible_assets
from services.preannotation_examples import example_messages,json_object

UNKNOWN={'unknown','none','null','n/a','未知','无法判断','不确定','看不清','未识别','无','未提供'}


async def common_attribute_values(db,project):
    from sqlalchemy import func,true,type_coerce
    fields=[field['key'] for field in (project.label_schema or {}).get('object_attributes',{}).get('fields',[]) if not field.get('options')]
    if not fields:return {}
    entries=(func.jsonb_each if db.bind.dialect.name=='postgresql' else func.json_each)(Annotation.annotation_data['attributes']).table_valued('key','value')
    value=type_coerce(entries.c.value,Annotation.annotation_data.type)['value'].as_string()
    count=func.count()
    query=select(entries.c.key.label('key'),value.label('value'),count.label('count')).select_from(Asset).join(Annotation,Annotation.asset_id==Asset.id).join(entries,true()).where(
        Annotation.project_id==project.id,eligible_assets(project),(Annotation.is_latest == true()),
        Annotation.status_for_query('image').is_distinct_from('rejected'),Annotation.annotation_data['skipped'].as_boolean().is_not(True),
        entries.c.key.in_(fields),value.is_not(None),func.length(value).between(1,500)).group_by(entries.c.key,value).having(count>=2).subquery()
    ranked=select(query,func.row_number().over(partition_by=query.c.key,order_by=(query.c.count.desc(),query.c.value)).label('rank')).subquery()
    rows=(await db.execute(select(ranked.c.key,ranked.c.value).where(ranked.c.rank<=20).order_by(ranked.c.key,ranked.c.rank))).all()
    result={}
    for key,value in rows:
        if value.strip() and value.casefold() not in UNKNOWN:result.setdefault(key,[]).append(value)
    return result


def attribute_query(project,attribute_key=None,attribute_value=None,attribute_state='all',review_status=None):
    query=select(Asset,Annotation).outerjoin(Annotation,(Annotation.asset_id==Asset.id)&
        (Annotation.project_id==project.id)&(Annotation.is_latest == true())).where(
        Asset.project_id==project.id,eligible_assets(project),or_(Asset.source_type=='model_crop',
            Annotation.annotation_data['attribute_schema']['target_label'].as_string().is_not(None)))
    if not attribute_key and (attribute_value is not None or attribute_state!='all'):
        raise HTTPException(400,'请先选择要筛选的属性')
    if attribute_key:
        value=Annotation.annotation_data['attributes'][attribute_key]['value'].as_string()
        reason=Annotation.annotation_data['attributes'][attribute_key]['reason'].as_string()
        if attribute_value is not None:query=query.where(value==attribute_value)
        if attribute_state=='analyzed':query=query.where(reason.is_not(None))
        if attribute_state=='known':query=query.where(value.is_not(None))
        elif attribute_state=='unknown':query=query.where(value.is_(None),reason.is_not(None))
        elif attribute_state=='missing':query=query.where(reason.is_(None))
    if review_status:query=query.where(Annotation.status_for_query('image')==review_status)
    return query


def validate_attributes(config,values):
    try:
        schema=AttributeSchema.model_validate(config)
        if not isinstance(values,dict) or set(values)!={field.key for field in schema.fields}:
            raise ValueError('属性字段与配置不一致')
        for field in schema.fields:
            item=AttributeValue.model_validate(values[field.key])
            if item.value is not None and (item.value.casefold() in UNKNOWN or field.options and item.value not in field.options):
                raise ValueError('属性值不在可选范围；无法判断须保存为空')
            if item.probabilities is not None and set(item.probabilities)!=set(field.options):
                raise ValueError('概率选项与属性可选值不一致')
            values[field.key]=item.model_dump(exclude_none=True)|{'value':item.value}
        return schema
    except (ValueError,ValidationError) as exc:
        raise HTTPException(400,'属性配置或结果无效：'+str(exc)[:300]) from None


def parse_attributes(text,config):
    schema=AttributeSchema.model_validate(config)
    try:
        raw=json_object(text)
        keys={field.key for field in schema.fields}
        nested=raw.get('fields')
        flat={key:raw[key] for key in keys if key in raw and not (key=='fields' and isinstance(nested,list))}
        matched=flat.copy()
        if isinstance(nested,list):
            for item in nested:
                key=item.get('key') if isinstance(item,dict) else None
                if not isinstance(key,str) or key not in keys or key in flat:continue
                if key in matched:raise ValueError('重复属性')
                matched[key]=item
        if not matched:raise ValueError('缺少已配置属性')
        values={}
        for field in schema.fields:
            item=matched.get(field.key,{'value':None,'reason':'模型未返回此属性'})
            if not isinstance(item,dict):raise ValueError()
            value,reason=item.get('value'),item.get('reason','')
            if isinstance(value,str):
                value=value.strip()
                if not value or value.casefold() in UNKNOWN:value=None
                elif field.options and value not in field.options:value=None;reason='模型返回了可选值之外的结果'
            values[field.key]=AttributeValue(value=value,reason=reason or ('模型无法确认' if value is None else '')).model_dump(exclude={'probabilities'})
    except (ValueError,ValidationError,TypeError):
        raise HTTPException(502,'模型未返回有效的属性JSON，请重试或人工标注') from None
    return values


async def analyze(config,content,schema,client,example=None,prompt='',attribute_prompt=''):
    if config.protocol=='jev_decide':
        if prompt.strip() or attribute_prompt.strip():raise HTTPException(400,'JEV choice协议不支持自由提示词，请使用属性判断说明和可选值')
        definition=AttributeSchema.model_validate(schema)
        validate_choice_schema(definition,example)
        image=image_content(content,'')[1]['image_url']['url']
        values={}
        for field in definition.fields:
            data=await response_json(client,'POST',config.base_url+config.endpoint,headers=headers(config),timeout=config.timeout_seconds,
                json=dict(kind='choice',state=[f'画面中央的一个目标，目标标签为{"、".join(definition.target_labels)}。仅判断此目标的可见属性。',{'image':image}],
                    question=f'这个目标的{field.name}是什么？{field.instruction}',options=field.options,thinking='off'))
            options,probabilities=data.get('options'),data.get('probabilities')
            if (not isinstance(options,list) or not all(isinstance(option,str) for option in options) or
                len(options)!=len(field.options) or set(options)!=set(field.options) or
                not isinstance(probabilities,list) or len(probabilities)!=len(options) or any(
                    isinstance(prob,bool) or not isinstance(prob,(int,float)) or not math.isfinite(prob) or not 0<=prob<=1 for prob in probabilities)):
                raise HTTPException(502,'JEV返回的选项或概率无效，未保存本图属性')
            scores=dict(zip(options,probabilities));best=max(scores,key=scores.get)
            unknown=best.casefold() in UNKNOWN or probabilities.count(scores[best])>1
            values[field.key]=dict(value=None if unknown else best,
                reason='候选概率并列或模型无法判断，请人工核验' if unknown else 'JEV最高概率候选，待人工核验',probabilities=scores)
        return values
    if config.protocol!='openai_chat':raise HTTPException(400,'属性分析需要OpenAI视觉对话模型')
    definition=AttributeSchema.model_validate(schema)
    instruction=((attribute_prompt.strip() or ATTRIBUTES_PROMPT)+
        f'\n系统输出约束（优先于核心及补充要求）：候选目标标签为{"、".join(definition.target_labels)}。'
        '目标不符、遮挡、模糊、过小或不可见时无法判断，value必须为null并在reason说明原因。'
        '可选值非空时只能使用其中一个值；可选值为空时使用简短文本。'
        '只返回JSON对象，各key对应{"value":字符串或null,"reason":简短可见证据或无法判断原因}。不得增加其他字段。属性定义：'
        +json.dumps(definition.model_dump(),ensure_ascii=False))
    if prompt.strip():instruction+='\n本次补充要求（仍须遵守以上属性定义、可选值、无法判断规则和JSON格式）：\n'+prompt.strip()
    messages=example_messages(content,instruction,example)
    data=await response_json(client,'POST',config.base_url+config.endpoint,headers=headers(config),timeout=config.timeout_seconds,
        json=dict(model=config.model_name,messages=messages,temperature=0,
            max_tokens=min(6000,256+len(definition.fields)*160),chat_template_kwargs={'enable_thinking':False}))
    try:
        choice=data['choices'][0]
        if choice.get('finish_reason')=='length':raise ValueError()
        text=choice['message']['content']
        if not isinstance(text,str):raise ValueError()
    except (KeyError,IndexError,TypeError,ValueError):raise HTTPException(502,'属性输出缺失或被截断，请缩小属性配置后重试') from None
    return parse_attributes(text,schema)


def validate_choice_schema(definition,example=None):
    if example:raise HTTPException(400,'JEV choice协议暂不支持正确范例，请清除范例后重试')
    if any(not field.options for field in definition.fields):
        raise HTTPException(400,'JEV choice协议要求每个属性填写可选值，不支持自由文本属性')
