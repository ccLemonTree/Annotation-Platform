"""Pure detection metrics; reference SDK runs only in an isolated interpreter."""
import copy
import importlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import subprocess

import pytest


LABELS = ['person', 'cat']


def box(label='person', x=0, y=0, width=10, height=10, score=1):
    return dict(x=x, y=y, width=width, height=height, label=label, score=score)


@pytest.fixture
def metrics():
    return importlib.import_module('services.detection_metrics')


def test_metrics_module_exists():
    assert importlib.util.find_spec('services.detection_metrics') is not None, 'detection metrics module is missing'


def test_target_detection_tolerates_position_and_merges_duplicate_boxes(metrics):
    truth=[box(width=100,height=100)]
    predictions=[box(width=60,height=60,score=.9),box(width=55,height=55,score=.8)]
    standard=metrics.evaluate_image(truth,predictions,LABELS)
    target=metrics.evaluate_image(truth,predictions,LABELS,match_iou=.3,merge_duplicates=True)
    assert (standard['tp'],standard['fp'],standard['fn'])==(0,2,1)
    assert (target['tp'],target['fp'],target['fn'])==(1,0,0)
    assert target['ignored_duplicates']==[1]
    summary=metrics.aggregate_metrics([dict(truth=truth,predictions=predictions)],LABELS,match_iou=.3,merge_duplicates=True)
    assert summary['merged_predictions']==1 and summary['precision']==summary['recall']==1
    assert summary['per_class'][0]['prediction_count']==1 and summary['map50'] is None


def test_target_detection_still_rejects_wrong_position_class_and_missing_targets(metrics):
    result=metrics.evaluate_image([box(),box(x=30)],[box(),box(),box(x=100),box('cat')],LABELS,match_iou=.3,merge_duplicates=True)
    assert (result['tp'],result['fp'],result['fn'])==(1,2,1)
    assert result['ignored_duplicates']==[1]
    assert 'class_confusion' not in result['errors']


@pytest.mark.parametrize('truth,prediction,threshold,tp,overlap', [
    (box(),box(x=8),.2,1,.2),
    (box(),box(x=8.01),.2,0,None),
    (box(),box(x=10),.2,0,None),
    (box(),box(x=8),.21,0,None),
    (box(width=100,height=100),box(x=20,y=20),1,1,1),
    (box(x=20,y=20),box(width=100,height=100),1,1,1),
    (box(),box('cat'),.2,0,None),
    (box(width=1e200,height=1e200),box(width=1e-200,height=1e-200),1,1,1),
    (box(width=1e-200,height=1e-200),box(width=1e-200,height=1e-200),1,1,1),
])
def test_target_intersection_over_smaller_area(metrics,truth,prediction,threshold,tp,overlap):
    result=metrics.evaluate_image([truth],[prediction],LABELS,match_iou=threshold,merge_duplicates=True)
    assert (result['tp'],result['fp'],result['fn'])==(tp,1-tp,1-tp)
    if tp:
        assert result['matches'][0]['overlap']==pytest.approx(overlap)
        assert 'iou' not in result['matches'][0]
    summary=metrics.aggregate_metrics([dict(truth=[truth],predictions=[prediction])],LABELS,
        match_iou=threshold,merge_duplicates=True)
    assert summary['tp']==tp
    assert summary['method']['overlap_metric']=='intersection_over_min_area'
    assert summary['method']['match_overlap']==threshold and 'match_iou' not in summary['method']


def test_target_intersection_does_not_match_one_prediction_to_two_truths(metrics):
    result=metrics.evaluate_image([box(),box(x=20)],[box(width=30)],LABELS,match_iou=.2,merge_duplicates=True)
    assert (result['tp'],result['fp'],result['fn'])==(1,0,1)


def test_perfect_image_contract(metrics):
    result = metrics.evaluate_image([box()], [box()], LABELS)
    assert (result['tp'], result['fp'], result['fn']) == (1, 0, 0)
    assert (result['precision'], result['recall'], result['f1']) == (1, 1, 1)
    assert result['matches'] == [dict(truth_index=0, prediction_index=0, iou=1)]
    assert result['unmatched_truth'] == result['unmatched_predictions'] == result['errors'] == []
    assert result['false_positive_negative'] is False


@pytest.mark.parametrize('truth,predictions,counts,rates,errors,negative_fp', [
    ([], [], (0, 0, 0), (None, None, None), [], False),
    ([box()], [], (0, 0, 1), (None, 0, 0), ['false_negative'], False),
    ([], [box()], (0, 1, 0), (0, None, 0), ['false_positive'], True),
    ([box()], [box('cat')], (0, 1, 1), (0, 0, 0),
     ['false_positive', 'false_negative', 'class_confusion'], False),
    ([box()], [box(x=20)], (0, 1, 1), (0, 0, 0), ['false_positive', 'false_negative'], False),
])
def test_empty_negative_and_wrong_class(metrics, truth, predictions, counts, rates, errors, negative_fp):
    result = metrics.evaluate_image(truth, predictions, LABELS)
    assert tuple(result[k] for k in ('tp', 'fp', 'fn')) == counts
    assert tuple(result[k] for k in ('precision', 'recall', 'f1')) == rates
    assert result['errors'] == errors
    assert result['false_positive_negative'] is negative_fp


def test_confidence_sort_preserves_original_indices_and_input(metrics):
    truth, predictions = [box()], [box(score=.4), box(score=.9), box(x=30, score=.1)]
    original = copy.deepcopy((truth, predictions))
    result = metrics.evaluate_image(truth, predictions, LABELS)
    assert result['matches'] == [dict(truth_index=0, prediction_index=1, iou=1)]
    assert result['unmatched_predictions'] == [0]
    assert (result['tp'], result['fp'], result['fn']) == (1, 1, 0)
    assert (truth, predictions) == original


def test_equal_scores_stable_and_equal_iou_uses_latest_truth_like_coco(metrics):
    result = metrics.evaluate_image([box(), box(x=2)], [box(x=1), box()], LABELS, match_iou=.8)
    assert [(m['truth_index'], m['prediction_index']) for m in result['matches']] == [(1, 0), (0, 1)]
    duplicate = metrics.evaluate_image([box()], [box(), box()], LABELS)
    assert duplicate['matches'][0]['prediction_index'] == 0
    assert duplicate['unmatched_predictions'] == [1]


def test_best_iou_and_class_match_take_priority(metrics):
    result = metrics.evaluate_image([box(), box(x=2)], [box(x=2, score=.8)], LABELS)
    assert result['matches'][0]['truth_index'] == 1
    result = metrics.evaluate_image([box()], [box('cat', score=.9), box(score=.8)], LABELS)
    assert (result['tp'], result['fp'], result['fn']) == (1, 1, 0)
    assert result['errors'] == ['false_positive']


def test_inclusive_thresholds_and_missing_score(metrics):
    prediction = box(width=5, score=.25)
    assert metrics.evaluate_image([box()], [prediction], LABELS)['tp'] == 1
    assert metrics.evaluate_image([box()], [prediction], LABELS, match_iou=.500001)['tp'] == 0
    assert metrics.evaluate_image([], [prediction], LABELS, confidence=.250001)['fp'] == 0
    del prediction['score']
    assert metrics.evaluate_image([box()], [prediction], LABELS, confidence=1)['tp'] == 1
    # Pixel coordinates have no artificial [0, 1] upper bound.
    assert metrics.evaluate_image([box(x=500)], [box(x=500)], LABELS)['tp'] == 1


def test_zero_iou_threshold_is_inclusive(metrics):
    assert metrics.evaluate_image([box()], [box(x=100)], LABELS, match_iou=0)['tp'] == 1


def test_aggregate_shape_and_confusion_matrix(metrics):
    samples = [dict(truth=[box()], predictions=[box()]),
               dict(truth=[box()], predictions=[box('cat')]),
               dict(truth=[], predictions=[box('cat')]),
               dict(truth=[], predictions=[]), dict(truth=[box('cat')], predictions=[])]
    result = metrics.aggregate_metrics(samples, LABELS)
    assert (result['tp'], result['fp'], result['fn']) == (1, 2, 2)
    assert (result['precision'], result['recall'], result['f1']) == pytest.approx((1/3, 1/3, 1/3))
    assert (result['images'], result['negative_images'], result['false_positive_negative_images']) == (5, 2, 1)
    assert result['confusion_matrix'] == dict(labels=LABELS + ['背景'], matrix=[[1, 1, 0], [0, 0, 1], [0, 1, 0]])
    person, cat = result['per_class']
    assert {k: person[k] for k in ('label', 'truth_count', 'prediction_count', 'tp', 'fp', 'fn')} == dict(
        label='person', truth_count=2, prediction_count=1, tp=1, fp=0, fn=1)
    assert (cat['truth_count'], cat['prediction_count'], cat['tp'], cat['fp'], cat['fn']) == (1, 2, 0, 2, 1)
    assert {'precision', 'recall', 'f1', 'ap50', 'ap50_95'} <= person.keys()
    method = result['method']
    assert method['ap'] == '101-point interpolated precision'
    assert method['iou_thresholds'] == pytest.approx([.5 + i*.05 for i in range(10)])
    assert method['max_detections'] == 100
    assert method['max_detections_scope'] == 'per_image_per_class'
    assert method['confidence'] == .25 and method['match_iou'] == .5
    assert method['confusion_matrix_axes'] == dict(rows='truth', columns='prediction')
    assert method['area_ranges'] is False and method['crowd'] is False
    json.dumps(result, allow_nan=False)


def test_confusion_matching_is_one_to_one(metrics):
    result = metrics.aggregate_metrics([dict(truth=[box()], predictions=[box('cat'), box('cat')])], LABELS)
    assert result['confusion_matrix']['matrix'] == [[0, 1, 0], [0, 0, 0], [0, 1, 0]]
    assert (result['tp'], result['fp'], result['fn']) == (0, 2, 1)


@pytest.mark.parametrize('samples', [[], [dict(truth=[], predictions=[])], [dict(truth=[], predictions=[box()])]])
def test_absent_truth_has_null_ap(metrics, samples):
    result = metrics.aggregate_metrics(samples, LABELS)
    assert result['map50'] is result['map50_95'] is None
    assert all(row['ap50'] is row['ap50_95'] is None for row in result['per_class'])
    assert result['recall'] is None
    if not samples or not samples[0]['predictions']:
        assert result['precision'] is result['f1'] is None


def test_truth_without_predictions_has_zero_ap_and_f1(metrics):
    result = metrics.aggregate_metrics([dict(truth=[box()], predictions=[])], LABELS)
    assert result['precision'] is None
    assert result['recall'] == result['f1'] == result['map50'] == result['map50_95'] == 0
    assert result['per_class'][1]['ap50'] is None


@pytest.mark.parametrize('predictions,expected', [
    ([box(score=.1)], 1),
    ([box(x=40, score=.9), box(score=.1)], .5),
    ([box(score=.9), box(x=40, score=.1)], 1),
    ([box(x=40), box()], .5),
    ([box(), box(x=40)], 1),
])
def test_ap_uses_all_scores_and_stable_ranking(metrics, predictions, expected):
    result = metrics.aggregate_metrics([dict(truth=[box()], predictions=predictions)], LABELS, confidence=.95)
    assert result['map50'] == pytest.approx(expected)
    assert result['map50_95'] == pytest.approx(expected)
    assert result['per_class'][0]['ap50'] == pytest.approx(expected)
    assert result['per_class'][1]['ap50'] is None


def test_ap101_recall_grid_and_iou_sweep(metrics):
    result = metrics.aggregate_metrics([dict(truth=[box(), box(x=30)], predictions=[box()])], LABELS)
    assert result['map50'] == pytest.approx(51/101)
    result = metrics.aggregate_metrics([dict(truth=[box()], predictions=[box(width=5)])], LABELS)
    assert result['map50'] == 1
    assert result['map50_95'] == pytest.approx(.1)


def test_global_class_ranking_spans_images_and_keeps_input_order_for_ties(metrics):
    samples = [dict(truth=[], predictions=[box(score=.8)]), dict(truth=[box()], predictions=[box(score=.8)])]
    assert metrics.aggregate_metrics(samples, LABELS)['map50'] == pytest.approx(.5)
    assert metrics.aggregate_metrics(list(reversed(samples)), LABELS)['map50'] == 1


def test_maxdet_is_100_per_image_per_class(metrics):
    truth = [box(x=20*i) for i in range(101)] + [box('cat')]
    predictions = [box(x=20*i, score=.9) for i in range(101)] + [box('cat', score=.1)]
    result = metrics.aggregate_metrics([dict(truth=truth, predictions=predictions)], LABELS, confidence=0)
    assert result['tp'] == 101 and result['fn'] == 1
    assert [r['prediction_count'] for r in result['per_class']] == [100, 1]
    assert result['per_class'][0]['ap50'] == pytest.approx(100/101)
    assert result['per_class'][1]['ap50'] == 1
    assert result['map50'] == pytest.approx((100/101 + 1)/2)


@pytest.mark.parametrize('field,value', [
    ('x', -1), ('y', -1), ('width', 0), ('height', -1), ('x', math.nan),
    ('y', math.inf), ('width', -math.inf), ('height', '10'), ('x', True),
    ('score', -.1), ('score', 1.1), ('score', math.nan), ('score', math.inf),
    ('score', True), ('label', 'unknown'), ('label', ''), ('label', None),
])
def test_rejects_invalid_boxes_even_below_confidence(metrics, field, value):
    invalid = {**box(score=.001), field: value}
    for truth, predictions in [([invalid], []), ([], [invalid])]:
        with pytest.raises(ValueError):
            metrics.evaluate_image(truth, predictions, LABELS)
        with pytest.raises(ValueError):
            metrics.aggregate_metrics([dict(truth=truth, predictions=predictions)], LABELS)


@pytest.mark.parametrize('labels', [['person', 'person'], [''], [' '], ['背景'], [1], 'person', None])
def test_rejects_invalid_labels(metrics, labels):
    with pytest.raises(ValueError):
        metrics.evaluate_image([], [], labels)
    with pytest.raises(ValueError):
        metrics.aggregate_metrics([], labels)


@pytest.mark.parametrize('name,value', [('confidence', -.01), ('confidence', 1.01), ('confidence', math.nan),
                                       ('confidence', True), ('match_iou', -.1), ('match_iou', 1.1),
                                       ('match_iou', math.inf), ('match_iou', None)])
def test_rejects_invalid_thresholds_even_for_empty_aggregate(metrics, name, value):
    with pytest.raises(ValueError):
        metrics.evaluate_image([], [], LABELS, **{name: value})
    with pytest.raises(ValueError):
        metrics.aggregate_metrics([], LABELS, **{name: value})


@pytest.mark.parametrize('sample', [{}, {'truth': []}, {'truth': [], 'predictions': None},
                                  {'truth': [None], 'predictions': []},
                                  {'truth': [{k: v for k, v in box().items() if k != 'x'}], 'predictions': []}])
def test_rejects_malformed_samples(metrics, sample):
    with pytest.raises(ValueError):
        metrics.aggregate_metrics([sample], LABELS)


def test_empty_label_set_and_finite_large_geometry(metrics):
    assert metrics.aggregate_metrics([], [])['confusion_matrix'] == dict(labels=['背景'], matrix=[[0]])
    for size in (1e-160, 1e160, 10**200):
        result = metrics.evaluate_image([box(width=size, height=size)], [box(width=size, height=size)], LABELS)
        assert result['tp'] == 1 and result['matches'][0]['iou'] == 1


def test_small_boxes_at_large_finite_origins_preserve_iou(metrics):
    truth = [box(x=1e100, y=1e100)]
    predictions = [box(x=1e100, y=1e100, width=5)]
    result = metrics.evaluate_image(truth, predictions, LABELS)
    assert result['tp'] == 1
    assert result['matches'][0]['iou'] == .5


def test_partial_overlap_retains_exact_half_iou(metrics):
    result = metrics.evaluate_image([box(x=5, width=15)], [box(width=15)], LABELS)
    assert result['tp'] == 1
    assert result['matches'][0]['iou'] == .5


def test_aggregate_does_not_mutate_samples_or_labels(metrics):
    samples, labels = [dict(truth=[box()], predictions=[box(score=.1), box('cat')])], list(LABELS)
    original = copy.deepcopy((samples, labels))
    low = metrics.aggregate_metrics(samples, labels, confidence=0)
    high = metrics.aggregate_metrics(samples, labels, confidence=.25)
    assert (low['tp'], high['tp']) == (1, 0)
    assert low['map50'] == high['map50'] == 1
    assert (samples, labels) == original


COCO_REFERENCE = r'''
import contextlib, io, json, sys
import numpy as np
from pycocotools.coco import COCO
from pycocotools.cocoeval import COCOeval
payload = json.load(sys.stdin)
results = []
with contextlib.redirect_stdout(io.StringIO()):
    for samples in payload['cases']:
        gt = COCO()
        annotations, detections = [], []
        for image_id, sample in enumerate(samples, 1):
            for b in sample['truth']:
                annotations.append(dict(id=len(annotations)+1, image_id=image_id,
                    category_id=payload['labels'].index(b['label'])+1,
                    bbox=[b[k] for k in ('x','y','width','height')], area=b['width']*b['height'], iscrowd=0))
            for b in sample['predictions']:
                detections.append(dict(image_id=image_id, category_id=payload['labels'].index(b['label'])+1,
                    bbox=[b[k] for k in ('x','y','width','height')], score=b.get('score', 1)))
        gt.dataset = dict(info={}, images=[dict(id=i) for i in range(1, len(samples)+1)],
            categories=[dict(id=i, name=n) for i,n in enumerate(payload['labels'], 1)], annotations=annotations)
        gt.createIndex()
        if detections:
            dt = gt.loadRes(detections)
        else:
            dt = COCO()
            dt.dataset = {**gt.dataset, 'annotations': []}
            dt.createIndex()
        ev = COCOeval(gt, dt, 'bbox')
        ev.params.maxDets = [100]
        ev.params.areaRng = [[0, float('inf')]]
        ev.params.areaRngLbl = ['all']
        ev.evaluate()
        ev.accumulate()
        precision = ev.eval['precision'][:, :, :, 0, 0]
        per_class = []
        for k in range(len(payload['labels'])):
            p = precision[:, :, k]
            per_class.append(dict(ap50=float(np.mean(p[0][p[0] >= 0])) if np.any(p[0] >= 0) else None,
                ap50_95=float(np.mean(p[p >= 0])) if np.any(p >= 0) else None))
        results.append(dict(per_class=per_class,
            matches=[dict(truth_index=int(g)-1, prediction_index=int(d)-1)
                for row in ev.evalImgs if row is not None
                for d,g in zip(row['dtIds'], row['dtMatches'][0]) if g > 0]))
print(json.dumps(results))
'''


def test_ap_matches_independent_pycocotools_subprocess(metrics):
    loader = os.environ.get('P2_LOADER_PYTHON') or str(Path(__file__).resolve().parents[1] / '.backups/loader-env/Scripts/python.exe')
    if not Path(loader).is_file():
        pytest.skip('isolated pycocotools interpreter unavailable; set P2_LOADER_PYTHON')
    cases = [
        [dict(truth=[box(), box(x=2)], predictions=[box(x=1, score=.8), box(score=.7)])],
        [dict(truth=[box(), box(x=2)], predictions=[box(x=1, score=.8), box(score=.8)])],
        [dict(truth=[box(), box(x=30)], predictions=[box(width=5, score=.1)])],
        [dict(truth=[], predictions=[box()]), dict(truth=[box()], predictions=[box()])],
        [dict(truth=[box()], predictions=[box('cat')])],
        [dict(truth=[box()], predictions=[])],
        [dict(truth=[], predictions=[box()])],
        [dict(truth=[box(x=20*i) for i in range(101)] + [box('cat')],
              predictions=[box(x=20*i, score=.9) for i in range(101)] + [box('cat', score=.001)])],
    ]
    rng = random.Random(20261016)
    for _ in range(20):
        samples = []
        for _ in range(3):
            truth = [box(rng.choice(LABELS), x=rng.randrange(5)*5, width=rng.choice([5, 10, 15]))
                     for _ in range(rng.randrange(5))]
            predictions = [box(rng.choice(LABELS), x=rng.randrange(6)*5, width=rng.choice([5, 10, 15]),
                               score=rng.choice([0, .001, .25, .5, .9, 1])) for _ in range(rng.randrange(6))]
            samples.append(dict(truth=truth, predictions=predictions))
        cases.append(samples)
    # Include exact IoU sweep boundaries and a nontrivial precision envelope.
    cases.extend([dict(truth=[box()], predictions=[box(width=width)])] for width in (5, 5.5, 6, 6.5, 7, 7.5, 8, 8.5, 9, 9.5))
    cases.append([dict(truth=[box(x=i*20) for i in range(3)],
                       predictions=[box(x=x, score=1-i*.1) for i, x in enumerate([0, 100, 20, 40, 40])])])
    process = subprocess.run([loader, '-c', COCO_REFERENCE], input=json.dumps(dict(cases=cases, labels=LABELS)),
        capture_output=True, text=True, encoding='utf-8', timeout=60,
        env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
    assert process.returncode == 0, process.stdout + process.stderr
    reference = json.loads(process.stdout)
    for samples, expected in zip(cases, reference):
        result = metrics.aggregate_metrics(samples, LABELS)
        for actual_class, expected_class in zip(result['per_class'], expected['per_class']):
            for name in ('ap50', 'ap50_95'):
                if expected_class[name] is None:
                    assert actual_class[name] is None
                else:
                    assert actual_class[name] == pytest.approx(expected_class[name], abs=1e-12), json.dumps(
                        dict(samples=samples, label=actual_class['label'], metric=name))
        for name, per_name in [('map50', 'ap50'), ('map50_95', 'ap50_95')]:
            valid = [row[per_name] for row in expected['per_class'] if row[per_name] is not None]
            assert result[name] == (pytest.approx(sum(valid)/len(valid), abs=1e-12) if valid else None)
    tied = metrics.evaluate_image(cases[1][0]['truth'], cases[1][0]['predictions'], LABELS)
    assert [{k: m[k] for k in ('truth_index', 'prediction_index')} for m in tied['matches']] == reference[1]['matches']
