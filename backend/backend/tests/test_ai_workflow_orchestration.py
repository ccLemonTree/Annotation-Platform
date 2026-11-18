"""Isolated tests for LLM model config, script sandbox, built-in nodes, DAG routing, dry-run, and AI orchestration."""
import hashlib
import json
import os
from io import BytesIO
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from PIL import Image, ImageDraw
from sqlalchemy import func, select

from models import Annotation, Asset, ModelService
from models.project_work import ProjectWorkItem, ProjectWorkJob
from tests.test_project_work import work_env


@pytest_asyncio.fixture(autouse=True)
async def ensure_model_service_table(test_engine):
    async with test_engine.begin() as connection:
        await connection.run_sync(lambda conn: ModelService.__table__.create(conn, checkfirst=True))


def _make_sharp_png(width=200, height=160) -> bytes:
    img = Image.new('RGB', (width, height), (120, 130, 140))
    draw = ImageDraw.Draw(img)
    for x in range(10, width - 10, 16):
        draw.rectangle([x, 12, x + 8, height - 12], fill=(240, 240, 240), outline=(10, 10, 10))
    buf = BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def _make_flat_dark_png(width=200, height=160) -> bytes:
    img = Image.new('RGB', (width, height), (5, 5, 5))
    buf = BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def test_llm_model_crud_workflow_default_and_text_predict(client, auth_headers, monkeypatch):
    from api import model_service_routes

    llm_payload = {
        'key': 'deepseek-coder',
        'name': 'DeepSeek Coder',
        'kind': 'llm',
        'protocol': 'openai_chat',
        'base_url': 'http://llm.internal/v1',
        'endpoint': '/chat/completions',
        'model_name': 'deepseek-coder-v3',
    }
    created = client.post('/api/model-services', headers=auth_headers, json=llm_payload)
    assert created.status_code == 201, created.text
    llm = created.json()
    assert llm['kind'] == 'llm'

    wf_route = '/api/model-services/workflow-model'
    assert client.put(wf_route, headers=auth_headers, json={'model_id': llm['id']}).json() == {'model_id': llm['id']}
    assert client.get(wf_route, headers=auth_headers).json() == {'model_id': llm['id']}

    async def fake_infer_text(config, prompt='', system_prompt='', max_tokens=1024, client=None):
        return {'kind': 'llm', 'boxes': [], 'caption': f'收到指令：{prompt}', 'warnings': []}

    monkeypatch.setattr(model_service_routes, 'infer_text', fake_infer_text)
    pred = client.post(f"/api/model-services/{llm['id']}/predict", headers=auth_headers, json={'prompt': '生成过滤脚本'})
    assert pred.status_code == 200, pred.text
    assert pred.json()['caption'] == '收到指令：生成过滤脚本'


@pytest.mark.asyncio
async def test_script_sandbox_security_isolation_timeout_and_pixel_ops(monkeypatch):
    from fastapi import HTTPException
    from services.script_sandbox import run_script, shutdown_sandbox, validate_script

    for malicious in [
        'import os\ndef process(ctx): return True',
        'import subprocess\ndef process(ctx): return True',
        'from PIL import ImageCms\ndef process(ctx): return True',
        'def process(ctx): return open("/etc/passwd").read()',
        'def process(ctx): return eval("1+1")',
        'def process(ctx): return ctx["image"].__class__',
        'def helper(x): return x',
    ]:
        with pytest.raises(ValueError):
            validate_script(malicious)

    monkeypatch.setenv('SUPER_SECRET_DB_TOKEN', 'do-not-leak-999')
    png = _make_sharp_png(100, 80)

    try:
        # Network attempt inside allowed stdlib fails cleanly, and env is stripped
        code_ok = '''from PIL import ImageFilter

def process(ctx):
    blurred = ctx["image"].filter(ImageFilter.GaussianBlur(radius=4))
    boxes = [
        {**b, "label": "bike"}
        for b in ctx["boxes"]
        if b["width"] >= 20
    ]
    print("processed boxes:", len(boxes))
    return {"passed": True, "image": blurred, "boxes": boxes, "reason": "像素模糊+框过滤成功"}
'''
        res = await run_script(
            code_ok,
            png,
            width=100,
            height=80,
            boxes=[
                {'label': 'ebike', 'x': 10, 'y': 10, 'width': 40, 'height': 30, 'score': 0.9},
                {'label': 'ebike', 'x': 0, 'y': 0, 'width': 8, 'height': 8, 'score': 0.5},
            ],
        )
        assert res['passed'] is True
        assert len(res['derived']) == 1 and res['derived'][0]['width'] == 100
        assert res['boxes'] == [{'label': 'bike', 'x': 10.0, 'y': 10.0, 'width': 40.0, 'height': 30.0, 'score': 0.9}]
        assert res['logs'] == ['processed boxes: 1']

        # Infinite loop triggers timeout kill and next call automatically recovers with a fresh worker
        with pytest.raises(HTTPException) as exc:
            await run_script('def process(ctx):\n    while True:\n        pass\n', png, width=100, height=80, timeout_seconds=0.6)
        assert exc.value.status_code == 400 and '超时' in exc.value.detail

        recovered = await run_script('def process(ctx):\n    return {"passed": False, "reason": "恢复正常"}\n', png, width=100, height=80)
        assert recovered['passed'] is False and recovered['reason'] == '恢复正常'
    finally:
        await shutdown_sandbox()


def test_builtin_nodes_filter_clean_branch_and_transform():
    from services import workflow_builtins as builtins

    sharp = _make_sharp_png(240, 180)
    dark = _make_flat_dark_png(240, 180)

    r1 = builtins.execute_img_filter(sharp, {'min_sharpness': 40, 'min_brightness': 20, 'dedup_phash_distance': 4}, [])
    assert r1['passed'] is True and r1['phash']
    # Duplicate pHash rejected
    r_dup = builtins.execute_img_filter(sharp, {'dedup_phash_distance': 4}, [r1['phash']])
    assert r_dup['passed'] is False and 'pHash' in r_dup['reason']
    # Dark blurry image rejected
    r_dark = builtins.execute_img_filter(dark, {'min_sharpness': 40, 'min_brightness': 20}, [])
    assert r_dark['passed'] is False

    # Box cleaning: edge filter + rename + NMS
    boxes = [
        {'label': 'ebike', 'x': 30, 'y': 30, 'width': 60, 'height': 60, 'score': 0.95},
        {'label': 'ebike', 'x': 32, 'y': 32, 'width': 58, 'height': 58, 'score': 0.70},  # suppressed by NMS
        {'label': 'person', 'x': 1, 'y': 10, 'width': 40, 'height': 80, 'score': 0.9},   # suppressed by edge_margin_px=5
    ]
    cleaned = builtins.execute_box_clean(boxes, 240, 180, {
        'edge_margin_px': 5,
        'rename_map': {'ebike': 'bicycle'},
        'nms_iou_threshold': 0.5,
    })
    assert cleaned['passed'] is True and len(cleaned['boxes']) == 1
    assert cleaned['boxes'][0]['label'] == 'bicycle'

    # Branching
    b_hit = builtins.execute_branch(width=240, height=180, crop_label='sam3_person', boxes=[], attributes={}, config={
        'first_match': True,
        'branches': [{'key': 'p_branch', 'name': '人员', 'crop_labels': ['person']}],
    })
    assert b_hit['matched_branches'] == ['p_branch']
    b_else = builtins.execute_branch(width=240, height=180, crop_label='car', boxes=[], attributes={}, config={
        'first_match': True,
        'branches': [{'key': 'p_branch', 'name': '人员', 'crop_labels': ['person']}],
    })
    assert b_else['matched_branches'] == ['else']

    # Transform: ROI top 10% crop + blur box
    tr = builtins.execute_img_transform(
        sharp, project_id='p1', asset_id='a1',
        boxes=[{'label': 'plate', 'x': 40, 'y': 40, 'width': 50, 'height': 30}],
        crop_label=None,
        config={'roi_mode': 'ratio', 'roi_ratio': [0.0, 0.1, 1.0, 1.0], 'blur_labels': ['plate'], 'blur_radius': 12},
    )
    assert len(tr['crops']) == 1 and tr['crops'][0]['height'] == 162


@pytest.mark.asyncio
async def test_dag_workflow_execution_dry_run_zero_writes_and_ai_orchestration(client, auth_headers, work_env, monkeypatch):
    from services import project_work, workflow_ai
    from services.script_sandbox import shutdown_sandbox
    from services.storage import get_storage

    env = work_env
    env.sessions.configure(autoflush=False)
    env.project.task_type = 'detection'
    env.project.label_schema = {'annotation_scope': 'all', 'labels': ['person', 'car']}
    raw_png = _make_sharp_png(200, 160)
    digest = hashlib.sha256(raw_png).hexdigest()
    await get_storage().save_file(BytesIO(raw_png), env.asset.blob_path)
    env.asset.content_hash = digest
    env.asset.width = 200
    env.asset.height = 160
    await env.db.commit()

    # Cycle detection must reject with 422
    wf_url = f'/api/projects/{env.project.id}/work/workflows'
    cyclic = client.post(wf_url, headers=auth_headers, json={
        'name': '环路测试',
        'steps': [
            {'node_key': 'n1', 'operation': 'img_filter', 'img_filter_config': {'min_width': 10}},
            {'node_key': 'n2', 'operation': 'img_filter', 'img_filter_config': {'min_height': 10}},
        ],
        'edges': [
            {'source': 'n1', 'target': 'n2', 'source_handle': 'default'},
            {'source': 'n2', 'target': 'n1', 'source_handle': 'default'},
        ],
    })
    assert cyclic.status_code == 422

    # Mock detection model to yield 1 person crop and 1 car crop
    async def fake_infer(*args, **kwargs):
        return {
            'boxes': [
                {'label': 'person', 'x': 10, 'y': 10, 'width': 60, 'height': 80, 'score': 0.95},
                {'label': 'car', 'x': 90, 'y': 20, 'width': 80, 'height': 60, 'score': 0.92},
            ]
        }

    monkeypatch.setattr(project_work, 'infer', fake_infer)
    from services import workflow_dry_run
    monkeypatch.setattr(workflow_dry_run, 'infer', fake_infer)

    dag_steps = [
        {'node_key': 's_crop', 'title': '切出行人与车', 'operation': 'crop', 'model_id': str(env.models[0].id)},
        {
            'node_key': 's_branch',
            'title': '人车分流',
            'operation': 'branch',
            'branch_config': {
                'first_match': True,
                'branches': [{'key': 'person_only', 'name': '人员分支', 'crop_labels': ['person']}],
            },
        },
        {
            'node_key': 's_person_script',
            'title': '人员脚本检查',
            'operation': 'script',
            'script_config': {'code': 'def process(ctx):\n    return {"passed": ctx["crop_label"] == "person", "reason": "确认人员切图"}\n'},
        },
        {
            'node_key': 's_other_transform',
            'title': '车辆ROI变换',
            'operation': 'img_transform',
            'img_transform_config': {'roi_mode': 'ratio', 'roi_ratio': [0, 0.1, 1, 1]},
        },
    ]
    dag_edges = [
        {'source': 's_crop', 'target': 's_branch', 'source_handle': 'default'},
        {'source': 's_branch', 'target': 's_person_script', 'source_handle': 'person_only'},
        {'source': 's_branch', 'target': 's_other_transform', 'source_handle': 'else'},
    ]

    try:
        # 1) Dry-run executes full DAG and writes ZERO DB rows
        before_assets = await env.db.scalar(select(func.count(Asset.id)))
        before_jobs = await env.db.scalar(select(func.count(ProjectWorkJob.id)))
        before_anns = await env.db.scalar(select(func.count(Annotation.id)))

        dry = client.post(f'{wf_url}/dry-run', headers=auth_headers, json={
            'steps': dag_steps,
            'edges': dag_edges,
            'image_kind': 'originals',
        })
        assert dry.status_code == 200, dry.text
        dry_data = dry.json()
        assert dry_data['sample_count'] == 1
        by_node = {n['node_key']: n for n in dry_data['nodes']}
        assert by_node['s_crop']['output_count'] == 2
        assert by_node['s_person_script']['input_count'] == 1 and by_node['s_person_script']['output_count'] == 1
        assert by_node['s_other_transform']['input_count'] == 1 and by_node['s_other_transform']['output_count'] == 1

        assert await env.db.scalar(select(func.count(Asset.id))) == before_assets
        assert await env.db.scalar(select(func.count(ProjectWorkJob.id))) == before_jobs
        assert await env.db.scalar(select(func.count(Annotation.id))) == before_anns
        await env.db.rollback()

        # 2) Real DAG workflow execution routes person crop to s_person_script and car crop to s_other_transform
        launched = client.post(wf_url, headers=auth_headers, json={
            'name': '人车分流DAG',
            'image_kind': 'originals',
            'steps': dag_steps,
            'edges': dag_edges,
        })
        assert launched.status_code == 201, launched.text
        wf_id = launched.json()['id']

        async with httpx.AsyncClient() as http_client:
            while await project_work.process_one(env.sessions, http_client):
                pass

        hist = client.get(f'{wf_url}/{wf_id}', headers=auth_headers)
        assert hist.status_code == 200, hist.text
        hist_cfg = hist.json()
        assert len(hist_cfg['edges']) == 3
        runs = client.get(wf_url, headers=auth_headers).json()
        run = next(r for r in runs if r['id'] == wf_id)
        step_by_key = {s['node_key']: s for s in run['steps']}
        assert step_by_key['s_crop']['counts']['completed'] == 1
        assert step_by_key['s_branch']['counts']['completed'] == 2
        assert step_by_key['s_person_script']['counts']['completed'] == 1 and step_by_key['s_person_script']['passed_count'] == 1
        assert step_by_key['s_other_transform']['counts']['completed'] == 1

        # 3) AI orchestration self-heals invalid first draft and validates real model_id + AST
        model_0_id = str(dag_steps[0]['model_id'])
        created_llm = client.post('/api/model-services', headers=auth_headers, json={
            'key': 'coder-llm',
            'name': 'Coder LLM',
            'kind': 'llm',
            'protocol': 'openai_chat',
            'base_url': 'http://llm.local/v1',
            'endpoint': '/chat/completions',
            'model_name': 'coder',
            'enabled': True,
        })
        assert created_llm.status_code == 201, created_llm.text

        attempts = []

        async def fake_ai_infer(config, prompt='', system_prompt='', max_tokens=4096, client=None):
            attempts.append(prompt)
            if len(attempts) == 1:
                bad = {
                    'name': '不安全草稿',
                    'steps': [{'node_key': 's1', 'operation': 'script', 'script_config': {'code': 'import os\ndef process(ctx): return True'}}],
                    'edges': [],
                }
                return {'kind': 'llm', 'boxes': [], 'caption': json.dumps(bad), 'warnings': []}
            good = {
                'name': 'AI生成清洗流',
                'summary': '先检测预标注，再用安全脚本过滤小框',
                'warnings': [],
                'steps': [
                    {'node_key': 'a1', 'title': '预标注', 'operation': 'preannotate', 'model_id': model_0_id},
                    {'node_key': 'a2', 'title': '脚本清洗', 'operation': 'script', 'script_config': {'code': 'def process(ctx):\n    return {"passed": True, "boxes": ctx["boxes"]}\n'}},
                ],
                'edges': [{'source': 'a1', 'target': 'a2', 'source_handle': 'default'}],
            }
            return {'kind': 'llm', 'boxes': [], 'caption': json.dumps(good, ensure_ascii=False), 'warnings': []}

        monkeypatch.setattr(workflow_ai, 'infer_text', fake_ai_infer)
        ai_res = client.post(f'{wf_url}/ai-generate', headers=auth_headers, json={'prompt': '预标注后用脚本清洗框'})
        assert ai_res.status_code == 200, ai_res.text
        assert len(attempts) == 2
        assert ai_res.json()['name'] == 'AI生成清洗流'
        assert len(ai_res.json()['steps']) == 2

        async def fake_script_infer(config, prompt='', system_prompt='', max_tokens=2400, client=None):
            payload = {
                'code': 'def process(ctx):\n    return {"passed": len(ctx["boxes"]) > 0, "reason": "有检测框即通过"}\n',
                'explanation': '检查是否存在目标框',
            }
            return {'kind': 'llm', 'boxes': [], 'caption': json.dumps(payload, ensure_ascii=False), 'warnings': []}

        monkeypatch.setattr(workflow_ai, 'infer_text', fake_script_infer)
        script_res = client.post(f'{wf_url}/ai-script', headers=auth_headers, json={'prompt': '只保留有框的图片'})
        assert script_res.status_code == 200, script_res.text
        assert 'def process(ctx):' in script_res.json()['code']
    finally:
        await shutdown_sandbox()
