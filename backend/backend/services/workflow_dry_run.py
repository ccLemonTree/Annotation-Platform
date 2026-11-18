"""In-memory dry-run execution of workflow DAGs on 1-3 sample assets (zero DB writes)."""
import asyncio
import base64
from io import BytesIO
from time import perf_counter
import httpx
from fastapi import HTTPException
from PIL import Image
from sqlalchemy import select

from models import Annotation, Asset
from schemas.model_service import ModelServiceConfig
from schemas.object_attributes import AttributeSchema
from schemas.project_work import MODEL_WORKFLOW_OPERATIONS, OPERATION_TITLES, WorkflowDryRunRequest, resolve_workflow_dag
from services import workflow_builtins as builtins
from services.automation_review import audit_image, auto_pass
from services.image_quality import analyze as quality_analyze
from services.image_semantics import caption, model_snapshot
from services.model_inference import infer
from services.object_attributes import analyze as analyze_attributes
from services.project_work import crop_attribute_template, crop_label, crop_region, read_content
from services.project_workflow import output_ids, result_job
from services.script_sandbox import run_script

MAX_STAGE_ITEMS = 6


def _preview_data_url(content: bytes, max_side: int = 280) -> str:
    with Image.open(BytesIO(content)) as opened:
        pic = opened.convert('RGB')
        pic.thumbnail((max_side, max_side))
        out = BytesIO()
        pic.save(out, format='JPEG', quality=82)
    return 'data:image/jpeg;base64,' + base64.b64encode(out.getvalue()).decode('ascii')


async def run_dry_run(db, project, request: WorkflowDryRunRequest) -> dict:
    topo_keys, by_key, adj, entry_keys, resolved_edges = resolve_workflow_dag(request.steps, request.edges)

    if request.sample_asset_ids:
        unique_ids = list(dict.fromkeys(request.sample_asset_ids))[:3]
        assets = list((await db.scalars(
            select(Asset).where(Asset.project_id == project.id, Asset.id.in_(unique_ids)).order_by(Asset.created_at.desc())
        )).all())
        if len(assets) != len(unique_ids):
            raise HTTPException(404, '所选试运行样本图片不存在或不属于当前项目')
    else:
        if request.source_job_id:
            source = await result_job(db, project, request.source_job_id)
            conds = [Asset.id.in_(output_ids(db, source))]
        else:
            conds = [] if request.image_kind == 'all' else [
                Asset.source_type == 'model_crop' if request.image_kind == 'crops' else Asset.source_type != 'model_crop'
            ]
        assets = list((await db.scalars(
            select(Asset).where(Asset.project_id == project.id, *conds).order_by(Asset.created_at.desc(), Asset.id).limit(2)
        )).all())
        if not assets:
            raise HTTPException(400, '当前输入范围暂无图片可供试运行，请先向项目添加图片')

    annotations = {
        ann.asset_id: ann for ann in (await db.scalars(
            select(Annotation).where(Annotation.asset_id.in_([a.id for a in assets]), Annotation.is_latest.is_(True))
        )).all()
    }

    snapshots: dict[str, dict] = {}
    for key in topo_keys:
        step = by_key[key]
        if step.operation in MODEL_WORKFLOW_OPERATIONS:
            kind = 'detection' if step.operation in ('crop', 'detect', 'preannotate') else 'caption'
            snapshots[key] = await model_snapshot(db, project.tenant_id, step.model_id, kind)

    project_id_str = str(project.id)
    project_labels = list((project.label_schema or {}).get('labels') or [])
    project_attr_def = request.attribute_schema.model_dump() if request.attribute_schema else (project.label_schema or {}).get('object_attributes')

    initial_items = []
    for asset in assets:
        content = await read_content(asset)
        ann = annotations.get(asset.id)
        data = dict(ann.annotation_data) if ann else {}
        initial_items.append({
            'sample_id': str(asset.id),
            'asset_id': str(asset.id),
            'original_name': asset.original_name,
            'content': content,
            'width': asset.width,
            'height': asset.height,
            'source_type': asset.source_type,
            'crop_label': crop_label(asset),
            'boxes': list(data.get('boxes') or []),
            'attributes': dict(data.get('attributes') or {}),
            'description': str(data.get('description') or ''),
            'annotation_source': ann.annotation_source if ann else None,
        })

    # Ensure read-only session leaves zero pending state before network/sandbox execution.
    await db.rollback()

    queues: dict[str, list[dict]] = {k: [] for k in topo_keys}
    for entry in entry_keys:
        queues[entry] = [dict(item) for item in initial_items]

    node_reports = []
    async with httpx.AsyncClient(follow_redirects=False, timeout=90.0) as client:
        for node_key in topo_keys:
            step = by_key[node_key]
            title = step.title or OPERATION_TITLES.get(step.operation, step.operation)
            incoming = queues[node_key][:MAX_STAGE_ITEMS]
            t0 = perf_counter()
            item_summaries = []
            routed_outputs: list[tuple[set[str], list[dict]]] = []
            seen_phashes: list[str] = []
            node_error = None

            try:
                for sim in incoming:
                    op = step.operation
                    width, height = sim['width'], sim['height']
                    content = sim['content']
                    if op == 'branch':
                        res = builtins.execute_branch(
                            width=width, height=height, crop_label=sim['crop_label'],
                            boxes=sim['boxes'], attributes=sim['attributes'],
                            config=step.branch_config.model_dump(),
                        )
                        handles = set(res['matched_branches'])
                        routed_outputs.append((handles, [sim]))
                        item_summaries.append({
                            'name': sim['original_name'], 'passed': True,
                            'reason': res['reason'], 'branches': res['matched_branches'],
                        })
                    elif op == 'img_filter':
                        fcfg = step.img_filter_config.model_dump()
                        res = await asyncio.to_thread(builtins.execute_img_filter, content, fcfg, seen_phashes)
                        if res['passed'] and res.get('phash'):
                            seen_phashes.append(res['phash'])
                        if res['passed']:
                            routed_outputs.append(({'default'}, [sim]))
                        item_summaries.append({
                            'name': sim['original_name'], 'passed': res['passed'],
                            'reason': res['reason'], 'metrics': res['metrics'],
                        })
                    elif op == 'box_clean':
                        res = builtins.execute_box_clean(sim['boxes'], width, height, step.box_clean_config.model_dump())
                        next_sim = {**sim, 'boxes': res['boxes']}
                        if res['passed']:
                            routed_outputs.append(({'default'}, [next_sim]))
                        item_summaries.append({
                            'name': sim['original_name'], 'passed': res['passed'],
                            'reason': res['reason'], 'boxes': res['boxes'][:30],
                            'preview_url': _preview_data_url(content),
                            'width': width, 'height': height,
                        })
                    elif op == 'img_transform':
                        res = await asyncio.to_thread(
                            builtins.execute_img_transform, content,
                            project_id=project_id_str, asset_id=sim['asset_id'],
                            boxes=sim['boxes'], crop_label=sim['crop_label'],
                            config=step.img_transform_config.model_dump(),
                        )
                        derived_sims = []
                        previews = []
                        for idx, c in enumerate(res['crops'][:4]):
                            derived_sims.append({
                                **sim, 'original_name': f"{sim['original_name']}#变换{idx+1}",
                                'content': c['content'], 'width': c['width'], 'height': c['height'],
                                'source_type': 'model_crop', 'crop_label': c['box']['label'], 'boxes': [],
                            })
                            previews.append(_preview_data_url(c['content']))
                        routed_outputs.append(({'default'}, derived_sims))
                        item_summaries.append({
                            'name': sim['original_name'], 'passed': True,
                            'reason': res['reason'], 'derived_previews': previews,
                        })
                    elif op == 'script':
                        scfg = step.script_config
                        res = await run_script(
                            scfg.code, content, width=width, height=height,
                            original_name=sim['original_name'], source_type=sim['source_type'],
                            crop_label=sim['crop_label'], boxes=sim['boxes'],
                            attributes=sim['attributes'], description=sim['description'],
                            timeout_seconds=scfg.timeout_seconds,
                        )
                        passed = bool(res.get('passed', True))
                        handles = set(res.get('branch') or []) | ({'default'} if passed else set())
                        previews = []
                        if res.get('derived'):
                            derived_sims = []
                            for idx, d in enumerate(res['derived'][:4]):
                                derived_sims.append({
                                    **sim, 'original_name': f"{sim['original_name']}#脚本衍生{idx+1}",
                                    'content': d['content'], 'width': d['width'], 'height': d['height'],
                                    'source_type': 'model_crop', 'crop_label': d.get('label') or sim['crop_label'],
                                    'boxes': res.get('boxes', []),
                                })
                                previews.append(_preview_data_url(d['content']))
                            if handles:
                                routed_outputs.append((handles, derived_sims))
                        else:
                            next_sim = {
                                **sim,
                                'boxes': res.get('boxes', sim['boxes']),
                                'attributes': res.get('attributes', sim['attributes']),
                                'description': res.get('description', sim['description']),
                            }
                            if handles and (passed or res.get('branch')):
                                routed_outputs.append((handles, [next_sim]))
                        item_summaries.append({
                            'name': sim['original_name'], 'passed': passed,
                            'reason': res.get('reason') or ('脚本通过' if passed else '脚本过滤'),
                            'logs': res.get('logs') or [],
                            'boxes': res.get('boxes', [])[:30] if 'boxes' in res else undefined_to_none(None),
                            'attributes': res.get('attributes'),
                            'description': res.get('description'),
                            'derived_previews': previews,
                        })
                    else:
                        cfg = ModelServiceConfig(**snapshots[node_key]['config'])
                        if op == 'caption':
                            desc = await caption(cfg, content, step.prompt, client)
                            next_sim = {**sim, 'description': desc}
                            routed_outputs.append(({'default'}, [next_sim]))
                            item_summaries.append({'name': sim['original_name'], 'passed': True, 'reason': desc[:200], 'description': desc})
                        elif op in ('quality', 'filter'):
                            qres = await quality_analyze(cfg, content, step.prompt, client)
                            if qres['passed']:
                                routed_outputs.append(({'default'}, [sim]))
                            item_summaries.append({'name': sim['original_name'], 'passed': qres['passed'], 'reason': qres['reason']})
                        elif op == 'attributes':
                            if step.attribute_templates:
                                dev_info = {'crop': {'box': {'label': sim['crop_label']}}} if sim['crop_label'] else {}
                                fake_asset = type('SimObj', (), {'source_type': sim['source_type'], 'device_info': dev_info})()
                                lbl, _, schema = crop_attribute_template(fake_asset, [t.model_dump() for t in step.attribute_templates])
                                if schema is None:
                                    item_summaries.append({'name': sim['original_name'], 'passed': False, 'reason': f'裁剪类别「{lbl or "无"}」未匹配属性模板（已跳过）'})
                                    continue
                            else:
                                if not project_attr_def:
                                    raise HTTPException(400, '请先配置目标与属性后再试运行属性分析')
                                schema = AttributeSchema.model_validate(project_attr_def).model_dump()
                            attrs = await analyze_attributes(cfg, content, schema, client, prompt=step.prompt, attribute_prompt=step.attribute_prompt)
                            next_sim = {**sim, 'attributes': attrs}
                            routed_outputs.append(({'default'}, [next_sim]))
                            item_summaries.append({'name': sim['original_name'], 'passed': True, 'reason': f'识别 {len(attrs)} 项属性', 'attributes': attrs})
                        elif op == 'ai_review':
                            if not sim['boxes']:
                                routed_outputs.append(({'default'}, [sim]))
                                item_summaries.append({'name': sim['original_name'], 'passed': True, 'reason': '无待审正样本框，转人工审核'})
                            else:
                                ev = await audit_image(cfg, content, sim['boxes'], step.labels or project_labels, step.expansion, step.prompt, client)
                                ok = auto_pass(sim['boxes'], ev, step.auto_approve_confidence, True)
                                routed_outputs.append(({'default'}, [sim]))
                                item_summaries.append({'name': sim['original_name'], 'passed': True, 'reason': ('AI 审核通过：' if ok else '转人工审核：') + ev['reason']})
                        else:
                            cfg = cfg.model_copy(update={'confidence': step.confidence, 'labels': step.labels or cfg.labels})
                            det = await infer(cfg, content, width, height, step.prompt, client=client)
                            allowed_labels = {cfg.label_mapping.get(l, l) for l in step.labels}
                            boxes = [b for b in det['boxes'] if not allowed_labels or b['label'] in allowed_labels]
                            if op == 'detect':
                                passed = bool(boxes)
                                if passed:
                                    routed_outputs.append(({'default'}, [{**sim, 'boxes': boxes}]))
                                item_summaries.append({'name': sim['original_name'], 'passed': passed, 'reason': f'检出 {len(boxes)} 个目标', 'boxes': boxes[:30], 'preview_url': _preview_data_url(content), 'width': width, 'height': height})
                            elif op == 'preannotate':
                                routed_outputs.append(({'default'}, [{**sim, 'boxes': boxes}]))
                                item_summaries.append({'name': sim['original_name'], 'passed': True, 'reason': f'预标注 {len(boxes)} 个目标框', 'boxes': boxes[:30], 'preview_url': _preview_data_url(content), 'width': width, 'height': height})
                            else:
                                derived_sims = []
                                previews = []
                                with Image.open(BytesIO(content)) as opened:
                                    pic = opened.convert('RGB')
                                    for idx, box in enumerate(boxes):
                                        reg = crop_region(box, width, height, step.expansion)
                                        if max(reg[2] - reg[0], reg[3] - reg[1]) < step.min_crop_long_edge:
                                            continue
                                        buf = BytesIO()
                                        pic.crop(reg).save(buf, format='PNG')
                                        raw_c = buf.getvalue()
                                        if len(derived_sims) < MAX_STAGE_ITEMS:
                                            derived_sims.append({
                                                **sim, 'original_name': f"{sim['original_name']}#{box['label']}_{idx+1}",
                                                'content': raw_c, 'width': reg[2] - reg[0], 'height': reg[3] - reg[1],
                                                'source_type': 'model_crop', 'crop_label': box['label'], 'boxes': [],
                                            })
                                        if len(previews) < 4:
                                            previews.append(_preview_data_url(raw_c))
                                if derived_sims:
                                    routed_outputs.append(({'default'}, derived_sims))
                                item_summaries.append({
                                    'name': sim['original_name'], 'passed': bool(derived_sims),
                                    'reason': f'切出 {len(derived_sims)} 张目标子图',
                                    'derived_previews': previews,
                                })
            except HTTPException as exc:
                node_error = str(exc.detail)
            except Exception as exc:
                node_error = str(exc)[:300]

            out_count = sum(len(items) for _, items in routed_outputs)
            for handles, out_items in routed_outputs:
                for edge_handle, tgt_key in adj[node_key]:
                    if edge_handle in handles and len(queues[tgt_key]) < MAX_STAGE_ITEMS:
                        queues[tgt_key].extend(out_items[:MAX_STAGE_ITEMS - len(queues[tgt_key])])

            node_reports.append({
                'node_key': node_key,
                'title': title,
                'operation': step.operation,
                'duration_ms': int(round((perf_counter() - t0) * 1000)),
                'input_count': len(incoming),
                'output_count': out_count,
                'error': node_error,
                'items': [{k: v for k, v in row.items() if v is not None} for row in item_summaries],
            })

    return {
        'sample_count': len(initial_items),
        'samples': [{'asset_id': s['asset_id'], 'original_name': s['original_name'], 'width': s['width'], 'height': s['height']} for s in initial_items],
        'edges': [e.model_dump() for e in resolved_edges],
        'nodes': node_reports,
    }


def undefined_to_none(val):
    return val
