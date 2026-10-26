"""Image-only ms-swift samples from saved annotations and explicit match judgments."""
import json
from fastapi import HTTPException

FORMATS=('swift_sft','infonce','contrastive','online_contrastive','cosine_similarity')


def text_messages(text):
    if any(token in text for token in ('<image>','<video>','<audio>')):
        raise HTTPException(400,'描述、类别或查询中含未绑定的模态占位符，请移除后再导出')
    return [{'role':'user','content':text}]


def dataset(documents,pairs,format,source,instruction):
    if format not in FORMATS or source not in ('descriptions','labels','matches'):
        raise HTTPException(400,'不支持的训练格式或数据来源')
    if format=='swift_sft' and source=='matches':
        raise HTTPException(400,'大模型监督微调请选择图片描述或分类标签，不使用检索判定')
    samples=[];report={'samples':0,'skipped_empty':0,'skipped_no_positive':0}
    image_message=[{'role':'user','content':'<image>'}]
    def pair_sample(anchor,image,label=None,negatives=()):
        row={**anchor,'positive_messages':[image_message],'positive_images':[[image]]}
        if label is not None:row['label']=float(label) if format=='cosine_similarity' else label
        if negatives:
            row.update(negative_messages=[image_message for _ in negatives],negative_images=[[n['image']] for n in negatives])
        samples.append(row)
    if source=='matches':
        judgments={}
        for pair in pairs:
            query=pair['query'];anchor=('image',query.get('content_hash') or query.get('image')) if query.get('image') else ('text',query['text'].strip())
            for key in ('positives','negatives'):
                for hit in pair[key]:
                    identity=(anchor,hit['image'])
                    if identity in judgments and judgments[identity]!=key:
                        raise HTTPException(400,'同一查询对同一图片存在正确/错误冲突，请在历史查询中统一判定后重新导出或冻结')
                    judgments[identity]=key
        for pair in pairs:
            query=pair['query']
            anchor={'messages':image_message,'images':[query['image']]} if query.get('image') else {'messages':text_messages(query['text'])}
            if format=='infonce':
                if not pair['positives']:report['skipped_no_positive']+=1
                for positive in pair['positives']:pair_sample(anchor,positive['image'],negatives=pair['negatives'])
            else:
                for key,label in [('positives',1),('negatives',0)]:
                    for hit in pair[key]:pair_sample(anchor,hit['image'],label)
    else:
        for document in documents:
            labels=document.get('labels',[])
            if source=='labels':texts=[json.dumps(labels,ensure_ascii=False)] if format=='swift_sft' and labels else labels
            else:texts=[document.get('description','')]
            texts=[text for text in texts if text.strip()]
            if not texts:report['skipped_empty']+=1
            for text in texts:
                if format=='swift_sft':
                    text_messages(text)
                    if (not instruction.strip() or instruction.count('<image>')>1 or any(token in instruction for token in ('<video>','<audio>'))):
                        raise HTTPException(400,'训练指令不能为空，且只支持一个<image>占位符')
                    prompt=instruction if '<image>' in instruction else '<image>'+instruction
                    samples.append({'messages':[{'role':'user','content':prompt},{'role':'assistant','content':text}], 'images':[document['image']]})
                else:pair_sample({'messages':text_messages(text)},document['image'],None if format=='infonce' else 1)
    if not samples:raise HTTPException(400,'当前格式没有可导出的样本：请检查描述/类别、审核范围；InfoNCE检索配对至少需要一个人工确认的正样本')
    report['samples']=len(samples)
    return samples,report
