"""AI-assisted workflow DAG orchestration and sandboxed Python script generation."""
import json
import re
from uuid import UUID
import httpx
from fastapi import HTTPException
from sqlalchemy import select

from models import ModelService, Tenant
from schemas.model_service import ModelServiceConfig
from schemas.project_work import (
    MODEL_WORKFLOW_OPERATIONS,
    WorkflowAiGenerateRequest,
    WorkflowAiScriptRequest,
    WorkflowTemplateCreate,
    resolve_workflow_dag,
)
from services.model_inference import infer_text, reject_offline_model, supports_workflow_llm
from services.preannotation_examples import json_object
from services.script_sandbox import validate_script


async def resolve_orchestrator_model(db, tenant_id: int, requested_id: UUID | None) -> tuple[UUID, ModelServiceConfig]:
    if requested_id is not None:
        row = await db.scalar(select(ModelService).where(ModelService.id == requested_id, ModelService.tenant_id == tenant_id))
        if row is None:
            raise HTTPException(404, '所选 AI 编排模型不存在')
        if not supports_workflow_llm(row):
            raise HTTPException(400, '请选择已启用的 OpenAI 文本/编排或图像对话模型')
        reject_offline_model(row)
        return row.id, ModelServiceConfig.model_validate(row)

    default_id = await db.scalar(select(Tenant.workflow_model_service_id).where(Tenant.id == tenant_id))
    if default_id is not None:
        row = await db.scalar(select(ModelService).where(ModelService.id == default_id, ModelService.tenant_id == tenant_id))
        if row is not None and supports_workflow_llm(row):
            try:
                reject_offline_model(row)
                return row.id, ModelServiceConfig.model_validate(row)
            except HTTPException:
                pass

    candidates = (await db.scalars(
        select(ModelService).where(ModelService.tenant_id == tenant_id, ModelService.enabled.is_(True)).order_by(ModelService.key)
    )).all()
    # Prefer dedicated text/code LLM first, then fallback to multimodal chat models.
    ordered = sorted(candidates, key=lambda r: 0 if r.kind == 'llm' else 1)
    for row in ordered:
        if not supports_workflow_llm(row):
            continue
        try:
            reject_offline_model(row)
            return row.id, ModelServiceConfig.model_validate(row)
        except HTTPException:
            continue

    raise HTTPException(400, '暂无可用的 AI 编排模型，请先在「模型管理」中添加并启用文本/编排或 OpenAI 对话模型')


def _available_models_catalog(rows: list[ModelService]) -> list[dict]:
    catalog = []
    for row in rows:
        if not row.enabled or row.kind not in ('detection', 'caption'):
            continue
        check = row.connection_check or {}
        if check.get('status') == 'offline' and check.get('checked_revision') == row.revision:
            continue
        catalog.append({
            'model_id': str(row.id),
            'name': row.name,
            'kind': row.kind,
            'protocol': row.protocol,
            'endpoint': row.endpoint,
            'default_labels': row.labels,
        })
    return catalog


WORKFLOW_SYSTEM_PROMPT = '''你是图像数据管理平台的工作流架构师。请根据管理员的自然语言需求，自动编排一个清晰、可执行的图像处理有向无环工作流（DAG），只输出严格的 JSON 对象，不要附加 Markdown 解释。

【平台支持的 13 种节点 operation】
一、平台内置功能节点（零模型消耗、极速确定性执行，model_id 必须为 null，优先选用）：
1. "img_filter"（图像基础规则过滤）：配置字段 img_filter_config: {min_width?, max_width?, min_height?, max_height?, min_short_edge?, min_long_edge?, min_aspect_ratio?, max_aspect_ratio?, min_sharpness?(清晰度推荐 60~120), min_brightness?(防黑屏推荐 15), max_brightness?(防过曝推荐 245), dedup_phash_distance?(近重复去重推荐 4~6)}（至少填一项）
2. "box_clean"（检测框规则清洗）：配置字段 box_clean_config: {min_box_width:0, min_box_height:0, min_area_ratio:0, max_area_ratio:1, edge_margin_px:0, keep_labels:[], drop_labels:[], rename_map:{}, nms_iou_threshold:null(如 0.5), pass_when_empty:false, save_boxes:true}
3. "img_transform"（图像变换与脱敏）：配置字段 img_transform_config: {roi_mode:"none"|"ratio"|"pixels", roi_ratio:[left,top,right,bottom](0~1), roi_margins_px:[左,上,右,下], blur_labels:[], blur_all_boxes:false, blur_radius:18}
4. "branch"（条件分流）：配置字段 branch_config: {first_match:true, branches:[{key:"b1"(字母数字下划线,不能为default/else), name:"分支名", match_mode:"all"|"any", crop_labels:[], box_labels:[], min_boxes:null, max_boxes:null, attribute_key:"", attribute_values:[], min_width:null, min_height:null}]}。连线 edges 中来自该节点的 source_handle 填对应分支的 key 或兜底 "else"。

二、受限沙箱 Python 脚本节点（当内置节点无法表达自定义几何/像素/业务规则时自动生成脚本，model_id 为 null）：
5. "script"：配置字段 script_config: {code: "def process(ctx):\n    ...", timeout_seconds: 5.0, save_boxes: true, save_attributes: true, save_description: true}
   - 沙箱仅允许导入: math, json, re, statistics, collections, itertools, PIL (Image, ImageFilter, ImageStat, ImageOps, ImageDraw), imagehash。严禁导入 os/sys/subprocess/socket 或访问任何以下划线 _ 开头的属性，严禁使用 open/eval/exec/getattr。
   - 入口函数必须为同步 `def process(ctx):`，ctx 是字典：ctx["image"](PIL.Image RGB), ctx["width"], ctx["height"], ctx["boxes"]([{"label","x","y","width","height","score"}]), ctx["attributes"], ctx["description"], ctx["crop_label"]。
   - 返回字典可含: {"passed": True/False, "reason": "说明", "boxes": [...], "attributes": {...}, "description": "...", "image": PIL_Image, "crops": [{"image": PIL_Image, "label": "..."}]}.

三、平台模型推理节点（必须从下方【当前在线可用模型列表】中选择真实 model_id，禁止编造不存在的 UUID）：
6. "crop"（检测切图，需 kind="detection" 模型）：可选字段 labels:[], confidence:0.85, expansion:0.2, min_crop_long_edge:0, save_input_boxes:false
7. "detect"（目标检测筛选，需 kind="detection" 模型）：labels:[], confidence:0.85
8. "preannotate"（目标检测预标注，需 kind="detection" 模型）：labels:[], confidence:0.85
9. "ai_review"（AI 审核检测框，需 protocol="openai_chat" 视觉模型）：labels:[], auto_approve_confidence:0.9, prompt:""
10. "filter"（大模型视觉筛选，需 protocol="openai_chat" 视觉模型）：必填 prompt(1~2000字筛选条件)
11. "quality"（大模型质量检查并剔除不合格图，需 protocol="openai_chat" 视觉模型）：必填 prompt(1~2000字合格要求)
12. "caption"（大模型图像描述，需 kind="caption" 模型）：prompt:""
13. "attributes"（目标属性分析，需 protocol="openai_chat" 或 "jev_decide" 模型）：attribute_prompt:"", prompt:""

【输出 JSON 格式要求】
{
  "name": "工作流简短中文名称",
  "summary": "向用户解释编排思路与各节点作用（1~3句话）",
  "warnings": ["若缺少某类在线模型导致改用内置规则/脚本替代，在此说明；否则为空数组"],
  "steps": [
    {"node_key": "step_1", "title": "节点中文标题", "operation": "...", "model_id": null或真实UUID, ...对应配置字段}
  ],
  "edges": [
    {"source": "step_1", "target": "step_2", "source_handle": "default"}
  ]
}
注意：每个步骤都必须有唯一的 node_key（如 step_1, step_2），edges 必须构成无环连通图（普通节点 source_handle 固定为 "default"，branch 节点填分支 key 或 "else"）。'''


SCRIPT_SYSTEM_PROMPT = '''你是图像数据管理平台的 Python 脚本专家。请根据用户需求编写运行在安全沙箱中的单图处理函数，只输出严格的 JSON 对象：
{"code": "def process(ctx):\\n    ...", "explanation": "简短中文说明"}

【严格沙箱规则】
1. 必须定义同步函数 `def process(ctx):`，参数只有一个 `ctx` 字典：
   - `ctx["image"]`: PIL.Image.Image (RGB 模式，模块 Image/ImageFilter/ImageStat/ImageOps/ImageDraw 已预置在全局作用域，也可显式 from PIL import Image)
   - `ctx["width"]`, `ctx["height"]`: 整数宽高
   - `ctx["boxes"]`: 检测框列表 `[{"label": str, "x": float, "y": float, "width": float, "height": float, "score": float}]`
   - `ctx["attributes"]`: 属性字典, `ctx["description"]`: 字符串, `ctx["crop_label"]`: 裁剪来源标签或 None
2. 允许导入的白名单模块仅限：`math`, `json`, `re`, `statistics`, `collections`, `itertools`, `PIL`, `imagehash`。
3. 严禁导入 os, sys, subprocess, socket, pathlib 等模块；严禁使用 open, eval, exec, compile, getattr, setattr, type, vars, globals, locals；严禁访问任何以 `_` 开头的私有属性（如 `.__class__`）。
4. 返回字典支持字段：
   - `"passed"`: bool（True 传递下游，False 拦截过滤）
   - `"reason"`: str（在界面明细中展示的处理原因）
   - `"boxes"`: 修改后的检测框列表（可选）
   - `"attributes"`: 字典（可选）, `"description"`: 字符串（可选）
   - `"image"`: 修改后的新 PIL.Image 对象（可选，平台自动存为衍生图，不覆盖原图）
   - `"crops"`: 子图列表 `[{"image": PIL_Image, "label": "标签"}]`（可选，最多 50 张）'''


def _extract_llm_json(text: str) -> dict:
    cleaned = re.sub(r'<think>[\s\S]*?</think>', '', text or '', flags=re.IGNORECASE).strip()
    try:
        return json_object(cleaned)
    except Exception:
        start = cleaned.find('{')
        if start < 0:
            raise ValueError('AI 未输出有效的 JSON 对象') from None
        obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
        if not isinstance(obj, dict):
            raise ValueError('AI 输出须为 JSON 对象')
        return obj


_NON_MODEL_CONFIG_KEYS = {
    'branch': 'branch_config',
    'img_filter': 'img_filter_config',
    'box_clean': 'box_clean_config',
    'img_transform': 'img_transform_config',
    'script': 'script_config',
}


def _sanitize_generated_workflow(raw: dict, valid_model_ids: set[str]) -> dict:
    name = str(raw.get('name') or 'AI 编排工作流').strip()[:100] or 'AI 编排工作流'
    raw_steps = raw.get('steps')
    if not isinstance(raw_steps, list) or not raw_steps:
        raise ValueError('AI 未返回有效的 steps 节点列表')

    allowed_step_keys = {
        'node_key', 'title', 'operation', 'model_id', 'prompt', 'attribute_prompt',
        'attribute_templates', 'labels', 'confidence', 'expansion', 'min_crop_long_edge',
        'save_input_boxes', 'auto_approve_confidence', 'branch_config', 'img_filter_config',
        'box_clean_config', 'img_transform_config', 'script_config',
    }
    cleaned_steps = []
    step_keys: set[str] = set()
    for idx, step in enumerate(raw_steps):
        if not isinstance(step, dict):
            raise ValueError(f'第 {idx + 1} 个节点格式无效')
        item = {k: v for k, v in step.items() if k in allowed_step_keys and v is not None}
        item['node_key'] = str(item.get('node_key') or f'step_{idx + 1}').strip()
        step_keys.add(item['node_key'])
        op = item.get('operation')
        if op != 'attributes':
            item.pop('attribute_prompt', None)
            item.pop('attribute_templates', None)
        if op in MODEL_WORKFLOW_OPERATIONS:
            mid = str(item.get('model_id') or '').strip()
            if mid not in valid_model_ids:
                raise ValueError(f'节点「{item.get("title") or op}」引用了不可用或不存在的 model_id: {mid}')
            item['model_id'] = mid
            for cfg_key in _NON_MODEL_CONFIG_KEYS.values():
                item.pop(cfg_key, None)
        else:
            item.pop('model_id', None)
            keep_cfg = _NON_MODEL_CONFIG_KEYS.get(str(op))
            for cfg_key in _NON_MODEL_CONFIG_KEYS.values():
                if cfg_key != keep_cfg:
                    item.pop(cfg_key, None)
        cleaned_steps.append(item)

    raw_edges = raw.get('edges')
    cleaned_edges = None
    if isinstance(raw_edges, list):
        cleaned_edges = []
        for e in raw_edges:
            if not isinstance(e, dict) or not e.get('source') or not e.get('target'):
                continue
            src = str(e.get('source')).strip()
            if src in ('input', 'start', 'root') and src not in step_keys:
                src = '__input__'
            cleaned_edges.append({
                'source': src,
                'target': str(e.get('target')).strip(),
                'source_handle': str(e.get('source_handle') or 'default').strip() or 'default',
            })

    validated = WorkflowTemplateCreate.model_validate({
        'name': name,
        'steps': cleaned_steps,
        'edges': cleaned_edges,
    })
    topo_keys, by_key, _, _, resolved_edges = resolve_workflow_dag(validated.steps, validated.edges)
    ordered_steps = []
    for k in topo_keys:
        dumped = by_key[k].model_dump(mode='json', exclude_none=True)
        dumped['node_key'] = k
        ordered_steps.append(dumped)

    return {
        'name': validated.name,
        'summary': str(raw.get('summary') or '已根据需求自动编排工作流。').strip()[:1000],
        'warnings': [str(w)[:300] for w in (raw.get('warnings') or []) if isinstance(w, (str, int, float))][:5],
        'steps': ordered_steps,
        'edges': [e.model_dump() for e in resolved_edges],
    }


async def generate_workflow(db, project, request: WorkflowAiGenerateRequest) -> dict:
    orchestrator_id, llm_config = await resolve_orchestrator_model(db, project.tenant_id, request.model_id)
    all_models = list((await db.scalars(select(ModelService).where(ModelService.tenant_id == project.tenant_id))).all())
    catalog = _available_models_catalog(all_models)
    valid_model_ids = {m['model_id'] for m in catalog}

    project_context = {
        'project_name': project.project_name,
        'task_type': project.task_type,
        'labels': (project.label_schema or {}).get('labels') or [],
        'has_object_attributes': bool((project.label_schema or {}).get('object_attributes')),
        'object_attributes': (project.label_schema or {}).get('object_attributes'),
    }
    await db.rollback()

    user_payload = (
        f"【当前项目上下文】\n{json.dumps(project_context, ensure_ascii=False)}\n\n"
        f"【当前在线可用模型列表】\n{json.dumps(catalog, ensure_ascii=False)}\n\n"
    )
    if request.current_workflow:
        user_payload += f"【当前画布已有工作流草稿（请在此基础上按指令调整或重排）】\n{json.dumps(request.current_workflow, ensure_ascii=False)[:6000]}\n\n"
    user_payload += f"【管理员编排需求】\n{request.prompt}"

    last_error = ''
    llm_timeout = min(max(llm_config.timeout_seconds, 120), 180)
    async with httpx.AsyncClient(timeout=llm_timeout, follow_redirects=False) as client:
        for attempt in range(2):
            prompt_text = user_payload if attempt == 0 else (
                f"{user_payload}\n\n【上一次生成的 JSON 未通过平台静态校验，请修正后重新输出完整 JSON】\n错误信息：{last_error}"
            )
            reply = await infer_text(
                llm_config,
                prompt=prompt_text,
                system_prompt=WORKFLOW_SYSTEM_PROMPT,
                max_tokens=2400,
                client=client,
                timeout=llm_timeout,
            )
            try:
                parsed = _extract_llm_json(reply['caption'])
                result = _sanitize_generated_workflow(parsed, valid_model_ids)
                result['orchestrator_model_id'] = str(orchestrator_id)
                result['orchestrator_model_name'] = llm_config.name
                return result
            except Exception as exc:
                last_error = str(exc)

    raise HTTPException(422, f'AI 生成的工作流未通过安全与拓扑校验（{last_error[:200]}），请换个描述方式重试')


async def generate_script(db, project, request: WorkflowAiScriptRequest) -> dict:
    orchestrator_id, llm_config = await resolve_orchestrator_model(db, project.tenant_id, request.model_id)
    project_context = {
        'project_name': project.project_name,
        'task_type': project.task_type,
        'labels': (project.label_schema or {}).get('labels') or [],
    }
    await db.rollback()

    user_payload = f"【项目信息】{json.dumps(project_context, ensure_ascii=False)}\n"
    if request.current_code.strip():
        user_payload += f"【当前脚本代码】\n```python\n{request.current_code[:6000]}\n```\n"
    user_payload += f"【脚本需求】\n{request.prompt}"

    last_error = ''
    llm_timeout = min(max(llm_config.timeout_seconds, 120), 180)
    async with httpx.AsyncClient(timeout=llm_timeout, follow_redirects=False) as client:
        for attempt in range(2):
            prompt_text = user_payload if attempt == 0 else (
                f"{user_payload}\n\n【上次代码未通过沙箱 AST 安全检查，请修正】\n错误原因：{last_error}"
            )
            reply = await infer_text(
                llm_config,
                prompt=prompt_text,
                system_prompt=SCRIPT_SYSTEM_PROMPT,
                max_tokens=2400,
                client=client,
                timeout=llm_timeout,
            )
            try:
                parsed = _extract_llm_json(reply['caption'])
                code = str(parsed.get('code') or '').strip()
                validate_script(code)
                return {
                    'code': code,
                    'explanation': str(parsed.get('explanation') or '已生成安全沙箱脚本。').strip()[:600],
                    'orchestrator_model_id': str(orchestrator_id),
                    'orchestrator_model_name': llm_config.name,
                }
            except Exception as exc:
                last_error = str(exc)

    raise HTTPException(422, f'AI 生成的脚本未通过沙箱安全检查（{last_error[:200]}），请调整描述后重试')
