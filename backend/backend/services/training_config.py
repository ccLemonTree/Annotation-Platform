"""Recipe catalog and scripts generated solely from validated JSON values."""
import json
from pathlib import Path
from fastapi import HTTPException
from pydantic import ValidationError
from schemas.training_run import BUNDLED_DETECTION,PARAMETERS,ENVIRONMENT,DETECTION_IMAGE,SWIFT_IMAGE,LIGHTLY_IMAGE,absolute_path,trainer_parameters

NAMES={'yolo26':'YOLO26','rfdetr_medium':'RF-DETR Medium','rfdetr_2xlarge':'RF-DETR 2XLarge',
       'yolo11':'YOLO11','damo_yolo':'DAMO-YOLO',
       'swift_sft':'Swift 大模型SFT','wemm':'WeMM 向量微调','dinov3_vits16_ltdetr':'DINOv3 ViT-S/16 · LTDETR 检测微调'}


def catalog(overrides=None):
    overrides=overrides or {}
    return {kind:dict(name=NAMES[kind],schema=model.model_json_schema(),
        defaults={**model().model_dump(),**overrides.get(kind,{})},overrides=overrides.get(kind,{}),
        image=BUNDLED_DETECTION[kind]['image'] if kind in BUNDLED_DETECTION else
            LIGHTLY_IMAGE if kind=='dinov3_vits16_ltdetr' else SWIFT_IMAGE if kind in ('swift_sft','wemm') else DETECTION_IMAGE,
        **({'dataset_formats':BUNDLED_DETECTION[kind]['dataset_formats']} if kind in BUNDLED_DETECTION else {}),
        environment=ENVIRONMENT if kind in ('swift_sft','wemm') else {'CUDA_VISIBLE_DEVICES':'auto'})
        for kind,model in PARAMETERS.items()}


def validate_defaults(kind,parameters):
    if kind not in PARAMETERS:
        raise HTTPException(404,'内置训练器不存在')
    try:
        params=PARAMETERS[kind].model_validate(parameters).model_dump(exclude_unset=True)
        full=PARAMETERS[kind].model_validate(params)
        if kind=='dinov3_vits16_ltdetr' and full.warmup_steps>=full.steps:
            raise ValueError('学习率预热步数必须小于总训练步数')
        if kind in ('swift_sft','wemm'):
            if full.val_dataset:absolute_path(full.val_dataset)
            if full.eval_strategy!='no' and not full.val_dataset:
                raise ValueError('启用评估需要服务器验证JSONL路径')
            if full.task_type=='causal_lm' and full.loss_type:
                raise ValueError('向量损失函数只适用于embedding任务')
        return params
    except ValidationError as error:
        raise HTTPException(422,error.errors(include_url=False,include_context=False,include_input=False)) from None
    except ValueError as error:
        raise HTTPException(422,str(error)) from None


def effective_config(request):
    config=request.model_dump(mode='json')
    if config.get('trainer_id') is None:config.pop('trainer_id',None)
    environment=dict(ENVIRONMENT) if request.kind in ('swift_sft','wemm') else {}
    if config.get('execution_backend')=='k8s' and request.kind=='wemm':
        from services.k8s_training_recipes import recipe_defaults
        environment.update(recipe_defaults('wemm')['environment'])
    environment.update(request.environment)
    count=config['k8s']['gpus_per_node'] if config.get('execution_backend')=='k8s' else len(request.gpu_uuids)
    if environment.get('NPROC_PER_NODE')=='auto':environment['NPROC_PER_NODE']=str(count)
    if environment.get('CUDA_VISIBLE_DEVICES','auto')=='auto':environment['CUDA_VISIBLE_DEVICES']=','.join(str(i) for i in range(count))
    config['environment']={k:v for k,v in environment.items() if v!=''}
    config['adapter_version']=1
    return config


def configure_trainer(config,definition,format,manifest):
    if format!=definition['dataset_format']:
        raise HTTPException(400,'所选冻结数据格式与训练器登记格式不匹配')
    detection=format in ('yolo','coco')
    if not detection and config.get('selected_labels') is not None:
        raise HTTPException(422,'仅检测数据支持选择训练标签')
    parameters=definition['parameters']
    for key,value in config['parameters'].items():
        default=parameters.get(key)
        compatible=type(value) is type(default) or (type(default) is float and type(value) is int)
        if key not in parameters or not compatible:
            raise HTTPException(422,f'训练参数 {key} 未登记或类型与默认值不符')
    try:
        config['parameters']=trainer_parameters({**parameters,**config['parameters']})
    except ValueError as error:
        raise HTTPException(422,str(error)) from error
    if config['image'] and config['image']!=definition['image']:
        raise HTTPException(422,'训练镜像由登记配置指定')
    config['image']=definition['image']
    config['trainer']=definition
    if detection:
        validate_snapshot('yolo26' if format=='yolo' else 'rfdetr_medium',format,manifest)
        return '/output/prepared-dataset/data.yaml' if format=='yolo' else '/output/prepared-dataset'
    validate_snapshot('swift_sft',format,manifest,
                      {'task_type':'causal_lm' if format=='swift_sft' else 'embedding','loss_type':format})
    return '/output/dataset/train.jsonl'


def validate_snapshot(kind,format,manifest,parameters=None):
    if kind in ('swift_sft','wemm'):
        params=parameters or PARAMETERS[kind]().model_dump()
        expected=(params.get('loss_type') or 'infonce') if params['task_type']=='embedding' else 'swift_sft'
        training=manifest.get('training',{})
        if format!=expected or training.get('format')!=expected or training.get('samples',0)<1:
            raise HTTPException(400,'冻结数据格式与训练任务/损失函数不匹配，或没有可用训练样本')
        return
    formats=BUNDLED_DETECTION.get(kind,{}).get('dataset_formats',('yolo',) if kind=='yolo26' else ('yolo','coco'))
    if format not in formats:
        raise HTTPException(400,'所选训练器支持的冻结数据格式：'+'、'.join(value.upper() for value in formats))
    assets=manifest.get('assets',[])
    splits={a.get('split') for a in assets}
    if not {'train','val'}<=splits or splits-{'train','val','test'}:
        raise HTTPException(400,'冻结导出必须包含train和val划分，且只允许train/val/test；不会自动拆分或混用测试集')
    approved_only=manifest.get('approved_only')
    for asset in assets:
        status=asset.get('review_status')
        if status=='approved' or (status=='pending' and approved_only is False):
            continue
        if 'review_status' not in asset and approved_only is True:
            continue
        raise HTTPException(400,'冻结导出包含已驳回、超出所选审核范围或缺少审核状态的标注，请重新冻结')


def build_script(config):
    if config['kind'] in BUNDLED_DETECTION:
        kind=config['kind']
        helper=Path(__file__).with_name('training_dataset.py').read_text(encoding='utf-8')
        module=BUNDLED_DETECTION[kind]['module']
        return '\n'.join([helper,'import subprocess, sys',"if __name__ == '__main__':",
            '    source = Path("/output/dataset")',
            '    if (source / "rfdetr").is_dir(): source = source / "rfdetr"',
            *(['    source = source / "data.yaml"'] if kind=='yolo11' else []),
            f'    prepare_detection(str(source), "/output/prepared-dataset", {("yolo26" if kind=="yolo11" else kind)!r}, {config.get("selected_labels")!r})',
            f'    subprocess.run([sys.executable, "-I", "-u", "-m", {module!r}, "--config", "/output/run_config.json"], check=True)',
            ''])
    if config['kind']=='custom':
        format=config['trainer']['dataset_format']
        lines=['import subprocess, sys', "if __name__ == '__main__':"]
        if format in ('yolo','coco'):
            helper=Path(__file__).with_name('training_dataset.py').read_text(encoding='utf-8')
            lines.insert(0,helper)
            source,kind=('/output/dataset/data.yaml','yolo26') if format=='yolo' else ('/output/dataset/rfdetr','coco')
            lines.append(f'    prepare_detection(frozen_detection_source({source!r}), "/output/prepared-dataset", {kind!r}, {config.get("selected_labels")!r})')
        lines.append(f'    subprocess.run([sys.executable, "-I", "-u", "-m", {config["trainer"]["module"]!r}, "--config", "/output/run_config.json"], check=True)')
        return '\n'.join(lines)+'\n'
    if config['kind'] in ('model_export','model_merge'):
        from services.model_output import build_script as output_script
        return output_script(config)
    if config.get('execution_backend')=='k8s':
        from services.k8s_training_recipes import build_script as k8s_script
        return k8s_script(config)
    params=dict(config['parameters']);kind=config['kind']
    dataset=config.get('runtime_dataset',config['dataset_path'])
    output='/output/'+config['output_subdir']
    lines=['import json, subprocess','',"if __name__ == '__main__':"]
    if config.get('snapshot_id') and kind not in ('swift_sft','wemm'):
        helper=Path(__file__).with_name('training_dataset.py').read_text(encoding='utf-8')
        lines=[helper,*lines]
        lines.append(f'    dataset = prepare_detection(frozen_detection_source({dataset!r}), "/output/prepared-dataset", {kind!r}, {config.get("selected_labels")!r})')
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
