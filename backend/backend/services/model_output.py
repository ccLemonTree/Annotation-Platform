"""Output jobs share the existing durable training lifecycle and GPU holds."""
from pathlib import PurePosixPath
from uuid import uuid4,uuid5
from fastapi import HTTPException
from models.training_run import TrainingRun
from schemas.model_output import EXPORT_IMAGE
from services import training_runs


async def create(db,user,parent_id,request):
    parent=await training_runs.scoped(db,parent_id,user.tenant_id)
    merge=request.format=='merge'
    supported=('swift_sft','wemm') if merge else ('yolo26','rfdetr_medium','rfdetr_2xlarge')
    if parent.kind not in supported:raise HTTPException(422,'此训练器不支持所选导出/合并格式')
    if merge and parent.config['parameters'].get('tuner_type')!='lora':
        raise HTTPException(422,'仅LoRA微调需要合并；全参数训练已是完整模型')
    if not merge and PurePosixPath(request.source_path).suffix.lower() not in ('.pt','.pth','.onnx'):
        raise HTTPException(422,'请选择.pt/.pth检查点；TensorRT也支持已有.onnx')
    if not merge and PurePosixPath(request.source_path).suffix.lower()=='.onnx' and request.format!='tensorrt':
        raise HTTPException(422,'已有ONNX请选择TensorRT构建')
    if request.format=='onnx' and request.server_id!=parent.server['id']:
        raise HTTPException(422,'ONNX须在训练服务器导出；其他GPU服务器用于TensorRT构建')
    if merge and request.server_id!=parent.server['id'] and not request.base_model_path:
        raise HTTPException(422,'切换服务器后须填写目标服务器上的基础模型目录；平台不自动传输权重')
    if merge and request.image and request.image!=parent.config['image']:
        raise HTTPException(422,'LoRA合并必须沿用此任务的训练镜像')
    server=await training_runs.server_snapshot(db,request.server_id,user.tenant_id)
    id=uuid4();kind='model_merge' if merge else 'model_export'
    options=request.model_dump(mode='json')
    config=dict(kind=kind,output=options,model_kind=parent.kind,
        model_path=(request.base_model_path or parent.config['model_path']) if merge else request.source_path,
        adapter_path=request.source_path if merge else '',dataset_path='',parameters={},mounts=[],
        snapshot_id=None,gpu_uuids=[request.gpu_uuid],output_root=request.output_root,output_subdir='result',
        image=parent.config['image'] if merge else request.image or EXPORT_IMAGE,
        shm_size_gb=4,environment={'CUDA_VISIBLE_DEVICES':'0','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'})
    if request.format=='tensorrt':
        source_gpu=request.source_gpu_uuid or parent.config['gpu_uuids'][0]
        source_config={**config,'gpu_uuids':[source_gpu],'image':request.onnx_image or request.image or EXPORT_IMAGE,
            'output':{**options,'format':'onnx','server_id':parent.server['id'],'gpu_uuid':source_gpu}}
        received=request.output_root+'/'+str(id)+'/onnx/model.onnx'
        config.update(model_path=received,received_onnx=True,output={**options,'source_path':received},
            onnx_stage=dict(id=str(uuid5(id,'onnx')),server=parent.server,config=source_config))
    job=TrainingRun(id=id,tenant_id=user.tenant_id,user_id=user.id,name=parent.name[:175]+(' · LoRA合并' if merge else ' · '+request.format.upper()),
        kind=kind,config=config,source=dict(kind='model_output',run_id=str(parent.id),model_kind=parent.kind,
            source_path=request.source_path,source_server_id=parent.server['id']),
        server=server,output_dir=request.output_root+'/'+str(id),status='draft',inspection={})
    db.add(job);await db.commit();await db.refresh(job)
    return job


def build_script(config):
    import json
    from pathlib import Path
    runner=Path(__file__).with_name('model_output_runner.py').read_text(encoding='utf-8')
    return runner+'\nif __name__ == "__main__":\n    run(json.loads('+repr(json.dumps(config,ensure_ascii=False))+'))\n'
