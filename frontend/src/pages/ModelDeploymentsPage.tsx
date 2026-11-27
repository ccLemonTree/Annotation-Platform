import { useEffect,useState } from 'react';
import { Link,useSearchParams } from 'react-router-dom';
import { Alert,Button,Card,Collapse,Drawer,Form,Input,Modal,Popconfirm,Result,Select,Space,Table,Tabs,Tag,Typography } from 'antd';
import { useCurrentUser } from '../components/currentUser';
import ModelDeployment,{DeploymentFields} from '../components/ModelDeployment';
import DeploymentPlatforms from '../components/DeploymentPlatforms';
import CursorPager from '../components/CursorPager';
import DeploymentLogs,{DeploymentSshLinks} from '../components/DeploymentLogs';
import {deploymentBusy,deploymentError,deploymentNames} from '../utils/modelDeployment';
import { archiveModel,createDeploymentBatch,deploymentBatchAction,getDeployment,getDeployments,getDeploymentServers,getDistributionSources,getModelReleases,getServerTargets,inspectServerTarget,retryModelArchive,saveServerTarget,getDeploymentBatch,deleteDeployments } from '../api/modelDeployments';
import type { DeploymentInput,DeploymentServer,ModelDeployment as Record,ModelRelease,ServerTarget } from '../api/modelDeployments';
import { getTrainingArtifacts,getTrainingRun } from '../api/training';
import type { TrainingRun } from '../api/training';
import { getRepositoryModel,getRepositoryModels,repositoryBatchLabel } from '../api/modelRepository';
import type { RepositoryModel } from '../api/modelRepository';

type Source=TrainingRun&{runtimes?:DeploymentInput['runtime'][]};
type TargetForm=DeploymentInput&{name:string;kind:'model'|'image';runtime_options:string;server_copies?:string[]};
const runtimeOptions=[{value:'triton_vllm',label:'大模型 · Triton vLLM backend'},{value:'vllm',label:'向量 · vLLM'}];
const versionStates:{[key:string]:string}={unmanaged:'未纳管 / 版本未知',unknown:'待核对',verified:'全部实例已核验',recorded:'文件已核对，服务待核验',observed:'已读取运行镜像'};
const archiveStates:{[key:string]:string}={archiving:'正在拉取并校验',ready:'已归档',failed:'归档失败',interrupted:'归档中断'};
const localTime=(value?:string)=>value?new Date(value).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai'}):'尚未核对';

export default function ModelDeploymentsPage(){
  const [params]=useSearchParams();
  const admin=useCurrentUser()?.role==='admin';
  const [archiveForm]=Form.useForm<{run_id:string;name:string;version:string;runtime:DeploymentInput['runtime'];artifact_path:string}>(),[targetForm]=Form.useForm<TargetForm>();
  const selectedRun=Form.useWatch('run_id',archiveForm),runtime=Form.useWatch('runtime',archiveForm),targetKind=Form.useWatch('kind',targetForm)||'model';
  const [tab,setTab]=useState(params.get('legacy')?'targets':'platforms'),[servers,setServers]=useState<DeploymentServer[]>([]),[sources,setSources]=useState<Source[]>([]),[paths,setPaths]=useState<string[]>([]);
  const [requestedVersion,setRequestedVersion]=useState<string>();
  const [sourcePage,setSourcePage]=useState(1),[sourceMore,setSourceMore]=useState(false),[releasePage,setReleasePage]=useState(1),[releaseMore,setReleaseMore]=useState(false);
  const [releases,setReleases]=useState<ModelRelease[]>([]),[targets,setTargets]=useState<ServerTarget[]>([]),[records,setRecords]=useState<Record[]>([]),[recordPage,setRecordPage]=useState(1),[recordMore,setRecordMore]=useState(false);
  const [recordKind,setRecordKind]=useState<'model'|'image'>(),[recordVersion,setRecordVersion]=useState(''),[recordServer,setRecordServer]=useState('');
  const [deleteIds,setDeleteIds]=useState<string[]>([]);
  const [kind,setKind]=useState<'model'|'image'>('model'),[targetIds,setTargetIds]=useState<string[]>([]),[release,setRelease]=useState<ModelRelease>(),[image,setImage]=useState('');
  const [modelSource,setModelSource]=useState<'repository'|'full_model'>('repository'),[repositoryModel,setRepositoryModel]=useState<RepositoryModel>();
  const [repositoryModels,setRepositoryModels]=useState<RepositoryModel[]>([]),[repositoryPage,setRepositoryPage]=useState(1),[repositoryTotal,setRepositoryTotal]=useState(0);
  const [repositoryQuery,setRepositoryQuery]=useState(''),[gpuModel,setGpuModel]=useState<string>(),[gpuModels,setGpuModels]=useState<string[]>([]);
  const [selected,setSelected]=useState<Record>(),[batch,setBatch]=useState<string>(),[batchPreview,setBatchPreview]=useState<Record[]>([]),[confirm,setConfirm]=useState<'activate'|'rollback'>();
  const [error,setError]=useState(''),[notice,setNotice]=useState(''),[busy,setBusy]=useState(''),[reload,setReload]=useState(0);
  const [editing,setEditing]=useState<string>(),[targetOpen,setTargetOpen]=useState<string[]>([]);
  const [logTarget,setLogTarget]=useState<ServerTarget>();
  useEffect(()=>setDeleteIds([]),[recordKind,recordVersion,recordServer,batch]);
  const refresh=()=>setReload(value=>value+1);
  useEffect(()=>{if(!admin)return;const controller=new AbortController();getDeploymentServers(controller.signal).then(setServers).catch(cause=>{if(!controller.signal.aborted)setError(deploymentError(cause));});return()=>controller.abort();},[admin]);
  useEffect(()=>{if(!admin)return;const controller=new AbortController();
    getDistributionSources(sourcePage,controller.signal).then(result=>{setSources(values=>[...new Map([...values,...result.items].map(row=>[row.id,row])).values()]);setSourceMore(result.has_more);})
      .catch(cause=>{if(!controller.signal.aborted)setError(deploymentError(cause));});return()=>controller.abort();
  },[sourcePage,admin]);
  useEffect(()=>{const id=params.get('run_id');if(!admin||!id)return;let live=true;
    getTrainingRun(id).then(run=>{if(!live)return;
      if(!['model_merge','swift_sft','wemm'].includes(run.kind)){setNotice('小模型请先存入模型仓库，再从仓库选择下发。');return;}
      setTab('releases');setSources(values=>values.some(value=>value.id===id)?values:[run,...values]);const modelKind=run.source.model_kind||run.config.model_kind||run.kind;
      archiveForm.setFieldsValue({run_id:id,name:run.name,runtime:modelKind==='wemm'?'vllm':'triton_vllm',artifact_path:run.kind==='model_merge'?'merged':''});})
      .catch(cause=>{if(live)setError(deploymentError(cause));});return()=>{live=false;};
  },[params,archiveForm,admin]);
  useEffect(()=>{if(!admin||!selectedRun)return;let live=true;
    getTrainingArtifacts(selectedRun).then(result=>{if(live)setPaths([...new Set(result.files.flatMap(file=>/(^|\/)config\.json$/.test(file.path)?[file.path.replace(/\/?config\.json$/,'')]:[]))]);})
      .catch(cause=>{if(live)setError(deploymentError(cause));});return()=>{live=false;};
  },[selectedRun,runtime,admin]);
  useEffect(()=>{if(!admin)return;const controller=new AbortController();
    getRepositoryModels({page:repositoryPage,q:repositoryQuery||undefined,format:'tensorrt',gpu_model:gpuModel},controller.signal).then(result=>{
      setRepositoryModels(result.items);setRepositoryTotal(result.total);setGpuModels(result.gpu_models);
    }).catch(cause=>{if(!controller.signal.aborted)setError(deploymentError(cause));});return()=>controller.abort();
  },[admin,repositoryPage,repositoryQuery,gpuModel,reload]);
  useEffect(()=>{const id=params.get('repository_id');if(!admin||!id)return;const controller=new AbortController();
    getRepositoryModel(id,controller.signal).then(model=>{setRepositoryModel(model);setModelSource('repository');setKind('model');setTargetIds([]);setRequestedVersion('r:'+id);setTab(params.get('legacy')?'targets':'platforms');})
      .catch(cause=>{if(!controller.signal.aborted)setError(deploymentError(cause));});return()=>controller.abort();
  },[params,admin]);
  useEffect(()=>{if(!admin)return;const controller=new AbortController();
    Promise.all([getModelReleases(releasePage,controller.signal),getServerTargets(controller.signal),getDeployments(undefined,recordPage,controller.signal,batch,{kind:recordKind,version:recordVersion||undefined,server:recordServer||undefined})]).then(([versions,servers,jobs])=>{
      setReleases(versions.items);setReleaseMore(versions.has_more);setTargets(servers.items);setRecords(jobs.items);setRecordMore(jobs.has_more);
    }).catch(cause=>{if(!controller.signal.aborted)setError(deploymentError(cause));});return()=>controller.abort();
  },[admin,releasePage,recordPage,batch,recordKind,recordVersion,recordServer,reload]);
  useEffect(()=>{if(!admin||!batch){setBatchPreview([]);return;}const controller=new AbortController();
    getDeploymentBatch(batch,controller.signal).then(result=>setBatchPreview(result.items)).catch(cause=>{if(!controller.signal.aborted)setError(deploymentError(cause));});return()=>controller.abort();
  },[admin,batch,reload]);
  useEffect(()=>{
    const hasActiveTask=releases.some(row=>row.status==='archiving')||records.some(row=>deploymentBusy.includes(row.status))||batchPreview.some(row=>deploymentBusy.includes(row.status));
    if(!admin||!hasActiveTask)return;
    const onVisible=()=>{if(!document.hidden)refresh();};
    const timer=setInterval(()=>{if(!document.hidden)refresh();},5000);
    document.addEventListener('visibilitychange',onVisible);
    return()=>{clearInterval(timer);document.removeEventListener('visibilitychange',onVisible);};
  },[admin,releases,records,batchPreview]);
  useEffect(()=>{
    if(!selected||!deploymentBusy.includes(selected.status))return;
    const id=selected.id;
    const controller=new AbortController();
    let inFlight=false;
    const poll=()=>{
      if(inFlight||document.hidden||controller.signal.aborted)return;
      inFlight=true;
      getDeployment(id,controller.signal).then(setSelected).catch(cause=>{if(!controller.signal.aborted)setError(deploymentError(cause));}).finally(()=>{inFlight=false;});
    };
    const onVisible=()=>{if(!document.hidden)poll();};
    const timer=setInterval(poll,5000);
    document.addEventListener('visibilitychange',onVisible);
    return()=>{clearInterval(timer);controller.abort();document.removeEventListener('visibilitychange',onVisible);};
  },[selected]);
  const perform=async(name:string,work:()=>Promise<void>)=>{if(busy)return;setBusy(name);setError('');setNotice('');try{await work();refresh();}catch(cause){setError(deploymentError(cause));}finally{setBusy('');}};
  const saveTarget=(values:TargetForm)=>perform('target',async()=>{
    const primary=values.kind==='model'&&values.environment!=='k8s'?values.file_server:values.control_server;
    const names=editing?[primary]:[...new Set([primary,...(values.server_copies||[])])];
    const runtimeConfig=values.kind==='model'&&values.runtime_options?JSON.parse(values.runtime_options):{};
    const results=await Promise.allSettled(names.map(server=>{
      const common={environment:values.environment,control_server:server,containers:values.environment==='k8s'?[]:values.containers,
        namespace:values.namespace,workload_kind:values.workload_kind,workload:values.environment==='k8s'?values.workload:'',container:values.container};
      const config=values.kind==='image'?{...common,...(values.environment==='k8s'?{preload_playbook:values.preload_playbook||'',preload_inventory:values.preload_inventory||''}:{})}:{...common,runtime:values.runtime,file_server:values.environment==='k8s'?values.file_server:server,
        repository:values.repository,model_name:values.model_name,triton_config:values.triton_config||'',runtime_config:runtimeConfig};
      return saveServerTarget({kind:values.kind,name:values.name,target:config},editing);
    }));
    const failed=results.flatMap((result,index)=>result.status==='rejected'?[{server:names[index],error:deploymentError(result.reason)}]:[]),saved=names.length-failed.length;
    if(saved){setTargetIds([]);setNotice(`已保存 ${saved} 个服务器目标，可在下方多选。`);}
    if(failed.length){setError(failed.map(row=>`${row.server}：${row.error}`).join('；'));
      targetForm.setFieldsValue({...values,...(values.kind==='model'&&values.environment!=='k8s'?{file_server:failed[0].server}:{control_server:failed[0].server}),server_copies:failed.slice(1).map(row=>row.server)});
    }else{setEditing(undefined);targetForm.resetFields();setTargetOpen([]);}
  });
  const createBatch=()=>perform('batch',async()=>{
    const result=await createDeploymentBatch({kind,target_ids:targetIds,...(kind==='model'?modelSource==='repository'?{repository_id:repositoryModel?.id}:{release_id:release?.id}:{image})});
    setBatch(result.batch_id);setRecordPage(1);setRecords(result.items);setBatchPreview(result.items);setTab('records');
    const prepared=await deploymentBatchAction(result.batch_id,'transfer'),failed=prepared.results.filter(row=>row.error);
    setNotice(`已提交 ${result.items.length} 个平台的准备任务，后台自动检查、传输和校验；准备完成后确认生效。`);
    if(failed.length)setError(failed.map(row=>`${row.id.slice(0,8)}：${row.error}`).join('；'));
  });
  const bulk=(operation:string)=>perform('bulk',async()=>{
    if(!batch)return;const result=await deploymentBatchAction(batch,operation);const failed=result.results.filter(row=>row.error);
    if(failed.length)setError(failed.map(row=>`${row.id.slice(0,8)}：${row.error}`).join('；'));
    else setNotice('批次操作已提交，请查看每个目标的独立结果。');setConfirm(undefined);
  });
  const preview=(operation:'activate'|'rollback')=>perform('preview',async()=>{if(batch){const result=await getDeploymentBatch(batch);setBatchPreview(result.items);setConfirm(operation);}});
  const removeRecords=(ids:string[])=>perform('delete',async()=>{
    const result=await deleteDeployments(ids),deleted=new Set(result.deleted);
    setDeleteIds(values=>values.filter(id=>!deleted.has(id)));setRecords(values=>values.filter(row=>!deleted.has(row.id)));
    if(selected&&deleted.has(selected.id))setSelected(undefined);
    if(batch&&batchPreview.length&&batchPreview.every(row=>deleted.has(row.id)))setBatch(undefined);
    setBatchPreview(values=>values.filter(row=>!deleted.has(row.id)));setRecordPage(1);setNotice(`已删除 ${result.deleted.length} 条未生效记录。`);
  });
  const pager=(page:number,more:boolean,set:(page:number)=>void)=><CursorPager page={page} hasMore={more}
    onPrev={()=>set(page-1)} onNext={()=>set(page+1)}/>;
  const selectedSource=sources.find(row=>row.id===selectedRun);
  const readyReleases=[...new Map([...releases.filter(row=>row.status==='ready'&&row.runtime!=='triton_plan'),...(release?[release]:[])].map(row=>[row.id,row])).values()];
  const repositoryChoices=[...new Map([...(repositoryModel?[repositoryModel]:[]),...repositoryModels].map(row=>[row.id,row])).values()];
  const repositoryReason=(row:RepositoryModel)=>row.format!=='tensorrt'?'请先导出为TensorRT':row.status!=='ready'?'尚未就绪':!row.gpu_model||!row.tensorrt_version?'缺少GPU型号或TensorRT版本':'';
  const modelReady=modelSource==='repository'?!!repositoryModel&&!repositoryReason(repositoryModel):!!release;
  const targetRuntime=modelSource==='repository'?'triton_plan':release?.runtime;
  const staged=batchPreview.filter(row=>row.status==='staged').length,active=batchPreview.filter(row=>row.status==='active').length;
  const preparing=batchPreview.filter(row=>deploymentBusy.includes(row.status)).length;
  const canActivate=!!batchPreview.length&&staged>0&&staged+active===batchPreview.length;
  if(!admin)return <Result status="403" title="无权限" subTitle="模型下发及镜像更新仅管理员可用。" />;
  return <Space orientation="vertical" size="middle" style={{width:'100%',minWidth:0,overflowWrap:'anywhere'}} styles={{item:{minWidth:0,maxWidth:'100%'}}}>
    <Space wrap className="platform-related">
      <Link to={params.get('run_id')?`/model-repository?run_id=${encodeURIComponent(params.get('run_id')!)}`:'/model-repository'}>打开模型仓库</Link>
      <Link to="/gpu-terminal?source=deployment" target="_blank" rel="noopener noreferrer">SSH调试服务器</Link>
    </Space>
    {error&&<Alert closable onClose={()=>setError('')} type="error" showIcon title={error}/>} {notice&&<Alert type="success" showIcon title={notice}/>}
    <Tabs activeKey={tab} onChange={setTab} items={[
      {key:'platforms',label:'平台与模型更新',children:<DeploymentPlatforms servers={servers} initialVersion={requestedVersion} onPrepared={(result,cause)=>{setBatch(result.batch_id);setRecordPage(1);setRecords(result.items);setBatchPreview(result.items);setTab('records');setNotice(`已提交 ${result.items.length} 项模型更新准备，完成后统一确认生效。`);if(cause)setError(cause);refresh();}}/>},
      {key:'targets',label:'镜像更新 / 旧版模型下发',children:<Space orientation="vertical" size="middle" style={{width:'100%'}}>
        <Collapse activeKey={targetOpen} onChange={keys=>setTargetOpen(Array.isArray(keys)?keys:[keys])} items={[{key:'target',label:editing?'编辑服务器目标':'添加服务器目标',children:<Form name="deployment-target" form={targetForm} layout="vertical" initialValues={{kind:'model',environment:'docker',runtime:'triton_plan',repository:'/ai/chen/code/huggingface/aiseefor_model',namespace:'ai',workload_kind:'StatefulSet',container:'triton-server',containers:[]}} disabled={!!busy} onFinish={values=>void saveTarget(values)}>
          <Form.Item name="name" label="目标名称" rules={[{required:true,max:100}]}><Input placeholder="例如 杭州检测模型 / 丽水Kafka worker"/></Form.Item>
          <Form.Item name="kind" label="目标用途"><Select options={[{value:'model',label:'模型下发'},{value:'image',label:'镜像更新'}]} onChange={value=>targetForm.setFieldValue('container',value==='image'?'nexusai':'triton-server')}/></Form.Item>
          <DeploymentFields form={targetForm} servers={servers} kind={targetKind} editing={!!editing}/>
          <Space>{editing&&<Button onClick={()=>{setEditing(undefined);targetForm.resetFields();setTargetOpen([]);}}>取消编辑</Button>}<Button type="primary" htmlType="submit" loading={busy==='target'}>保存服务器目标</Button></Space>
        </Form>}]} />
        <Select aria-label="批量操作类型" value={kind} style={{width:220}} options={[{value:'model',label:'模型下发'},{value:'image',label:'镜像更新'}]} onChange={value=>{setKind(value);setTargetIds([]);}}/>
        {kind==='model'?<>
          <Select aria-label="模型来源" value={modelSource} style={{width:'100%'}} options={[{value:'repository',label:'模型仓库 · TensorRT'},{value:'full_model',label:'大模型 / 向量完整目录归档'}]} onChange={value=>{setModelSource(value);setTargetIds([]);}}/>
          {modelSource==='repository'?<>
            <Space wrap><Input.Search aria-label="搜索仓库模型" placeholder="搜索模型名称或版本" allowClear onSearch={value=>{setRepositoryQuery(value);setRepositoryPage(1);}}/>
              <Select aria-label="筛选GPU型号" value={gpuModel} placeholder="全部GPU型号" allowClear style={{minWidth:200}} options={gpuModels.map(value=>({value,label:value}))} onChange={value=>{setGpuModel(value);setRepositoryPage(1);}}/></Space>
            <Select aria-label="待下发仓库模型" style={{width:'100%'}} value={repositoryModel?.id} placeholder="选择模型仓库中的TensorRT模型" options={repositoryChoices.map(row=>({value:row.id,label:`${row.name} · ${row.version} · ${row.gpu_model||'未标注GPU'} · TRT ${row.tensorrt_version||'未标注'} · ${repositoryBatchLabel(row.batch_profile)}${repositoryReason(row)?`（${repositoryReason(row)}）`:''}`,disabled:!!repositoryReason(row)}))} onChange={id=>{setRepositoryModel(repositoryChoices.find(row=>row.id===id));setTargetIds([]);}}/>
            {pager(repositoryPage,repositoryPage*20<repositoryTotal,setRepositoryPage)}
            {repositoryModel&&repositoryReason(repositoryModel)&&<Alert type="warning" showIcon title={repositoryReason(repositoryModel)} description="请在模型仓库选择已就绪且构建信息完整的TensorRT模型。"/>}
          </>:<><Select aria-label="待下发完整模型版本" style={{width:'100%'}} value={release?.id} placeholder="选择已归档的大模型 / 向量完整目录" options={readyReleases.map(row=>({value:row.id,label:`${row.name} · ${row.version}`}))} onChange={id=>{setRelease(readyReleases.find(row=>row.id===id));setTargetIds([]);}}/><Button onClick={()=>setTab('releases')}>打开完整目录归档</Button></>}
        </>:<><Input aria-label="目标镜像版本" value={image} onChange={event=>setImage(event.target.value)} placeholder="仓库/镜像:明确版本 或 仓库/镜像@sha256:摘要"/><Alert type="info" showIcon title="K8s先用本环境Ansible剧本预分发到全部节点，逐节点核验后手动生效；Docker更新所选Triton/vLLM、NexusAI HTTP或Kafka worker。"/></>}
        <Table rowKey="id" size="small" dataSource={targets.filter(row=>row.kind===kind)} pagination={{pageSize:20}} scroll={{x:950}} rowSelection={{selectedRowKeys:targetIds,preserveSelectedRowKeys:true,onChange:keys=>setTargetIds(keys.map(String)),getCheckboxProps:row=>({disabled:kind==='model'&&(!modelReady||row.target.runtime!==targetRuntime)})}} columns={[
          {title:'服务器 / 目标',render:(_,row)=><><strong>{row.name}</strong><div>{row.target.control_server}</div>{row.target.file_server&&row.target.file_server!==row.target.control_server&&<div>文件：{row.target.file_server}</div>}</>},
          {title:'环境 / 服务',render:(_,row)=><><Tag>{row.target.environment}</Tag><div>{row.kind==='model'?row.target.model_name:row.target.workload||row.target.containers.join('、')}</div>{row.kind==='model'&&<Typography.Text type="secondary">{row.target.repository}</Typography.Text>}</>},
          {title:'当前版本记录',render:(_,row)=><><strong>{row.current.version||row.current.image||[...new Set(row.current.instances?.map(value=>value.image))].join('、')||'版本未知'}</strong><div>{versionStates[row.current.state]||row.current.state}</div><div>{localTime(row.current.checked_at)}</div>{row.current.instances?.map(instance=><div key={instance.uid}><Typography.Text type="secondary">{instance.name} · {instance.image_id}</Typography.Text></div>)}</>},
          {title:'最近操作',render:(_,row)=>row.latest?<Button size="small" onClick={()=>setSelected(row.latest)}>{deploymentNames[row.latest.status]||row.latest.status}</Button>:'暂无'},
          {title:'操作',render:(_,row)=><Space size="small" wrap><Button size="small" onClick={()=>setLogTarget(row)}>实时日志</Button><DeploymentSshLinks control={row.target.control_server} file={row.target.file_server}/><Button size="small" disabled={!!busy} onClick={()=>void perform('inspect',async()=>{await inspectServerTarget(row.id);setNotice('已读取目标状态；文件版本核对不代表全部推理实例已重新加载。');})}>读取当前版本</Button><Button size="small" disabled={!!busy} onClick={()=>{setEditing(row.id);setTargetOpen(['target']);targetForm.setFieldsValue({...row.target,name:row.name,kind:row.kind,runtime_options:JSON.stringify(row.target.runtime_config||{})});}}>编辑目标</Button></Space>},
        ]}/>
        <Space wrap><Typography.Text>已选 {targetIds.length} 个目标</Typography.Text><Button type="primary" disabled={!!busy||!targetIds.length||kind==='model'&&!modelReady||kind==='image'&&!image.trim()} loading={busy==='batch'} onClick={()=>void createBatch()}>一键准备</Button></Space>
      </Space>},
      {key:'records',label:'下发与镜像记录',children:<Space orientation="vertical" size="middle" style={{width:'100%'}}>
        {batch&&<Card size="small" title={`批次 ${batch}`} extra={<Button size="small" onClick={()=>{setBatch(undefined);setRecordPage(1);}}>查看全部记录</Button>}>
          <Typography.Paragraph>共 {new Set(batchPreview.map(row=>row.target.platform_id||row.target.control_server)).size} 个平台、{batchPreview.length} 项更新：准备中 {preparing}，待生效 {staged}，已生效 {active}，其他 / 失败 {batchPreview.length-preparing-staged-active}。全部准备通过后可确认生效。</Typography.Paragraph><Space wrap><Button disabled={!!busy||preparing>0} onClick={()=>void bulk('transfer')}>重试准备</Button><Button type="primary" disabled={!!busy||!canActivate} onClick={()=>void preview('activate')}>确认生效</Button><Button danger disabled={!!busy} onClick={()=>void preview('rollback')}>本批次恢复旧版本</Button><Button disabled={!!busy} onClick={()=>void bulk('refresh')}>核对本批次</Button></Space>
        </Card>}
        <Space wrap><Select aria-label="筛选记录类型" allowClear value={recordKind} placeholder="全部类型" style={{width:150}} options={[{value:'model',label:'模型'},{value:'image',label:'镜像'}]} onChange={value=>{setRecordKind(value);setRecordPage(1);}}/>
          <Input.Search aria-label="筛选版本" placeholder="模型版本或镜像版本" allowClear style={{width:240,maxWidth:'100%'}} onChange={event=>{if(!event.target.value){setRecordVersion('');setRecordPage(1);}}} onSearch={value=>{setRecordVersion(value.trim());setRecordPage(1);}}/>
          <Input.Search aria-label="筛选服务器" placeholder="服务器名称" allowClear style={{width:200,maxWidth:'100%'}} onChange={event=>{if(!event.target.value){setRecordServer('');setRecordPage(1);}}} onSearch={value=>{setRecordServer(value.trim());setRecordPage(1);}}/>
          <Button disabled={!!busy} onClick={refresh}>刷新记录</Button>
          <Popconfirm title={`删除所选 ${deleteIds.length} 条未生效记录？`} description="已准备的远端文件保留，执行中或需恢复现场的记录不可删除。" okText="确认删除" cancelText="取消" onConfirm={()=>void removeRecords(deleteIds)}><Button danger disabled={!!busy||!deleteIds.length}>删除所选未生效记录</Button></Popconfirm></Space>
        <Table rowKey="id" size="small" dataSource={records} pagination={false} scroll={{x:850}} rowSelection={{selectedRowKeys:deleteIds,preserveSelectedRowKeys:true,onChange:keys=>setDeleteIds(keys.map(String)),getCheckboxProps:row=>({disabled:!row.deletable||!!busy})}} columns={[
          {title:'类型 / 版本',render:(_,row)=><><Tag>{row.kind==='image'?'镜像':'模型'}</Tag><div>{row.source.name||row.source.image||row.target.model_name}</div><div>{row.source.version}</div></>},
          {title:'服务器',render:(_,row)=><>{row.target.control_server}<div>{row.target.workload||row.target.containers.join('、')}</div></>},
          {title:'状态',render:(_,row)=><><Tag color={row.status==='active'?'success':row.status==='staged'?'processing':undefined}>{deploymentNames[row.status]||row.status}</Tag>{row.error&&<div>{row.error}</div>}</>},
          {title:'时间',render:(_,row)=>localTime(row.created_at)},{title:'操作',render:(_,row)=><Space size="small" wrap><Button size="small" onClick={()=>setSelected(row)}>查看详情</Button>{row.batch_id&&<Button size="small" onClick={()=>{setBatch(row.batch_id);setRecordPage(1);}}>查看本批次</Button>}{row.deletable&&<Popconfirm title="删除这条未生效记录？" description="已准备的远端文件保留。" okText="确认删除" cancelText="取消" onConfirm={()=>void removeRecords([row.id])}><Button size="small" danger disabled={!!busy}>删除记录</Button></Popconfirm>}</Space>},
        ]}/>{pager(recordPage,recordMore,setRecordPage)}
      </Space>},
      {key:'releases',label:'大模型完整目录归档',children:<Space orientation="vertical" style={{width:'100%'}}>
        <Alert type="info" showIcon title="仅归档大模型 / 向量的完整目录；PT、ONNX、TensorRT统一在模型仓库管理。旧归档记录继续保留。"/>
        <Card title="保存大模型 / 向量完整目录"><Form name="model-archive" form={archiveForm} layout="vertical" disabled={!!busy} initialValues={{runtime:'triton_vllm'}} onFinish={values=>void perform('archive',async()=>{await archiveModel(values);setNotice('已开始后台归档，校验通过后可用于多服务器下发。');})}>
          <Form.Item name="run_id" label="来源训练 / 合并任务" rules={[{required:true}]}><Select showSearch optionFilterProp="label" options={sources.filter(row=>['model_merge','swift_sft','wemm'].includes(row.kind)&&(!row.runtimes||row.runtimes.some(value=>value!=='triton_plan'))).map(row=>({value:row.id,label:`${row.name} · ${row.kind}`}))}
            onChange={id=>{setPaths([]);const row=sources.find(value=>value.id===id);if(row)archiveForm.setFieldsValue({name:row.name,runtime:row.runtimes?.find(value=>value!=='triton_plan')||'triton_vllm',artifact_path:row.kind==='model_merge'?'merged':''});}}/></Form.Item>
          {sourceMore&&<Button onClick={()=>setSourcePage(value=>value+1)}>加载更多来源任务</Button>}
          <Form.Item name="runtime" label="模型类型" rules={[{required:true}]}><Select onChange={()=>setPaths([])} options={runtimeOptions.map(option=>({...option,disabled:!!selectedSource?.runtimes&&!selectedSource.runtimes.includes(option.value as DeploymentInput['runtime'])}))}/></Form.Item>
          <Form.Item name="name" label="模型名称" rules={[{required:true,max:100}]}><Input/></Form.Item>
          <Form.Item name="version" label="版本号" rules={[{required:true,max:100}]} extra="同一模型的版本号不可重复，例如 v20261024-01；归档后保留来源和SHA256。"><Input/></Form.Item>
          {paths.length>0&&<Form.Item label="从产物选择"><Select aria-label="选择归档产物" options={paths.map(path=>({value:path,label:path}))} onChange={path=>archiveForm.setFieldValue('artifact_path',path)}/></Form.Item>}
          <Form.Item name="artifact_path" label="完整模型目录" rules={[{required:true}]} extra="选择完整权重目录，LoRA需先合并。"><Input/></Form.Item>
          <Button type="primary" htmlType="submit" loading={busy==='archive'}>拉取并保存版本</Button>
        </Form></Card>
        <Table rowKey="id" size="small" dataSource={releases} pagination={false} scroll={{x:850}} columns={[
          {title:'模型版本',render:(_,row)=><><strong>{row.name}</strong><div>{row.version}</div></>},{title:'归档状态',render:(_,row)=><><Tag color={row.status==='ready'?'success':undefined}>{archiveStates[row.status]||row.status}</Tag>{row.error&&<div>{row.error}</div>}</>},
          {title:'本地记录',render:(_,row)=><><div>{row.archive.bytes?`${(row.archive.bytes/1024**3).toFixed(3)} GiB`:'尚未完成'}</div><Typography.Text type="secondary" copyable={!!row.archive.sha256}>{row.archive.sha256}</Typography.Text><div>{row.archive.path}</div><div>{localTime(row.archive.archived_at)}</div></>},
          {title:'操作',render:(_,row)=>row.runtime==='triton_plan'?<Link to={`/model-repository?run_id=${row.run_id}`}>历史TensorRT · 存入模型仓库</Link>:<Space size="small" wrap><Button size="small" disabled={row.status!=='ready'} onClick={()=>{setRelease(row);setModelSource('full_model');setKind('model');setTargetIds([]);setRequestedVersion('a:'+row.id);setTab('platforms');}}>选择服务器下发</Button>{['failed','interrupted'].includes(row.status)&&<Button size="small" disabled={!!busy} onClick={()=>void perform('retry',async()=>{await retryModelArchive(row.id);})}>重试归档</Button>}</Space>},
        ]}/>{pager(releasePage,releaseMore,setReleasePage)}
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
