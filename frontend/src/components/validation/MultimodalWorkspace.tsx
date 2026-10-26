import { useEffect,useRef,useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { Alert,Button,Card,Empty,Form,Input,InputNumber,Modal,Pagination,Popconfirm,Progress,Select,Space,Table,Tag,Typography } from 'antd';
import api from '../../api/client';
import { getCurrentUser } from '../../api/auth';
import { getProjects,type Project } from '../../api/projects';
import { getModelServices,type ModelService } from '../../api/modelServices';
import { getTrainingCatalog } from '../../api/training';
import { createValidationSet,deleteValidationSet,refreshValidationSet,validationError,validationStatusLabels,type ValidationSet,type CheckpointInput } from '../../api/validation';
import { listMultimodalSets,listMultimodalRuns,multimodalDetail,multimodalRoot,startMultimodal,reviewCaption,type MultimodalKind,type MultimodalRun,type MultimodalItem,type ReviewVerdict } from '../../api/multimodalValidation';
import useImageNavigation from '../useImageNavigation';
import CheckpointFields from './CheckpointFields';

const percent=(value?:number|null)=>value==null?'—':`${(value*100).toFixed(2)}%`;
function ResultImage({path,name}:{path:string;name:string}){
  const [url,setUrl]=useState(''),[error,setError]=useState<{path:string;message:string}>();
  const current=useRef('');
  useEffect(()=>{
    const controller=new AbortController();
    api.get(path,{responseType:'blob',signal:controller.signal}).then(result=>{
      if(controller.signal.aborted)return;
      const next=URL.createObjectURL(result.data);const old=current.current;current.current=next;setUrl(next);if(old)URL.revokeObjectURL(old);
    }).catch(()=>{if(!controller.signal.aborted)setError({path,message:'图片读取失败，请重新打开此图'});});
    return()=>controller.abort();
  },[path]);
  useEffect(()=>()=>{if(current.current)URL.revokeObjectURL(current.current);},[]);
  return error?.path===path?<Alert type="error" title={error.message}/>:url?<img src={url} alt={name} style={{display:'block',maxWidth:'100%',maxHeight:520,margin:'auto',objectFit:'contain'}}/>:<div style={{minHeight:160}}>正在读取图片…</div>;
}

interface Values {source:'service'|'checkpoint';model_id?:string;checkpoint:CheckpointInput;weight_source:string;prompt:string;max_tokens:number;query_type:'text'|'image';query_text?:string;query_image_number:number}
export default function MultimodalWorkspace({kind,preview=false}:{kind:MultimodalKind;preview?:boolean}){
  const [form]=Form.useForm<Values>(),[setForm]=Form.useForm();const [params,setParams]=useSearchParams();
  const [admin,setAdmin]=useState(false),[ready,setReady]=useState(false),[models,setModels]=useState<ModelService[]>([]),[projects,setProjects]=useState<Project[]>([]);
  const [sets,setSets]=useState<ValidationSet[]>([]),[setCount,setSetCount]=useState(0),[setListPage,setSetPage]=useState(1);
  const [runs,setRuns]=useState<MultimodalRun[]>([]),[runCount,setRunCount]=useState(0),[runPage,setRunPage]=useState(1);
  const [setModal,setSetModal]=useState(false),[launch,setLaunch]=useState<ValidationSet>(),[files,setFiles]=useState<File[]>([]);
  const [detail,setDetail]=useState<{run:MultimodalRun;items:MultimodalItem[];page:number}>(),[page,setPage]=useState(1),[revision,setRevision]=useState(0);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[draft,setDraft]=useState({id:'',value:''}),[elapsed,setElapsed]=useState(0),[upload,setUpload]=useState<number>();
  const writing=useRef(false),picker=useRef<HTMLInputElement>(null);
  const source=Form.useWatch('source',form)||'service',queryType=Form.useWatch('query_type',form)||'text';
  const modelId=Form.useWatch('model_id',form),fixedCaption=source==='service'&&kind==='caption'&&models.find(m=>m.id===modelId)?.protocol==='model_service';
  const runId=params.get('semantic_run_id')||'',root=multimodalRoot(preview),title=kind==='caption'?'大模型图片描述':'向量检索';
  const active=!!detail&&['pending','running'].includes(detail.run.status);
  const navigation=useImageNavigation({scope:runId,items:detail?.run.id===runId&&detail.page===page?detail.items:undefined,page,pageSize:20,total:detail?.run.total||0,onPage:setPage,enabled:!busy&&!setModal&&!launch});
  const item=navigation.item,metrics=detail?.run.metrics;
  const comment=draft.id===item?.id?draft.value:item?.evaluation?.review?.comment||'';
  const selectedQuery=metrics?.queries?.[0];
  const [queryId,setQueryId]=useState(''),[hitItem,setHitItem]=useState<{id:string;name:string}>();
  const query=metrics?.queries?.find(q=>q.id===queryId)||selectedQuery;
  const open=(id:string)=>{setPage(1);setDetail(undefined);setHitItem(undefined);setQueryId('');setParams(previous=>{const next=new URLSearchParams(previous);next.set('semantic_run_id',id);return next;},{preventScrollReset:true});};
  useEffect(()=>{
    let alive=true;
    Promise.all([getCurrentUser(),getModelServices({kind,enabled:true}),getTrainingCatalog()]).then(async([user,rows,catalog])=>{
      const all:Project[]=[];
      if(!preview&&user.role==='admin')for(let n=1;;n++){
        const data=await getProjects({page:n,page_size:100,task_type:kind==='caption'?'caption':'vector'});all.push(...data.items);if(all.length>=data.total||!data.items.length)break;
      }
      if(!alive)return;setAdmin(user.role==='admin');setModels(rows.filter(m=>m.kind===kind&&m.enabled));setProjects(all);setReady(true);
      if(!form.isFieldTouched(['checkpoint','image']))form.setFieldValue(['checkpoint','image'],catalog[kind==='caption'?'swift_sft':'wemm']?.image||'');
    }).catch(e=>{if(alive)setError(validationError(e,'读取模型或项目失败'));});
    return()=>{alive=false;};
  },[kind,preview,form]);
  useEffect(()=>{
    if(preview)return;const controller=new AbortController();
    listMultimodalSets(kind,setListPage,controller.signal).then(data=>{setSets(data.items);setSetCount(data.total);}).catch(e=>{if(!controller.signal.aborted)setError(validationError(e,'读取验证集失败'));});
    return()=>controller.abort();
  },[kind,preview,setListPage,revision]);
  useEffect(()=>{
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
    const load=async()=>{try{const data=await listMultimodalRuns(kind,preview,runPage,controller.signal);if(controller.signal.aborted)return;setRuns(data.items);setRunCount(data.total);if(data.items.some(r=>['pending','running'].includes(r.status)))timer=setTimeout(load,3000);}catch(e){if(!controller.signal.aborted)setError(validationError(e,'读取任务失败'));}};
    void load();return()=>{controller.abort();clearTimeout(timer);};
  },[kind,preview,runPage,revision]);
  useEffect(()=>{
    if(!runId)return;const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{try{const data=await multimodalDetail(runId,preview,page,controller.signal);if(controller.signal.aborted)return;setDetail({...data,page});if(['pending','running'].includes(data.run.status))timer=setTimeout(poll,3000);}catch(e){if(!controller.signal.aborted)setError(validationError(e,'读取结果失败'));}};
    void poll();return()=>{controller.abort();clearTimeout(timer);};
  },[runId,preview,page,revision]);
  useEffect(()=>{if(!busy)return;const start=Date.now();const timer=setInterval(()=>setElapsed(Math.floor((Date.now()-start)/1000)),1000);return()=>clearInterval(timer);},[busy]);
  async function act(operation:()=>Promise<unknown>,after?:()=>void){
    if(writing.current)return;writing.current=true;setBusy(true);setError('');setElapsed(0);
    try{await operation();after?.();setRevision(n=>n+1);}catch(e){setError(validationError(e,'操作失败，请核对后重试'));}finally{writing.current=false;setBusy(false);}
  }
  const start=(values:Values)=>act(async()=>{
    if(preview&&!files.length)throw Error('请选择预览图片或压缩包');
    const input={task_type:kind,prompt:values.prompt,max_tokens:values.max_tokens,
      ...(values.source==='checkpoint'?{checkpoint:{...values.checkpoint,...(values.weight_source==='remote'?{training_run_id:undefined}:{})}}:{model_id:values.model_id}),
      ...(preview&&kind==='embedding'?(values.query_type==='image'?{query_image_index:values.query_image_number-1}:{query_text:values.query_text?.trim()}):{})};
    const run=await startMultimodal(input,preview,files,launch?.id,setUpload);open(run.id);setLaunch(undefined);setFiles([]);setUpload(undefined);
  });
  const change=(run:MultimodalRun,action:'cancel'|'retry'|'delete')=>act(async()=>{
    if(action==='delete'){await api.delete(`${root}/${run.id}`,{timeout:600000});if(runId===run.id){setDetail(undefined);setParams(previous=>{const next=new URLSearchParams(previous);next.delete('semantic_run_id');return next;},{preventScrollReset:true});}}
    else await api.post(`${root}/${run.id}/${action}`,undefined,{timeout:600000});
  });
  const download=()=>act(async()=>{
    const data=preview?new Blob([JSON.stringify(detail,null,2)],{type:'application/json'}):(await api.get(`${root}/${runId}/report`,{responseType:'blob',timeout:120000})).data;
    const url=URL.createObjectURL(data),a=document.createElement('a');a.href=url;a.download=`${kind}-${runId}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  });
  const modelForm=<Form form={form} layout="vertical" disabled={busy} onFinish={start} initialValues={{source:'service',prompt:kind==='caption'?'描述图片中可见的内容，不要猜测。':'',max_tokens:512,query_type:'text',query_image_number:1,weight_source:'training',checkpoint:{server_id:'default',kind,device:'cpu',image:'',class_names:[],accept_platform_model_license:false}}}>
    <Form.Item name="source" label="模型来源"><Select aria-label="语义模型来源" options={[{value:'service',label:'已部署的模型服务'},{value:'checkpoint',label:'服务器训练权重 / 合并模型',disabled:!admin}]}/></Form.Item>
    {source==='service'?<Form.Item name="model_id" label="模型服务" rules={[{required:true,message:'请选择模型服务'}]}><Select aria-label="语义模型服务" showSearch optionFilterProp="label" options={models.map(m=>({value:m.id,label:m.name}))} notFoundContent="没有已启用且用途匹配的模型，请到模型管理配置" onChange={id=>{if(kind==='caption'&&!form.isFieldTouched('prompt'))form.setFieldValue('prompt',models.find(m=>m.id===id)?.chat_prompts?.caption||'描述图片中可见的内容，不要猜测。');}}/></Form.Item>:<CheckpointFields form={form} busy={busy} preview={preview} taskType={kind}/>}
    {kind==='caption'&&<><Form.Item name="prompt" label="描述提示词" extra={fixedCaption?'此固定描述服务使用自身提示词，不能覆盖；自定义提示词请选择OpenAI视觉接口或服务器权重。':undefined} rules={[{required:true,whitespace:true}]}><Input.TextArea aria-label="描述提示词" rows={3} maxLength={8000} disabled={fixedCaption||busy}/></Form.Item><Form.Item name="max_tokens" label="最大输出 tokens"><InputNumber min={1} max={4096} disabled={fixedCaption||busy}/></Form.Item></>}
    {preview&&kind==='embedding'&&<><Form.Item name="query_type" label="查询方式"><Select aria-label="向量预览查询方式" options={[{value:'text',label:'文搜图'},{value:'image',label:'图搜图'}]}/></Form.Item>{queryType==='text'?<Form.Item name="query_text" label="查询文本" rules={[{required:true,whitespace:true}]}><Input.TextArea aria-label="向量查询文本" rows={2} maxLength={2000}/></Form.Item>:<Form.Item name="query_image_number" label="查询图片序号（从1开始）" extra="从本次上传展开后的图片中选择，自动排除相同内容图片的自身命中。" rules={[{required:true}]}><InputNumber aria-label="向量查询图片序号" min={1} max={5000} precision={0}/></Form.Item>}</>}
    {preview&&<><input ref={picker} type="file" multiple accept=".jpg,.jpeg,.png,.bmp,.webp,.zip,.tar,.tar.gz,.tgz" hidden onChange={e=>{setFiles(Array.from(e.target.files||[]));e.target.value='';}}/><Button disabled={busy} onClick={()=>picker.current?.click()}>选择图片或压缩包</Button><Typography.Paragraph>{files.length?`已选择 ${files.length} 个文件`:'尚未选择图片'}</Typography.Paragraph>{files.slice(0,5).map((file,i)=><div key={i}>{file.name}</div>)}</>}
    {busy&&<Alert type="info" title={`正在提交，已等待 ${elapsed} 秒`}/>}{upload!==undefined&&<Progress percent={upload}/>}
    <Button type="primary" htmlType="submit" loading={busy} disabled={!ready||busy||preview&&!files.length} style={{marginTop:16}}>{preview?'开始效果预览':'开始验证'}</Button>
  </Form>;
  return <Space orientation="vertical" size="middle" style={{width:'100%',minWidth:0}}>
    <Typography.Title level={3}>{title}{preview?'效果预览':'验证'}</Typography.Title>
    {error&&<Alert type="error" closable title={error} onClose={()=>setError('')} action={<Button disabled={busy} onClick={()=>setRevision(n=>n+1)}>刷新</Button>}/>}
    <Alert type="info" title={kind==='caption'?'查看人工描述与模型输出，按语义复核，不要求字面完全一致':'分别评测文搜图、图搜图；自动排除同内容自身命中'} description={kind==='caption'?'验证集只采用人工审核通过的非空图片描述，生成结果不回写原标注。':'使用已人工审核的匹配关系。未判定的检索结果显示“未判定”，不自动算误报；本次任务独立检索，不改原项目向量索引。'}/>
    {preview?<Card title="上传并推理">{modelForm}</Card>:<Card title="验证集" extra={<Button disabled={!ready||!admin||busy} onClick={()=>setSetModal(true)}>创建验证集</Button>}>
      <Table<ValidationSet> rowKey="id" dataSource={sets} scroll={{x:650}} pagination={{current:setListPage,pageSize:20,total:setCount,onChange:setSetPage,showSizeChanger:false}} columns={[
        {title:'名称',dataIndex:'name'},{title:'图片数',dataIndex:'total'},{title:'查询数',render:(_,s)=>kind==='embedding'?String(s.source.query_count||0):'—'},
        {title:'操作',render:(_,s)=><Space wrap><Button disabled={!admin||busy} onClick={()=>setLaunch(s)}>验证模型</Button><Button disabled={!admin||busy||!s.project_id} onClick={()=>void act(()=>refreshValidationSet(s.id))}>更新真值</Button><Popconfirm title="删除验证集及其历史任务？原图和项目标注保留。" onConfirm={()=>act(()=>deleteValidationSet(s.id))}><Button danger disabled={!admin||busy}>删除</Button></Popconfirm></Space>}
      ]}/>
    </Card>}
    <Card title={preview?'最近预览':'验证任务'} extra={<Button disabled={busy} onClick={()=>setRevision(n=>n+1)}>刷新任务</Button>}>
      <Table<MultimodalRun> rowKey="id" dataSource={runs} scroll={{x:800}} pagination={{current:runPage,pageSize:20,total:runCount,onChange:setRunPage,showSizeChanger:false}} columns={[
        {title:'名称',dataIndex:'name'},{title:'模型',render:(_,r)=>`${r.model.name} · ${r.model.weight_sha256?.slice(0,8)||'r'+r.model.revision}`},
        {title:'状态',render:(_,r)=><Tag color={r.status==='failed'?'error':r.status==='completed'?'success':'default'}>{validationStatusLabels[r.status]}</Tag>},
        {title:'已处理 / 总数',render:(_,r)=>`${r.processed} / ${r.total}`},{title:'错误',dataIndex:'error'},
        {title:'操作',render:(_,r)=><Space wrap><Button onClick={()=>open(r.id)}>查看结果</Button>{(admin||preview)&&<>{(['pending','running'].includes(r.status)||r.remote_active)&&<Button disabled={busy} onClick={()=>void change(r,'cancel')}>取消 / 确认停止</Button>}{!['pending','running'].includes(r.status)&&(r.failed_count>0||r.status==='canceled')&&<Button disabled={busy||r.remote_active} onClick={()=>void change(r,'retry')}>重试失败项</Button>}<Popconfirm title="删除此任务及结果？原图与模型保留。" onConfirm={()=>change(r,'delete')}><Button danger disabled={busy}>删除</Button></Popconfirm></>}</Space>}
      ]}/>
    </Card>
    {detail&&detail.run.id===runId&&<Card title="验证结果" extra={<Button disabled={busy} onClick={()=>void download()}>下载 JSON 报告</Button>}>
      <Space wrap><Tag>{validationStatusLabels[detail.run.status]}</Tag><strong>{detail.run.model.name}</strong><span>已处理 {detail.run.processed} / {detail.run.total} · 失败 {detail.run.failed_count}</span></Space>
      {detail.run.model.source==='checkpoint'&&<Typography.Paragraph type="secondary">权重：{detail.run.model.weight_path} · SHA256：{detail.run.model.weight_sha256} · 设备：{detail.run.model.device}</Typography.Paragraph>}
      {active&&<Alert type="info" title={detail.run.processed===0?'准备、上传或批量推理中，完成后统一取回结果':'正在处理，请稍候'}/>}
      {detail.run.error&&<Alert type="error" title={detail.run.error}/>}
      {metrics?.coverage&&!metrics.coverage.complete&&!active&&<Alert type="warning" title="结果覆盖不完整，不能代表整个验证集"/>}
      {kind==='caption'?<>
        {!preview&&<Typography.Paragraph>已复核 {metrics?.reviewed||0} 张；正确 {metrics?.correct||0}，错误 {metrics?.incorrect||0}，无法判断 {metrics?.uncertain||0}；人工复核通过率 {percent(metrics?.pass_rate)}（不含未复核和无法判断）。</Typography.Paragraph>}
        <Space wrap><Button disabled={!navigation.canPrevious} onClick={navigation.previous}>← 上一张</Button><span>{navigation.position} / {detail.run.total} · 方向键切图</span><Button disabled={!navigation.canNext} onClick={navigation.next}>下一张 →</Button></Space>
        <Pagination current={page} pageSize={20} total={detail.run.total} onChange={setPage} showSizeChanger={false}/>
        {item?<div style={{display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(min(100%,300px),1fr))',gap:20,marginTop:16}}>
          <ResultImage path={`${root}/${runId}/items/${item.id}/image`} name={item.original_name}/>
          <div><Typography.Text strong>{item.original_name}</Typography.Text>{item.error&&<Alert type="error" title={item.error}/>}
            {!preview&&<><Typography.Title level={5}>人工参考描述</Typography.Title><Typography.Paragraph style={{whiteSpace:'pre-wrap'}}>{item.truth[0]?.description||'—'}</Typography.Paragraph></>}
            <Typography.Title level={5}>模型描述</Typography.Title><Typography.Paragraph style={{whiteSpace:'pre-wrap'}}>{item.evaluation?.caption||validationStatusLabels[item.status]}</Typography.Paragraph>
            {admin&&!preview&&item.status==='completed'&&<><Input.TextArea aria-label="描述复核说明" value={comment} onChange={e=>setDraft({id:item.id,value:e.target.value})} maxLength={2000} placeholder="复核说明（选填）"/><Space wrap style={{marginTop:12}}>{(['correct','incorrect','uncertain'] as ReviewVerdict[]).map((verdict,i)=><Button key={verdict} disabled={busy} type={item.evaluation.review?.verdict===verdict?'primary':'default'} onClick={()=>void act(()=>reviewCaption(runId,item.id,verdict,comment))}>{['描述正确','描述错误','无法判断'][i]}</Button>)}</Space></>}
          </div>
        </div>:<Empty description="结果正在读取"/>}
      </>:<>
        {!preview&&<Table rowKey="kind" size="small" pagination={false} dataSource={(['text','image'] as const).map(k=>({kind:k,...metrics?.[k]}))} scroll={{x:650}} columns={[
          {title:'查询方式',dataIndex:'kind',render:k=>k==='text'?'文搜图':'图搜图'},{title:'有效查询',dataIndex:'evaluated_queries'},
          ...[1,5,10].map(k=>({title:`Hit@${k}`,dataIndex:`hit_at_${k}`,render:percent})),...[1,5,10].map(k=>({title:`Recall@${k}`,dataIndex:`recall_at_${k}`,render:percent})),{title:'MRR',dataIndex:'mrr',render:(v?:number|null)=>v==null?'—':v.toFixed(4)}
        ]}/>}
        <Select aria-label="查看检索查询" value={query?.id} style={{width:'100%',marginTop:16}} placeholder="完成推理后查看检索排名" onChange={id=>{setQueryId(id);setHitItem(undefined);}} options={(metrics?.queries||[]).map(q=>({value:q.id,label:`${q.kind==='text'?'文搜图：'+q.text:'图搜图：'+q.asset_id}`}))}/>
        {query?.excluded_reason&&<Alert type={preview?'info':'warning'} title={query.excluded_reason}/>}
        {query&&<Table rowKey="asset_id" dataSource={query.hits} pagination={false} scroll={{x:600}} columns={[
          {title:'排名',render:(_,__,i)=>i+1},{title:'图片',dataIndex:'original_name'},{title:'余弦相似度',dataIndex:'score',render:v=>v.toFixed(4)},
          {title:'人工真值',dataIndex:'judgment',render:v=>({correct:'匹配',incorrect:'不匹配',unknown:'未判定'}[v as string]||v)},
          {title:'操作',render:(_,h)=><Button onClick={()=>setHitItem({id:h.item_id,name:h.original_name})}>查看图片</Button>}
        ]}/>}
        {hitItem&&<><Typography.Title level={5}>{hitItem.name}</Typography.Title><ResultImage path={`${root}/${runId}/items/${hitItem.id}/image`} name={hitItem.name}/></>}
      </>}
    </Card>}
    {setModal&&<Modal open title={`创建${title}验证集`} footer={null} onCancel={()=>{if(!busy)setSetModal(false);}}>
      <Form form={setForm} layout="vertical" disabled={busy} initialValues={{project_id:params.get('project_id')||undefined}} onFinish={values=>act(()=>createValidationSet({...values,split:'val'}),()=>{setSetModal(false);setForm.resetFields();})}>
        <Form.Item name="name" label="验证集名称" rules={[{required:true,whitespace:true}]}><Input maxLength={200}/></Form.Item>
        <Form.Item name="project_id" label="标注项目" rules={[{required:true}]}><Select aria-label="语义验证项目" showSearch optionFilterProp="label" options={projects.map(p=>({value:p.id,label:p.project_name}))}/></Form.Item>
        <Alert type="info" title={kind==='caption'?'固定当前已人工审核的图片描述':'固定当前候选图片与已审核文搜图 / 图搜图匹配关系'} description="创建时间不能证明与训练集独立，请使用独立验证项目。"/>
        {busy&&<Typography.Paragraph>正在保存，已等待 {elapsed} 秒</Typography.Paragraph>}<Button type="primary" htmlType="submit" loading={busy} style={{marginTop:16}}>保存验证集</Button>
      </Form>
    </Modal>}
    {launch&&<Modal open title={`验证模型 · ${launch.name}`} footer={null} width={640} onCancel={()=>{if(!busy)setLaunch(undefined);}}>{modelForm}</Modal>}
  </Space>;
}
