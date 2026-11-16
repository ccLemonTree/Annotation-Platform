import json
import copy
import sys
import types
import pytest


def config(kind):
    from schemas.training_run import PARAMETERS
    return dict(kind=kind, execution_backend='k8s', k8s={'nodes':['n1','n2'], 'gpus_per_node':2},
        parameters=PARAMETERS[kind]().model_dump(), gpu_uuids=[], dataset_path='/data/train.yaml',
        model_path='/data/model', output_subdir='train', accept_platform_model_license=True)


@pytest.mark.parametrize('kind', ['yolo26','rfdetr_medium','rfdetr_2xlarge'])
def test_distributed_script_uses_global_ranks(kind, monkeypatch):
    from services.training_config import build_script
    calls=[]
    class Model:
        @staticmethod
        def _detect_num_classes_for_training(dataset): return 3
        def __init__(self,*args,**kwargs): calls.append(('init',kwargs))
        def train(self,**kwargs): calls.append(('train',kwargs))
    monkeypatch.setenv('WORLD_SIZE','4'); monkeypatch.setenv('LOCAL_WORLD_SIZE','2')
    monkeypatch.setitem(sys.modules,'ultralytics',types.SimpleNamespace(YOLO=Model))
    monkeypatch.setitem(sys.modules,'rfdetr',types.SimpleNamespace(RFDETR=Model,RFDETRMedium=Model,RFDETR2XLarge=Model))
    exec(compile(build_script(config(kind)),'train.py','exec'),{'__name__':'__main__'})
    train=calls[-1][1]
    if kind=='yolo26':
        assert train['device']=='0,1,0,1'
        assert train['save_dir']=='/output/train'
    else:
        assert train['devices']==2 and train['num_nodes']==2
        assert calls[0][1]['num_classes']==3
        if kind=='rfdetr_2xlarge': assert calls[0][1]['accept_platform_model_license'] is True


def test_wemm_uses_reference_parameters_and_overrides_paths():
    from services.k8s_training_recipes import build_assets,recipe_defaults
    from services.training_config import build_script
    cfg=config('wemm'); cfg['parameters']['learning_rate']=0.00002
    defaults=recipe_defaults('wemm')
    assert defaults['parameters']['learning_rate']==0.00002
    assert defaults['dataset_path']=='/adb-ssd-mount/ai/chen/train/wemm/dataset/20260918/wemm_embedding_train.jsonl'
    train=json.loads(build_assets(cfg)['train.yaml'])
    assert train['model']=='/data/model' and train['dataset']=='/data/train.yaml'
    assert train['output_dir']=='/output/train'
    assert train['model_type']=='wemm_embedding' and train['ENV']['IMAGE_MAX_TOKEN_NUM']=='768'
    assert train['learning_rate']==0.00002
    assert 'resume_from_checkpoint' not in train
    assert '/config/train.yaml' in build_script(cfg)
    cfg['dataset_path']=defaults['dataset_path']
    assert json.loads(build_assets(cfg)['train.yaml'])['dataset']=='/ai/chen/train/wemm/dataset/20260918/wemm_embedding_train.jsonl'


def test_effective_config_uses_gpu_count_per_node():
    from services.training_config import effective_config
    class Request:
        kind='wemm'
        environment={}
        gpu_uuids=[]
        def model_dump(self,mode): return config('wemm')
    env=effective_config(Request())['environment']
    assert env['NPROC_PER_NODE']=='2'
    assert env['CUDA_VISIBLE_DEVICES']=='0,1'


@pytest.mark.parametrize(('kind','runtime_dataset'), [
    ('yolo26','/output/prepared-dataset/data.yaml'),
    ('rfdetr_medium','/output/prepared-dataset'),
    ('rfdetr_2xlarge','/output/prepared-dataset'),
    ('wemm','/output/dataset/train.jsonl'),
])
def test_snapshot_uses_prepared_dataset_without_repreparing(kind,runtime_dataset):
    from services.training_config import build_script
    from services.k8s_training_recipes import build_assets
    cfg=config(kind)
    cfg.update(snapshot_id='snapshot-id',dataset_path='',runtime_dataset=runtime_dataset,selected_labels=['smoke'])
    original=copy.deepcopy(cfg)
    script=build_script(cfg)
    assert 'prepare_detection' not in script
    if kind=='wemm':
        assert json.loads(build_assets(cfg)['train.yaml'])['dataset']==runtime_dataset
    else:
        assert runtime_dataset in script
    assert cfg==original
