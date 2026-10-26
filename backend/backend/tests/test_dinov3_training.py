"""DINOv3 detection adapter contracts; no business data, Docker or GPU writes."""
import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import pytest_asyncio
from pydantic import ValidationError


def spec(**values):
    from schemas.training_run import TrainingCreate
    return TrainingCreate(**dict(dict(name='电瓶车检测',kind='dinov3_vits16_ltdetr',
        gpu_uuids=['GPU-test-1'],dataset_path='/data/electric/data.yaml',
        model_path='/models/dinov3_vits16_ltdetr_coco.pt',output_root='/data/runs'),**values))


def test_catalog_uses_user_supplied_lightly_image_and_supervised_defaults():
    from services.training_config import catalog,effective_config
    config=effective_config(spec())
    entry=catalog()['dinov3_vits16_ltdetr']
    assert entry['image']==config['image']=='lightly/train:latest'
    assert config['parameters']['steps']==1000
    assert config['parameters']['batch_size']==4
    assert config['environment']=={'CUDA_VISIBLE_DEVICES':'0'}


@pytest.mark.parametrize('values',[
    {'model_path':''},{'model_path':'dinov3/vits16-ltdetr-coco'},
    {'parameters':{'steps':0}},{'parameters':{'batch_size':True}},
    {'parameters':{'lr':float('nan')}},{'parameters':{'precision':'bf16'}},
    {'parameters':{'epochs':10}},{'parameters':{'steps':50,'warmup_steps':100}},
    {'gpu_uuids':['GPU-a','GPU-b','GPU-c']},
])
def test_invalid_dinov3_requests_are_rejected(values):
    with pytest.raises(ValidationError):spec(**values)


@pytest.fixture
def yolo_data(tmp_path,monkeypatch):
    monkeypatch.setattr(Path,'symlink_to',lambda self,target:shutil.copyfile(target,self))
    root=tmp_path/'dataset'
    for split in ('train','val','test'):
        images=root/'images'/split;images.mkdir(parents=True)
        labels=root/'labels'/split;labels.mkdir(parents=True)
        (images/'vehicle.jpg').write_bytes(b'image')
        (labels/'vehicle.txt').write_text('0 0.5 0.5 0.4 0.4\n1 0.5 0.5 0.2 0.2\n')
        (images/'background.jpg').write_bytes(b'background')
    (root/'data.yaml').write_text(json.dumps(dict(path=str(root),names=['bicycle','electric_bicycle'],
        train='images/train',val='images/val',test='images/test')))
    return root


def test_lightly_yolo_data_keeps_absolute_paths_names_and_excludes_test(yolo_data):
    from services.training_dataset import lightly_detection_data,inspect_server_detection
    before={str(p):p.read_bytes() for p in yolo_data.rglob('*') if p.is_file()}
    data=lightly_detection_data(str(yolo_data/'data.yaml'))
    assert data==dict(format='yolo',path=str(yolo_data),train=str(yolo_data/'images/train'),
        val=str(yolo_data/'images/val'),names={0:'bicycle',1:'electric_bicycle'})
    assert inspect_server_detection(str(yolo_data),'dinov3_vits16_ltdetr')['splits']['val']==2
    assert before=={str(p):p.read_bytes() for p in yolo_data.rglob('*') if p.is_file()}


@pytest.mark.parametrize('format',['yolo','coco'])
def test_frozen_dinov3_data_filters_labels_and_preserves_background_and_test(yolo_data,tmp_path,format):
    from services.training_dataset import prepare_detection,lightly_detection_data
    if format=='coco':
        for split in ('train','val','test'):
            folder=yolo_data/split;folder.mkdir()
            (folder/'vehicle.jpg').write_bytes(b'image');(folder/'background.jpg').write_bytes(b'background')
            (folder/'_annotations.coco.json').write_text(json.dumps(dict(
                images=[dict(id=1,file_name='vehicle.jpg'),dict(id=2,file_name='background.jpg')],
                categories=[dict(id=3,name='bicycle'),dict(id=8,name='electric_bicycle')],
                annotations=[dict(id=1,image_id=1,category_id=8,bbox=[1,1,2,2],area=4,iscrowd=0)])))
        (yolo_data/'data.yaml').unlink()
    output=tmp_path/'prepared'
    dataset=prepare_detection(str(yolo_data),str(output),'dinov3_vits16_ltdetr',['electric_bicycle'])
    data=lightly_detection_data(dataset)
    assert data['format']==format and 'test' not in data
    mapping=json.loads((output/'class_mapping.json').read_text())
    assert mapping['labels']==['electric_bicycle']
    assert mapping['images_by_split']==dict(train=2,val=2,test=2)
    if format=='yolo':
        assert data['names']=={0:'electric_bicycle'}
        assert '' in [p.read_text() for p in (output/'train/labels').iterdir()]
    else:
        assert data['val']['images']==str(output/'valid')
        document=json.loads(Path(data['val']['annotations']).read_text())
        assert document['categories']==[dict(id=0,name='electric_bicycle')]


def test_generated_script_calls_supervised_api_with_local_full_weights(yolo_data,tmp_path,monkeypatch):
    from services.training_config import build_script,effective_config
    calls=[]
    monkeypatch.setitem(sys.modules,'lightly_train',SimpleNamespace(train_object_detection=lambda **kwargs:calls.append(kwargs)))
    config=effective_config(spec(gpu_uuids=['GPU-a','GPU-b'],parameters={'steps':240,'warmup_steps':20,'save_steps':40,'backbone_freeze':True}))
    config['dataset_path']=str(yolo_data/'data.yaml')
    code=build_script(config)
    exec(compile(code,'train.py','exec'),{'__name__':'__main__'})
    call=calls[0]
    assert call['model']==config['model_path']
    assert call['devices']==2 and call['batch_size']==4 and call['steps']==240
    assert call['data']['names']=={0:'bicycle',1:'electric_bicycle'}
    assert 'test' not in call['data']
    assert call['model_args']==dict(lr=1e-4,backbone_lr_factor=.01,backbone_freeze=True,lr_warmup_steps=20)
    assert call['save_checkpoint_args']==dict(save_every_num_steps=40)
    assert 'pretrain(' not in code and not (yolo_data/'prepared-dataset').exists()


def test_lightly_cpu_probe_checks_public_api_model_identity_and_load_errors(monkeypatch):
    from services.training_dataset import inspect_lightly_model
    def train_object_detection(*,out,data,model,steps,batch_size,devices,precision,num_workers,seed,model_args,save_checkpoint_args,gradient_accumulation_steps):pass
    loaded=[]
    def load_model(path,device):
        loaded.append((path,device))
        return SimpleNamespace(init_args={'model_name':'dinov3/vits16-ltdetr-coco'})
    module=SimpleNamespace(__version__='0.17.0',train_object_detection=train_object_detection,load_model=load_model)
    monkeypatch.setitem(sys.modules,'lightly_train',module)
    assert inspect_lightly_model('/models/full.pt')['version']=='0.17.0'
    assert loaded==[('/models/full.pt','cpu')]
    module.load_model=lambda *args,**kwargs:SimpleNamespace(init_args={'model_name':'dinov3/vitt16-ltdetr-coco'})
    with pytest.raises(ValueError,match='vits16'):inspect_lightly_model('/models/wrong.pt')
    module.load_model=lambda *args,**kwargs:(_ for _ in ()).throw(ValueError('invalid weights'))
    with pytest.raises(ValueError,match='invalid weights'):inspect_lightly_model('/models/backbone.pth')


def test_dinov3_precheck_probes_without_gpu_or_network(monkeypatch,tmp_path):
    from services import training_executor as executor
    config=spec(snapshot_id=uuid4(),dataset_path='').model_dump(mode='json')
    weights=tmp_path/'model.pt';weights.write_bytes(b'weights');config['model_path']=str(weights)
    config['output_root']=str(tmp_path/'runs')
    calls=[]
    def docker(args,**kwargs):
        calls.append(args)
        if args[:2]==['image','inspect']:return SimpleNamespace(returncode=0,stdout='[{"Id":"sha256:lightly"}]')
        assert '--gpus' not in args and args[args.index('--network')+1]=='none'
        assert all(arg.endswith(',readonly') for i,arg in enumerate(args) if i and args[i-1]=='--mount')
        return SimpleNamespace(returncode=0,stdout='{"version":"0.17.0","model":"dinov3/vits16-ltdetr-coco"}')
    monkeypatch.setattr(executor,'docker',docker)
    monkeypatch.setattr(executor.subprocess,'run',lambda args,**kwargs:SimpleNamespace(returncode=0,stdout=(
        '0, GPU-test-1, test GPU, 20000, Disabled\n' if 'index,uuid' in args[1] else '')))
    result=executor.precheck(dict(config=config,dataset_code=Path(executor.__file__).with_name('training_dataset.py').read_text(encoding='utf-8')))
    assert result['ok'] and any(c['name']=='Lightly依赖与检测权重' for c in result['checks'])
    assert any('inspect_lightly_model' in ' '.join(args) for args in calls)
    def broken(*args,**kwargs):raise RuntimeError('cannot load full detector')
    monkeypatch.setattr(executor,'lightly_metadata',broken)
    result=executor.precheck(dict(config=config,dataset_code='helper'))
    assert not result['ok'] and any('cannot load' in c['detail'] for c in result['checks'])


@pytest_asyncio.fixture
async def training_tables(test_engine):
    from models.training_run import TrainingRun,TrainingGpuHold
    async with test_engine.begin() as conn:
        await conn.run_sync(lambda c:TrainingRun.__table__.create(c,checkfirst=True))
        await conn.run_sync(lambda c:TrainingGpuHold.__table__.create(c,checkfirst=True))


def test_dinov3_draft_api_saves_and_edits_without_launching(client,auth_headers,test_project,training_tables,monkeypatch):
    from services import training_runs
    async def no_remote(*args,**kwargs):raise AssertionError('draft must not contact training server')
    monkeypatch.setattr(training_runs,'remote',no_remote)
    payload=spec().model_dump(mode='json')
    response=client.post('/api/training/runs',headers=auth_headers,json=payload)
    assert response.status_code==201,response.text
    run=response.json();assert run['status']=='draft'
    payload['parameters']['backbone_freeze']=True
    response=client.put('/api/training/runs/'+run['id'],headers=auth_headers,json=payload)
    assert response.status_code==200 and response.json()['config']['parameters']['backbone_freeze']
    assert client.get('/api/training/catalog',headers=auth_headers).json()['dinov3_vits16_ltdetr']['image']=='lightly/train:latest'


@pytest.mark.parametrize('class_id',[0,8])
def test_dinov3_checkpoint_defaults_and_predictor_load_full_model_once(tmp_path,monkeypatch,class_id):
    from schemas.validation import CheckpointInput
    from services import checkpoint_predict as predictor
    from PIL import Image
    checkpoint=CheckpointInput(kind='dinov3_vits16_ltdetr',weight_path='/models/exported_best.pt')
    assert checkpoint.image=='lightly/train:latest' and checkpoint.class_names==[]
    inputs=tmp_path/'input';inputs.mkdir()
    samples=[]
    for index in range(2):
        path=inputs/f'{index}.png';Image.new('RGB',(32,24)).save(path)
        samples.append(dict(asset_id=str(index),file=path.name,sha256=predictor.sha(path),width=32,height=24))
    (inputs/'manifest.json').write_text(json.dumps(samples))
    config=checkpoint.model_dump();config.update(weight_sha256='weights',policy=dict(inference_confidence=.2,nms_iou=.45,
        labels=[],label_mapping={'electric_bicycle':'电瓶车'}))
    (tmp_path/'config.json').write_text(json.dumps(config))
    original=predictor.sha
    monkeypatch.setattr(predictor,'sha',lambda path:'weights' if str(path).startswith('/weights/') else original(path))
    class Tensor:
        def __init__(self,values):self.values=values
        def cpu(self):return self
        def tolist(self):return self.values
    calls=[]
    class Model:
        init_args={'model_name':'dinov3/vits16-ltdetr-coco'}
        classes={class_id:'electric_bicycle'}
        def predict(self,image,threshold):
            assert threshold==.2
            return dict(bboxes=Tensor([[1,2,11,12]]),scores=Tensor([.9]),labels=Tensor([class_id]))
    def load_model(path,device):calls.append((path,device));return Model()
    monkeypatch.setitem(sys.modules,'lightly_train',SimpleNamespace(load_model=load_model))
    predictor.predict_batch(tmp_path)
    assert calls==[('/weights/model.pt','cpu')]
    assert all(json.loads((tmp_path/'results'/f'{index}.json').read_text())['boxes'][0]==dict(
        label='电瓶车',x=1,y=2,width=10,height=10,score=.9) for index in range(2))
    config['class_names']=['wrong'];(tmp_path/'config.json').write_text(json.dumps(config))
    with pytest.raises(ValueError,match='类别'):predictor.predict_batch(tmp_path)


@pytest.mark.asyncio
@pytest.mark.parametrize('format',['yolo','coco'])
async def test_dinov3_snapshot_api_keeps_format_labels_and_requires_approved_splits(
        client,auth_headers,test_db,test_project,test_user,training_tables,format):
    from models import ExportSnapshot
    from services.training_config import build_script
    manifest=dict(labels=['bicycle','电瓶车'],assets=[{'split':'train'},{'split':'val'},{'split':'test'}],approved_only=True)
    snapshot=ExportSnapshot(project_id=test_project.id,user_id=test_user.id,export_format=format,
        blob_path='isolated.zip',content_hash='a'*64,manifest=manifest)
    test_db.add(snapshot);await test_db.commit()
    payload=spec(snapshot_id=snapshot.id,dataset_path='',selected_labels=['电瓶车']).model_dump(mode='json')
    response=client.post('/api/training/runs',headers=auth_headers,json=payload)
    assert response.status_code==201,response.text
    run=response.json();assert run['source']['format']==format and run['status']=='draft'
    assert run['config']['selected_labels']==['电瓶车']
    assert run['config']['runtime_dataset']=='/output/dataset/rfdetr'
    code=build_script(run['config']);compile(code,'train.py','exec')
    assert 'prepare_detection(' in code and 'data=lightly_detection_data(dataset)' in code
    snapshot.manifest={**manifest,'approved_only':False};await test_db.commit()
    assert client.post('/api/training/runs',headers=auth_headers,json=payload).status_code==400
