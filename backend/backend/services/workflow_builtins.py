"""Deterministic built-in workflow node executors (zero external model calls)."""
import json
import math
from io import BytesIO
import imagehash
from PIL import Image, ImageFilter, ImageStat, PngImagePlugin

LAPLACIAN_KERNEL = ImageFilter.Kernel((3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128)


def _norm_label(value: str | None) -> str:
    return (value or '').strip().casefold().removeprefix('sam3_')


def compute_image_metrics(image: Image.Image, need_sharpness: bool, need_brightness: bool, need_phash: bool) -> dict:
    width, height = image.size
    metrics: dict = {'width': width, 'height': height, 'aspect_ratio': round(width / max(1, height), 4)}
    if need_sharpness or need_brightness:
        gray = image.convert('L')
        if need_brightness:
            metrics['brightness'] = round(ImageStat.Stat(gray).mean[0], 2)
        if need_sharpness:
            sample = gray.copy()
            sample.thumbnail((512, 512))
            edges = sample.filter(LAPLACIAN_KERNEL)
            metrics['sharpness'] = round(ImageStat.Stat(edges).var[0], 2)
    if need_phash:
        metrics['phash'] = str(imagehash.phash(image))
    return metrics


def execute_img_filter(content: bytes, config: dict, existing_phashes: list[str] | None = None) -> dict:
    with Image.open(BytesIO(content)) as opened:
        image = opened.convert('RGB')
        width, height = image.size
        short_edge, long_edge = min(width, height), max(width, height)
        ratio = width / max(1, height)
        need_sharpness = config.get('min_sharpness') is not None
        need_brightness = config.get('min_brightness') is not None or config.get('max_brightness') is not None
        dedup_dist = config.get('dedup_phash_distance')
        metrics = compute_image_metrics(image, need_sharpness, need_brightness, dedup_dist is not None)

    reasons = []
    if config.get('min_width') and width < config['min_width']:
        reasons.append(f'宽度 {width}px < {config["min_width"]}px')
    if config.get('max_width') and width > config['max_width']:
        reasons.append(f'宽度 {width}px > {config["max_width"]}px')
    if config.get('min_height') and height < config['min_height']:
        reasons.append(f'高度 {height}px < {config["min_height"]}px')
    if config.get('max_height') and height > config['max_height']:
        reasons.append(f'高度 {height}px > {config["max_height"]}px')
    if config.get('min_short_edge') and short_edge < config['min_short_edge']:
        reasons.append(f'短边 {short_edge}px < {config["min_short_edge"]}px')
    if config.get('min_long_edge') and long_edge < config['min_long_edge']:
        reasons.append(f'长边 {long_edge}px < {config["min_long_edge"]}px')
    if config.get('min_aspect_ratio') and ratio < config['min_aspect_ratio']:
        reasons.append(f'宽高比 {ratio:.2f} < {config["min_aspect_ratio"]}')
    if config.get('max_aspect_ratio') and ratio > config['max_aspect_ratio']:
        reasons.append(f'宽高比 {ratio:.2f} > {config["max_aspect_ratio"]}')
    if need_sharpness and metrics['sharpness'] < config['min_sharpness']:
        reasons.append(f'清晰度 {metrics["sharpness"]} < {config["min_sharpness"]}')
    if config.get('min_brightness') is not None and metrics['brightness'] < config['min_brightness']:
        reasons.append(f'平均亮度 {metrics["brightness"]} < {config["min_brightness"]}')
    if config.get('max_brightness') is not None and metrics['brightness'] > config['max_brightness']:
        reasons.append(f'平均亮度 {metrics["brightness"]} > {config["max_brightness"]}')
    if not reasons and dedup_dist is not None and existing_phashes:
        current_hash = imagehash.hex_to_hash(metrics['phash'])
        for prev in existing_phashes:
            try:
                dist = current_hash - imagehash.hex_to_hash(prev)
            except Exception:
                continue
            if dist <= dedup_dist:
                reasons.append(f'pHash 近似重复（距离 {dist} <= {dedup_dist}）')
                break

    passed = not reasons
    summary_parts = [f'{width}×{height}']
    if 'sharpness' in metrics:
        summary_parts.append(f'清晰度 {metrics["sharpness"]}')
    if 'brightness' in metrics:
        summary_parts.append(f'亮度 {metrics["brightness"]}')
    reason = f'规则通过（{" · ".join(summary_parts)}）' if passed else '规则过滤：' + '；'.join(reasons)
    return {'passed': passed, 'metrics': metrics, 'phash': metrics.get('phash'), 'reason': reason}


def _box_iou(a: dict, b: dict) -> float:
    ax1, ay1, ax2, ay2 = a['x'], a['y'], a['x'] + a['width'], a['y'] + a['height']
    bx1, by1, bx2, by2 = b['x'], b['y'], b['x'] + b['width'], b['y'] + b['height']
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    union = a['width'] * a['height'] + b['width'] * b['height'] - inter
    return inter / union if union > 0 else 0.0


def execute_box_clean(boxes: list[dict], width: int, height: int, config: dict) -> dict:
    img_area = max(1.0, float(width * height))
    min_w = float(config.get('min_box_width') or 0)
    min_h = float(config.get('min_box_height') or 0)
    min_ratio = float(config.get('min_area_ratio') or 0)
    max_ratio = float(config.get('max_area_ratio') if config.get('max_area_ratio') is not None else 1.0)
    margin = float(config.get('edge_margin_px') or 0)
    keep_set = {_norm_label(x) for x in (config.get('keep_labels') or []) if x}
    drop_set = {_norm_label(x) for x in (config.get('drop_labels') or []) if x}
    rename_map = {str(k).strip(): str(v).strip() for k, v in (config.get('rename_map') or {}).items() if str(k).strip() and str(v).strip()}
    rename_fold = {k.casefold(): v for k, v in rename_map.items()}
    nms_iou = config.get('nms_iou_threshold')

    filtered = []
    for raw in boxes:
        label = str(raw.get('label') or '').strip()
        if not label:
            continue
        label = rename_map.get(label) or rename_fold.get(label.casefold()) or label
        norm = _norm_label(label)
        if keep_set and norm not in keep_set:
            continue
        if drop_set and norm in drop_set:
            continue
        x, y, w, h = float(raw['x']), float(raw['y']), float(raw['width']), float(raw['height'])
        if w < min_w or h < min_h:
            continue
        area_ratio = (w * h) / img_area
        if area_ratio < min_ratio or area_ratio > max_ratio:
            continue
        if margin > 0 and (x < margin or y < margin or (x + w) > (width - margin) or (y + h) > (height - margin)):
            continue
        entry = {**raw, 'label': label, 'x': x, 'y': y, 'width': w, 'height': h}
        filtered.append(entry)

    if nms_iou is not None and 0 < float(nms_iou) < 1 and len(filtered) > 1:
        ordered = sorted(filtered, key=lambda b: (float(b.get('score', 1.0)), b['width'] * b['height']), reverse=True)
        kept: list[dict] = []
        class_agnostic = bool(config.get('class_agnostic_nms', False))
        for candidate in ordered:
            if any(
                (class_agnostic or existing['label'] == candidate['label']) and _box_iou(existing, candidate) >= float(nms_iou)
                for existing in kept
            ):
                continue
            kept.append(candidate)
        filtered = kept

    pass_empty = bool(config.get('pass_when_empty', False))
    passed = bool(filtered) or pass_empty
    reason = f'框清洗：{len(boxes)} → {len(filtered)} 个目标框'
    if not filtered and not pass_empty:
        reason += '（无剩余有效框，不传递下游）'
    return {
        'passed': passed,
        'boxes': filtered,
        'boxes_before': len(boxes),
        'boxes_after': len(filtered),
        'detected_count': len(filtered),
        'reason': reason,
    }


def execute_branch(
    *,
    width: int,
    height: int,
    crop_label: str | None,
    boxes: list[dict],
    attributes: dict,
    config: dict,
) -> dict:
    cases = config.get('branches') or []
    first_match = config.get('first_match', True)
    matched_keys: list[str] = []
    matched_names: list[str] = []
    norm_crop = _norm_label(crop_label)
    box_norms = {_norm_label(b.get('label')) for b in boxes if b.get('label')}

    for case in cases:
        checks: list[bool] = []
        if case.get('crop_labels'):
            targets = {_norm_label(x) for x in case['crop_labels'] if x}
            checks.append(bool(norm_crop and norm_crop in targets))
        if case.get('box_labels'):
            targets = {_norm_label(x) for x in case['box_labels'] if x}
            checks.append(bool(box_norms & targets))
        if case.get('min_boxes') is not None:
            checks.append(len(boxes) >= int(case['min_boxes']))
        if case.get('max_boxes') is not None:
            checks.append(len(boxes) <= int(case['max_boxes']))
        if case.get('attribute_key'):
            attr_entry = attributes.get(case['attribute_key'])
            actual = attr_entry.get('value') if isinstance(attr_entry, dict) else attr_entry
            expected = {str(v).strip().casefold() for v in (case.get('attribute_values') or []) if str(v).strip()}
            checks.append(actual is not None and (not expected or str(actual).strip().casefold() in expected))
        if case.get('min_width') is not None:
            checks.append(width >= int(case['min_width']))
        if case.get('min_height') is not None:
            checks.append(height >= int(case['min_height']))

        if not checks:
            continue
        hit = all(checks) if case.get('match_mode', 'all') == 'all' else any(checks)
        if hit:
            matched_keys.append(case['key'])
            matched_names.append(case.get('name') or case['key'])
            if first_match:
                break

    if not matched_keys:
        matched_keys = ['else']
        matched_names = ['默认 / 其他分支']

    return {
        'passed': True,
        'matched_branches': matched_keys,
        'reason': '分流命中：' + '、'.join(matched_names),
    }


def execute_img_transform(
    content: bytes,
    *,
    project_id: str,
    asset_id: str,
    boxes: list[dict],
    crop_label: str | None,
    config: dict,
) -> dict:
    with Image.open(BytesIO(content)) as opened:
        image = opened.convert('RGB')
        width, height = image.size

    blur_labels = {_norm_label(x) for x in (config.get('blur_labels') or []) if x}
    blur_all = bool(config.get('blur_all_boxes', False))
    blur_radius = max(2, min(60, int(config.get('blur_radius') or 18)))
    blurred_count = 0

    if blur_all or blur_labels:
        for box in boxes:
            if not blur_all and _norm_label(box.get('label')) not in blur_labels:
                continue
            bx1 = max(0, int(math.floor(float(box['x']))))
            by1 = max(0, int(math.floor(float(box['y']))))
            bx2 = min(width, int(math.ceil(float(box['x']) + float(box['width']))))
            by2 = min(height, int(math.ceil(float(box['y']) + float(box['height']))))
            if bx2 > bx1 and by2 > by1:
                patch = image.crop((bx1, by1, bx2, by2)).filter(ImageFilter.GaussianBlur(radius=blur_radius))
                image.paste(patch, (bx1, by1))
                blurred_count += 1

    roi_mode = config.get('roi_mode') or 'none'
    rx1, ry1, rx2, ry2 = 0, 0, width, height
    if roi_mode == 'ratio':
        r = config.get('roi_ratio') or [0.0, 0.0, 1.0, 1.0]
        rx1 = max(0, min(width - 1, int(round(width * float(r[0])))))
        ry1 = max(0, min(height - 1, int(round(height * float(r[1])))))
        rx2 = max(rx1 + 1, min(width, int(round(width * float(r[2])))))
        ry2 = max(ry1 + 1, min(height, int(round(height * float(r[3])))))
    elif roi_mode == 'pixels':
        p = config.get('roi_margins_px') or [0, 0, 0, 0]
        rx1 = max(0, int(p[0]))
        ry1 = max(0, int(p[1]))
        rx2 = width - max(0, int(p[2]))
        ry2 = height - max(0, int(p[3]))
        if rx2 <= rx1 or ry2 <= ry1:
            raise ValueError('ROI 像素边距过大，裁剪后区域为空')

    if (rx1, ry1, rx2, ry2) != (0, 0, width, height):
        image = image.crop((rx1, ry1, rx2, ry2))

    out_w, out_h = image.size
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('project_crop', json.dumps([project_id, asset_id, [rx1, ry1, rx2, ry2]]))
    out = BytesIO()
    image.save(out, 'PNG', pnginfo=metadata)
    label = crop_label or 'transformed'
    actions = []
    if blurred_count:
        actions.append(f'脱敏打码 {blurred_count} 处')
    if (rx1, ry1, rx2, ry2) != (0, 0, width, height):
        actions.append(f'ROI 裁剪至 {out_w}×{out_h}')
    if not actions:
        actions.append(f'输出衍生图 {out_w}×{out_h}')

    return {
        'crops': [{
            'content': out.getvalue(),
            'width': out_w,
            'height': out_h,
            'region': [rx1, ry1, rx2, ry2],
            'box': {'label': label, 'x': rx1, 'y': ry1, 'width': out_w, 'height': out_h},
        }],
        'filtered_count': 0,
        'source_boxes': [],
        'reason': ' · '.join(actions),
    }
