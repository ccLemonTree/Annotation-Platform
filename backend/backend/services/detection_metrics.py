"""Dependency-free bbox metrics, with COCO-style AP but no area/crowd rules."""
from bisect import bisect_left
from math import fsum, isfinite
from sys import float_info


# Use the same inclusive linspace grid as COCOeval.
_AP_IOUS = [.5 + i * ((.95 - .5) / 9) for i in range(10)]
_MAX_DETECTIONS = 100


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return isfinite(value)
    except OverflowError:
        return False


def _validate_options(labels, confidence, match_iou):
    if not isinstance(labels, list) or any(not isinstance(label, str) or not label.strip() or label == '背景' for label in labels):
        raise ValueError('labels must be a list of nonempty strings, excluding 背景')
    if len(labels) != len(set(labels)):
        raise ValueError('labels must be unique')
    for name, value in [('confidence', confidence), ('match_iou', match_iou)]:
        if not _finite(value) or not 0 <= value <= 1:
            raise ValueError(f'{name} must be finite and in [0, 1]')


def _validate_boxes(boxes, labels):
    if not isinstance(boxes, list):
        raise ValueError('boxes must be a list')
    for box in boxes:
        if not isinstance(box, dict) or not isinstance(box.get('label'), str) or box['label'] not in labels:
            raise ValueError('every box must have a known label')
        if any(not _finite(box.get(key)) for key in ('x', 'y', 'width', 'height')):
            raise ValueError('box coordinates must be finite numbers')
        if box['x'] < 0 or box['y'] < 0 or box['width'] <= 0 or box['height'] <= 0:
            raise ValueError('box origins must be nonnegative and sizes positive')
        if not _finite(box['x'] + box['width']) or not _finite(box['y'] + box['height']):
            raise ValueError('box endpoints must be finite')
        score = box.get('score', 1)
        if not _finite(score) or not 0 <= score <= 1:
            raise ValueError('box score must be finite and in [0, 1]')


def _overlap(a, b, minimum_area=False):
    # Relative origins retain small box sizes even at large finite coordinates.
    width = max(0, min(a['width'] - max(0, b['x'] - a['x']), b['width'] - max(0, a['x'] - b['x'])))
    height = max(0, min(a['height'] - max(0, b['y'] - a['y']), b['height'] - max(0, a['y'] - b['y'])))
    if minimum_area:
        # Normalize before multiplication, including when either box contains the other.
        return min(1, max((width / a['width']) * (height / a['height']),
                          (width / b['width']) * (height / b['height'])))
    intersection = width * height
    area_a, area_b = a['width'] * a['height'], b['width'] * b['height']
    union = area_a + area_b - intersection
    if _finite(union) and min(area_a, area_b) >= float_info.min:
        return min(1, intersection / union)
    # Scaling each axis avoids area overflow/underflow for finite coordinates.
    sx, sy = max(a['width'], b['width']), max(a['height'], b['height'])
    intersection = (width / sx) * (height / sy)
    union = (a['width'] / sx) * (a['height'] / sy) + (b['width'] / sx) * (b['height'] / sy) - intersection
    return min(1, intersection / union) if union else 0.0


def _candidates(truth, predictions, minimum_area=False):
    counts, indices = {}, []
    for index in sorted(range(len(predictions)), key=lambda i: -predictions[i].get('score', 1)):
        label = predictions[index]['label']
        counts[label] = counts.get(label, 0) + 1
        if counts[label] <= _MAX_DETECTIONS:
            indices.append(index)
    return [(pi, [(ti, _overlap(t, predictions[pi], minimum_area)) for ti, t in enumerate(truth)
                  if t['label'] == predictions[pi]['label']]) for pi in indices]


def _match(candidates, threshold, field='iou'):
    used, matches = set(), []
    for pi, overlaps in candidates:
        best, best_iou = None, threshold
        for ti, iou in overlaps:
            # COCOeval keeps the later GT when IoUs tie.
            if ti not in used and iou >= best_iou:
                best, best_iou = ti, iou
        if best is not None:
            used.add(best)
            matches.append(dict(truth_index=best, prediction_index=pi, **{field:best_iou}))
    return matches


def _rates(tp, fp, fn):
    return dict(tp=tp, fp=fp, fn=fn,
                precision=tp / (tp + fp) if tp + fp else None,
                recall=tp / (tp + fn) if tp + fn else None,
                f1=2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None)


def _evaluate(truth, predictions, candidates, confidence, match_iou, merge_duplicates=False):
    operating = [(pi, overlaps) for pi, overlaps in candidates if predictions[pi].get('score', 1) >= confidence]
    field = 'overlap' if merge_duplicates else 'iou'
    matches = _match(operating, match_iou, field)
    matched_truth = {m['truth_index'] for m in matches}
    matched_predictions = {m['prediction_index'] for m in matches}
    duplicates = {pi for pi, overlaps in operating if pi not in matched_predictions and
        any(ti in matched_truth and iou >= match_iou for ti, iou in overlaps)} if merge_duplicates else set()
    operating = [(pi, overlaps) for pi, overlaps in operating if pi not in duplicates]
    unmatched_truth = [ti for ti in range(len(truth)) if ti not in matched_truth]
    unmatched_predictions = sorted(pi for pi, _ in operating if pi not in matched_predictions)
    # Pair remaining spatial overlaps only for the off-diagonal confusion cells.
    confusions = _match([(pi, [(ti, _overlap(truth[ti], predictions[pi], merge_duplicates)) for ti in unmatched_truth])
                         for pi, _ in operating if pi not in matched_predictions], match_iou, field)
    tp, fp, fn = len(matches), len(unmatched_predictions), len(unmatched_truth)
    errors = []
    if fp:
        errors.append('false_positive')
    if fn:
        errors.append('false_negative')
    if confusions:
        errors.append('class_confusion')
    result = dict(**_rates(tp, fp, fn), false_positive_negative=not truth and bool(fp), errors=errors,
                  matches=matches, unmatched_truth=unmatched_truth, unmatched_predictions=unmatched_predictions)
    if merge_duplicates: result['ignored_duplicates'] = sorted(duplicates)
    return result, confusions


def evaluate_image(truth: list[dict], predictions: list[dict], labels: list[str],
                   confidence: float = .25, match_iou: float = .5, *, merge_duplicates: bool = False) -> dict:
    """Match score-ranked detections to same-class GT; indices refer to input lists.

    Missing scores mean 1.0. MaxDet is 100 per image per class. Null rates have
    zero denominators. Wrong-class overlaps remain FP + FN, with an error flag.
    Target detection (merge_duplicates) uses intersection / smaller box area.
    """
    _validate_options(labels, confidence, match_iou)
    _validate_boxes(truth, labels)
    _validate_boxes(predictions, labels)
    result, _ = _evaluate(truth, predictions, _candidates(truth, predictions, merge_duplicates), confidence, match_iou, merge_duplicates)
    return result


def _ap101(ranked, truth_count):
    tp, recalls, precisions = 0, [], []
    for index, (_, matched) in enumerate(sorted(ranked, key=lambda row: -row[0]), 1):
        tp += matched
        recalls.append(tp / truth_count)
        precisions.append(tp / index)
    for index in range(len(precisions) - 2, -1, -1):
        precisions[index] = max(precisions[index], precisions[index + 1])
    values = []
    for step in range(101):
        index = bisect_left(recalls, step * .01)
        values.append(precisions[index] if index < len(precisions) else 0)
    return fsum(values) / 101


def _class_ap(prepared, label, truth_count):
    if not truth_count:
        return None, None
    ranked = [[] for _ in _AP_IOUS]
    for predictions, candidates in prepared:
        selected = [(pi, overlaps) for pi, overlaps in candidates if predictions[pi]['label'] == label]
        for index, threshold in enumerate(_AP_IOUS):
            matched = {m['prediction_index'] for m in _match(selected, threshold)}
            ranked[index].extend((predictions[pi].get('score', 1), pi in matched) for pi, _ in selected)
    values = [_ap101(rows, truth_count) for rows in ranked]
    return values[0], fsum(values) / len(values)


def aggregate_metrics(samples: list[dict], labels: list[str], confidence: float = .25,
                      match_iou: float = .5, *, merge_duplicates: bool = False) -> dict:
    """Aggregate image operating counts and AP from all supplied inference scores.

    Samples must already represent successful inference; no failures are silently
    dropped. AP ignores the operating confidence and excludes absent-GT classes.
    Confusion matrix rows are truth, columns prediction, with background last.
    """
    _validate_options(labels, confidence, match_iou)
    if not isinstance(samples, list):
        raise ValueError('samples must be a list')
    counts = {label: dict(truth_count=0, prediction_count=0, tp=0) for label in labels}
    indices = {label: index for index, label in enumerate(labels)}
    background = len(labels)
    matrix = [[0] * (background + 1) for _ in range(background + 1)]
    prepared, negative_images, false_positive_negative_images, merged_predictions = [], 0, 0, 0
    for sample in samples:
        if not isinstance(sample, dict) or 'truth' not in sample or 'predictions' not in sample:
            raise ValueError('each sample must contain truth and predictions')
        truth, predictions = sample['truth'], sample['predictions']
        _validate_boxes(truth, labels)
        _validate_boxes(predictions, labels)
        candidates = _candidates(truth, predictions, merge_duplicates)
        prepared.append((predictions, candidates))
        result, confusions = _evaluate(truth, predictions, candidates, confidence, match_iou, merge_duplicates)
        ignored = set(result.get('ignored_duplicates', []))
        merged_predictions += len(ignored)
        negative_images += not truth
        false_positive_negative_images += result['false_positive_negative']
        for t in truth:
            counts[t['label']]['truth_count'] += 1
        for pi, _ in candidates:
            if predictions[pi].get('score', 1) >= confidence and pi not in ignored:
                counts[predictions[pi]['label']]['prediction_count'] += 1
        for match in result['matches']:
            label = truth[match['truth_index']]['label']
            counts[label]['tp'] += 1
            matrix[indices[label]][indices[label]] += 1
        confused_truth = {m['truth_index'] for m in confusions}
        confused_predictions = {m['prediction_index'] for m in confusions}
        for match in confusions:
            matrix[indices[truth[match['truth_index']]['label']]][indices[predictions[match['prediction_index']]['label']]] += 1
        for ti in result['unmatched_truth']:
            if ti not in confused_truth:
                matrix[indices[truth[ti]['label']]][background] += 1
        for pi in result['unmatched_predictions']:
            if pi not in confused_predictions:
                matrix[background][indices[predictions[pi]['label']]] += 1
    per_class = []
    for label, row in counts.items():
        tp = row['tp']
        ap50, ap50_95 = (None, None) if merge_duplicates else _class_ap(prepared, label, row['truth_count'])
        per_class.append(dict(label=label, truth_count=row['truth_count'], prediction_count=row['prediction_count'],
                              **_rates(tp, row['prediction_count'] - tp, row['truth_count'] - tp),
                              ap50=ap50, ap50_95=ap50_95))
    ap50s = [row['ap50'] for row in per_class if row['ap50'] is not None]
    ap50_95s = [row['ap50_95'] for row in per_class if row['ap50_95'] is not None]
    return dict(**_rates(*(sum(row[key] for row in per_class) for key in ('tp', 'fp', 'fn'))),
                **(dict(merged_predictions=merged_predictions) if merge_duplicates else {}),
                map50=fsum(ap50s) / len(ap50s) if ap50s else None,
                map50_95=fsum(ap50_95s) / len(ap50_95s) if ap50_95s else None,
                per_class=per_class, images=len(samples), negative_images=negative_images,
                false_positive_negative_images=false_positive_negative_images,
                confusion_matrix=dict(labels=labels + ['背景'], matrix=matrix),
                method=dict(ap=None if merge_duplicates else '101-point interpolated precision', iou_thresholds=[] if merge_duplicates else list(_AP_IOUS),
                            max_detections=_MAX_DETECTIONS, max_detections_scope='per_image_per_class',
                            confidence=confidence, **(dict(overlap_metric='intersection_over_min_area', match_overlap=match_iou)
                                if merge_duplicates else dict(match_iou=match_iou)), ap_confidence_filter=None,
                            missing_score=1.0, prediction_ties='input_image_then_prediction_order',
                            truth_iou_ties='last_unmatched_truth', absent_truth_ap=None,
                            confusion_matrix_axes=dict(rows='truth', columns='prediction'),
                            area_ranges=False, crowd=False))
