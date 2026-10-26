"""Read an extracted platform package with the real CPU dataset loaders."""
import hashlib
import json
import os
import sys
from pathlib import Path
import importlib.metadata

def verify(root):
    root = Path(root).resolve()
    manifest = json.loads((root / 'manifest.json').read_text('utf-8'))
    settings = root.parent / '.loader-settings'
    settings.mkdir(parents=True, exist_ok=True)
    os.environ['YOLO_CONFIG_DIR'] = str(settings)
    os.environ['YOLO_OFFLINE'] = 'true'
    assets = manifest['assets']
    for asset in assets:
        path = (root / asset['image_path']).resolve()
        assert path.is_relative_to(root) and path.is_file()
        assert hashlib.sha256(path.read_bytes()).hexdigest() == asset['content_hash']
    splits = sorted({a['split'] for a in assets})
    results = []
    if manifest['format'] == 'yolo':
        import numpy as np
        from ultralytics.data import utils
        from ultralytics.data.dataset import YOLODataset
        # Only disable optional rendering-font download; dataset parsing stays real.
        utils.check_font = lambda *args, **kwargs: None
        data = utils.check_det_dataset(root / 'data.yaml', autodownload=False)
        assert Path(data['path']).resolve() == root, 'dataset root resolved outside extracted package'
        assert data['names'] == dict(enumerate(manifest['labels']))
        for split in splits:
            expected = [a for a in assets if a['split'] == split]
            key = 'train' if split == 'all' else split
            dataset = YOLODataset(img_path=data[key], data=data, augment=False, imgsz=128, cache=False)
            assert len(dataset) == len(expected)
            lookup = {Path(a['image_path']).stem: a for a in expected}
            count = 0
            for index, label in enumerate(dataset.labels):
                asset = lookup[Path(label['im_file']).stem]
                boxes = asset['annotation_data']['boxes']
                xywh = [[(b['x']+b['width']/2)/asset['width'], (b['y']+b['height']/2)/asset['height'],
                         b['width']/asset['width'], b['height']/asset['height']] for b in boxes]
                np.testing.assert_allclose(label['bboxes'], np.asarray(xywh).reshape(-1, 4), rtol=0, atol=1e-6)
                assert label['cls'].flatten().tolist() == [manifest['labels'].index(b['label']) for b in boxes]
                sample = dataset[index]
                assert sample['img'].ndim == 3 and sample['img'].shape[0] == 3
                assert len(sample['cls']) == len(boxes)
                count += len(boxes)
            results.append(dict(split=split, images=len(dataset), boxes=count))
        sdk = 'ultralytics'
    else:
        from torchvision.datasets import CocoDetection
        for split in splits:
            dataset = CocoDetection(str(root), str(root / f'annotations/instances_{split}.json'))
            expected = [a for a in assets if a['split'] == split]
            assert len(dataset) == len(expected)
            categories = dataset.coco.loadCats(dataset.coco.getCatIds())
            assert {c['id']:c['name'] for c in categories} == {i+1:name for i,name in enumerate(manifest['labels'])}
            lookup = {a['image_path']: a for a in expected}
            count = 0
            for index, image_id in enumerate(dataset.ids):
                info = dataset.coco.loadImgs([image_id])[0]
                asset = lookup[info['file_name']]
                image, target = dataset[index]
                assert image.size == (asset['width'], asset['height'])
                boxes = asset['annotation_data']['boxes']
                assert len(target) == len(boxes)
                for actual, box in zip(target, boxes):
                    assert actual['category_id'] == manifest['labels'].index(box['label']) + 1
                    assert actual['bbox'] == [box[k] for k in ('x', 'y', 'width', 'height')]
                    assert actual['area'] == box['width'] * box['height']
                count += len(target)
            results.append(dict(split=split, images=len(dataset), boxes=count))
        sdk = 'pycocotools'
    return dict(format=manifest['format'], sdk=sdk, sdk_version=importlib.metadata.version(sdk),
                torchvision_version=importlib.metadata.version('torchvision'), splits=results, labels=manifest['labels'],
                negative_images=sum(not a['annotation_data']['boxes'] for a in assets), model_or_training_run=False)

if __name__ == '__main__':
    result = verify(sys.argv[1])
    if len(sys.argv) > 2:
        Path(sys.argv[2]).write_text(json.dumps(result, ensure_ascii=False, indent=2), 'utf-8')
    print(json.dumps(result, ensure_ascii=False))
