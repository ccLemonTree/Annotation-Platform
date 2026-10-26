import json
import shutil
from pathlib import Path

import pytest


@pytest.fixture
def source(tmp_path,monkeypatch):
    # Linux containers use symlinks; Windows tests need no symlink privilege.
    monkeypatch.setattr(Path,'symlink_to',lambda self,target:shutil.copyfile(target,self))
    root=tmp_path/'input';root.mkdir()
    for split in ('train','valid'):
        (root/split/'images').mkdir(parents=True);(root/split/'labels').mkdir()
        (root/split/'images/a.jpg').write_bytes(b'picture')
        (root/split/'labels/a.txt').write_text('0 0.5 0.5 0.2 0.2\n1 0.4 0.4 0.1 0.1\n2 0.3 0.3 0.1 0.1\n')
        (root/split/'images/negative.jpg').write_bytes(b'negative')
        (root/split/'labels/negative.txt').write_text('0 0.5 0.5 0.2 0.2\n')
    (root/'data.yaml').write_text('path: /workspace/dataset\ntrain: train/images\nval: valid/images\nnames: {0: fire, 1: smoke, 2: star}\nnc: 3\n')
    return root


@pytest.mark.parametrize('kind',['yolo26','rfdetr_medium','rfdetr_2xlarge'])
def test_generate_config_and_filter_classes_without_changing_input(source,tmp_path,kind):
    from services.training_dataset import inspect_detection,prepare_detection
    before={str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()}
    info=inspect_detection(str(source/'data.yaml'))
    assert info['labels']==['fire','smoke','star']
    assert info['splits']=={'train':2,'val':2}
    output=tmp_path/'prepared'
    runtime=prepare_detection(str(source/'data.yaml'),str(output),kind,['star','smoke'])
    assert Path(runtime)==(output/'data.yaml' if kind=='yolo26' else output)
    document=json.loads((output/'data.yaml').read_text())
    assert document['names']==['smoke','star'] and document['nc']==2
    assert document['path']==str(output)
    labels=[p.read_text() for p in (output/'train/labels').glob('*.txt')]
    assert '' in labels
    assert any(text.startswith('0 0.4') and '\n1 0.3' in text for text in labels)
    assert before=={str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()}
    with pytest.raises(ValueError,match='不存在'):prepare_detection(str(source),str(tmp_path/'bad'),kind,['missing'])


def test_directory_source_automatically_uses_existing_class_metadata(source,tmp_path):
    from services.training_dataset import prepare_detection
    prepare_detection(str(source),str(tmp_path/'prepared'),'yolo26',None)
    assert json.loads((tmp_path/'prepared/data.yaml').read_text())['names']==['fire','smoke','star']


def test_yolo_missing_negative_label_stays_a_background_sample(source,tmp_path):
    from services.training_dataset import prepare_detection
    (source/'train/labels/negative.txt').unlink()
    prepare_detection(str(source),str(tmp_path/'prepared'),'yolo26',['smoke'])
    assert len(list((tmp_path/'prepared/train/images').iterdir()))==2
    assert '' in [p.read_text() for p in (tmp_path/'prepared/train/labels').glob('*.txt')]


def test_coco_filter_preserves_images_splits_and_remaps_categories(tmp_path,monkeypatch):
    from services.training_dataset import prepare_detection,inspect_detection
    monkeypatch.setattr(Path,'symlink_to',lambda self,target:shutil.copyfile(target,self))
    root=tmp_path/'coco'
    for split in ('train','valid'):
        folder=root/split;folder.mkdir(parents=True);(folder/'a.jpg').write_bytes(b'img')
        (folder/'_annotations.coco.json').write_text(json.dumps(dict(images=[dict(id=7,file_name='a.jpg')],
            categories=[dict(id=3,name='fire'),dict(id=8,name='smoke')],
            annotations=[dict(id=1,image_id=7,category_id=3),dict(id=2,image_id=7,category_id=8)])))
    assert inspect_detection(str(root))['labels']==['fire','smoke']
    prepare_detection(str(root),str(tmp_path/'prepared'),'rfdetr_medium',['smoke'])
    data=json.loads((tmp_path/'prepared/valid/_annotations.coco.json').read_text())
    assert len(data['images'])==1 and len(data['annotations'])==1
    assert data['annotations'][0]['category_id']==0
    assert data['categories']==[dict(id=0,name='smoke')]


def test_missing_metadata_or_sparse_ids_are_rejected(tmp_path):
    from services.training_dataset import inspect_detection
    with pytest.raises(ValueError):inspect_detection(str(tmp_path))
    file=tmp_path/'data.yaml';file.write_text('names: {0: fire, 2: smoke}\ntrain: train/images\nval: valid/images')
    with pytest.raises(ValueError):inspect_detection(str(file))


def test_server_yolo_precheck_uses_framework_config_without_rebasing(source,monkeypatch):
    import sys,types
    from services.training_dataset import inspect_server_detection
    calls=[]
    def check(path,autodownload):
        calls.append((path,autodownload))
        return dict(names={0:'smoke'},train='/configured/train',val='/configured/val')
    monkeypatch.setitem(sys.modules,'ultralytics.data.utils',types.SimpleNamespace(check_det_dataset=check))
    original=(source/'data.yaml').read_bytes()
    info=inspect_server_detection(str(source/'data.yaml'),'yolo26')
    assert calls==[(str(source/'data.yaml'),False)]
    assert info['splits']['train']=='/configured/train'
    assert original==(source/'data.yaml').read_bytes()


def test_server_rfdetr_precheck_uses_framework_directory_rules(source,monkeypatch):
    import sys,types
    from services.training_dataset import inspect_server_detection
    calls=[]
    def resolve(root,config,split):
        calls.append((root,config,split))
        folder=root/('valid' if split=='val' else split)
        return folder/'images',folder/'labels'
    monkeypatch.setitem(sys.modules,'rfdetr.datasets.yolo',types.SimpleNamespace(
        _extract_yolo_class_names=lambda p:['fire','smoke','star'],_resolve_yolo_split_dirs=resolve))
    info=inspect_server_detection(str(source),'rfdetr_medium')
    assert info['labels']==['fire','smoke','star']
    assert calls==[(source,source/'data.yaml','train'),(source,source/'data.yaml','val')]
    assert inspect_server_detection(str(source/'data.yaml'),'rfdetr_medium')==info
    with pytest.raises(ValueError,match='data.yaml/data.yml'):inspect_server_detection(str(source/'yolo-data.yaml'),'rfdetr_medium')
