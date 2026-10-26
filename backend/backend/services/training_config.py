"""Recipe catalog and scripts generated solely from validated JSON values."""
import json
from pathlib import Path
from fastapi import HTTPException
from schemas.training_run import PARAMETERS,ENVIRONMENT,DETECTION_IMAGE,SWIFT_IMAGE,LIGHTLY_IMAGE

NAMES={'yolo26':'YOLO26','rfdetr_medium':'RF-DETR Medium','rfdetr_2xlarge':'RF-DETR 2XLarge',
       'swift_sft':'Swift 大模型SFT','wemm':'WeMM 向量微调','dinov3_vits16_ltdetr':'DINOv3 ViT-S/16 · LTDETR 检测微调'}


def catalog():
    return {kind:dict(name=NAMES[kind],schema=model.model_json_schema(),defaults=model().model_dump(),
        image=LIGHTLY_IMAGE if kind=='dinov3_vits16_ltdetr' else SWIFT_IMAGE if kind in ('swift_sft','wemm') else DETECTION_IMAGE,
        environment=ENVIRONMENT if kind in ('swift_sft','wemm') else {'CUDA_VISIBLE_DEVICES':'auto'})
        for kind,model in PARAMETERS.items()}


def effective_config(request):
    config=request.model_dump(mode='json')
    environment=dict(ENVIRONMENT) if request.kind in ('swift_sft','wemm') else {}
    environment.update(request.environment)
    if environment.get('NPROC_PER_NODE')=='auto':environment['NPROC_PER_NODE']=str(len(request.gpu_uuids))
    if environment.get('CUDA_VISIBLE_DEVICES','auto')=='auto':environment['CUDA_VISIBLE_DEVICES']=','.join(str(i) for i in range(len(request.gpu_uuids)))
    config['environment']={k:v for k,v in environment.items() if v!=''}
    config['adapter_version']=1
    return config


def validate_snapshot(kind,format,manifest,parameters=None):
    if kind in ('swift_sft','wemm'):
        params=parameters or PARAMETERS[kind]().model_dump()
        expected=(params.get('loss_type') or 'infonce') if params['task_type']=='embedding' else 'swift_sft'
        training=manifest.get('training',{})
        if format!=expected or training.get('format')!=expected or training.get('samples',0)<1:
            raise HTTPException(400,'冻结数据格式与训练任务/损失函数不匹配，或没有可用训练样本')
        return
    if format not in ('yolo','coco') or (kind=='yolo26' and format!='yolo'):
        raise HTTPException(400,'所选冻结导出格式不适用于此训练器；YOLO需YOLO格式，RF-DETR/DINOv3支持YOLO/COCO')
    splits={a.get('split') for a in manifest.get('assets',[])}
    if not {'train','val'}<=splits or splits-{'train','val','test'} or not manifest.get('approved_only'):
        raise HTTPException(400,'请选择仅已审核标注且包含明确train/val划分的冻结导出，不自动拆分或混用测试集')


def build_script(config):
    if config['kind'] in ('model_export','model_merge'):
        from services.model_output import build_script as output_script
        return output_script(config)
    params=dict(config['parameters']);kind=config['kind']
    dataset=config.get('runtime_dataset',config['dataset_path'])
    output='/output/'+config['output_subdir']
    lines=['import json, subprocess','',"if __name__ == '__main__':"]
    if config.get('snapshot_id') and kind not in ('swift_sft','wemm'):
        helper=Path(__file__).with_name('training_dataset.py').read_text(encoding='utf-8')
        lines=[helper,*lines]
        lines.append(f'    dataset = prepare_detection({dataset!r}, "/output/prepared-dataset", {kind!r}, {config.get("selected_labels")!r})')
    elif kind not in ('swift_sft','wemm'):
        if kind=='dinov3_vits16_ltdetr':
            lines=[Path(__file__).with_name('training_dataset.py').read_text(encoding='utf-8'),*lines]
        lines.append(f'    dataset = {dataset!r}')
        if kind.startswith('rfdetr'):
            lines+=['    from pathlib import Path',
                "    if Path(dataset).suffix.lower() in ('.yaml', '.yml'): dataset = str(Path(dataset).parent)"]
    if kind=='yolo26':
        if params['device']=='auto':params['device']=','.join(str(i) for i in range(len(config['gpu_uuids'])))
        params.update(project='/output',name=config['output_subdir'],exist_ok=False)
        lines+=['    from ultralytics import YOLO',f'    model = YOLO({config["model_path"]!r})',f'    model.train(data=dataset, **json.loads({json.dumps(params,ensure_ascii=False)!r}))']
    elif kind.startswith('rfdetr'):
        cls='RFDETRMedium' if kind=='rfdetr_medium' else 'RFDETR2XLarge'
        init={}
        if config['model_path']:init['pretrain_weights']=config['model_path']
        if kind=='rfdetr_2xlarge':init['accept_platform_model_license']=config['accept_platform_model_license']
        params.update(output_dir=output)
        lines+=[f'    from rfdetr import RFDETR, {cls}',
            '    num_classes = RFDETR._detect_num_classes_for_training(dataset)',
            "    if num_classes < 1: raise ValueError('训练数据必须包含至少一个类别')",
            "    print(f'RF-DETR training dataset: {dataset}; num_classes={num_classes}', flush=True)",
            f'    model = {cls}(num_classes=num_classes, **json.loads({json.dumps(init)!r}))',
            f'    model.train(dataset_dir=dataset, **json.loads({json.dumps(params,ensure_ascii=False)!r}))']
    elif kind=='dinov3_vits16_ltdetr':
        model_args={key:params.pop(key) for key in ('lr','backbone_lr_factor','backbone_freeze')}
        model_args['lr_warmup_steps']=params.pop('warmup_steps')
        params.update(devices=len(config['gpu_uuids']),model_args=model_args,
            save_checkpoint_args=dict(save_every_num_steps=params.pop('save_steps')))
        lines+=['    import lightly_train',
            f'    lightly_train.train_object_detection(out={output!r}, model={config["model_path"]!r}, data=lightly_detection_data(dataset), **json.loads({json.dumps(params,ensure_ascii=False)!r}))']
    else:
        params.update(model=config['model_path'],dataset=dataset,output_dir=output)
        args=['swift','sft']
        for key,value in params.items():
            if value is None or value=='':continue
            if config['parameters']['tuner_type']=='full' and key in ('lora_rank','lora_alpha'):continue
            args+=['--'+key,str(value).lower() if isinstance(value,bool) else str(value)]
        lines+=[f'    subprocess.run(json.loads({json.dumps(args,ensure_ascii=False)!r}), check=True)']
    return '\n'.join(lines)+'\n'
