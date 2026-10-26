import { useEffect,useState } from 'react';
import { Link,useSearchParams } from 'react-router-dom';
import { Alert,Button,Card,Collapse,Drawer,Form,Input,Modal,Select,Space,Table,Tabs,Tag,Typography } from 'antd';
import ModelDeployment,{DeploymentFields} from '../components/ModelDeployment';
import DeploymentLogs,{DeploymentSshLinks} from '../components/DeploymentLogs';
import {deploymentBusy,deploymentError,deploymentNames} from '../utils/modelDeployment';
import { archiveModel,createDeploymentBatch,deploymentBatchAction,getDeployment,getDeployments,getDeploymentServers,getDistributionSources,getModelReleases,getServerTargets,inspectServerTarget,retryModelArchive,saveServerTarget,getDeploymentBatch } from '../api/modelDeployments';
import type { DeploymentInput,DeploymentServer,ModelDeployment as Record,ModelRelease,ServerTarget } from '../api/modelDeployments';
import { getTrainingArtifacts,getTrainingRun } from '../api/training';
import type { TrainingRun } from '../api/training';

type Source=TrainingRun&{runtimes?:DeploymentInput['runtime'][]};
type TargetForm=DeploymentInput&{name:string;kind:'model'|'image';runtime_options:string};
const runtimeOptions=[{value:'triton_plan',label:'小模型 · Triton TensorRT'},{value:'triton_vllm',label:'大模型 · Triton vLLM backend'},{value:'vllm',label:'向量 · vLLM'}];
const versionStates:{[key:string]:string}={unmanaged:'未纳管 / 版本未知',unknown:'待核对',verified:'全部实例已核验',recorded:'文件已核对，服务待核验',observed:'已读取运行镜像'};
const archiveStates:{[key:string]:string}={archiving:'正在拉取并校验',ready:'已归档',failed:'归档失败',interrupted:'归档中断'};
const localTime=(value?:string)=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'尚未核对';

export default function ModelDeploymentsPage(){
  const [params]=useSearchParams();
  const [admin]=useState(()=>{try{return JSON.parse(localStorage.getItem('user')||'null')?.role==='admin';}catch{return false;}});
  const [archiveForm]=Form.useForm<{run_id:string;name:string;version:string;runtime:DeploymentInput['runtime'];artifact_path:string}>(),[targetForm]=Form.useForm<TargetForm>();
  const selectedRun=Form.useWatch('run_id',archiveForm),runtime=Form.useWatch('runtime',archiveForm),targetKind=Form.useWatch('kind',targetForm)||'model';
  const [tab,setTab]=useState('releases'),[servers,setServers]=useState<DeploymentServer[]>([]),[sources,setSources]=useState<Source[]>([]),[paths,setPaths]=useState<string[]>([]);
  const [sourcePage,setSourcePage]=useState(1),[sourceMore,setSourceMore]=useState(false),[releasePage,setReleasePage]=useState(1),[releaseMore,setReleaseMore]=useState(false);
  const [releases,setReleases]=useState<ModelRelease[]>([]),[targets,setTargets]=useState<ServerTarget[]>([]),[records,setRecords]=useState<Record[]>([]),[recordPage,setRecordPage]=useState(1),[recordMore,setRecordMore]=useState(false);
  const [kind,setKind]=useState<'model'|'image'>('model'),[targetIds,setTargetIds]=useState<string[]>([]),[release,setRelease]=useState<ModelRelease>(),[image,setImage]=useState('');
  const [selected,setSelected]=useState<Record>(),[batch,setBatch]=useState<string>(),[batchPreview,setBatchPreview]=useState<Record[]>([]),[confirm,setConfirm]=useState<'activate'|'rollback'>();
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(''),[reload,setReload]=useState(0);
  const [editing,setEditing]=useState<string>(),[targetOpen,setTargetOpen]=useState<string[]>([]);
  const [logTarget,setLogTarget]=useState<ServerTarget>();
  const refresh=()=>setReload(value=>value+1);
  useEffect(()=>{if(!admin)return;const controller=new AbortController();getDeploymentServers(controller.signal).then(setServers).catch(cause=>{if(!controller.signal.aborted)setError(deploymentError(cause));});return()=>controller.abort();},[admin]);
  useEffect(()=>{if(!admin)return;const controller=new AbortController();
    getDistributionSources(sourcePage,controller.signal).then(result=>{setSources(values=>[...new Map([...values,...result.items].map(row=>[row.id,row])).values()]);setSourceMore(result.has_more);})
      .catch(cause=>{if(!controller.signal.aborted)setError(deploymentError(cause));});return()=>controller.abort();
  },[sourcePage,admin]);
  useEffect(()=>{const id=params.get('run_id');if(!admin||!id)return;let live=true;
    getTrainingRun(id).then(run=>{if(!live)return;setSources(values=>values.some(value=>value.id===id)?values:[run,...values]);const modelKind=run.source.model_kind||run.config.model_kind||run.kind;
      archiveForm.setFieldsValue({run_id:id,name:run.name,runtime:modelKind==='wemm'?'vllm':['swift_sft','model_merge'].includes(modelKind)?'triton_vllm':'triton_plan',artifact_path:run.kind==='model_export'?'model.engine':run.kind==='model_merge'?'merged':''});})
      .catch(cause=>{if(live)setError(deploymentError(cause));});return()=>{live=false;};
  },[params,archiveForm,admin]);
  useEffect(()=>{if(!admin||!selectedRun)return;let live=true;
    getTrainingArtifacts(selectedRun).then(result=>{if(live)setPaths([...new Set(result.files.flatMap(file=>runtime==='triton_plan'?/\.(engine|plan)$/.test(file.path)?[file.path]:[]:/(^|\/)config\.json$/.test(file.path)?[file.path.replace(/\/?config\.json$/,'')]:[]))]);})
      .catch(cause=>{if(live)setError(deploymentError(cause));});return()=>{live=false;};
  },[selectedRun,runtime,admin]);
  useEffect(()=>{if(!admin)return;const controller=new AbortController();
    Promise.all([getModelReleases(releasePage,controller.signal),getServerTargets(controller.signal),getDeployments(undefined,recordPage,controller.signal,batch)]).then(([versions,servers,jobs])=>{
      setReleases(versions.items);setReleaseMore(versions.has_more);setTargets(servers.items);setRecords(jobs.items);setRecordMore(jobs.has_more);
    }).catch(cause=>{if(!controller.signal.aborted)setError(deploymentError(cause));});return()=>controller.abort();
  },[admin,releasePage,recordPage,batch,reload]);
  useEffect(()=>{if(!admin||!releases.some(row=>row.status==='archiving')&&!records.some(row=>deploymentBusy.includes(row.status)))return;
    const timer=setInterval(refresh,5000);return()=>clearInterval(timer);
  },[admin,releases,records]);
  useEffect(()=>{if(!selected||!deploymentBusy.includes(selected.status))return;const controller=new AbortController();
    const timer=setInterval(()=>{getDeployment(selected.id,controller.signal).then(setSelected).catch(cause=>{if(!controller.signal.aborted)setError(deploymentError(cause));});},5000);
    return()=>{clearInterval(timer);controller.abort();};
  },[selected]);
  const perform=async(name:string,work:()=>Promise<void>)=>{if(busy)return;setBusy(name);setError('');setNotice('');try{await work();refresh();}catch(cause){setError(deploymentError(cause));}finally{setBusy('');}};
  const saveTarget=(values:TargetForm)=>perform('target',async()=>{
    const common={environment:values.environment,control_server:values.environment!=='k8s'&&values.kind==='model'?values.file_server:values.control_server,
      containers:values.environment==='k8s'?[]:values.containers,namespace:values.namespace,workload_kind:values.workload_kind,workload:values.environment==='k8s'?values.workload:'',container:values.container};
    const config=values.kind==='image'?{...common,...(values.environment==='k8s'?{preload_playbook:values.preload_playbook||'',preload_inventory:values.preload_inventory||''}:{})}:{...common,runtime:values.runtime,file_server:values.file_server,repository:values.repository,model_name:values.model_name,
      triton_config:values.triton_config||'',runtime_config:values.runtime_options?JSON.parse(values.runtime_options):{}};
    await saveServerTarget({kind:values.kind,name:values.name,target:config},editing);setEditing(undefined);targetForm.resetFields();setTargetOpen([]);setTargetIds([]);setNotice('服务器目标已保存，可在下方多选。');
  });
  const createBatch=()=>perform('batch',async()=>{
    const result=await createDeploymentBatch({kind,target_ids:targetIds,...(kind==='model'?{release_id:release?.id}:{image})});
    setBatch(result.batch_id);setRecordPage(1);setRecords(result.items);setTab('records');setNotice(`已创建 ${result.items.length} 个目标记录，请先准备，再手动生效。`);
  });
  const bulk=(operation:string)=>perform('bulk',async()=>{
    if(!batch)return;const result=await deploymentBatchAction(batch,operation);const failed=result.results.filter(row=>row.error);
    if(failed.length)setError(failed.map(row=>`${row.id.slice(0,8)}：${row.error}`).join('；'));
    else setNotice('批次操作已提交，请查看每个目标的独立结果。');setConfirm(undefined);
  });
  const preview=(operation:'activate'|'rollback')=>perform('preview',async()=>{if(batch){const result=await getDeploymentBatch(batch);setBatchPreview(result.items);setConfirm(operation);}});
  const pager=(page:number,more:boolean,set:(page:number)=>void)=><Space><Button disabled={page===1} onClick={()=>set(page-1)}>上一页</Button><Typography.Text>第 {page} 页</Typography.Text><Button disabled={!more} onClick={()=>set(page+1)}>下一页</Button></Space>;
  const selectedSource=sources.find(row=>row.id===selectedRun);
  const readyReleases=[...new Map([...releases.filter(row=>row.status==='ready'),...(release?[release]:[])].map(row=>[row.id,row])).values()];
  if(!admin)return <Alert type="warning" title="模型下发及镜像更新仅管理员可用"/>;
  return <Space orientation="vertical" size="middle" style={{width:'100%',minWidth:0,overflowWrap:'anywhere'}} styles={{item:{minWidth:0,maxWidth:'100%'}}}>
    <Alert type="info" showIcon title="先准备并校验，再手动生效" description="模型统一保存在平台后端；每个服务器目标记录自己的模型版本和运行镜像。同一服务器可保存多个模型槽位或服务。"/>
    <Link to="/gpu-terminal?source=deployment" target="_blank" rel="noopener noreferrer">SSH调试服务器</Link>
    {error&&<Alert closable onClose={()=>setError('')} type="error" showIcon title={error}/>} {notice&&<Alert type="success" title={notice}/>}
    <Tabs activeKey={tab} onChange={setTab} items={[
      {key:'releases',label:'模型版本库',children:<Space orientation="vertical" style={{width:'100%'}}>
        <Card title="拉取已导出的模型到本地"><Form name="model-archive" form={archiveForm} layout="vertical" disabled={!!busy} initialValues={{runtime:'triton_plan',artifact_path:'model.engine'}} onFinish={values=>void perform('archive',async()=>{await archiveModel(values);setNotice('已开始后台归档，校验通过后可用于多服务器下发。');})}>
          <Form.Item name="run_id" label="来源训练 / 导出任务" rules={[{required:true}]}><Select showSearch optionFilterProp="label" options={sources.filter(row=>!row.runtimes||row.runtimes.length).map(row=>({value:row.id,label:`${row.name} · ${row.kind}`}))}
            onChange={id=>{setPaths([]);const row=sources.find(value=>value.id===id);if(row)archiveForm.setFieldsValue({name:row.name,runtime:row.runtimes?.[0]||'triton_plan',artifact_path:row.kind==='model_export'?'model.engine':row.kind==='model_merge'?'merged':''});}}/></Form.Item>
          {sourceMore&&<Button onClick={()=>setSourcePage(value=>value+1)}>加载更多来源任务</Button>}
          <Form.Item name="runtime" label="模型类型" rules={[{required:true}]}><Select onChange={()=>setPaths([])} options={runtimeOptions.map(option=>({...option,disabled:!!selectedSource?.runtimes&&!selectedSource.runtimes.includes(option.value as DeploymentInput['runtime'])}))}/></Form.Item>
          <Form.Item name="name" label="模型名称" rules={[{required:true,max:100}]}><Input/></Form.Item>
          <Form.Item name="version" label="版本号" rules={[{required:true,max:100}]} extra="同一模型的版本号不可重复，例如 v20261024-01；归档后保留来源和SHA256。"><Input/></Form.Item>
          {paths.length>0&&<Form.Item label="从产物选择"><Select aria-label="选择归档产物" options={paths.map(path=>({value:path,label:path}))} onChange={path=>archiveForm.setFieldValue('artifact_path',path)}/></Form.Item>}
          <Form.Item name="artifact_path" label="产物相对文件 / 完整模型目录" rules={[{required:true}]} extra="TensorRT选择engine/plan；大模型和向量选择完整权重目录，LoRA需先合并。"><Input/></Form.Item>
          <Button type="primary" htmlType="submit" loading={busy==='archive'}>拉取并保存版本</Button>
        </Form></Card>
        <Table rowKey="id" size="small" dataSource={releases} pagination={false} scroll={{x:850}} columns={[
          {title:'模型版本',render:(_,row)=><><strong>{row.name}</strong><div>{row.version}</div></>},{title:'归档状态',render:(_,row)=><><Tag color={row.status==='ready'?'success':undefined}>{archiveStates[row.status]||row.status}</Tag>{row.error&&<div>{row.error}</div>}</>},
          {title:'本地记录',render:(_,row)=><><div>{row.archive.bytes?`${(row.archive.bytes/1024**3).toFixed(3)} GiB`:'尚未完成'}</div><Typography.Text type="secondary" copyable={!!row.archive.sha256}>{row.archive.sha256}</Typography.Text><div>{row.archive.path}</div><div>{localTime(row.archive.archived_at)}</div></>},
          {title:'操作',render:(_,row)=><Space wrap><Button disabled={row.status!=='ready'} onClick={()=>{setRelease(row);setKind('model');setTargetIds([]);setTab('targets');}}>选择服务器下发</Button>{['failed','interrupted'].includes(row.status)&&<Button disabled={!!busy} onClick={()=>void perform('retry',async()=>{await retryModelArchive(row.id);})}>重试归档</Button>}</Space>},
        ]}/>{pager(releasePage,releaseMore,setReleasePage)}
      </Space>},
      {key:'targets',label:'服务器版本与批量下发',children:<Space orientation="vertical" size="middle" style={{width:'100%'}}>
        <Collapse activeKey={targetOpen} onChange={keys=>setTargetOpen(Array.isArray(keys)?keys:[keys])} items={[{key:'target',label:editing?'编辑服务器目标':'添加服务器目标',children:<Form name="deployment-target" form={targetForm} layout="vertical" initialValues={{kind:'model',environment:'docker',runtime:'triton_plan',repository:'/ai/chen/code/huggingface/aiseefor_model',namespace:'ai',workload_kind:'StatefulSet',container:'triton-server',containers:[]}} disabled={!!busy} onFinish={values=>void saveTarget(values)}>
          <Form.Item name="name" label="目标名称" rules={[{required:true,max:100}]}><Input placeholder="例如 杭州检测模型 / 丽水Kafka worker"/></Form.Item>
          <Form.Item name="kind" label="目标用途"><Select options={[{value:'model',label:'模型下发'},{value:'image',label:'镜像更新'}]} onChange={value=>targetForm.setFieldValue('container',value==='image'?'nexusai':'triton-server')}/></Form.Item>
          <DeploymentFields form={targetForm} servers={servers} kind={targetKind}/><Space><Button type="primary" htmlType="submit" loading={busy==='target'}>保存服务器目标</Button>{editing&&<Button onClick={()=>{setEditing(undefined);targetForm.resetFields();setTargetOpen([]);}}>取消编辑</Button>}</Space>
        </Form>}]} />
        <Select aria-label="批量操作类型" value={kind} style={{width:220}} options={[{value:'model',label:'模型下发'},{value:'image',label:'镜像更新'}]} onChange={value=>{setKind(value);setTargetIds([]);}}/>
        {kind==='model'?<Select aria-label="待下发模型版本" style={{width:'100%'}} value={release?.id} placeholder="选择已完成本地归档的版本" options={readyReleases.map(row=>({value:row.id,label:`${row.name} · ${row.version}`}))} onChange={id=>{setRelease(readyReleases.find(row=>row.id===id));setTargetIds([]);}}/>:<><Input aria-label="目标镜像版本" value={image} onChange={event=>setImage(event.target.value)} placeholder="仓库/镜像:明确版本 或 仓库/镜像@sha256:摘要"/><Alert type="info" title="K8s先用本环境Ansible剧本预分发到全部节点，逐节点核验后手动生效；Docker更新所选Triton/vLLM、NexusAI HTTP或Kafka worker。"/></>}
        <Table rowKey="id" size="small" dataSource={targets.filter(row=>row.kind===kind)} pagination={{pageSize:20}} scroll={{x:950}} rowSelection={{selectedRowKeys:targetIds,preserveSelectedRowKeys:true,onChange:keys=>setTargetIds(keys.map(String)),getCheckboxProps:row=>({disabled:kind==='model'&&!!release&&row.target.runtime!==release.runtime})}} columns={[
          {title:'服务器 / 目标',render:(_,row)=><><strong>{row.name}</strong><div>{row.target.control_server}</div>{row.target.file_server&&row.target.file_server!==row.target.control_server&&<div>文件：{row.target.file_server}</div>}</>},
          {title:'环境 / 服务',render:(_,row)=><><Tag>{row.target.environment}</Tag><div>{row.kind==='model'?row.target.model_name:row.target.workload||row.target.containers.join('、')}</div>{row.kind==='model'&&<Typography.Text type="secondary">{row.target.repository}</Typography.Text>}</>},
          {title:'当前版本记录',render:(_,row)=><><strong>{row.current.version||row.current.image||[...new Set(row.current.instances?.map(value=>value.image))].join('、')||'版本未知'}</strong><div>{versionStates[row.current.state]||row.current.state}</div><div>{localTime(row.current.checked_at)}</div>{row.current.instances?.map(instance=><div key={instance.uid}><Typography.Text type="secondary">{instance.name} · {instance.image_id}</Typography.Text></div>)}</>},
          {title:'最近操作',render:(_,row)=>row.latest?<Button onClick={()=>setSelected(row.latest)}>{deploymentNames[row.latest.status]||row.latest.status}</Button>:'暂无'},
          {title:'操作',render:(_,row)=><Space wrap><Button onClick={()=>setLogTarget(row)}>实时日志</Button><DeploymentSshLinks control={row.target.control_server} file={row.target.file_server}/><Button disabled={!!busy} onClick={()=>void perform('inspect',async()=>{await inspectServerTarget(row.id);setNotice('已读取目标状态；文件版本核对不代表全部推理实例已重新加载。');})}>读取当前版本</Button><Button disabled={!!busy} onClick={()=>{setEditing(row.id);setTargetOpen(['target']);targetForm.setFieldsValue({...row.target,name:row.name,kind:row.kind,runtime_options:JSON.stringify(row.target.runtime_config||{})});}}>编辑目标</Button></Space>},
        ]}/>
        <Space wrap><Typography.Text>已选 {targetIds.length} 个目标</Typography.Text><Button type="primary" disabled={!!busy||!targetIds.length||kind==='model'&&!release||kind==='image'&&!image.trim()} loading={busy==='batch'} onClick={()=>void createBatch()}>创建批量任务</Button></Space>
      </Space>},
      {key:'records',label:'下发与镜像记录',children:<Space orientation="vertical" size="middle" style={{width:'100%'}}>
        {batch&&<Card size="small" title={`批次 ${batch}`} extra={<Button onClick={()=>{setBatch(undefined);setRecordPage(1);}}>查看全部记录</Button>}>
          <Space wrap><Button disabled={!!busy} onClick={()=>void bulk('transfer')}>准备本批次</Button><Button type="primary" disabled={!!busy} onClick={()=>void preview('activate')}>本批次生效</Button><Button danger disabled={!!busy} onClick={()=>void preview('rollback')}>本批次恢复旧版本</Button><Button disabled={!!busy} onClick={()=>void bulk('refresh')}>核对本批次</Button></Space>
        </Card>}
        <Button disabled={!!busy} onClick={refresh}>刷新记录</Button>
        <Table rowKey="id" size="small" dataSource={records} pagination={false} scroll={{x:850}} columns={[
          {title:'类型 / 版本',render:(_,row)=><><Tag>{row.kind==='image'?'镜像':'模型'}</Tag><div>{row.source.name||row.source.image||row.target.model_name}</div><div>{row.source.version}</div></>},
          {title:'服务器',render:(_,row)=><>{row.target.control_server}<div>{row.target.workload||row.target.containers.join('、')}</div></>},
          {title:'状态',render:(_,row)=><><Tag color={row.status==='active'?'success':row.status==='staged'?'processing':undefined}>{deploymentNames[row.status]||row.status}</Tag>{row.error&&<div>{row.error}</div>}</>},
          {title:'时间',render:(_,row)=>localTime(row.created_at)},{title:'操作',render:(_,row)=><Space wrap><Button onClick={()=>setSelected(row)}>查看详情</Button>{row.batch_id&&<Button onClick={()=>{setBatch(row.batch_id);setRecordPage(1);}}>查看本批次</Button>}</Space>},
        ]}/>{pager(recordPage,recordMore,setRecordPage)}
      </Space>},
    ]}/>
    <Drawer open={!!selected} title="下发 / 镜像更新详情" size={1000} styles={{wrapper:{maxWidth:'100vw'}}} onClose={()=>setSelected(undefined)} destroyOnHidden>{selected&&<ModelDeployment key={selected.id} record={selected} onChange={row=>{setSelected(row);refresh();}}/>}</Drawer>
    <Drawer open={!!logTarget} title={logTarget?`${logTarget.name} · 实时日志`:'实时日志'} size={1000} styles={{wrapper:{maxWidth:'100vw'}}} onClose={()=>setLogTarget(undefined)} destroyOnHidden>
      {logTarget&&<Space orientation="vertical" style={{width:'100%'}}><DeploymentSshLinks control={logTarget.target.control_server} file={logTarget.target.file_server}/><DeploymentLogs key={logTarget.id} id={logTarget.id} target/></Space>}
    </Drawer>
    {confirm&&<Modal open title={confirm==='activate'?'确认本批次生效？':'确认本批次恢复旧版本？'} okText={confirm==='activate'?'确认生效':'确认恢复'} cancelText="返回" okButtonProps={{danger:confirm==='rollback'}} confirmLoading={busy==='bulk'} onCancel={()=>{if(!busy)setConfirm(undefined);}} onOk={()=>void bulk(confirm)}>
      <Typography.Paragraph>本批次共有 {batchPreview.length} 个目标，分别执行并记录结果。仅符合操作条件的记录会执行；已完成的记录跳过。期间可能影响服务。</Typography.Paragraph>
      <ul>{batchPreview.map(row=><li key={row.id}>{row.target.control_server} · {row.source.name||row.source.image||row.target.model_name} {row.source.version} · {deploymentNames[row.status]||row.status}</li>)}</ul>
      {error&&<Alert type="error" title={error}/>}</Modal>}
  </Space>;
}
