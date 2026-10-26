"""Bounded one-image inference; responses never write annotations."""
import base64
import asyncio
import json
import math
import os
from io import BytesIO
import httpx
from fastapi import HTTPException
from PIL import Image
from config.settings import settings
from schemas.prompts import CAPTION_PROMPT


def headers(config):
    key = config.api_key_env
    value = (getattr(settings, key, '') or os.environ.get(key, '')) if key else ''
    if key and not value:
        raise HTTPException(400, '配置的模型鉴权环境变量未设置')
    return {'Authorization': 'Bearer ' + value} if value else {}


async def response_json(client, method, url, retry_transient=False, **kwargs):
    for attempt in range(2 if retry_transient else 1):
        try:
            async with client.stream(method, url, **kwargs) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > 4 * 1024 * 1024:
                        raise HTTPException(502, '模型返回超过大小限制')
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError('Object required')
            return data
        except httpx.TimeoutException:
            raise HTTPException(504, '模型服务超时，请稍后重试')
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if retry_transient and attempt == 0 and status in (502, 503, 504):
                await asyncio.sleep(.5)
                continue
            reason = ('鉴权失败，请检查服务凭据' if status in (401, 403) else
                '接口不存在，请检查服务路径' if status == 404 else
                '网关或模型后端暂时不可用，请稍后重试' if status >= 500 else '请检查服务配置和请求参数')
            raise HTTPException(502, f'模型服务返回 HTTP {status}：{reason}') from None
        except httpx.HTTPError:
            raise HTTPException(502, '无法连接模型服务，请检查网络和服务地址') from None
        except (ValueError, TypeError):
            raise HTTPException(502, '模型返回格式无效')


def detection_result(data, config, width, height):
    rows = data.get('detections')
    if not isinstance(rows, list) or len(rows) > 5000:
        raise HTTPException(502, '模型检测结果缺少有效detections列表')
    boxes, warnings = [], []
    unscored = not config.has_confidence or data.get('score_type') == 'uncalibrated'
    if unscored:
        warnings.append('模型未提供可用检测置信度；已忽略占位分数和置信度筛选，请人工复核')
    for row in rows:
        try:
            label, bbox = row['class'], row['bbox']
            if not isinstance(label, str) or not label.strip() or len(label) > 100 or not isinstance(bbox, list) or len(bbox) != 4:
                raise ValueError()
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in bbox):
                raise ValueError()
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                raise ValueError()
            if not unscored:
                score = row['score']
                if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(score) or not 0 <= score <= 1:
                    raise ValueError()
                if score < config.confidence:
                    continue
            x1, y1, x2, y2 = [max(0, min(limit, value)) for value, limit in zip(bbox, (width, height, width, height))]
            if [x1, y1, x2, y2] != bbox and '模型越界框已裁到原图边界' not in warnings:
                warnings.append('模型越界框已裁到原图边界')
            if x2 <= x1 or y2 <= y1:
                continue
            boxes.append(dict(label=config.label_mapping.get(label, label), x=x1, y=y1, width=x2-x1, height=y2-y1,
                              **({} if unscored else {'score': score})))
        except (KeyError, TypeError, ValueError):
            raise HTTPException(502, '模型检测框坐标、类别或置信度无效')
    if not boxes:
        warnings.append('模型未检出目标，不等于人工确认负样本')
    return dict(boxes=boxes, warnings=warnings, **({'score_type':'uncalibrated'} if unscored else {}))


def image_content(content, prompt):
    with Image.open(BytesIO(content)) as picture:
        picture = picture.convert('RGB'); picture.thumbnail((1024, 1024))
        output = BytesIO(); picture.save(output, format='JPEG', quality=90)
    return [{'type': 'text', 'text': prompt or CAPTION_PROMPT},
            {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode()}}]


async def infer(config, content, width, height, prompt='', client=None):
    if config.kind == 'reranker':
        raise HTTPException(400, 'Reranker配置已保留，尚未接入推理')
    if client is None:
        async with httpx.AsyncClient(timeout=config.timeout_seconds, follow_redirects=False) as session:
            return await infer(config, content, width, height, prompt, session)
    url = config.base_url + config.endpoint
    auth = headers(config)
    if config.protocol == 'model_service':
        form = {}
        if config.kind == 'detection':
            form = {'labels': json.dumps({name: ({'conf': config.confidence} if config.has_confidence else {}) for name in config.labels})}
            if config.has_confidence:
                form['conf'] = str(config.confidence)
            if config.has_nms:
                form['nms'] = str(config.iou)
            if prompt:
                form['prompt'] = prompt
        data = await response_json(client, 'POST', url, headers=auth, data=form, files={'image': ('image.bin', content, 'application/octet-stream')})
        if config.kind == 'detection':
            return {'kind': config.kind, **detection_result(data, config, width, height)}
        caption = data.get('caption')
    else:
        try:
            multimodal = image_content(content, prompt or (config.chat_prompts.caption if config.protocol == 'openai_chat' else ''))
        except (OSError, ValueError, Image.DecompressionBombError):
            raise HTTPException(400, '图片无法解码，已保留原图')
        body = {'model': config.model_name, 'messages': [{'role': 'user', 'content': multimodal}]}
        if config.protocol == 'openai_embedding':
            body['encoding_format'] = 'float'
        else:
            body.update(temperature=0, max_tokens=512, chat_template_kwargs={'enable_thinking': False})
        data = await response_json(client, 'POST', url, headers=auth, json=body)
        try:
            if config.protocol == 'openai_embedding':
                vector = data['data'][0]['embedding']
                if not isinstance(vector, list) or not vector or len(vector) > 65536 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector):
                    raise ValueError()
                return dict(kind=config.kind, boxes=[], warnings=[], embedding_dimensions=len(vector), embedding_preview=vector[:8])
            caption = data['choices'][0]['message']['content']
        except (KeyError, IndexError, TypeError, ValueError):
            raise HTTPException(502, '模型返回的描述或Embedding格式无效')
    if not isinstance(caption, str) or not caption.strip() or len(caption) > 20000:
        raise HTTPException(502, '模型返回的描述文本无效')
    return dict(kind=config.kind, boxes=[], caption=caption, warnings=[])


async def test_connection(config):
    async with httpx.AsyncClient(timeout=min(config.timeout_seconds, 15), follow_redirects=False) as client:
        if config.protocol == 'model_service':
            schema = await response_json(client, 'GET', config.base_url + '/openapi.json', headers=headers(config))
            if 'post' not in schema.get('paths', {}).get(config.endpoint, {}):
                raise HTTPException(400, '服务可访问，但配置的POST接口不存在')
            health = await response_json(client, 'GET', config.base_url + '/health', headers=headers(config))
            if health.get('status') not in ('ok', 'healthy'):
                raise HTTPException(502, '接口存在，但推理后端未就绪')
            return {'ok': True, 'detail': '接口存在，推理后端可连接；模型效果请另行试运行'}
        data = await response_json(client, 'GET', config.base_url + '/models', headers=headers(config))
        models = [row['id'] for row in data.get('data', []) if isinstance(row, dict) and isinstance(row.get('id'), str)]
        if config.model_name not in models:
            raise HTTPException(400, '服务可访问，但模型ID不存在')
        return {'ok': True, 'detail': '模型ID可用；实际图片推理请使用试运行', 'models': models}
