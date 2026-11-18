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

from models import Annotation, Asset, ModelService, ObjectAttributeTemplate, Project, ProjectStatus, TaskType, Tenant
from models.project_work import ProjectWorkItem, ProjectWorkJob
from tests.test_project_work import work_env


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
        observed_timeouts = []

        async def fake_ai_infer(config, prompt='', system_prompt='', max_tokens=2400, client=None, timeout=None):
            attempts.append(prompt)
            observed_timeouts.append(timeout)
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
                    {'node_key': 'a1', 'title': '预标注', 'operation': 'preannotate', 'model_id': model_0_id, 'attribute_prompt': '多余字段应被自动忽略'},
                    {'node_key': 'a2', 'title': '脚本清洗', 'operation': 'script', 'script_config': {'code': 'def process(ctx):\n    return {"passed": True, "boxes": ctx["boxes"]}\n'}},
                ],
                'edges': [
                    {'source': 'input', 'target': 'a1', 'source_handle': 'default'},
                    {'source': 'a1', 'target': 'a2', 'source_handle': 'default'},
                ],
            }
            wrapped = f"<think>先思考一下节点拓扑...</think>\n好的，生成的配置如下：\n```json\n{json.dumps(good, ensure_ascii=False)}\n```"
            return {'kind': 'llm', 'boxes': [], 'caption': wrapped, 'warnings': []}

        monkeypatch.setattr(workflow_ai, 'infer_text', fake_ai_infer)
        ai_res = client.post(f'{wf_url}/ai-generate', headers=auth_headers, json={'prompt': '预标注后用脚本清洗框'})
        assert ai_res.status_code == 200, ai_res.text
        assert len(attempts) == 2
        assert all(t >= 120 for t in observed_timeouts)
        assert ai_res.json()['name'] == 'AI生成清洗流'
        assert len(ai_res.json()['steps']) == 2

        async def fake_script_infer(config, prompt='', system_prompt='', max_tokens=2400, client=None, timeout=None):
            assert timeout is not None and timeout >= 120
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


@pytest.mark.asyncio
async def test_infer_text_disables_thinking_and_falls_back_on_strict_openai(monkeypatch):
    from fastapi import HTTPException
    from schemas.model_service import ModelServiceConfig
    from services import model_inference

    cfg = ModelServiceConfig(
        key='qwen-test',
        name='Qwen Test',
        kind='caption',
        protocol='openai_chat',
        base_url='http://llm.local/v1',
        endpoint='/chat/completions',
        model_name='Qwen3.8-27B',
        timeout_seconds=60,
    )
    calls = []

    async def fake_response_json(client, method, url, **kwargs):
        calls.append({'timeout': kwargs.get('timeout'), 'json': kwargs.get('json')})
        if len(calls) == 1:
            raise HTTPException(502, '模型服务返回 HTTP 400：请检查服务配置和请求参数')
        return {'choices': [{'message': {'content': '{"ok": true}'}}]}

    monkeypatch.setattr(model_inference, 'response_json', fake_response_json)
    async with httpx.AsyncClient() as http_client:
        res = await model_inference.infer_text(cfg, prompt='hi', max_tokens=512, client=http_client, timeout=150)

    assert res['caption'] == '{"ok": true}'
    assert len(calls) == 2
    assert calls[0]['timeout'] == 150
    assert calls[0]['json']['chat_template_kwargs'] == {'enable_thinking': False}
    assert 'chat_template_kwargs' not in calls[1]['json']


def test_crop_to_branch_auto_aligns_crop_labels_and_matches_subimage():
    from uuid import uuid4
    from services import workflow_builtins as builtins
    from services.workflow_ai import _sanitize_generated_workflow

    # 1) Runtime fallback: cropped sub-image (crop_label='person', boxes=[]) matches box_labels=['person']
    br_res = builtins.execute_branch(
        width=80,
        height=160,
        crop_label='person',
        boxes=[],
        attributes={},
        config={
            'first_match': True,
            'branches': [{'key': 'b_person', 'name': '人员分支', 'match_mode': 'any', 'crop_labels': [], 'box_labels': ['person']}],
        },
    )
    assert br_res['matched_branches'] == ['b_person']

    # 2) Sanitizer auto-migration: upstream is 'crop' -> branch box_labels moves to crop_labels
    det_id = str(uuid4())
    sanitized = _sanitize_generated_workflow(
        {
            'name': '切图分流自校正',
            'steps': [
                {'node_key': 's1', 'operation': 'crop', 'model_id': det_id, 'labels': ['person']},
                {
                    'node_key': 's2',
                    'operation': 'branch',
                    'branch_config': {
                        'first_match': True,
                        'branches': [{'key': 'b_person', 'name': '人员分支', 'box_labels': ['person']}],
                    },
                },
            ],
            'edges': [{'source': 's1', 'target': 's2', 'source_handle': 'default'}],
        },
        valid_model_ids={det_id},
    )
    branch_step = next(s for s in sanitized['steps'] if s['node_key'] == 's2')
    assert branch_step['branch_config']['branches'][0]['crop_labels'] == ['person']
    assert branch_step['branch_config']['branches'][0]['box_labels'] == []


@pytest.mark.asyncio
async def test_lifespan_orphan_backup_cleanup_and_conftest_all_tables(test_engine, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    import main as main_mod
    from database import get_db_session
    from sqlalchemy import inspect

    # 1) Verify test_engine creates full ORM schema without per-test manual create_all
    async with test_engine.connect() as conn:
        table_names = set(await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names()))
    for expected in (
        'model_services',
        'project_work_jobs',
        'project_work_items',
        'project_workflow_templates',
        'object_attribute_templates',
        'validation_sets',
        'gpu_servers',
        'training_runs',
        'model_deployments',
        'model_repository_items',
        'vector_test_models',
        'image_semantic_jobs',
        'work_activity_intervals',
    ):
        assert expected in table_names, f'missing table {expected}'

    # 2) Verify startup orphan cleanup removes *-transfer and artifact-downloads while keeping validation-results
    fake_backups = tmp_path / '.backups'
    for d in ('training-transfer', 'model-deployment-transfer', 'artifact-downloads', 'validation-results'):
        folder = fake_backups / d
        folder.mkdir(parents=True)
        (folder / 'sample.bin').write_bytes(b'data')

    main_mod._cleanup_orphan_transfer_dirs(fake_backups)
    assert not (fake_backups / 'training-transfer').exists()
    assert not (fake_backups / 'model-deployment-transfer').exists()
    assert not (fake_backups / 'artifact-downloads').exists()
    assert (fake_backups / 'validation-results' / 'sample.bin').is_file()

    # 3) Verify isolated TestClient shutdown skips global engine.dispose()
    from types import SimpleNamespace
    disposed = []

    async def fake_dispose():
        disposed.append(True)

    monkeypatch.setattr(main_mod, 'engine', SimpleNamespace(dispose=fake_dispose))
    async def dummy_db():
        yield None

    main_mod.app.dependency_overrides[get_db_session] = dummy_db
    try:
        with TestClient(main_mod.app) as tc:
            assert tc.get('/health').status_code == 200
    finally:
        main_mod.app.dependency_overrides.clear()
    assert disposed == []


@pytest.mark.asyncio
async def test_fan_in_diamond_dag_deduplicates_enqueue_and_completes(client, auth_headers, work_env, monkeypatch):
    from services import project_work
    from services.storage import get_storage

    env = work_env
    env.sessions.configure(autoflush=False)
    env.project.task_type = 'detection'
    env.project.label_schema = {'annotation_scope': 'all', 'labels': ['person', 'car']}

    raw_png_1 = _make_sharp_png(200, 160)
    digest_1 = hashlib.sha256(raw_png_1).hexdigest()
    await get_storage().save_file(BytesIO(raw_png_1), env.asset.blob_path)
    env.asset.original_name = 'both_targets.png'
    env.asset.content_hash = digest_1
    env.asset.width = 200
    env.asset.height = 160

    raw_png_2 = _make_flat_dark_png(200, 160)
    digest_2 = hashlib.sha256(raw_png_2).hexdigest()
    await get_storage().save_file(BytesIO(raw_png_2), 'car_only.png')
    asset_2 = Asset(
        project_id=env.project.id,
        original_name='car_only.png',
        blob_path='car_only.png',
        content_hash=digest_2,
        file_size=len(raw_png_2),
        source_type='upload',
        width=200,
        height=160,
        format='PNG',
        annotation_status='pending',
    )
    env.db.add(asset_2)
    env.project.total_assets += 1
    await env.db.commit()

    async def fake_preannotate(config, content, width, height, *args, **kwargs):
        if hashlib.sha256(content).hexdigest() == digest_1:
            return {
                'boxes': [
                    {'label': 'person', 'x': 20, 'y': 20, 'width': 50, 'height': 90, 'score': 0.96},
                    {'label': 'car', 'x': 90, 'y': 30, 'width': 80, 'height': 60, 'score': 0.91},
                    {'label': 'person', 'x': 1, 'y': 10, 'width': 12, 'height': 20, 'score': 0.60},  # edge noise
                ]
            }
        return {
            'boxes': [
                {'label': 'car', 'x': 40, 'y': 35, 'width': 90, 'height': 65, 'score': 0.94},
            ]
        }

    monkeypatch.setattr(project_work, 'infer', fake_preannotate)

    # Diamond DAG (Fan-out + Fan-in):
    # s_pre -> s_branch (first_match=False: b_person -> s_left, b_car -> s_right) -> s_merge_clean -> s_final_script
    # asset_1 matches BOTH b_person and b_car, so both s_left and s_right forward asset_1 into s_merge_clean!
    diamond_steps = [
        {'node_key': 's_pre', 'title': '预标注检测', 'operation': 'preannotate', 'model_id': str(env.models[0].id)},
        {
            'node_key': 's_branch',
            'title': '多路并发分流',
            'operation': 'branch',
            'branch_config': {
                'first_match': False,
                'branches': [
                    {'key': 'b_person', 'name': '含人分支', 'box_labels': ['person']},
                    {'key': 'b_car', 'name': '含车分支', 'box_labels': ['car']},
                ],
            },
        },
        {
            'node_key': 's_left',
            'title': '人员路校验',
            'operation': 'script',
            'script_config': {
                'code': 'def process(ctx):\n    return {"passed": any(b["label"] == "person" for b in ctx["boxes"])}\n',
                'save_boxes': False,
            },
        },
        {
            'node_key': 's_right',
            'title': '车辆路校验',
            'operation': 'script',
            'script_config': {
                'code': 'def process(ctx):\n    return {"passed": any(b["label"] == "car" for b in ctx["boxes"])}\n',
                'save_boxes': False,
            },
        },
        {
            'node_key': 's_merge_clean',
            'title': '汇流贴边框清洗',
            'operation': 'box_clean',
            'box_clean_config': {'edge_margin_px': 5, 'save_boxes': True},
        },
        {
            'node_key': 's_final_script',
            'title': '汇流终态汇总',
            'operation': 'script',
            'script_config': {
                'code': 'def process(ctx):\n    return {"passed": len(ctx["boxes"]) >= 1, "reason": f"汇聚剩余{len(ctx[\'boxes\'])}框"}\n',
                'save_boxes': False,
            },
        },
    ]
    diamond_edges = [
        {'source': 's_pre', 'target': 's_branch', 'source_handle': 'default'},
        {'source': 's_branch', 'target': 's_left', 'source_handle': 'b_person'},
        {'source': 's_branch', 'target': 's_right', 'source_handle': 'b_car'},
        {'source': 's_left', 'target': 's_merge_clean', 'source_handle': 'default'},
        {'source': 's_right', 'target': 's_merge_clean', 'source_handle': 'default'},
        {'source': 's_merge_clean', 'target': 's_final_script', 'source_handle': 'default'},
    ]

    wf_url = f'/api/projects/{env.project.id}/work/workflows'
    launched = client.post(wf_url, headers=auth_headers, json={
        'name': '菱形汇流去重DAG',
        'image_kind': 'originals',
        'steps': diamond_steps,
        'edges': diamond_edges,
    })
    assert launched.status_code == 201, launched.text
    wf_id = launched.json()['id']

    async with httpx.AsyncClient() as http_client:
        while await project_work.process_one(env.sessions, http_client):
            pass

    runs = client.get(wf_url, headers=auth_headers).json()
    run = next(r for r in runs if r['id'] == wf_id)
    by_key = {s['node_key']: s for s in run['steps']}
    assert by_key['s_pre']['counts']['completed'] == 2
    assert by_key['s_branch']['counts']['completed'] == 2
    assert by_key['s_left']['counts']['completed'] == 1
    assert by_key['s_right']['counts']['completed'] == 2
    # Fan-in deduplication: 1 (from s_left) + 2 (from s_right) share asset_1 -> must enqueue & complete 2, NOT 3!
    assert by_key['s_merge_clean']['total'] == 2
    assert by_key['s_merge_clean']['counts']['completed'] == 2
    assert by_key['s_merge_clean']['passed_count'] == 2
    assert by_key['s_final_script']['total'] == 2
    assert by_key['s_final_script']['counts']['completed'] == 2
    assert by_key['s_final_script']['passed_count'] == 2

    async with env.sessions() as verify_db:
        ann_1 = await verify_db.scalar(
            select(Annotation).where(Annotation.asset_id == env.asset.id, Annotation.is_latest.is_(True))
        )
        assert ann_1 is not None
        assert len(ann_1.annotation_data['boxes']) == 2  # edge noise box at x=1 removed by s_merge_clean


@pytest.mark.asyncio
async def test_cancel_workflow_cascades_across_multi_branch_dag(client, auth_headers, work_env, monkeypatch):
    from services import project_work
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

    raw_png_2 = _make_flat_dark_png(200, 160)
    digest_2 = hashlib.sha256(raw_png_2).hexdigest()
    await get_storage().save_file(BytesIO(raw_png_2), 'second_orig.png')
    asset_2 = Asset(
        project_id=env.project.id,
        original_name='second_orig.png',
        blob_path='second_orig.png',
        content_hash=digest_2,
        file_size=len(raw_png_2),
        source_type='upload',
        width=200,
        height=160,
        format='PNG',
        annotation_status='pending',
    )
    env.db.add(asset_2)
    env.project.total_assets += 1
    await env.db.commit()

    async def fake_crop_infer(*args, **kwargs):
        return {
            'boxes': [
                {'label': 'person', 'x': 10, 'y': 10, 'width': 50, 'height': 80, 'score': 0.95},
                {'label': 'car', 'x': 80, 'y': 20, 'width': 70, 'height': 50, 'score': 0.90},
            ]
        }

    monkeypatch.setattr(project_work, 'infer', fake_crop_infer)

    steps = [
        {'node_key': 'c_crop', 'operation': 'crop', 'model_id': str(env.models[0].id)},
        {
            'node_key': 'c_branch',
            'operation': 'branch',
            'branch_config': {
                'first_match': True,
                'branches': [{'key': 'b_person', 'name': '人员切图', 'crop_labels': ['person']}],
            },
        },
        {
            'node_key': 'c_person_script',
            'operation': 'script',
            'script_config': {'code': 'def process(ctx):\n    return {"passed": True}\n'},
        },
        {
            'node_key': 'c_car_filter',
            'operation': 'img_filter',
            'img_filter_config': {'min_width': 10},
        },
        {
            'node_key': 'c_sink',
            'operation': 'script',
            'script_config': {'code': 'def process(ctx):\n    return {"passed": True}\n'},
        },
    ]
    edges = [
        {'source': 'c_crop', 'target': 'c_branch', 'source_handle': 'default'},
        {'source': 'c_branch', 'target': 'c_person_script', 'source_handle': 'b_person'},
        {'source': 'c_branch', 'target': 'c_car_filter', 'source_handle': 'else'},
        {'source': 'c_person_script', 'target': 'c_sink', 'source_handle': 'default'},
        {'source': 'c_car_filter', 'target': 'c_sink', 'source_handle': 'default'},
    ]

    wf_url = f'/api/projects/{env.project.id}/work/workflows'
    launched = client.post(wf_url, headers=auth_headers, json={
        'name': '多分支取消测试',
        'image_kind': 'originals',
        'steps': steps,
        'edges': edges,
    })
    assert launched.status_code == 201, launched.text
    wf_id = launched.json()['id']

    # Partially execute 2 items so pending items exist simultaneously across entry, branch, and downstream branch queues
    async with httpx.AsyncClient() as http_client:
        assert await project_work.process_one(env.sessions, http_client) is True
        assert await project_work.process_one(env.sessions, http_client) is True

    cancelled = client.post(f'{wf_url}/{wf_id}/cancel', headers=auth_headers)
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()['changed_count'] >= 2

    runs = client.get(wf_url, headers=auth_headers).json()
    run = next(r for r in runs if r['id'] == wf_id)
    assert len(run['steps']) == 5
    total_cancelled_items = 0
    for s in run['steps']:
        assert s['workflow_cancelled'] is True
        assert s['counts'].get('pending', 0) == 0
        assert s['counts'].get('running', 0) == 0
        total_cancelled_items += s['counts'].get('cancelled', 0)
    assert total_cancelled_items == cancelled.json()['changed_count']

    # Worker must find nothing left to process after cascade cancel
    async with httpx.AsyncClient() as http_client:
        assert await project_work.process_one(env.sessions, http_client) is False


@pytest.mark.asyncio
async def test_dry_run_cross_project_404_and_template_ai_invalid_model_cycle_guards(client, auth_headers, work_env, monkeypatch):
    from services import workflow_ai
    from services.storage import get_storage

    env = work_env
    raw_png = _make_sharp_png(120, 90)
    await get_storage().save_file(BytesIO(raw_png), env.asset.blob_path)
    env.asset.content_hash = hashlib.sha256(raw_png).hexdigest()
    env.asset.width = 120
    env.asset.height = 90

    # Prepare a second project in the same tenant + an asset belonging to that other project
    other_project = Project(
        tenant_id=env.project.tenant_id,
        project_name='other isolated project',
        task_type=TaskType.DETECTION,
        status=ProjectStatus.ACTIVE,
        total_assets=1,
        annotated_count=0,
        reviewed_count=0,
    )
    env.db.add(other_project)
    await env.db.flush()
    other_asset = Asset(
        project_id=other_project.id,
        original_name='foreign.png',
        blob_path=env.asset.blob_path,
        content_hash=env.asset.content_hash,
        file_size=len(raw_png),
        source_type='upload',
        width=120,
        height=90,
        format='PNG',
        annotation_status='pending',
    )
    env.db.add(other_asset)

    # Prepare another tenant + cross-tenant models + offline models in current tenant
    other_tenant = Tenant(id=2, tenant_name='other_tenant')
    env.db.add(other_tenant)
    await env.db.flush()

    foreign_det = ModelService(
        tenant_id=2,
        key='foreign-det',
        name='跨租户检测模型',
        kind='detection',
        protocol='model_service',
        base_url='http://foreign.local',
        endpoint='/detect',
        model_name='det',
        enabled=True,
    )
    foreign_llm = ModelService(
        tenant_id=2,
        key='foreign-llm',
        name='跨租户编排模型',
        kind='llm',
        protocol='openai_chat',
        base_url='http://foreign.local/v1',
        endpoint='/chat/completions',
        model_name='llm',
        enabled=True,
    )
    offline_det = ModelService(
        tenant_id=1,
        key='offline-det',
        name='离线检测模型',
        kind='detection',
        protocol='model_service',
        base_url='http://offline.local',
        endpoint='/detect',
        model_name='det-off',
        enabled=True,
        revision=3,
        connection_check={'status': 'offline', 'checked_revision': 3, 'message': '连接超时'},
    )
    offline_llm = ModelService(
        tenant_id=1,
        key='offline-llm',
        name='离线编排LLM',
        kind='llm',
        protocol='openai_chat',
        base_url='http://offline.local/v1',
        endpoint='/chat/completions',
        model_name='llm-off',
        enabled=True,
        revision=2,
        connection_check={'status': 'offline', 'checked_revision': 2, 'message': '连接拒绝'},
    )
    online_llm = ModelService(
        tenant_id=1,
        key='online-llm',
        name='在线编排LLM',
        kind='llm',
        protocol='openai_chat',
        base_url='http://online.local/v1',
        endpoint='/chat/completions',
        model_name='llm-ok',
        enabled=True,
    )
    env.db.add_all([foreign_det, foreign_llm, offline_det, offline_llm, online_llm])
    await env.db.commit()

    wf_url = f'/api/projects/{env.project.id}/work/workflows'
    simple_steps = [{'node_key': 'n1', 'operation': 'img_filter', 'img_filter_config': {'min_width': 10}}]

    # 1) Dry-run with cross-project sample_asset_ids must be rejected with 404
    dry_cross = client.post(f'{wf_url}/dry-run', headers=auth_headers, json={
        'steps': simple_steps,
        'sample_asset_ids': [str(other_asset.id)],
    })
    assert dry_cross.status_code == 404, dry_cross.text
    assert '不存在或不属于当前项目' in dry_cross.json()['detail']

    dry_mixed = client.post(f'{wf_url}/dry-run', headers=auth_headers, json={
        'steps': simple_steps,
        'sample_asset_ids': [str(env.asset.id), str(other_asset.id)],
    })
    assert dry_mixed.status_code == 404, dry_mixed.text

    # 2) Saving workflow template & dry-run with cyclic edges must be rejected with 422
    cyclic_payload = {
        'name': '有环工作流模板',
        'steps': [
            {'node_key': 't1', 'operation': 'img_filter', 'img_filter_config': {'min_width': 10}},
            {'node_key': 't2', 'operation': 'box_clean', 'box_clean_config': {'edge_margin_px': 2}},
            {'node_key': 't3', 'operation': 'img_filter', 'img_filter_config': {'min_height': 10}},
        ],
        'edges': [
            {'source': '__input__', 'target': 't1', 'source_handle': 'default'},
            {'source': 't1', 'target': 't2', 'source_handle': 'default'},
            {'source': 't2', 'target': 't3', 'source_handle': 'default'},
            {'source': 't3', 'target': 't1', 'source_handle': 'default'},
        ],
    }
    tpl_cyclic = client.post('/api/workflow-templates', headers=auth_headers, json=cyclic_payload)
    assert tpl_cyclic.status_code == 422, tpl_cyclic.text

    dry_cyclic = client.post(f'{wf_url}/dry-run', headers=auth_headers, json={
        'steps': cyclic_payload['steps'],
        'edges': cyclic_payload['edges'],
    })
    assert dry_cyclic.status_code == 422, dry_cyclic.text

    # 3) Workflow execution & dry-run referencing cross-tenant or offline model_id must be intercepted
    for bad_mid, expected_code in ((str(foreign_det.id), 404), (str(offline_det.id), 400)):
        bad_steps = [{'node_key': 'm1', 'operation': 'detect', 'model_id': bad_mid}]
        res_run = client.post(wf_url, headers=auth_headers, json={'name': '非法模型测试', 'steps': bad_steps})
        assert res_run.status_code == expected_code, res_run.text
        res_dry = client.post(f'{wf_url}/dry-run', headers=auth_headers, json={'steps': bad_steps})
        assert res_dry.status_code == expected_code, res_dry.text

    # 4) AI orchestration (both project-level & template-level) rejects cross-tenant / offline orchestrator & generated invalid models / cycles
    assert client.post(
        f'{wf_url}/ai-generate', headers=auth_headers, json={'prompt': '检测行人', 'model_id': str(foreign_llm.id)}
    ).status_code == 404
    assert client.post(
        '/api/workflow-templates/ai-generate', headers=auth_headers, json={'prompt': '检测行人', 'model_id': str(offline_llm.id)}
    ).status_code == 400

    for bad_target_mid in (str(offline_det.id), str(foreign_det.id)):
        async def fake_bad_model_llm(*args, _mid=bad_target_mid, **kwargs):
            draft = {
                'name': '引用非法模型草稿',
                'steps': [{'node_key': 's1', 'operation': 'detect', 'model_id': _mid}],
                'edges': [],
            }
            return {'kind': 'llm', 'boxes': [], 'caption': json.dumps(draft), 'warnings': []}

        monkeypatch.setattr(workflow_ai, 'infer_text', fake_bad_model_llm)
        ai_bad_model = client.post(
            '/api/workflow-templates/ai-generate',
            headers=auth_headers,
            json={'prompt': '使用检测模型过滤', 'model_id': str(online_llm.id)},
        )
        assert ai_bad_model.status_code == 422, ai_bad_model.text
        assert 'model_id' in ai_bad_model.json()['detail']

    async def fake_cyclic_llm(*args, **kwargs):
        draft = {
            'name': '有环AI草稿',
            'steps': [
                {'node_key': 's1', 'operation': 'img_filter', 'img_filter_config': {'min_width': 10}},
                {'node_key': 's2', 'operation': 'img_filter', 'img_filter_config': {'min_height': 10}},
            ],
            'edges': [
                {'source': '__input__', 'target': 's1', 'source_handle': 'default'},
                {'source': 's1', 'target': 's2', 'source_handle': 'default'},
                {'source': 's2', 'target': 's1', 'source_handle': 'default'},
            ],
        }
        return {'kind': 'llm', 'boxes': [], 'caption': json.dumps(draft), 'warnings': []}

    monkeypatch.setattr(workflow_ai, 'infer_text', fake_cyclic_llm)
    ai_cyclic = client.post(
        f'{wf_url}/ai-generate',
        headers=auth_headers,
        json={'prompt': '生成循环工作流', 'model_id': str(online_llm.id)},
    )
    assert ai_cyclic.status_code == 422, ai_cyclic.text
    assert '循环连线' in ai_cyclic.json()['detail']
