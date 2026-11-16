"""Distributed recipes based on the Jiashan training manifests."""
import json

from schemas.training_run import PARAMETERS, SWIFT_IMAGE


def recipe_defaults(kind):
    if kind != 'wemm':
        return {}
    parameters=PARAMETERS['wemm']().model_dump()
    parameters.update(learning_rate=2e-5,save_steps=1000)
    return dict(parameters=parameters, environment=dict(IMAGE_MAX_TOKEN_NUM='768',
        PYTORCH_CUDA_ALLOC_CONF='expandable_segments:True', INFONCE_USE_BATCH='false',
        INFONCE_HARD_NEGATIVES='4', TOKENIZERS_PARALLELISM='false'), image=SWIFT_IMAGE,
        model_path='/adb-ssd-mount/ai/chen/code/huggingface/WeMM-Embedding-2B',
        dataset_path='/adb-ssd-mount/ai/chen/train/wemm/dataset/20260918/wemm_embedding_train.jsonl')


def _container_path(path):
    prefix='/adb-ssd-mount/ai'
    return '/ai'+path[len(prefix):] if path==prefix or path.startswith(prefix+'/') else path


def build_assets(config):
    if config['kind']!='wemm':
        return {}
    params=dict(config['parameters'])
    if not params.get('val_dataset'): params.pop('val_dataset',None)
    params.update(model=_container_path(config['model_path']), model_type='wemm_embedding',
        dataset=_container_path(config.get('runtime_dataset',config['dataset_path'])),
        output_dir='/output/'+config['output_subdir'])
    if params.get('val_dataset'): params['val_dataset']=_container_path(params['val_dataset'])
    defaults=recipe_defaults('wemm')['environment']
    params['ENV']={k:v for k,v in config.get('environment',defaults).items() if k in defaults}
    return {'train.yaml':json.dumps(params,ensure_ascii=False)}


def build_script(config):
    if config['kind']=='wemm':
        return "import subprocess\nsubprocess.run(['swift', 'sft', '/config/train.yaml'], check=True)\n"
    from services.training_config import build_script as local_script
    adapted=dict(config,execution_backend='docker',snapshot_id=None,parameters=dict(config['parameters']))
    for key in ('model_path','dataset_path','runtime_dataset'):
        if adapted.get(key): adapted[key]=_container_path(adapted[key])
    if config['kind'].startswith('rfdetr'):
        adapted['parameters'].update(devices=config['k8s']['gpus_per_node'],num_nodes=len(config['k8s']['nodes']))
        return local_script(adapted)
    adapted['parameters'].update(device='auto',save_dir='/output/'+config['output_subdir'])
    adapted['gpu_uuids']=['']*config['k8s']['gpus_per_node']
    script=local_script(adapted)
    return _yolo_script(script)


def _yolo_script(script):
    script=script.replace('import json, subprocess','import json, subprocess, os')
    prefix='    model.train(data=dataset, **json.loads('
    before, encoded=script.split(prefix,1)
    return (before+"    world_size = int(os.environ['WORLD_SIZE'])\n"
        "    local_world_size = int(os.environ['LOCAL_WORLD_SIZE'])\n"
        "    if local_world_size < 1 or world_size < 1 or world_size % local_world_size: raise ValueError('Invalid distributed world size')\n"
        '    params = json.loads('+encoded.rsplit('))',1)[0]+')\n'
        "    params['device'] = ','.join(map(str, list(range(local_world_size)) * (world_size // local_world_size)))\n"
        '    model.train(data=dataset, **params)\n')
