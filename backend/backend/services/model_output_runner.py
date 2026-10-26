"""Container-side export/merge. Copied verbatim into an isolated output job."""
import hashlib
import importlib.metadata
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys


TRTEXEC='/usr/src/tensorrt/bin/trtexec'


def sha(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda:stream.read(1024**2),b''):digest.update(chunk)
    return digest.hexdigest()


def tensorrt_version():
    # This runner ships alone into the export container; record trtexec's C runtime.
    import ctypes,ctypes.util
    library=ctypes.util.find_library('nvinfer')
    if not library:raise ValueError('未找到TensorRT推理运行库')
    value=ctypes.CDLL(library).getInferLibVersion()
    divisor=10000 if value>=10000 else 1000
    return '{}.{}.{}'.format(value//divisor,(value%divisor)//100,value%100)


def sources(config):
    model=Path(config['model_path'])
    if config.get('received_onnx'):
        bundle=model.parent/'bundle.json'
        manifest=json.loads(bundle.read_text())
        info=manifest['onnx']
        if (not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,63}',info['input_name'])
                or any(type(info[key]) is not int or not 1<=info[key]<=8192 for key in ('height','width'))):
            raise ValueError('ONNX输入形状清单无效')
        hashes={}
        for row in manifest['files']:
            name=row['path'];path=model.parent/name
            if ('\\' in name or Path(name).is_absolute() or '..' in Path(name).parts or path.resolve()!=path
                    or not path.is_file() or path.stat().st_size!=row['bytes'] or sha(path)!=row['sha256']):
                raise ValueError('接收的ONNX产物校验失败')
            hashes[name]=row['sha256']
        if 'model.onnx' not in hashes:raise ValueError('ONNX清单缺少模型')
        return dict(bundle_sha256=sha(bundle),files=hashes)
    if config['kind']=='model_merge':
        adapter=Path(config['adapter_path'])
        if not model.is_dir() or not (model/'config.json').is_file():raise ValueError('基础模型目录缺少config.json')
        if not any(model.glob('*.safetensors')) and not any(model.glob('pytorch_model*.bin')):raise ValueError('基础模型目录缺少完整权重')
        if not adapter.is_dir() or not (adapter/'adapter_config.json').is_file():raise ValueError('adapter目录缺少adapter_config.json')
        files=[path for path in adapter.iterdir() if path.name in ('adapter_config.json','adapter_model.safetensors','adapter_model.bin')]
        if len(files)<2:raise ValueError('adapter目录缺少LoRA权重')
        result={'adapter':{path.name:sha(path) for path in files},'base_config_sha256':sha(model/'config.json')}
        result['base_weights']={p.name:{'bytes':p.stat().st_size,'mtime_ns':p.stat().st_mtime_ns}
            for p in model.iterdir() if p.suffix in ('.bin','.safetensors')}
        return result
    if not model.is_file():raise ValueError('导出来源权重不存在')
    result={'weight_sha256':sha(model),'bytes':model.stat().st_size}
    if model.suffix.lower()=='.onnx':
        import onnx
        graph=onnx.load(str(model),load_external_data=False)
        tensors=list(graph.graph.initializer)
        external={entry.value for tensor in tensors for entry in tensor.external_data if entry.key=='location'}
        result['external_data']={}
        for name in sorted(external):
            path=model.parent/name
            if Path(name).is_absolute() or '..' in Path(name).parts or not path.resolve().is_relative_to(model.parent.resolve()):
                raise ValueError('ONNX外部张量路径越界')
            result['external_data'][name]=sha(path)
    return result


def check(config):
    identity=sources(config)
    if config['kind']=='model_merge':
        if not shutil.which('swift'):raise ValueError('训练镜像没有swift命令')
    else:
        if config.get('received_onnx'):
            if not Path(TRTEXEC).is_file():raise ValueError('导出镜像缺少'+TRTEXEC)
            return identity
        import onnx
        if Path(config['model_path']).suffix.lower()!='.onnx':
            if config['model_kind']=='yolo26':
                from ultralytics import YOLO
            else:
                from rfdetr import RFDETR
                if not hasattr(RFDETR,'from_checkpoint'):raise ValueError('镜像RF-DETR不支持from_checkpoint，请更新导出环境')
        if config['output']['format']=='tensorrt' and not Path(TRTEXEC).is_file():raise ValueError('导出镜像缺少'+TRTEXEC)
    return identity


def swift_args(config,output):
    options=config['output']
    args=['swift','export','--model',config['model_path'],'--adapters',config['adapter_path'],
        '--merge_lora','true','--torch_dtype',options['torch_dtype'],'--output_dir',str(output)]
    if options['model_type']:args+=['--model_type',options['model_type']]
    return args


def trtexec_args(onnx,engine,options,input_name,height,width):
    args=[TRTEXEC,'--onnx='+str(onnx),'--saveEngine='+str(engine),'--skipInference']
    if options['fp16']:args+=['--fp16']
    if options['dynamic_batch']:
        for key,flag in [('min_batch','minShapes'),('opt_batch','optShapes'),('max_batch','maxShapes')]:
            args+=['--'+flag+'='+input_name+':'+str(options[key])+f'x3x{height}x{width}']
    return args


def rename_io(model,mapping):
    """Rename all references, including nested graphs and intermediate value info."""
    from onnx import AttributeProto
    def graph(value):
        for item in [*value.input,*value.output,*value.value_info,*value.initializer]:
            item.name=mapping.get(item.name,item.name)
        for node in value.node:
            for names in (node.input,node.output):
                for index,name in enumerate(names):names[index]=mapping.get(name,name)
            for attr in node.attribute:
                if attr.type==AttributeProto.GRAPH:graph(attr.g)
                elif attr.type==AttributeProto.GRAPHS:
                    for child in attr.graphs:graph(child)
    graph(model.graph)


def prepare_onnx(path,options,yolo=False):
    import onnx
    model=onnx.load(str(path))
    if len(model.graph.input)!=1:raise ValueError('当前导出仅支持一个图像输入，不能猜测多输入模型的TensorRT形状')
    old=model.graph.input[0].name
    mapping={old:options['input_name']}
    if yolo:
        if len(model.graph.output)!=1:raise ValueError('YOLO模型有多个输出，不能只重命名第一个输出')
        mapping[model.graph.output[0].name]=options['output_name']
    names={name for node in model.graph.node for name in [*node.input,*node.output]}
    if any(new!=old and new in names for old,new in mapping.items()):raise ValueError('新输入/输出名称与已有节点冲突')
    rename_io(model,mapping)
    dims=model.graph.input[0].type.tensor_type.shape.dim
    if len(dims)!=4:raise ValueError('ONNX图像输入须为NCHW四维')
    if yolo and options['dynamic_batch']:
        # Ultralytics dynamic=True also makes H/W symbolic; keep only batch dynamic.
        for dim in dims[2:]:
            if not dim.dim_value:dim.dim_value=options['image_size'] or 640
    if dims[1].dim_value!=3 or not dims[2].dim_value or not dims[3].dim_value:
        raise ValueError('ONNX输入需要固定3通道与高宽')
    if options['dynamic_batch']:
        if dims[0].HasField('dim_value'):raise ValueError('来源ONNX是固定batch，不能创建动态TensorRT profile；请重新导出动态ONNX')
    elif dims[0].dim_value!=options['batch']:
        raise ValueError('来源ONNX的batch与指定固定batch不一致')
    onnx.checker.check_model(model)
    onnx.save_model(model,str(path))
    onnx.checker.check_model(str(path))
    return dict(input_name=model.graph.input[0].name,outputs=[item.name for item in model.graph.output],
        height=dims[2].dim_value,width=dims[3].dim_value,input_dtype=model.graph.input[0].type.tensor_type.elem_type)


def export_onnx(config,output):
    options=config['output'];source=Path(config['model_path'])
    yolo=config['model_kind']=='yolo26';batch=options['opt_batch'] if options['dynamic_batch'] else options['batch']
    if source.suffix.lower()=='.onnx':
        # Keep external tensors alongside the graph, without copying unrelated checkpoints.
        import onnx
        graph=onnx.load(str(source),load_external_data=True)
        onnx.save_model(graph,str(output/'model.onnx'),save_as_external_data=True,
            all_tensors_to_one_file=True,location='model.data',size_threshold=1024)
        path=output/'model.onnx'
    elif yolo:
        from ultralytics import YOLO
        local=output/'source.pt';shutil.copyfile(source,local)
        try:
            model=YOLO(str(local))
            path=Path(model.export(format='onnx',imgsz=options['image_size'] or 640,
                opset=options['opset'],simplify=options['simplify'],batch=batch,
                dynamic=options['dynamic_batch'],half=False,device=0))
        finally:local.unlink(missing_ok=True)
    else:
        from rfdetr import RFDETR
        model=RFDETR.from_checkpoint(str(source),trust_checkpoint=options['trust_checkpoint'])
        size=options['image_size'] or model.model_config.resolution
        print('RF-DETR:',type(model).__name__,'resolution:',size,flush=True)
        model.export(format='onnx',output_dir=str(output),shape=(size,size),batch_size=batch,
            dynamic_batch=options['dynamic_batch'],fp16=options['fp16'])
        files=list(output.glob('*.onnx'))
        if len(files)!=1:raise ValueError('RF-DETR未生成唯一ONNX文件，请查看导出日志')
        path=files[0]
    info=prepare_onnx(path,options,yolo=yolo)
    return path,info


def run(config):
    before=check(config)
    output=Path('/output')/config['output_subdir'];output.mkdir(exist_ok=False)
    options=config['output'];details={}
    if config['kind']=='model_merge':
        merged=output/'merged'
        subprocess.run(swift_args(config,merged),check=True)
        if not (merged/'config.json').is_file() or not (list(merged.glob('*.safetensors')) or list(merged.glob('pytorch_model*.bin'))):
            raise ValueError('合并未生成完整模型config和权重，禁止标记成功')
        if (merged/'adapter_config.json').exists():raise ValueError('输出仍为adapter，合并不完整')
        for index in merged.glob('*.index.json'):
            shards=set(json.loads(index.read_text())['weight_map'].values())
            if any(not (merged/name).is_file() for name in shards):raise ValueError('合并模型缺少权重分片')
    else:
        if config.get('received_onnx'):
            source=Path(config['model_path']).parent
            bundle=json.loads((source/'bundle.json').read_text());details=bundle['onnx']
            for row in bundle['files']:
                target=output/row['path'];target.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source/row['path'],target)
            onnx=output/'model.onnx'
        else:onnx,details=export_onnx(config,output)
        if options['format']=='tensorrt':
            engine=output/'model.engine'
            args=trtexec_args(onnx,engine,options,details['input_name'],details['height'],details['width'])
            print('TensorRT arguments:',json.dumps(args),flush=True)
            subprocess.run(args,check=True)
            if not engine.is_file() or not engine.stat().st_size:raise ValueError('TensorRT未生成非空engine')
    if before!=sources(config):raise ValueError('导出/合并期间来源权重发生变化，请使用稳定检查点重建任务')
    versions={}
    for name in ('torch','ultralytics','rfdetr','onnx','tensorrt','ms-swift'):
        try:versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:pass
    if options['format']=='tensorrt':versions['tensorrt_runtime']=tensorrt_version()
    files=[]
    for path in output.rglob('*'):
        if path.is_file():files.append({'path':str(path.relative_to(output)),'bytes':path.stat().st_size,'sha256':sha(path)})
    gpu=subprocess.run(['nvidia-smi','--query-gpu=name','--format=csv,noheader'],capture_output=True,text=True,check=True).stdout.strip()
    manifest=dict(format=options['format'],source=before,options=options,onnx=details,versions=versions,
        gpu_uuid=config['gpu_uuids'][0],gpu_name=gpu,files=files)
    (output/'result.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print('模型输出成功：',str(output),flush=True)
