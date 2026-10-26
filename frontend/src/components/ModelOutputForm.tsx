import { useEffect,useRef,useState } from 'react';
import { Alert,Button,Checkbox,Col,Form,Input,InputNumber,Row,Select,Space,Switch,Typography } from 'antd';
import { createModelOutput,getTrainingArtifacts,type ModelOutputInput,type TrainingRun } from '../api/training';
import { getGpuResources,type GpuResource,type GpuServer } from '../api/gpuResources';

const exportImage='aisfh5.com:9091/triton-server/triton-server:25.11_py3_vllm';
function errorText(error:unknown){
  const e=error as {response?:{data?:{detail?:string|{msg:string}[]}};message?:string};
  const detail=e.response?.data?.detail;
  return typeof detail==='string'?detail:Array.isArray(detail)?detail.map(item=>item.msg).join('；'):e.message||'操作失败，请重试';
}

export default function ModelOutputForm({run,servers,admin,initialSource,onCreated}:{run:TrainingRun;servers:GpuServer[];admin:boolean;initialSource?:string;onCreated:(job:TrainingRun)=>void}){
  const merge=['swift_sft','wemm'].includes(run.kind);
  const [form]=Form.useForm<ModelOutputInput>();
  const server=Form.useWatch('server_id',form),format=Form.useWatch('format',form),dynamic=Form.useWatch('dynamic_batch',form);
  const [gpuResult,setGpuResult]=useState<{server:string;gpus:GpuResource[];error:string}>();
  const [sourceGpus,setSourceGpus]=useState<GpuResource[]>([]),[sourceGpuError,setSourceGpuError]=useState('');
  const gpus=gpuResult&&gpuResult.server===server?gpuResult.gpus:[],gpuError=gpuResult&&gpuResult.server===server?gpuResult.error:'',gpuLoading=!!server&&gpuResult?.server!==server;
  const [paths,setPaths]=useState<string[]>([]),[error,setError]=useState(''),[saving,setSaving]=useState(false);
  const writing=useRef(false);
  useEffect(()=>{if(initialSource)form.setFieldValue('source_path',initialSource);},[initialSource,form]);
  useEffect(()=>{if(!admin||!server)return;const controller=new AbortController();
    getGpuResources(controller.signal,server).then(result=>{if(!controller.signal.aborted)setGpuResult({server,gpus:result.gpus,error:''});})
      .catch(cause=>{if(!controller.signal.aborted)setGpuResult({server,gpus:[],error:errorText(cause)});});
    return()=>controller.abort();
  },[server,admin]);
  useEffect(()=>{if(!admin||merge)return;const controller=new AbortController();
    getGpuResources(controller.signal,run.server.id).then(result=>{if(!controller.signal.aborted)setSourceGpus(result.gpus);})
      .catch(cause=>{if(!controller.signal.aborted)setSourceGpuError(errorText(cause));});
    return()=>controller.abort();
  },[run.server.id,admin,merge]);
  useEffect(()=>{if(!merge&&format==='onnx')form.setFieldsValue({server_id:run.server.id,gpu_uuid:form.getFieldValue('source_gpu_uuid')||run.config.gpu_uuids[0]});},[format,merge,form,run.server.id,run.config.gpu_uuids]);
  useEffect(()=>{let active=true;
    getTrainingArtifacts(run.id).then(result=>{if(!active)return;
      const found=result.files.filter(file=>merge?file.path.endsWith('adapter_config.json'):/\.(pt|pth|onnx)$/i.test(file.path));
      const root=run.output_dir+'/'+run.config.output_subdir+'/';
      setPaths([...new Set(found.map(file=>root+(merge?file.path.replace(/\/?adapter_config\.json$/,''):file.path)))]);
    }).catch(()=>{ /* Manual paths remain available when the server cannot list artifacts. */ });
    return()=>{active=false;};
  },[run.id,run.output_dir,run.config.output_subdir,merge]);
  const save=async(values:ModelOutputInput)=>{if(writing.current)return;writing.current=true;setSaving(true);setError('');
    try{onCreated(await createModelOutput(run.id,{...values,image:merge?run.config.image:values.image,base_model_path:merge?values.base_model_path:undefined}));}
    catch(cause){setError(errorText(cause));}finally{writing.current=false;setSaving(false);}
  };
  if(merge&&run.config.parameters.tuner_type!=='lora')return <Alert type="info" title="此任务为全参数训练，产物已经是完整模型，无需LoRA合并。"/>;
  return <Form form={form} layout="vertical" disabled={!admin||saving} onFinish={values=>{void save(values);}}
    initialValues={{format:merge?'merge':'onnx',server_id:run.server.id,gpu_uuid:run.config.gpu_uuids[0],source_gpu_uuid:run.config.gpu_uuids[0],onnx_image:exportImage,source_path:initialSource,
      base_model_path:merge?run.config.model_path:'',output_root:'/ai/chen/training/exports',
      image:merge?run.config.image:exportImage,batch:1,dynamic_batch:false,min_batch:1,opt_batch:1,max_batch:8,
      fp16:true,opset:13,simplify:true,input_name:'input',output_name:'output',trust_checkpoint:false,
      model_type:run.kind==='wemm'?'wemm_embedding':'',torch_dtype:'float16'}}>
    <Alert type="info" showIcon title={merge?'将LoRA adapter合并为独立模型':'导出训练权重为部署模型'}
      description={merge?'创建独立任务后，先预检查再启动。输入只读挂载，输出使用新目录。':'ONNX在训练服务器导出；选择TensorRT后自动传输到目标GPU服务器构建。在同一任务查看阶段、日志及下载ONNX / engine；执行期间预留两阶段所选GPU。'} style={{marginBottom:16}}/>
    {error&&<Alert type="error" showIcon title={error} style={{marginBottom:16}}/>}
    <Row gutter={16}>
      <Col xs={24} md={12}><Form.Item name="format" label="输出格式" rules={[{required:true}]}><Select options={merge?[{value:'merge',label:'Swift LoRA合并'}]:[{value:'onnx',label:'ONNX'},{value:'tensorrt',label:'TensorRT（先导出ONNX）'}]}/></Form.Item></Col>
      <Col xs={24} md={12}><Form.Item name="server_id" label={format==='tensorrt'?'TensorRT目标GPU服务器':'执行GPU服务器'} rules={[{required:true}]} extra={merge?'请选择adapter与基础模型所在服务器。':format==='tensorrt'?'平台自动传输ONNX，无需在此服务器准备原始训练权重。':'固定使用原训练服务器。'}><Select disabled={!merge&&format==='onnx'} onChange={()=>{form.setFieldValue('gpu_uuid',undefined);if(merge){form.setFieldValue('source_path',undefined);form.setFieldValue('base_model_path',undefined);}}} options={servers.map(s=>({value:s.id,label:s.name,disabled:!s.enabled}))}/></Form.Item></Col>
    </Row>
    {gpuError&&<Alert type="error" title={gpuError}/>}
    <Form.Item name="gpu_uuid" label={format==='tensorrt'?'TensorRT构建GPU':'执行GPU'} rules={[{required:true,message:'请选择一张执行GPU'}]} extra="预检查和启动时核对占用，不抢占其他服务。"><Select loading={gpuLoading} options={gpus.map(g=>({value:g.uuid,label:`${g.index} · ${g.name} · ${g.uuid}`}))}/></Form.Item>
    {!merge&&format==='tensorrt'&&<>{sourceGpuError&&<Alert type="error" title={sourceGpuError}/>}<Form.Item name="source_gpu_uuid" label={`ONNX导出GPU（训练服务器：${run.server.name}）`} rules={[{required:true}]}><Select options={sourceGpus.map(g=>({value:g.uuid,label:`${g.index} · ${g.name} · ${g.uuid}`}))}/></Form.Item></>}
    {(!merge||server===run.server.id)&&paths.length>0&&<Form.Item label={merge?'从任务产物选择adapter':'从训练任务产物选择权重'}><Select aria-label="选择导出来源产物" showSearch optionFilterProp="label" allowClear options={paths.map(path=>({value:path,label:path}))} onChange={path=>form.setFieldValue('source_path',path)}/></Form.Item>}
    <Form.Item name="source_path" label={merge?'目标服务器adapter目录':`训练服务器检查点路径（${run.server.name}）`} rules={[{required:true}]} extra={merge?'选择含adapter_config.json和adapter_model权重的checkpoint目录。':'支持训练服务器上的.pt/.pth；TensorRT也可选择其已有.onnx，自动传输到目标服务器。'}><Input placeholder={merge?'/ai/chen/train/run/checkpoint-4083':'/ai/chen/train/run/checkpoint_best_total.pth'}/></Form.Item>
    {merge?<>
      <Form.Item name="base_model_path" label="基础模型目录" rules={[{required:true}]}><Input/></Form.Item>
      <Row gutter={16}><Col xs={24} md={12}><Form.Item name="model_type" label="Swift model_type" extra="WeMM默认wemm_embedding；其他模型留空自动识别，或填写镜像支持的类型。"><Input/></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name="torch_dtype" label="合并精度"><Select options={['float16','bfloat16','float32'].map(value=>({value,label:value}))}/></Form.Item></Col></Row>
    </>:<>
      <Row gutter={16}><Col xs={24} md={12}><Form.Item name="dynamic_batch" label="动态batch" valuePropName="checked"><Switch/></Form.Item></Col>
        {!dynamic&&<Col xs={24} md={12}><Form.Item name="batch" label="固定batch" rules={[{required:true}]}><InputNumber min={1} max={1024}/></Form.Item></Col>}</Row>
      {dynamic&&<Row gutter={16}>{(['min_batch','opt_batch','max_batch'] as const).map((key,index)=><Col xs={24} md={8} key={key}><Form.Item name={key} label={['最小batch','最佳batch（ONNX导出batch）','最大batch'][index]} rules={[{required:true}]}><InputNumber min={1} max={1024}/></Form.Item></Col>)}</Row>}
      <Form.Item name="image_size" label="输入分辨率（正方形边长）" extra="留空：YOLO使用640；RF-DETR从checkpoint读取（Medium通常576，2XLarge通常880），不按名称硬编码。已有ONNX沿用文件形状。"><InputNumber min={32} max={8192}/></Form.Item>
      <Form.Item name="fp16" valuePropName="checked"><Checkbox>FP16（RF-DETR ONNX / TensorRT；YOLO ONNX沿用FP32）</Checkbox></Form.Item>
      <Row gutter={16}><Col xs={24} md={12}><Form.Item name="input_name" label="ONNX输入名称" rules={[{required:true}]}><Input/></Form.Item></Col>
        {run.kind==='yolo26'&&<Col xs={24} md={12}><Form.Item name="output_name" label="YOLO输出名称" rules={[{required:true}]}><Input/></Form.Item></Col>}</Row>
      {run.kind==='yolo26'?<Row gutter={16}><Col xs={12}><Form.Item name="opset" label="YOLO ONNX opset"><InputNumber min={13} max={21}/></Form.Item></Col><Col xs={12}><Form.Item name="simplify" label="简化ONNX" valuePropName="checked"><Switch/></Form.Item></Col></Row>:
        <Form.Item name="trust_checkpoint" valuePropName="checked" extra="仅在确认权重来源可信且加载需要时启用；反序列化可执行权重中的代码。"><Checkbox>信任RF-DETR checkpoint（trust_checkpoint）</Checkbox></Form.Item>}
      {format==='tensorrt'&&<Alert type="warning" showIcon title="TensorRT engine与构建GPU、TensorRT版本相关" description="将使用所选GPU构建；动态batch按min/opt/max生成profile。保存环境及产物校验信息，不保证跨GPU/版本通用。" style={{marginBottom:16}}/>}
    </>}
    {!merge&&format==='tensorrt'&&<Form.Item name="onnx_image" label="训练服务器ONNX导出镜像" rules={[{required:true}]} extra="需支持对应的YOLO / RF-DETR导出；不在目标服务器加载原始训练权重。"><Input/></Form.Item>}
    <Form.Item name="image" label={merge?'合并镜像（沿用训练镜像）':format==='tensorrt'?'目标服务器TensorRT构建镜像':'导出环境镜像'} rules={[{required:true}]} extra="服务器必须已有镜像；启动时不自动拉取或安装依赖。"><Input disabled={merge}/></Form.Item>
    <Form.Item name="output_root" label="输出根目录" rules={[{required:true}]} extra="自动增加独立任务UUID/result目录；LoRA合并产物在其merged子目录。"><Input/></Form.Item>
    <Space wrap><Button htmlType="submit" type="primary" loading={saving}>{merge?'创建合并任务':'创建导出任务'}</Button><Typography.Text type="secondary">创建后不会自动执行。</Typography.Text></Space>
  </Form>;
}
