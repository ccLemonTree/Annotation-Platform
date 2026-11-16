import { useEffect,useRef,useState } from 'react';
import { Alert,Button,Checkbox,Col,Form,Input,InputNumber,Row,Select,Space,Switch,Typography } from 'antd';
import { createModelOutput,getTrainingArtifacts,updateModelOutput,type ModelOutputInput,type TrainingRun } from '../api/training';
import { getGpuResources,type GpuResource,type GpuServer } from '../api/gpuResources';

const exportImage='aisfh5.com:9091/triton-server/triton-server:25.11_py3_vllm';
function errorText(error:unknown){
  const e=error as {response?:{data?:{detail?:string|{msg:string}[]}};message?:string};
  const detail=e.response?.data?.detail;
  return typeof detail==='string'?detail:Array.isArray(detail)?detail.map(item=>item.msg).join('；'):e.message||'操作失败，请重试';
}

export default function ModelOutputForm({run,job,servers,admin,initialSource,onCreated}:{run:TrainingRun;job?:TrainingRun;servers:GpuServer[];admin:boolean;initialSource?:string;onCreated:(job:TrainingRun)=>void}){
  const merge=['swift_sft','wemm'].includes(run.kind);
  const k8s=run.config.execution_backend==='k8s';
  const serverOptions=servers.filter(s=>s.enabled&&(s.id==='default'||s.has_password)).map(s=>({value:s.id,label:s.name}));
  const defaultServer=k8s?undefined:run.server.id;
  const saved=job?.config.output;
  const [form]=Form.useForm<ModelOutputInput>();
  const server=Form.useWatch('server_id',form),format=Form.useWatch('format',form),dynamic=Form.useWatch('dynamic_batch',form);
  const onnxServer=Form.useWatch('onnx_server_id',form);
  const checkpointServer=format==='tensorrt'?onnxServer:server;
  const [gpuResult,setGpuResult]=useState<{server:string;gpus:GpuResource[];error:string}>();
  const gpus=gpuResult&&gpuResult.server===server?gpuResult.gpus:[],gpuError=gpuResult&&gpuResult.server===server?gpuResult.error:'',gpuLoading=!!server&&gpuResult?.server!==server;
  const [paths,setPaths]=useState<string[]>([]),[error,setError]=useState(''),[saving,setSaving]=useState(false);
  const writing=useRef(false);
  useEffect(()=>{if(initialSource)form.setFieldValue('source_path',initialSource);},[initialSource,form]);
  useEffect(()=>{if(!admin||!server||(!merge&&format!=='tensorrt'))return;const controller=new AbortController();
    getGpuResources(controller.signal,server).then(result=>{if(!controller.signal.aborted)setGpuResult({server,gpus:result.gpus,error:''});})
      .catch(cause=>{if(!controller.signal.aborted)setGpuResult({server,gpus:[],error:errorText(cause)});});
    return()=>controller.abort();
  },[server,admin,merge,format]);
  useEffect(()=>{let active=true;
    getTrainingArtifacts(run.id).then(result=>{if(!active)return;
      const found=result.files.filter(file=>merge?file.path.endsWith('adapter_config.json'):/\.(pt|pth|onnx)$/i.test(file.path));
      const root=run.output_dir+'/'+run.config.output_subdir+'/';
      const choices=[...new Set(found.map(file=>root+(merge?file.path.replace(/\/?adapter_config\.json$/,''):file.path)))];
      setPaths(choices);
      if(merge&&!form.getFieldValue('source_path')&&choices.length===1)form.setFieldValue('source_path',choices[0]);
      if(merge&&!choices.length)setError('此训练任务没有可用的 LoRA 检查点');
      else if(merge&&result.cached)setError('来源服务器暂时不可达，已显示训练完成时记录的检查点');
    }).catch(cause=>{if(active&&merge)setError(errorText(cause));});
    return()=>{active=false;};
  },[run.id,run.output_dir,run.config.output_subdir,merge,form]);
  const save=async(values:ModelOutputInput)=>{if(writing.current)return;writing.current=true;setSaving(true);setError('');
    const input={...values,output_root:saved?.output_root||'/ai/chen/training/exports',onnx_server_id:values.format==='tensorrt'?values.onnx_server_id:undefined,gpu_uuid:values.format==='onnx'?'':values.gpu_uuid,source_gpu_uuid:undefined,image:merge?run.config.image:values.image};
    try{onCreated(await (job?updateModelOutput(job.id,input):createModelOutput(run.id,input)));}
    catch(cause){setError(errorText(cause));}finally{writing.current=false;setSaving(false);}
  };
  if(merge&&run.config.parameters.tuner_type!=='lora')return <Alert type="info" title="此任务为全参数训练，产物已经是完整模型，无需LoRA合并。"/>;
  return <Form form={form} layout="vertical" disabled={!admin||saving} onFinish={values=>{void save(values);}}
    onValuesChange={changed=>{if(changed.format==='tensorrt'&&!form.getFieldValue('onnx_server_id'))form.setFieldValue('onnx_server_id',form.getFieldValue('server_id')||defaultServer);
      if(changed.onnx_image!==undefined)localStorage.setItem('model-output-onnx-image',changed.onnx_image);
      if(changed.image!==undefined&&!merge)localStorage.setItem('model-output-image',changed.image);}}
    initialValues={{format:merge?'merge':'onnx',server_id:defaultServer,gpu_uuid:merge?run.config.gpu_uuids[0]:undefined,onnx_image:localStorage.getItem('model-output-onnx-image')||exportImage,
      base_model_path:merge?run.config.model_path:'',output_root:merge&&k8s?'/adb-ssd-mount/ai/chen/training/exports':'/ai/chen/training/exports',
      image:merge?run.config.image:localStorage.getItem('model-output-image')||exportImage,batch:1,dynamic_batch:false,min_batch:1,opt_batch:1,max_batch:8,
      fp16:true,opset:13,simplify:true,input_name:'input',output_name:'output',trust_checkpoint:false,
      model_type:run.kind==='wemm'?'wemm_embedding':'',torch_dtype:'float16',...saved,
      onnx_server_id:saved?.onnx_server_id||job?.config.onnx_stage?.server.id||defaultServer,
      source_path:job?.source.source_path||initialSource||saved?.source_path}}>
    <Alert type="info" showIcon title={merge?'将LoRA adapter合并为独立模型':'导出训练权重为部署模型'}
      description={merge?'启动时自动归档训练检查点和基础模型，目标服务器已有相同文件时直接复用。合并结果自动加入模型仓库。':'启动时自动将所选训练权重传到 ONNX 导出服务器。选择 TensorRT 后，ONNX 会继续传到目标 GPU 服务器构建。导出成功后自动加入模型仓库。'} style={{marginBottom:16}}/>
    {error&&<Alert type="error" showIcon title={error} style={{marginBottom:16}}/>}
    <Row gutter={16}>
      <Col xs={24} md={12}><Form.Item name="format" label="输出格式" rules={[{required:true}]}><Select options={merge?[{value:'merge',label:'Swift LoRA合并'}]:[{value:'onnx',label:'ONNX'},{value:'tensorrt',label:'TensorRT（先导出ONNX）'}]}/></Form.Item></Col>
      <Col xs={24} md={12}><Form.Item name="server_id" label={format==='tensorrt'?'TensorRT目标GPU服务器':merge?'执行GPU服务器':'ONNX导出服务器（CPU）'} rules={[{required:true}]}><Select placeholder="选择服务器" onChange={()=>{form.setFieldValue('gpu_uuid',undefined);if(merge&&!k8s){form.setFieldValue('source_path',undefined);form.setFieldValue('base_model_path',undefined);}}} options={serverOptions}/></Form.Item></Col>
    </Row>
    {format==='tensorrt'&&<Form.Item name="onnx_server_id" label="ONNX导出服务器（CPU）" rules={[{required:true}]}><Select placeholder="选择执行 ONNX 导出的服务器" options={serverOptions}/></Form.Item>}
    {(merge||format==='tensorrt')&&<>{gpuError&&<Alert type="error" title={gpuError}/>}
    <Form.Item name="gpu_uuid" label={format==='tensorrt'?'TensorRT构建GPU':'执行GPU'} rules={[{required:true,message:'请选择一张执行GPU'}]} extra={format==='tensorrt'?'允许共享已有计算进程的GPU；启动前复核剩余显存，仍可能因显存不足构建失败。':'预检查和启动时核对占用，不抢占其他服务。'}><Select loading={gpuLoading} options={gpus.map(g=>({value:g.uuid,label:`${g.index} · ${g.name} · 剩余 ${Math.round(g.memory_free_mib||0)} MiB · ${g.uuid}`}))}/></Form.Item></>}
    {!merge&&paths.length>0&&<Form.Item label="从训练任务产物选择权重"><Select aria-label="选择导出来源产物" showSearch optionFilterProp="label" allowClear options={paths.map(path=>({value:path,label:path}))} onChange={path=>form.setFieldValue('source_path',path)}/></Form.Item>}
    {merge?<Form.Item name="source_path" label="训练检查点" rules={[{required:true}]}><Select showSearch placeholder="选择已训练的 LoRA 检查点" options={paths.map(path=>({value:path,label:path.slice(run.output_dir.length+1)}))}/></Form.Item>:
    <Form.Item name="source_path" label={checkpointServer===run.server.id?`训练服务器检查点路径（${run.server.name}）`:'检查点路径'} rules={[{required:true}]} extra="训练任务产物目录下的 .pt 或 .pth 会按需自动传输。也可以填写 ONNX 服务器上已有的绝对路径。TensorRT 也接受已有的 .onnx。"><Input placeholder="/ai/chen/train/run/checkpoint_best_total.pth"/></Form.Item>}
    {merge?<>
      <Row gutter={16}><Col xs={24} md={12}><Form.Item name="model_type" label="Swift model_type" extra="WeMM默认wemm_embedding；其他模型留空自动识别，或填写镜像支持的类型。"><Input/></Form.Item></Col>
        <Col xs={24} md={12}><Form.Item name="torch_dtype" label="合并精度"><Select options={['float16','bfloat16','float32'].map(value=>({value,label:value}))}/></Form.Item></Col></Row>
    </>:<>
      <Row gutter={16}><Col xs={24} md={12}><Form.Item name="dynamic_batch" label="动态batch" valuePropName="checked"><Switch/></Form.Item></Col>
        {!dynamic&&<Col xs={24} md={12}><Form.Item name="batch" label="固定batch" rules={[{required:true}]}><InputNumber min={1} max={1024}/></Form.Item></Col>}</Row>
      {dynamic&&<Row gutter={16}>{(['min_batch','opt_batch','max_batch'] as const).map((key,index)=><Col xs={24} md={8} key={key}><Form.Item name={key} label={['最小batch','最佳batch（ONNX导出batch）','最大batch'][index]} rules={[{required:true}]}><InputNumber min={1} max={1024}/></Form.Item></Col>)}</Row>}
      <Form.Item name="image_size" label="输入分辨率（正方形边长）" extra="留空：YOLO使用640；RF-DETR从checkpoint读取（Medium通常576，2XLarge通常880），不按名称硬编码。已有ONNX沿用文件形状。"><InputNumber min={32} max={8192}/></Form.Item>
      <Form.Item name="fp16" valuePropName="checked"><Checkbox>FP16（TensorRT构建；ONNX沿用导出器精度）</Checkbox></Form.Item>
      <Row gutter={16}><Col xs={24} md={12}><Form.Item name="input_name" label="ONNX输入名称" rules={[{required:true}]}><Input/></Form.Item></Col>
        {run.kind==='yolo26'&&<Col xs={24} md={12}><Form.Item name="output_name" label="YOLO输出名称" rules={[{required:true}]}><Input/></Form.Item></Col>}</Row>
      {run.kind==='yolo26'?<Row gutter={16}><Col xs={12}><Form.Item name="opset" label="YOLO ONNX opset"><InputNumber min={13} max={21}/></Form.Item></Col><Col xs={12}><Form.Item name="simplify" label="简化ONNX" valuePropName="checked"><Switch/></Form.Item></Col></Row>:
        <Form.Item name="trust_checkpoint" valuePropName="checked" extra="仅在确认权重来源可信且加载需要时启用；反序列化可执行权重中的代码。"><Checkbox>信任RF-DETR checkpoint（trust_checkpoint）</Checkbox></Form.Item>}
      {format==='tensorrt'&&<Alert type="warning" showIcon title="TensorRT engine与构建GPU、TensorRT版本相关" description="将使用所选GPU构建；动态batch按min/opt/max生成profile。保存环境及产物校验信息，不保证跨GPU/版本通用。" style={{marginBottom:16}}/>}
    </>}
    {!merge&&format==='tensorrt'&&<Form.Item name="onnx_image" label="ONNX导出镜像" rules={[{required:true}]} extra="需在 ONNX 导出服务器上准备支持对应 YOLO 或 RF-DETR 的镜像。"><Input/></Form.Item>}
    <Form.Item name="image" label={merge?'合并镜像（沿用训练镜像）':format==='tensorrt'?'目标服务器TensorRT构建镜像':'导出环境镜像'} rules={[{required:true}]} extra="服务器必须已有镜像；启动时不自动拉取或安装依赖。"><Input disabled={merge}/></Form.Item>
    <Space wrap><Button htmlType="submit" type="primary" loading={saving}>{job?'保存任务配置':merge?'创建合并任务':'创建导出任务'}</Button><Typography.Text type="secondary">{job?'保存后须重新预检查，不会自动执行。':'创建后不会自动执行。'}</Typography.Text></Space>
  </Form>;
}
