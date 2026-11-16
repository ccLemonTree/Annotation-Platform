"""Output jobs share the existing durable training lifecycle and GPU holds."""
from pathlib import PurePosixPath
from uuid import UUID,uuid4,uuid5
from fastapi import HTTPException
from models.training_run import TrainingRun
from schemas.model_output import EXPORT_IMAGE
from services import training_runs


def training_checkpoint(parent,path):
    checkpoint=PurePosixPath(path)
    root=PurePosixPath(parent.output_dir)/parent.config['output_subdir']
    if checkpoint.suffix.lower() in ('.pt','.pth') and checkpoint.is_relative_to(root):
        return str(checkpoint.relative_to(root))


async def configuration(db,user,parent,request,id):
    merge=request.format=='merge'
    k8s=parent.config.get('execution_backend')=='k8s'
    supported=('swift_sft','wemm') if merge else ('yolo26','rfdetr_medium','rfdetr_2xlarge')
    if parent.kind not in supported:raise HTTPException(422,'此训练器不支持所选导出/合并格式')
    if merge and k8s and parent.status!='completed':raise HTTPException(409,'请先完成来源训练再合并 LoRA')
    if merge and parent.config['parameters'].get('tuner_type')!='lora':
        raise HTTPException(422,'仅LoRA微调需要合并；全参数训练已是完整模型')
    if not merge and PurePosixPath(request.source_path).suffix.lower() not in ('.pt','.pth','.onnx'):
        raise HTTPException(422,'请选择.pt/.pth检查点；TensorRT也支持已有.onnx')
    if not merge and PurePosixPath(request.source_path).suffix.lower()=='.onnx' and request.format!='tensorrt':
        raise HTTPException(422,'已有ONNX请选择TensorRT构建')
    artifact_root=PurePosixPath(parent.output_dir)/parent.config['output_subdir'] if merge else None
    adapter=PurePosixPath(request.source_path)
    managed=merge and parent.status=='completed' and adapter.is_relative_to(artifact_root)
    if merge and not managed and request.server_id!=parent.server['id'] and not request.base_model_path:
        raise HTTPException(422,'切换服务器后须填写目标服务器上的基础模型目录；平台不自动传输权重')
    if merge and request.image and request.image!=parent.config['image']:
        raise HTTPException(422,'LoRA合并必须沿用此任务的训练镜像')
    server=await training_runs.server_snapshot(db,request.server_id,user.tenant_id)
    source_server=server
    kind='model_merge' if merge else 'model_export'
    options=request.model_dump(mode='json')
    if not merge:options['source_gpu_uuid']=''
    if request.format=='onnx':options['gpu_uuid']=''
    config=dict(kind=kind,output=options,model_kind=parent.kind,
        model_path=(request.base_model_path or parent.config['model_path']) if merge else request.source_path,
        adapter_path=request.source_path if merge else '',dataset_path='',parameters={},mounts=[],
        snapshot_id=None,gpu_uuids=[] if request.format=='onnx' else [request.gpu_uuid],output_root=request.output_root,output_subdir='result',
        image=parent.config['image'] if merge else request.image or EXPORT_IMAGE,
        shm_size_gb=4,environment={'CUDA_VISIBLE_DEVICES':'0','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'})
    if request.format=='onnx':config['environment'].update(CUDA_VISIBLE_DEVICES='',NVIDIA_VISIBLE_DEVICES='void')
    if request.format=='tensorrt':
        source_id=request.onnx_server_id or (server['id'] if k8s else parent.server['id'])
        if source_id!=server['id']:
            source_server=parent.server if source_id==parent.server['id'] and not k8s else await training_runs.server_snapshot(db,source_id,user.tenant_id)
        source_config={**config,'gpu_uuids':[],'image':request.onnx_image or request.image or EXPORT_IMAGE,
            'environment':{**config['environment'],'CUDA_VISIBLE_DEVICES':'','NVIDIA_VISIBLE_DEVICES':'void'},
            'output':{**options,'format':'onnx','server_id':source_server['id'],'gpu_uuid':''}}
        received=request.output_root+'/'+str(id)+'/onnx/model.onnx'
        config.update(model_path=received,received_onnx=True,output={**options,'source_path':received},
            onnx_stage=dict(id=str(uuid5(id,'onnx')),server=source_server,config=source_config))
    source=dict(kind='model_output',run_id=str(parent.id),model_kind=parent.kind,
        source_path=request.source_path,source_server_id=source_server['id'],project_id=parent.source.get('project_id'))
    if managed:
        source['merge_inputs']={'base':'base/config.json','adapter':str(adapter.relative_to(artifact_root)/'adapter_config.json')}
        directory=f"{config['output_root']}/{id}"
        config.update(model_path=directory+'/base',adapter_path=directory+'/adapter')
    checkpoint=None if merge else training_checkpoint(parent,request.source_path)
    if checkpoint and (k8s or source_server['id']!=parent.server['id']):
        stage=config.get('onnx_stage')
        cpu=stage['config'] if stage else config
        staged=request.output_root+'/'+(stage['id'] if stage else str(id))+'/checkpoint'+PurePosixPath(checkpoint).suffix.lower()
        cpu.update(model_path=staged,output={**cpu['output'],'source_path':staged})
        source['checkpoint_artifact']=checkpoint
    return config,source,server


async def create(db,user,parent_id,request):
    parent=await training_runs.scoped(db,parent_id,user.tenant_id)
    id=uuid4();config,source,server=await configuration(db,user,parent,request,id)
    job=TrainingRun(id=id,tenant_id=user.tenant_id,user_id=user.id,
        name=parent.name[:175]+(' · LoRA合并' if request.format=='merge' else ' · '+request.format.upper()),
        kind=config['kind'],config=config,source=source,
        server=server,output_dir=request.output_root+'/'+str(id),status='draft',inspection={})
    db.add(job);await db.commit();await db.refresh(job)
    return job


async def update(db,user,id,request):
    job=await training_runs.scoped(db,id,user.tenant_id,lock=True)
    if job.kind not in ('model_export','model_merge'):
        raise HTTPException(409,'此任务不是导出/合并任务')
    if job.status!='draft':raise HTTPException(409,'仅待启动任务可以修改配置；已提交任务请创建新任务')
    if (request.format=='merge')!=(job.kind=='model_merge'):
        raise HTTPException(422,'不能将导出任务改为合并任务，或将合并任务改为导出任务')
    parent=await training_runs.scoped(db,UUID(job.source['run_id']),user.tenant_id)
    config,source,server=await configuration(db,user,parent,request,job.id)
    job.name=parent.name[:175]+(' · LoRA合并' if request.format=='merge' else ' · '+request.format.upper())
    job.config=config;job.source=source;job.server=server
    job.output_dir=request.output_root+'/'+str(job.id);job.inspection={};job.error=None
    await db.commit();await db.refresh(job)
    return job


def build_script(config):
    import json
    from pathlib import Path
    runner=Path(__file__).with_name('model_output_runner.py').read_text(encoding='utf-8')
    return runner+'\nif __name__ == "__main__":\n    run(json.loads('+repr(json.dumps(config,ensure_ascii=False))+'))\n'
