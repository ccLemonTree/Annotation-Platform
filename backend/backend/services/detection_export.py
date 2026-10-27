"""Training packages pin annotation versions, category mapping and image hashes."""
import hashlib
import asyncio
import json
import logging
import tempfile
import zipfile
from hashlib import sha256
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from PIL import Image
import imagehash
from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select
from models import Asset, Annotation
from services.annotations import detection_boxes
from services.storage import get_storage
from services.dataset_splits import SplitPolicy, split_report
from services.annotation_scope import eligible_assets


def split_failure_reason(report, rows):
    error = report['errors'][0]
    examples = [f'{asset.original_name}（{asset.id}）' for asset, _ in rows
                if str(asset.id) in error.get('asset_ids', [])[:2]]
    return error['message'] + ('；冲突图片：' + '、'.join(examples) if examples else '')


def simple_split(rows, labels, mode, ratios, budgets, groups=None):
    if mode not in ('ratio', 'train_only', 'val_only'):
        raise HTTPException(400, '划分模式无效')
    if mode == 'ratio' and (not isinstance(ratios, dict) or set(ratios) != {'train', 'val', 'test'} or
            any(type(value) is not int or value < 0 or value > 100 for value in ratios.values()) or sum(ratios.values()) != 100):
        raise HTTPException(400, '训练、验证、测试比例必须为0到100的整数且合计100')
    if not isinstance(budgets, dict) or set(budgets) - set(labels) or any(
            value is not None and (type(value) is not int or value < 1) for value in budgets.values()):
        raise HTTPException(400, '每类图片预算无效')
    ordered = sorted(rows, key=lambda row: sha256(str(row[0].id).encode()).hexdigest())
    splits, train_counts = {}, Counter()
    if groups is None:
        target_train = len(rows) if mode == 'train_only' else 0 if mode == 'val_only' else round(len(rows) * ratios['train'] / 100)
        for asset, annotation in ordered:
            key = str(asset.id)
            image_labels = {box['label'] for box in annotation.annotation_data['boxes'] if box['label'] in labels}
            fits_budget = all(budgets.get(label) is None or train_counts[label] < budgets[label] for label in image_labels)
            if len(splits) < target_train and fits_budget:
                splits[key] = 'train'
                train_counts.update(image_labels)
        remaining = [row for row in ordered if str(row[0].id) not in splits]
        if mode == 'val_only':
            splits.update({str(asset.id): 'val' for asset, _ in remaining})
        if mode == 'ratio':
            holdout_ratio = ratios['val'] + ratios['test']
            val_target = round(len(rows) * ratios['val'] / 100)
            for index, (asset, _) in enumerate(remaining):
                split = 'val' if holdout_ratio and len([v for v in splits.values() if v == 'val']) < val_target else 'test'
                splits[str(asset.id)] = split
    else:
        ratios = ratios if mode == 'ratio' else dict(train=100 if mode=='train_only' else 0,val=100 if mode=='val_only' else 0,test=0)
        sizes = Counter()
        per_image = {str(asset.id):{box['label'] for box in ann.annotation_data['boxes'] if box['label'] in labels} for asset,ann in rows}
        # Place large connected groups first; ties never depend on database row order.
        for group in sorted((sorted(group) for group in groups),key=lambda group:(-len(group),sha256('\0'.join(group).encode()).hexdigest())):
            counts = Counter(label for key in group for label in per_image[key])
            candidates = [split for split in ('train','val','test') if ratios[split]>0 and (split!='train' or
                all(budgets.get(label) is None or train_counts[label]+count<=budgets[label] for label,count in counts.items()))]
            if not candidates:continue
            split = min(candidates,key=lambda candidate:sum((sizes[name]+(len(group) if name==candidate else 0)-len(rows)*ratios[name]/100)**2 for name in ('train','val','test')))
            splits.update(dict.fromkeys(group,split));sizes[split]+=len(group)
            if split=='train':train_counts.update(counts)
    labels_by_split = {split: {label: 0 for label in labels} for split in ('train', 'val', 'test')}
    for asset, annotation in rows:
        split = splits.get(str(asset.id))
        if split:
            for label in {box['label'] for box in annotation.annotation_data['boxes'] if box['label'] in labels}:
                labels_by_split[split][label] += 1
    result = dict(splits=splits, labels_by_split=labels_by_split,
                images_by_split=dict(Counter(splits.values())), excluded_asset_ids=[str(a.id) for a, _ in rows if str(a.id) not in splits])
    if groups is not None:
        result['grouping']=dict(group_count=len(groups),largest_group=max(map(len,groups),default=0),
            actual_ratios={name:round(sum(value==name for value in splits.values())/len(splits)*100,2) if splits else 0 for name in ('train','val','test')},
            empty_requested_splits=[name for name in ('train','val','test') if ratios[name]>0 and name not in splits.values()])
    return result

async def export_rows(db, project, approved_only, lock=True):
    if project.task_type != 'detection':
        raise HTTPException(400, '训练图片包当前仅支持检测项目')
    query = select(Asset).where(Asset.project_id == project.id,eligible_assets(project)).order_by(Asset.id)
    assets = (await db.execute(query.with_for_update(of=Asset) if lock else query)).scalars().all()
    annotations = (await db.execute(select(Annotation).join(Asset).where(
        Asset.project_id == project.id, Annotation.is_latest.is_(True)))).scalars().all()
    latest = {a.asset_id: a for a in annotations}
    rows, excluded = [], []
    for asset in assets:
        ann = latest.get(asset.id)
        if ann is None or (approved_only and ann.review_status != 'approved'):
            excluded.append(str(asset.id))
        else:
            detection_boxes(ann.annotation_data, asset.width, asset.height)
            rows.append((asset, ann))
    return rows, excluded

def category_labels(project, rows, selected):
    declared = (project.label_schema or {}).get('labels', [])
    found = {box['label'] for _, ann in rows for box in ann.annotation_data['boxes']}
    if isinstance(declared, list) and any(not isinstance(label, str) or not label.strip() for label in declared):
        raise HTTPException(400, '项目labels必须为非空名称列表')
    labels = list(dict.fromkeys((declared if isinstance(declared, list) else []) + sorted(found))) or ['人']
    if selected:
        if not set(selected) <= set(labels):
            raise HTTPException(400, '导出类别不存在')
        labels = [label for label in labels if label in selected]
    return labels

def coco_document(rows, labels, image_paths):
    data = dict(images=[], annotations=[], categories=[dict(id=i+1, name=label, supercategory='object') for i,label in enumerate(labels)])
    ids = {label:i+1 for i,label in enumerate(labels)}
    for i,(asset,ann) in enumerate(rows,1):
        data['images'].append(dict(id=i, file_name=image_paths[str(asset.id)], width=asset.width, height=asset.height))
        for box in ann.annotation_data['boxes']:
            if box['label'] not in ids: continue
            data['annotations'].append(dict(id=len(data['annotations'])+1, image_id=i, category_id=ids[box['label']],
                bbox=[box[k] for k in ('x','y','width','height')], area=box['width']*box['height'], iscrowd=0, segmentation=[]))
    return data

async def training_package(db, project, format, selected=None, approved_only=True, splits=None, policy=None, selection=None):
    rows, excluded = await export_rows(db, project, approved_only)
    selection_result = None
    if selection is not None:
        labels = category_labels(project, rows, selected)
        selection_result = await prepare_selection(rows, labels, selection)
        ratios = selection['ratios'] if selection['mode'] == 'ratio' else {
            'train': 100 if selection['mode'] == 'train_only' else 0, 'val': 100 if selection['mode'] == 'val_only' else 0, 'test': 0}
        selection_result.update(mode=selection['mode'], ratios=ratios, budgets=selection['budgets'],
            check_duplicates=selection['check_duplicates'], group_similar=selection.get('group_similar',False), phash_distance=selection['phash_distance'],
            color_distance=selection['color_distance'], require_source=selection['require_source'])
        splits = selection_result['splits']
        excluded.extend(selection_result['excluded_asset_ids'])
        rows = [(asset, ann) for asset, ann in rows if str(asset.id) in splits]
        report = selection_result.get('leakage_report')
        if report and report['errors']:
            reason = split_failure_reason(report, rows)
            logging.getLogger(__name__).warning('Export rejected before ZIP: project=%s code=%s reason=%s', project.id, report['errors'][0]['code'], reason)
            raise HTTPException(409, reason)
    return await run_in_threadpool(lambda: asyncio.run(
        _build_package(project, rows, excluded, format, selected, approved_only, splits, policy, selection_result)))


async def preview_splits(db, project, selected, approved_only, policy, splits=None, suggest=False):
    rows, excluded = await export_rows(db, project, approved_only, lock=False)
    labels = category_labels(project, rows, selected)
    return dict(await inspect_splits(rows, labels, policy, splits, suggest), labels=labels, excluded_asset_ids=excluded)


async def inspect_splits(rows, labels, policy, splits=None, suggest=False):
    fingerprints = await image_fingerprints(rows)
    return await run_in_threadpool(split_report, rows, labels, policy, splits, suggest, fingerprints)


async def image_fingerprints(rows):

    async def fingerprint(asset):
        try:
            source = await get_storage().get_file(asset.blob_path)
        except (FileNotFoundError, ValueError):
            raise HTTPException(400, f'图片文件不可读：{asset.id}')
        try:
            digest = hashlib.sha256()
            for chunk in iter(lambda: source.read(65536), b''):
                digest.update(chunk)
            if digest.hexdigest() != asset.content_hash:
                raise HTTPException(400, '图片哈希与资产不一致')
            source.seek(0)
            with Image.open(source) as image:
                if image.size != (asset.width, asset.height):
                    raise HTTPException(400, '图片尺寸与标注不一致')
                image.verify()
            source.seek(0)
            with Image.open(source) as image:
                return str(asset.id), (imagehash.phash(image), image.convert('RGB').resize((1, 1)).getpixel((0, 0)))
        finally:
            source.close()

    def inspect():
        with ThreadPoolExecutor(max_workers=4) as workers:
            return dict(workers.map(lambda row: asyncio.run(fingerprint(row[0])), rows))

    return await run_in_threadpool(inspect)


async def prepare_selection(rows, labels, selection):
    result = simple_split(rows, labels, selection['mode'], selection['ratios'], selection['budgets'])
    result['can_freeze'] = bool(result['splits'])
    if selection['check_duplicates']:
        policy = SplitPolicy(phash_distance=selection['phash_distance'], color_distance=selection['color_distance'],
            training_budget_per_class=None, require_source=selection['require_source'], require_independent=False)
        if selection.get('group_similar'):
            fingerprints = await image_fingerprints(rows)
            grouping = await run_in_threadpool(split_report, rows, labels, policy, result['splits'], False, fingerprints)
            result = simple_split(rows, labels, selection['mode'], selection['ratios'], selection['budgets'], groups=grouping['groups'])
        chosen = [row for row in rows if str(row[0].id) in result['splits']]
        if selection.get('group_similar'):
            kept = {str(asset.id) for asset,_ in chosen}
            report = await run_in_threadpool(split_report, chosen, labels, policy, result['splits'], False,
                {key:value for key,value in fingerprints.items() if key in kept})
        else:report = await inspect_splits(chosen, labels, policy, result['splits'])
        result.update(errors=report['errors'], warnings=report['warnings'], can_freeze=bool(result['splits']) and not report['errors'],
            failure_reason=split_failure_reason(report, chosen) if report['errors'] else '',leakage_report=report)
    return result


async def preview_simple_split(db, project, selected, approved_only, selection):
    rows, excluded = await export_rows(db, project, approved_only, lock=False)
    labels = category_labels(project, rows, selected)
    result = await prepare_selection(rows, labels, selection)
    result.pop('leakage_report',None)
    result.update(labels=labels, excluded_asset_ids=excluded + result['excluded_asset_ids'])
    return result

async def _build_package(project, rows, excluded, format, selected, approved_only, splits, policy=None, selection_result=None):
    if not rows:
        raise HTTPException(400, '没有符合审核范围的已标注图片；请先保存并审核')
    labels = category_labels(project, rows, selected)
    ids = {str(a.id) for a,_ in rows}
    if splits is not None and (set(splits) != ids or any(v not in ('train','val','test') for v in splits.values())):
        raise HTTPException(400, '划分必须包含全部导出图片，且只能使用train/val/test')
    policy = policy or SplitPolicy(training_budget_per_class=None, require_independent=False)
    if selection_result and selection_result['check_duplicates']:
        policy = SplitPolicy(phash_distance=selection_result['phash_distance'], color_distance=selection_result['color_distance'],
            training_budget_per_class=None, require_source=selection_result['require_source'], require_independent=False)
    groups = {}
    for asset,_ in rows:
        split = splits[str(asset.id)] if splits is not None else 'all'
        old = groups.setdefault(asset.content_hash, split)
        if old != split:
            raise HTTPException(400, '同内容图片不能跨训练/验证/测试划分')
    if splits is not None and (selection_result is None or selection_result.get('check_duplicates')):
        evidence = split_report(rows, labels, policy, splits)
        if evidence['errors']:
            raise HTTPException(409, split_failure_reason(evidence, rows))
    result = tempfile.SpooledTemporaryFile(max_size=8*1024*1024)
    paths = {}
    manifest = dict(project_id=str(project.id), format=format, labels=labels, approved_only=approved_only,
        selected_labels=selected or labels, excluded_asset_ids=excluded, assets=[], distribution={})
    if selection_result is not None:
        manifest['selection'] = dict(mode=selection_result['mode'], ratios=selection_result['ratios'], budgets=selection_result['budgets'],
            images_by_split=selection_result['images_by_split'], labels_by_split=selection_result['labels_by_split'],
            independent=False, near_duplicate_checked=selection_result.get('check_duplicates', False),
            leakage_policy={key: selection_result[key] for key in ('phash_distance','color_distance','require_source')} if selection_result.get('check_duplicates') else None)
        manifest['selection']['group_similar'] = selection_result.get('group_similar',False)
        if 'grouping' in selection_result:manifest['selection']['grouping'] = selection_result['grouping']
    counts = Counter()
    fingerprints = {}
    try:
        with zipfile.ZipFile(result, 'w', zipfile.ZIP_DEFLATED) as archive:
            for asset,ann in rows:
                split = splits[str(asset.id)] if splits is not None else 'all'
                suffix = {'JPEG':'.jpg','PNG':'.png','WEBP':'.webp','BMP':'.bmp'}.get(asset.format)
                if not suffix: raise HTTPException(400, '图片格式无效')
                image_path = f'images/{split}/{asset.id}{suffix}'
                paths[str(asset.id)] = image_path
                try:
                    source = await get_storage().get_file(asset.blob_path)
                except (FileNotFoundError, ValueError):
                    raise HTTPException(400, f'图片文件不可读：{asset.id}')
                try:
                    digest = hashlib.sha256()
                    with archive.open(image_path, 'w') as output:
                        for chunk in iter(lambda: source.read(65536), b''):
                            output.write(chunk); digest.update(chunk)
                    source.seek(0)
                    with Image.open(source) as image:
                        if image.size != (asset.width, asset.height):
                            raise HTTPException(400, '图片尺寸与标注不一致')
                        image.verify()
                    if splits is not None and (selection_result is None or selection_result.get('check_duplicates')):
                        source.seek(0)
                        with Image.open(source) as image:
                            fingerprint = imagehash.phash(image)
                            color = image.convert('RGB').resize((1, 1)).getpixel((0, 0))
                        fingerprints[str(asset.id)] = (fingerprint, color)
                    if digest.hexdigest() != asset.content_hash:
                        raise HTTPException(400, '图片哈希与资产不一致')
                finally:
                    source.close()
                boxes = [b for b in ann.annotation_data['boxes'] if b['label'] in labels]
                for box in boxes: counts[box['label']] += 1
                manifest['assets'].append(dict(asset_id=str(asset.id), original_name=asset.original_name,
                    content_hash=asset.content_hash, image_path=image_path, annotation_id=str(ann.id),
                    annotation_version=ann.version, annotation_data={'boxes':boxes}, review_status=ann.review_status,
                    source_type=asset.source_type, width=asset.width, height=asset.height, split=split,
                    perceptual_hash=str(fingerprint) if splits is not None and (selection_result is None or selection_result.get('check_duplicates')) else None))
                if format == 'yolo':
                    lines = [f"{labels.index(b['label'])} {(b['x']+b['width']/2)/asset.width:.10f} "
                        f"{(b['y']+b['height']/2)/asset.height:.10f} {b['width']/asset.width:.10f} {b['height']/asset.height:.10f}"
                        for b in boxes]
                    archive.writestr(f'labels/{split}/{asset.id}.txt', '\n'.join(lines))
            if splits is not None and (selection_result is None or selection_result.get('check_duplicates')):
                evidence = split_report(rows, labels, policy, splits, fingerprints=fingerprints)
                if evidence['errors']:
                    raise HTTPException(409, split_failure_reason(evidence, rows))
                manifest['split_evidence'] = evidence
            manifest['distribution'] = dict(boxes_by_label=dict(counts), images_by_split=dict(Counter(a['split'] for a in manifest['assets'])),
                images_by_source=dict(Counter(a['source_type'] for a in manifest['assets'])), negative_images=sum(not a['annotation_data']['boxes'] for a in manifest['assets']))
            archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2))
            if format == 'yolo':
                split_names = sorted(set(a['split'] for a in manifest['assets']))
                # Omit path so loaders resolve images relative to this YAML, not their working directory.
                config = ''.join(f'{s}: images/{s}\n' if s in split_names else f'{s}: []\n' for s in ('train','val','test'))
                if split_names == ['all']:
                    config = 'train: images/all\nval: []\ntest: []\n# Unsplit package; freeze explicit train/val/test splits before training.\n'
                config += 'names:\n' + ''.join(f'  {i}: {json.dumps(label,ensure_ascii=False)}\n' for i,label in enumerate(labels))
                archive.writestr('data.yaml', config)
            else:
                for split in sorted(set(a['split'] for a in manifest['assets'])):
                    subset = [(a,n) for a,n in rows if (splits[str(a.id)] if splits else 'all') == split]
                    archive.writestr(f'annotations/instances_{split}.json', json.dumps(coco_document(subset, labels, paths),ensure_ascii=False))
        result.seek(0)
        return result, manifest
    except Exception:
        result.close()
        raise
