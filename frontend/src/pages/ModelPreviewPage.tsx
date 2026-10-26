import { useEffect,useRef,useState } from 'react';
import { Link,useSearchParams } from 'react-router-dom';
import { Alert,Button,Card,Checkbox,Empty,Form,InputNumber,Pagination,Popconfirm,Progress,Select,Space,Tag,Tabs } from 'antd';
import { UploadOutlined } from '@ant-design/icons';
import { getCurrentUser } from '../api/auth';
import { getModelServices,hasDetectionConfidence,hasDetectionNms } from '../api/modelServices';
import type { ModelService } from '../api/modelServices';
import { listModelPreviews,getModelPreview,createModelPreview,changeModelPreview,deleteModelPreview,getPreviewConfig } from '../api/modelPreview';
import type { ModelPreview,PreviewInput,PreviewConfig } from '../api/modelPreview';
import { activeValidationRun,validationError,validationStatusLabels } from '../api/validation';
import type { CheckpointInput,ValidationRun,ValidationItem } from '../api/validation';
import CheckpointFields from '../components/validation/CheckpointFields';
import PreviewDetectionImage from '../components/PreviewDetectionImage';
import useImageNavigation from '../components/useImageNavigation';
import './ModelPreviewPage.css';
import MultimodalWorkspace from '../components/validation/MultimodalWorkspace';

export default function ModelPreviewPage(){
  const [params,setParams]=useSearchParams();
  const kind=params.get('task_type')||'detection';
  return <Tabs activeKey={kind} onChange={value=>setParams(previous=>{const next=new URLSearchParams(previous);next.set('task_type',value);next.delete('semantic_run_id');return next;},{preventScrollReset:true})} items={[
    {key:'detection',label:'目标检测',children:<DetectionModelPreviewPage/>},
    {key:'caption',label:'大模型图片描述',children:<MultimodalWorkspace key="caption" kind="caption" preview/>},
    {key:'embedding',label:'向量检索',children:<MultimodalWorkspace key="embedding" kind="embedding" preview/>}
  ]} destroyOnHidden/>;
}

type Values={source:'service'|'checkpoint';model_id?:string;labels?:string[];checkpoint:CheckpointInput;weight_source:string;confidence:number;iou:number};
const imageFile=/\.(jpe?g|png|bmp|webp)$/i,acceptedFile=/\.(jpe?g|png|bmp|webp|zip|tar|tar\.gz|tgz)$/i;
const formatBytes=(bytes:number)=>bytes>=1024**3?`${(bytes/1024**3).toFixed(1)} GiB`:`${(bytes/1024**2).toFixed(0)} MiB`;
function DetectionModelPreviewPage(){
  const [params,setParams]=useSearchParams(),[form]=Form.useForm<Values>();
  const [models,setModels]=useState<ModelService[]>([]),[history,setHistory]=useState<ValidationRun[]>([]);
  const [files,setFiles]=useState<File[]>([]),[loaded,setDetail]=useState<ModelPreview>();
  const [shown,setShown]=useState<{runId:string;item:ValidationItem}>();
  const [admin,setAdmin]=useState(false),[ready,setReady]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [refresh,setRefresh]=useState(0),[showBoxes,setShowBoxes]=useState(true);
  const [config,setConfig]=useState<PreviewConfig>(),[selectionNote,setSelectionNote]=useState(''),[uploadProgress,setUploadProgress]=useState<number>();
  const picker=useRef<HTMLInputElement>(null),folder=useRef<HTMLInputElement>(null),uploadController=useRef<AbortController|null>(null),mounted=useRef(false),writing=useRef(false);
  const source=Form.useWatch('source',form)||'service',runId=params.get('run_id')||'';
  const modelId=Form.useWatch('model_id',form),model=models.find(row=>row.id===modelId);
  const hasConfidence=source==='checkpoint'||hasDetectionConfidence(model),hasNms=source==='checkpoint'||hasDetectionNms(model);
  const requestedPage=Number(params.get('page')||1),imagePage=Number.isSafeInteger(requestedPage)&&requestedPage>0?requestedPage:1;
  const detail=loaded?.id===runId?loaded:undefined,pageReady=!!detail&&(detail.page??1)===imagePage;
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;uploadController.current?.abort();};},[]);
  useEffect(()=>{
    let active=true;
    Promise.all([getCurrentUser(),getModelServices({kind:'detection',enabled:true}),getPreviewConfig()]).then(([user,models,config])=>{
      if(active){setAdmin(user.role==='admin');setModels(models.filter(m=>m.enabled&&m.kind==='detection'));setConfig(config);setReady(true);
        if(!form.isFieldTouched('labels'))form.setFieldValue('labels',models.find(row=>row.id===form.getFieldValue('model_id'))?.labels||[]);}
    }).catch(e=>{if(active)setError(validationError(e,'读取模型列表失败，请刷新重试'));});
    return()=>{active=false;};
  },[refresh,form]);
  useEffect(()=>{
    const controller=new AbortController();
    listModelPreviews(controller.signal).then(result=>setHistory(result.items)).catch(e=>{if(!controller.signal.aborted)setError(validationError(e,'读取最近预览失败'));});
    return()=>controller.abort();
  },[refresh,runId]);
  useEffect(()=>{
    if(!runId)return;
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{
      try{
        const result=await getModelPreview(runId,controller.signal,imagePage);if(controller.signal.aborted)return;
        setDetail(result);setHistory(rows=>[result,...rows.filter(row=>row.id!==result.id)]);
        if(activeValidationRun(result))timer=setTimeout(poll,2000);
      }catch(e){if(!controller.signal.aborted)setError(validationError(e,'读取预览失败，请重新打开或刷新'));}
    };
    void poll();return()=>{controller.abort();clearTimeout(timer);};
  },[runId,imagePage,refresh]);
  const open=(id:string)=>{setParams(previous=>{const next=new URLSearchParams(previous);next.delete('page');if(id)next.set('run_id',id);else next.delete('run_id');return next;});};
  const changePage=(page:number)=>setParams(previous=>{const next=new URLSearchParams(previous);next.set('page',String(page));return next;});
  const navigation=useImageNavigation({scope:runId,items:pageReady?detail?.items:undefined,page:imagePage,pageSize:20,total:detail?.total||0,onPage:changePage,enabled:!busy});
  const item=navigation.item||(shown?.runId===runId?shown.item:undefined);
  useEffect(()=>{if(navigation.item)setShown({runId,item:navigation.item});},[runId,navigation.item]);
  useEffect(()=>{
    const gallery=document.querySelector<HTMLElement>('.model-preview-gallery'),selected=gallery?.querySelector<HTMLElement>('.is-selected');
    if(!gallery||!selected)return;
    const bounds=gallery.getBoundingClientRect(),box=selected.getBoundingClientRect();
    if(box.left<bounds.left)gallery.scrollLeft+=box.left-bounds.left;
    else if(box.right>bounds.right)gallery.scrollLeft+=box.right-bounds.right;
  },[item?.id]);
  const choose=(incoming:File[],fromFolder=false)=>{
    if(writing.current||!config)return;
    const valid=fromFolder?incoming.filter(file=>imageFile.test(file.name)):incoming;
    if(valid.some(file=>!file.size||!acceptedFile.test(file.name))){setError('请选择非空图片或 ZIP / TAR / TAR.GZ / TGZ 压缩包');return;}
    if(!valid.length){setError('文件夹中没有支持的图片');return;}
    const next=[...files,...valid];
    if(next.length>config.max_images||next.reduce((sum,file)=>sum+file.size,0)>config.max_upload_size){setError(`最多选择${config.max_images}个文件，上传合计不超过${formatBytes(config.max_upload_size)}`);return;}
    if(valid.some(file=>imageFile.test(file.name)&&file.size>config.max_image_size)){setError(`单张图片不能超过${formatBytes(config.max_image_size)}`);return;}
    setSelectionNote(fromFolder&&valid.length<incoming.length?`已忽略 ${incoming.length-valid.length} 个非图片文件`:'');
    setError('');setFiles(next);
  };
  const start=async(values:Values)=>{
    if(writing.current)return;if(!files.length){setError('请先选择图片');return;}
    writing.current=true;setBusy(true);setError('');setUploadProgress(0);
    const controller=new AbortController();uploadController.current=controller;
    const config={...(hasConfidence?{confidence:values.confidence}:{}),...(hasNms?{iou:values.iou}:{})};
    const input:PreviewInput=values.source==='checkpoint'?{...config,checkpoint:{...values.checkpoint,...(values.weight_source==='remote'?{training_run_id:undefined}:{})}}:{...config,model_id:values.model_id!,...(values.labels?.length?{labels:values.labels}:{})};
    try{const run=await createModelPreview(files,input,controller.signal,setUploadProgress);if(mounted.current){open(run.id);setFiles([]);setSelectionNote('');setRefresh(n=>n+1);}}
    catch(e){if(mounted.current){setError(controller.signal.aborted?'上传请求已停止，所选文件已保留；已提交任务可从最近预览查看':validationError(e,'上传或启动推理失败，所选文件已保留'));setRefresh(n=>n+1);}}
    finally{writing.current=false;uploadController.current=null;if(mounted.current){setBusy(false);setUploadProgress(undefined);}}
  };
  const change=async(action:'cancel'|'retry'|'delete')=>{
    if(!detail||writing.current)return;writing.current=true;setBusy(true);setError('');
    try{
      if(action==='delete'){await deleteModelPreview(detail.id);if(mounted.current)open('');}
      else await changeModelPreview(detail.id,action);
      if(mounted.current)setRefresh(n=>n+1);
    }catch(e){if(mounted.current)setError(validationError(e,'预览操作失败，请重试'));}
    finally{writing.current=false;if(mounted.current)setBusy(false);}
  };
  return <div className="model-preview-page">
    {error&&<Alert type="error" showIcon title={error} action={<Button disabled={busy} onClick={()=>{setError('');setRefresh(n=>n+1);}}>刷新重试</Button>}/>}
    <div className="model-preview-layout">
      <Card title="上传并推理" className="model-preview-setup">
        <Form form={form} layout="vertical" disabled={busy} onFinish={start} initialValues={{source:'service',model_id:params.get('model_id')||undefined,confidence:.25,iou:.45,weight_source:'training',
          checkpoint:{server_id:'default',kind:'yolo26',device:'cpu',image:'aisfh5.com:9091/ai/jashan_train:yolo-rfdetr',class_names:[],accept_platform_model_license:false}}}>
          <Form.Item name="source" label="模型来源"><Select aria-label="预览模型来源" options={[{value:'service',label:'模型管理中的检测接口'},{value:'checkpoint',label:'YOLO / RF-DETR / DINOv3 训练权重',disabled:!admin}]} /></Form.Item>
          {source==='service'?<>
            <Form.Item name="model_id" label="检测模型" rules={[{required:true,message:'请选择检测模型'}]}>
              <Select aria-label="预览检测模型" loading={!ready} showSearch optionFilterProp="label" placeholder="选择已启用的检测模型" options={models.map(model=>({value:model.id,label:model.name}))} onChange={id=>form.setFieldValue('labels',models.find(row=>row.id===id)?.labels||[])}/>
            </Form.Item>
            <Form.Item name="labels" label="检测标签" normalize={(labels:string[])=>labels.map(label=>label.trim()).filter(Boolean)}
              rules={[{validator:(_,labels:string[]=[])=>labels.length<=100&&labels.every(label=>label.length<=100)?Promise.resolve():Promise.reject(Error('最多100个标签，每个不超过100字符'))}]}
              extra="输入后按回车，可用分号或逗号分隔；留空使用模型配置。仅用于本次预览，开放词汇模型建议用英文。">
              <Select aria-label="预览检测标签" mode="tags" tokenSeparators={[';','；',',','，']} open={false} placeholder="如 electric bicycle；person" options={(model?.labels||[]).map(label=>({value:label,label}))}/>
            </Form.Item>
            {ready&&!models.length&&<Alert type="info" title="暂无可用检测模型" action={<Link to="/models">配置检测模型</Link>}/>}
          </>:<CheckpointFields form={form} busy={busy} preview/>}
          <div className="model-preview-thresholds">
            {hasConfidence&&<Form.Item name="confidence" label="置信度阈值" rules={[{required:true}]}><InputNumber aria-label="预览置信度" min={0} max={1} step={.05}/></Form.Item>}
            {hasNms&&<Form.Item name="iou" label="NMS IoU" rules={[{required:true}]}><InputNumber aria-label="预览NMS IoU" min={0} max={1} step={.05}/></Form.Item>}
          </div>
          {!hasConfidence&&<Alert type="info" showIcon style={{marginBottom:16}} title="此模型不提供检测置信度，也不执行 NMS；保留全部检测结果。"/>}
          {hasConfidence&&!hasNms&&<p className="workspace-help">此接口不支持单独设置 NMS IoU。</p>}
          <input type="file" ref={picker} className="model-preview-picker" hidden multiple accept=".jpg,.jpeg,.png,.bmp,.webp,.zip,.tar,.tar.gz,.tgz" aria-label="选择模型预览图片" onChange={event=>{choose(Array.from(event.target.files||[]));event.target.value='';}}/>
          <input type="file" ref={element=>{folder.current=element;element?.setAttribute('webkitdirectory','');}} className="model-preview-picker" hidden multiple aria-label="选择模型预览文件夹" onChange={event=>{choose(Array.from(event.target.files||[]),true);event.target.value='';}}/>
          <Button className="model-preview-drop" icon={<UploadOutlined/>} disabled={busy} onClick={()=>picker.current?.click()}
            onDragOver={event=>event.preventDefault()} onDrop={event=>{event.preventDefault();choose(Array.from(event.dataTransfer.files));}}>选择图片或压缩包</Button>
          <Button disabled={busy||!ready} onClick={()=>folder.current?.click()}>选择文件夹</Button>
          <p className="workspace-help">支持 JPG / PNG / BMP / WebP 和 ZIP / TAR / TAR.GZ / TGZ；文件夹请用“选择文件夹”。{config&&`每批最多${config.max_images}张（含解包后），上传合计${formatBytes(config.max_upload_size)}，展开图片合计${formatBytes(config.max_expanded_size)}，单图${formatBytes(config.max_image_size)}。`}压缩包仅提取图片，其他文件忽略。</p>
          {selectionNote&&<p className="workspace-help">{selectionNote}</p>}
          {!!files.length&&<><div>已选择 {files.length} 个文件 <Button type="link" onClick={()=>{setFiles([]);setSelectionNote('');}}>清空所选</Button></div>
            <ul className="model-preview-files">{files.slice(0,50).map((file,index)=><li key={index}>{file.webkitRelativePath||file.name}<Button type="text" size="small" aria-label={`移除预览文件 ${index+1}`} onClick={()=>setFiles(current=>current.filter((_,i)=>i!==index))}>移除</Button></li>)}{files.length>50&&<li>另有 {files.length-50} 个文件待上传</li>}</ul></>}
          {uploadProgress!==undefined&&<><Progress percent={uploadProgress} size="small"/><p className="workspace-help">{uploadProgress<100?'正在上传文件…':'上传完成，正在检查压缩包并准备推理…'}</p><Button onClick={()=>uploadController.current?.abort()} disabled={false}>停止上传请求</Button></>}
          <Button aria-label="开始模型预览" type="primary" block htmlType="submit" loading={busy} disabled={!ready||!files.length||busy}>开始模型预览</Button>
          <p className="workspace-help model-preview-note">上传后展示检测框；模型提供有效置信度时同时显示分数。图片仅用于预览，不进入图片库，不生成标注、描述或分类。</p>
        </Form>
      </Card>
      <Card title="检测效果" className="model-preview-results" extra={<Checkbox checked={showBoxes} onChange={event=>setShowBoxes(event.target.checked)}>显示检测框</Checkbox>}>
        <Select aria-label="打开最近模型预览" placeholder="打开最近预览（刷新页面后可继续查看）" value={runId||undefined} disabled={busy} onChange={open} className="model-preview-history"
          options={history.map(run=>({value:run.id,label:`${run.name} · ${run.model.name} · ${validationStatusLabels[run.status]} · ${new Date(run.created_at).toLocaleString()}`}))}/>
        {!detail?<Empty description={runId?'正在读取预览…':'选择模型并上传图片，即可查看检测效果'}/>:<>
          <div className="model-preview-summary"><Space wrap><strong>{detail.model.name}</strong><Tag>{validationStatusLabels[detail.status]}</Tag><span>{detail.model.score_type==='uncalibrated'?'置信度：未提供':`置信度 ≥ ${detail.config.inference_confidence}`}</span></Space>
            {!!detail.config.labels?.length&&<p className="workspace-help">本次标签：{detail.config.labels.join('；')}</p>}
            {detail.model.source==='checkpoint'&&<p className="workspace-help">权重：{detail.model.weight_path} · 设备：{detail.model.device}</p>}
            <Space wrap><Button disabled={busy} onClick={()=>setRefresh(n=>n+1)}>刷新结果</Button>
              {(activeValidationRun(detail)||detail.remote_active)&&<Button disabled={busy} onClick={()=>void change('cancel')}>取消 / 确认停止</Button>}
              {!activeValidationRun(detail)&&detail.failed_count>0&&<Button disabled={busy} onClick={()=>void change('retry')}>重试失败图片</Button>}
              <Popconfirm title="删除本次预览和临时图片？" description="运行中会先取消；远端停止确认后才删除。" onConfirm={()=>change('delete')}><Button danger disabled={busy}>删除预览</Button></Popconfirm>
            </Space>
          </div>
          <Progress percent={Math.round(detail.processed/Math.max(1,detail.total)*100)} status={detail.failed_count?'exception':undefined} format={()=>`${detail.processed} / ${detail.total}`}/>
          {detail.error&&<Alert type="warning" title={detail.error}/>}
          {detail.remote_active&&detail.status!=='pending'&&detail.status!=='running'&&<Alert type="warning" title="远端停止待确认，请点击取消 / 确认停止；资源占用仍保留"/>}
          <div className="model-preview-gallery" aria-label="选择结果图片">{detail.items.map(row=><Button key={row.id} disabled={!pageReady} className={row.id===item?.id?'is-selected':''} onClick={()=>navigation.select(row.id)}>
            <span>{row.original_name}</span><small>{validationStatusLabels[row.status]}{row.status==='completed'?` · ${row.predictions.length} 个目标`:''}</small>
          </Button>)}</div>
          <Pagination current={imagePage} pageSize={20} total={detail.total} responsive showSizeChanger={false} onChange={page=>{navigation.select('');changePage(page);}} showTotal={total=>`共 ${total} 张图片 · 第 ${imagePage} / ${Math.max(1,Math.ceil(total/20))} 页（20 张/页）`}/>
          <Space wrap><Button aria-label="上一张图片" disabled={!navigation.canPrevious} onClick={navigation.previous}>← 上一张</Button><span>{pageReady?`${navigation.position} / ${detail.total}`:'正在读取…'} · 左右方向键切图</span><Button aria-label="下一张图片" disabled={!navigation.canNext} onClick={navigation.next}>下一张 →</Button></Space>
          {item&&<><p>{item.original_name} · {item.width} × {item.height}</p><PreviewDetectionImage key={`${detail.id}:${item.id}`} runId={detail.id} item={item} showBoxes={showBoxes} hasConfidence={detail.model.score_type!=='uncalibrated'}/></>}
          <p className="workspace-help model-preview-note">本次仅预览预测结果；未提供标注真值，不计算准确率、召回率或 mAP。</p>
        </>}
      </Card>
    </div>
  </div>;
}
