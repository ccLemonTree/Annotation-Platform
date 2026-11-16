"""Few-shot visual detection; model coordinates are validated, never scored as calibrated."""
import json,math
import httpx
from fastapi import HTTPException
from services.annotations import detection_boxes
from services.model_inference import headers,response_json
from services.preannotation_examples import example_messages,json_object
from schemas.prompts import DETECTION_PREANNOTATION_PROMPT


def parse_detection(text,labels,width,height):
    try:
        data=json_object(text)
        if set(data)!={'boxes','uncertain','reason'} or not isinstance(data['uncertain'],bool):raise ValueError()
        if not isinstance(data['reason'],str) or not data['reason'].strip() or len(data['reason'])>2000:raise ValueError()
        if not isinstance(data['boxes'],list) or len(data['boxes'])>100:raise ValueError()
        boxes=[]
        for row in data['boxes']:
            if not isinstance(row,dict) or set(row)!={'label','bbox'} or row['label'] not in labels:raise ValueError()
            bbox=row['bbox']
            if not isinstance(bbox,list) or len(bbox)!=4 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1000 for v in bbox):raise ValueError()
            x1,y1,x2,y2=bbox
            if x2<=x1 or y2<=y1:raise ValueError()
            left,top,right,bottom=x1*width/1000,y1*height/1000,x2*width/1000,y2*height/1000
            boxes.append(dict(label=row['label'],x=left,y=top,width=min(right-left,width-left),height=min(bottom-top,height-top)))
        detection_boxes({'boxes':boxes},width,height,labels)
    except (ValueError,TypeError,KeyError,HTTPException):raise HTTPException(502,'大模型检测框、类别或坐标无效，请重试或人工标注') from None
    return dict(boxes=boxes,uncertain=data['uncertain'],reason=data['reason'],score_type='uncalibrated',
        warnings=['大模型范例预标注未提供校准置信度，必须人工审核'])


async def detect_from_example(config,content,width,height,labels,example_content,snapshot,client=None,prompt='',detection_prompt=''):
    if client is None:
        async with httpx.AsyncClient(follow_redirects=False) as session:
            return await detect_from_example(config,content,width,height,labels,example_content,snapshot,session,prompt,detection_prompt)
    answer=dict(boxes=[dict(label=box['label'],bbox=[box['x']*1000/snapshot['width'],box['y']*1000/snapshot['height'],
        (box['x']+box['width'])*1000/snapshot['width'],(box['y']+box['height'])*1000/snapshot['height']]) for box in snapshot['boxes'] if box['label'] in labels],
        uncertain=False,reason='人工确认的正确标注')
    instruction=((detection_prompt.strip() or DETECTION_PREANNOTATION_PROMPT)+
        '\n系统输出约束（优先于核心及补充要求）：'
        '只输出JSON：{"boxes":[{"label":"项目类别","bbox":[x1,y1,x2,y2]}],"uncertain":false,"reason":"依据"}。'
        'bbox坐标按整张图片宽高分别归一化到0至1000，与范例相同；左上到右下，最多100框。'
        '无法确定时uncertain=true并说明原因，不编造框；无可靠目标返回boxes=[]。仅使用项目类别：'+json.dumps(labels,ensure_ascii=False))
    if prompt.strip():instruction+='\n本次补充要求（仍须遵守以上JSON格式、项目类别、坐标和无法判断规则）：\n'+prompt.strip()
    data=await response_json(client,'POST',config.base_url+config.endpoint,headers=headers(config),timeout=config.timeout_seconds,
        json=dict(model=config.model_name,messages=example_messages(content,instruction,dict(content=example_content,answer=answer)),
            temperature=0,max_tokens=4096,chat_template_kwargs={'enable_thinking':False}))
    try:
        choice=data['choices'][0]
        if choice.get('finish_reason')=='length' or not isinstance(choice['message']['content'],str):raise ValueError()
        return parse_detection(choice['message']['content'],labels,width,height)
    except (ValueError,KeyError,IndexError,TypeError):raise HTTPException(502,'大模型预标注输出缺失或截断') from None
