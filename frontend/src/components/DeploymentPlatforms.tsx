import {useEffect,useState} from 'react';
import {Alert,Button,Card,Col,Collapse,Form,Input,Row,Select,Space,Table,Tag,Typography} from 'antd';
import {createPlatformBatch,discoverPlatformModels,getDeploymentPlatforms,importDeploymentPlatforms,previewPlatformPlan,saveDeploymentPlatform} from '../api/deploymentPlatforms';
import type {DeploymentPlatform,PlanItem,PlatformConfig,PlatformPlan} from '../api/deploymentPlatforms';
import {deploymentBatchAction,getModelReleases} from '../api/modelDeployments';
import type {BatchResult,DeploymentServer} from '../api/modelDeployments';
import {getRepositoryModel,getRepositoryModels} from '../api/modelRepository';
import {deploymentError} from '../utils/modelDeployment';

type Choice={value:string;label:string;name:string;version:string};
type PlatformForm=PlatformConfig&{name:string;control_servers:string[]};
export default function DeploymentPlatforms({servers,onPrepared,initialVersion}:{servers:DeploymentServer[];onPrepared:(batch:BatchResult,error?:string)=>void;initialVersion?:string}){
  const [form]=Form.useForm<PlatformForm>(),environment=Form.useWatch('environment',form);
  const [platforms,setPlatforms]=useState<DeploymentPlatform[]>([]),[platformIds,setPlatformIds]=useState<string[]>([]),[versionKeys,setVersionKeys]=useState<string[]>([]);
  const [choices,setChoices]=useState<Choice[]>([]),[selectedChoices,setSelectedChoices]=useState<Choice[]>([]),[page,setPage]=useState(1),[more,setMore]=useState(false),[query,setQuery]=useState('');
  const [archivePage,setArchivePage]=useState(1),[archiveMore,setArchiveMore]=useState(false),[archives,setArchives]=useState<Choice[]>([]);
  const [assignments,setAssignments]=useState<{[key:string]:string}>({}),[plan,setPlan]=useState<PlanItem[]>([]),[planLoading,setPlanLoading]=useState(false);
  const [showMappings,setShowMappings]=useState(false);
  const [scanning,setScanning]=useState<string[]>([]),[busy,setBusy]=useState(''),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [editing,setEditing]=useState<string>(),[open,setOpen]=useState<string[]>([]);
  const options=servers.map(server=>({value:server.name,label:`${server.name} · ${server.host}:${server.port}`}));
  const load=()=>getDeploymentPlatforms().then(result=>setPlatforms(result.items));
  useEffect(()=>{const controller=new AbortController();getDeploymentPlatforms(controller.signal).then(result=>setPlatforms(result.items)).catch(cause=>{if(!controller.signal.aborted)setError(deploymentError(cause));});return()=>controller.abort();},[]);
  useEffect(()=>{const controller=new AbortController();getRepositoryModels({page,q:query||undefined,format:'tensorrt'},controller.signal).then(result=>{
    setChoices(result.items.filter(row=>row.status==='ready'&&row.gpu_model&&row.tensorrt_version).map(row=>({value:'r:'+row.id,label:`${row.name} · ${row.version} · ${row.gpu_model} · TRT ${row.tensorrt_version}`,name:row.name,version:row.version})));setMore(page*20<result.total);
  }).catch(cause=>{if(!controller.signal.aborted)setError(deploymentError(cause));});return()=>controller.abort();},[page,query]);
  useEffect(()=>{const controller=new AbortController();getModelReleases(archivePage,controller.signal).then(result=>{
    setArchives(values=>[...new Map([...values,...result.items.filter(row=>row.status==='ready'&&row.runtime!=='triton_plan').map(row=>({value:'a:'+row.id,label:`${row.name} · ${row.version} · 完整目录`,name:row.name,version:row.version}))].map(row=>[row.value,row])).values()]);setArchiveMore(result.has_more);
  }).catch(cause=>{if(!controller.signal.aborted)setError(deploymentError(cause));});return()=>controller.abort();},[archivePage]);
  useEffect(()=>{if(!initialVersion)return;setVersionKeys([initialVersion]);
    if(initialVersion.startsWith('r:'))getRepositoryModel(initialVersion.slice(2)).then(row=>setSelectedChoices([{value:initialVersion,label:`${row.name} · ${row.version} · ${row.gpu_model}`,name:row.name,version:row.version}])).catch(cause=>setError(deploymentError(cause)));
  },[initialVersion]);
  const request:PlatformPlan={platform_ids:platformIds,versions:versionKeys.map(value=>value.startsWith('r:')?{repository_id:value.slice(2)}:{release_id:value.slice(2)}),
    assignments:Object.entries(assignments).filter(([key])=>platformIds.includes(key.split('|')[0])&&versionKeys.includes(key.split('|')[1])).map(([key,model_key])=>({platform_id:key.split('|')[0],version_key:key.split('|')[1],model_key}))};
  const requestJson=JSON.stringify(request);
  useEffect(()=>{setPlan([]);if(!platformIds.length||!versionKeys.length)return;const controller=new AbortController();setPlanLoading(true);
    const timer=setTimeout(()=>{previewPlatformPlan(JSON.parse(requestJson),controller.signal).then(result=>setPlan(result.items)).catch(cause=>{if(!controller.signal.aborted)setError(deploymentError(cause));}).finally(()=>{if(!controller.signal.aborted)setPlanLoading(false);});},200);
    return()=>{clearTimeout(timer);controller.abort();};
  },[requestJson,platforms]);
  const update=(row:DeploymentPlatform)=>setPlatforms(values=>values.some(value=>value.id===row.id)?values.map(value=>value.id===row.id?row:value):[...values,row]);
  const scan=async(rows:DeploymentPlatform[])=>{setError('');setScanning(values=>[...new Set([...values,...rows.map(row=>row.id)])]);
    await Promise.all(rows.map(async row=>{try{update(await discoverPlatformModels(row.id));}catch(cause){setError(deploymentError(cause));}finally{setScanning(values=>values.filter(id=>id!==row.id));}}));};
  const perform=async(key:string,work:()=>Promise<void>)=>{if(busy)return;setBusy(key);setError('');setNotice('');try{await work();}catch(cause){setError(deploymentError(cause));}finally{setBusy('');}};
  const save=(values:PlatformForm)=>perform('save',async()=>{
    const added:DeploymentPlatform[]=[],failed:string[]=[];
    for(const server of values.control_servers){try{
      const row=await saveDeploymentPlatform({name:values.name||server,environment:values.environment,control_server:server,file_server:values.environment==='k8s'?values.file_server||server:server,
        namespace:values.namespace||'ai',services:values.services||[]},editing);update(row);added.push(row);
    }catch(cause){failed.push(`${server}：${deploymentError(cause)}`);}}
    if(added.length){setNotice(`已保存 ${added.length} 个平台，正在自动读取模型。`);setEditing(undefined);form.resetFields();setOpen([]);await scan(added);}
    if(failed.length)setError(failed.join('；'));
  });
  const prepare=()=>perform('prepare',async()=>{
    const batch=await createPlatformBatch(request);
    try{const result=await deploymentBatchAction(batch.batch_id,'transfer');
      const failed=result.results.filter(row=>row.error);onPrepared(batch,failed.map(row=>`${row.id.slice(0,8)}：${row.error}`).join('；'));
    }catch(cause){onPrepared(batch,deploymentError(cause));}
  });
  const available=[...new Map([...choices,...archives,...selectedChoices].map(row=>[row.value,row])).values()];
  const updateCount=plan.filter(row=>row.selected_key&&row.selected_key!=='skip'&&!row.error).length;
  const blockedCount=plan.filter(row=>!row.selected_key||row.error).length,autoSkipped=plan.filter(row=>row.auto_skipped).length;
  const canPrepare=plan.length===platformIds.length*versionKeys.length&&updateCount>0&&plan.every(row=>row.selected_key&&!row.error)&&!planLoading&&!scanning.length;
  return <Space orientation="vertical" size="middle" style={{width:'100%',minWidth:0}}>
    {error&&<Alert type="error" closable title={error} onClose={()=>setError('')}/>} {notice&&<Alert type="success" title={notice}/>}
    <Typography.Title level={5}>1. 选择本次更新的模型版本</Typography.Title>
    <Space wrap><Input.Search aria-label="搜索更新模型" placeholder="模型名称或版本" allowClear onSearch={value=>{setQuery(value);setPage(1);}}/>
      <Button disabled={page===1} onClick={()=>setPage(value=>value-1)}>上一页模型</Button><Typography.Text>第 {page} 页</Typography.Text><Button disabled={!more} onClick={()=>setPage(value=>value+1)}>下一页模型</Button>
      {archiveMore&&<Button onClick={()=>setArchivePage(value=>value+1)}>加载更多完整模型</Button>}</Space>
    <Select aria-label="本次模型版本" style={{width:'100%'}} mode="multiple" showSearch optionFilterProp="label" value={versionKeys} options={available} placeholder="可同时选择多个模型；每个目标目录选择一个版本" onChange={values=>{setVersionKeys(values);setSelectedChoices(available.filter(row=>values.includes(row.value)));setAssignments({});}}/>
    <Typography.Title level={5}>2. 选择平台</Typography.Title>
    <Typography.Text type="secondary">按实际推理实例的GPU型号与TensorRT版本匹配。环境核验结果有效15分钟，过期需重新读取；正式准备时再次复核。</Typography.Text>
    <Collapse activeKey={open} onChange={keys=>setOpen(Array.isArray(keys)?keys:[keys])} items={[{key:'register',label:editing?'编辑平台配置':'登记平台（首次使用，配置长期复用）',children:<Form form={form} layout="vertical" initialValues={{environment:'docker',namespace:'ai',services:[]}} disabled={!!busy} onFinish={values=>void save(values)}>
      <Row gutter={16}><Col xs={24} md={12}><Form.Item name="name" label="平台名称（可选，默认服务器名）"><Input maxLength={100}/></Form.Item></Col><Col xs={24} md={12}><Form.Item name="environment" label="平台环境" rules={[{required:true}]}><Select options={[{value:'docker',label:'Docker'},{value:'docker_kafka',label:'Docker Kafka'},{value:'k8s',label:'K8s'}]}/></Form.Item></Col></Row>
      <Form.Item name="control_servers" label={environment==='k8s'?'K8s控制服务器':'平台服务器'} rules={[{required:true,type:'array',min:1}]}><Select mode="multiple" maxCount={editing?1:50} showSearch optionFilterProp="label" options={options}/></Form.Item>
      {environment==='k8s'&&<Row gutter={16}><Col span={12}><Form.Item name="file_server" label="模型文件服务器" rules={[{required:true}]}><Select options={options} showSearch optionFilterProp="label"/></Form.Item></Col><Col span={12}><Form.Item name="namespace" label="命名空间"><Input/></Form.Item></Col></Row>}
      <Collapse items={[{key:'advanced',label:'高级服务配置（无法自动识别或需指定范围时填写）',children:<Form.List name="services">{(fields,{add,remove})=><Space orientation="vertical" style={{width:'100%'}}>{fields.map(field=><Card size="small" key={field.key} extra={<Button danger onClick={()=>remove(field.name)}>移除服务</Button>}>
        <Form.Item name={[field.name,'runtime']} label="服务类型" rules={[{required:true}]}><Select options={[{value:'triton_plan',label:'Triton（自动识别各模型类型）'},{value:'triton_vllm',label:'Triton vLLM'},{value:'vllm',label:'原生vLLM向量'}]}/></Form.Item>
        <Form.Item name={[field.name,'repository']} label="宿主机模型仓库路径" rules={[{required:true}]}><Input/></Form.Item>
        {environment==='k8s'?<><Form.Item name={[field.name,'workload_kind']} label="工作负载类型"><Select options={['Deployment','StatefulSet'].map(value=>({value}))}/></Form.Item><Form.Item name={[field.name,'workload']} label="工作负载名称" rules={[{required:true}]}><Input/></Form.Item><Form.Item name={[field.name,'container']} label="推理容器" rules={[{required:true}]}><Input/></Form.Item></>:<Form.Item name={[field.name,'containers']} label="推理容器" rules={[{required:true,type:'array',min:1}]}><Select mode="tags"/></Form.Item>}
      </Card>)}<Button onClick={()=>add({runtime:'triton_plan',namespace:form.getFieldValue('namespace')||'ai',workload_kind:'StatefulSet',container:'triton-server',containers:[]})}>添加服务配置</Button></Space>}</Form.List>}]} />
      <Space style={{marginTop:12}}><Button type="primary" htmlType="submit" loading={busy==='save'}>保存并读取模型</Button><Button onClick={()=>{form.resetFields();setEditing(undefined);setOpen([]);}}>取消</Button></Space>
    </Form>}]} />
    <Space wrap><Button disabled={!!busy} onClick={()=>void perform('import',async()=>{const result=await importDeploymentPlatforms();await load();setNotice(`已导入 ${result.imported} 个平台，勾选时自动读取模型。`);})}>导入已有目标配置</Button>
      <Button disabled={!platformIds.length||!!scanning.length} onClick={()=>void scan(platforms.filter(row=>platformIds.includes(row.id)))}>重新读取所选平台模型</Button></Space>
    <Table rowKey="id" size="small" dataSource={platforms} pagination={{pageSize:20}} scroll={{x:850}} expandable={{expandedRowRender:row=><Space orientation="vertical">{row.models.map(model=><Typography.Text key={model.key}>{model.name} · {model.target.repository} · {row.versions[model.key]?.version||'版本未纳管'}</Typography.Text>)}</Space>}} rowSelection={{selectedRowKeys:platformIds,onChange:keys=>{const ids=keys.map(String);setPlatformIds(ids);void scan(platforms.filter(row=>ids.includes(row.id)&&(!row.checked_at||row.stale||row.needs_runtime_refresh)&&!scanning.includes(row.id)));}}} columns={[
      {title:'平台',render:(_,row)=><><strong>{row.name}</strong><div>{row.config.control_server}</div><Tag>{row.config.environment}</Tag></>},
      {title:'模型清单',render:(_,row)=><>{row.models.length?`${row.models.length} 个模型：${row.models.slice(0,3).map(model=>model.name).join('、')}${row.models.length>3?'…':''}`:'尚未读取'}{!!row.warnings?.length&&<div>读取提示：{row.warnings.join('、')}</div>}</>},
      {title:'实际GPU / TensorRT',render:(_,row)=><>{[...new Set(row.models.flatMap(model=>model.environment?.instances?.map(instance=>`${[...new Set(instance.gpus)].join('、')} · TRT ${instance.tensorrt}`)||[]))].map(value=><div key={value}>{value}</div>)}{row.needs_runtime_refresh&&<Tag color="warning">环境待核验</Tag>}</>},
      {title:'读取结果',render:(_,row)=>scanning.includes(row.id)?'正在读取…':row.error?<Typography.Text type="danger">{row.error}</Typography.Text>:row.stale?'配置已变更，请重新读取':row.checked_at?new Date(row.checked_at).toLocaleString('zh-CN'):'勾选后自动读取'},
      {title:'操作',render:(_,row)=><Space wrap><Button disabled={scanning.includes(row.id)} onClick={()=>void scan([row])}>读取模型</Button><Button disabled={!!busy} onClick={()=>{setEditing(row.id);setOpen(['register']);form.setFieldsValue({...row.config,name:row.name,control_servers:[row.config.control_server]});}}>编辑平台</Button></Space>},
    ]}/>
    {autoSkipped>0&&<Alert type="info" showIcon title={`已按GPU和TensorRT自动匹配，跳过 ${autoSkipped} 项不兼容构建`} description="每个平台使用所选兼容版本，可展开对应关系查看分配结果。"/>}
    {!!platformIds.length&&!!versionKeys.length&&<Collapse activeKey={showMappings||blockedCount?['mapping']:[]} onChange={keys=>setShowMappings(keys.length>0)} items={[{key:'mapping',label:`模型对应关系：${blockedCount?`${blockedCount} 项待处理（对应关系或兼容性）`:`${plan.length} 项已确认，点击查看或调整`}（长期复用）`,children:<Table rowKey={row=>row.platform_id+'|'+row.version_key} size="small" loading={planLoading} dataSource={[...plan].sort((a,b)=>Number(!!b.error||!b.selected_key)-Number(!!a.error||!a.selected_key))} pagination={{pageSize:20}} scroll={{x:1000}} columns={[
      {title:'平台',dataIndex:'platform_name'},{title:'本次版本 / 构建环境',render:(_,row)=><>{row.name} · {row.version}{row.built_gpu&&<div>{row.built_gpu} · TRT {row.built_trt}</div>}</>},
      {title:'兼容检查',render:(_,row)=><><Tag color={row.compatibility==='compatible'?'success':row.compatibility==='incompatible'?'error':row.compatibility==='unknown'?'warning':undefined}>{({compatible:'兼容',incompatible:'不兼容',unknown:'待核验',not_required:'无需TensorRT匹配',skipped:'已跳过'})[row.compatibility]||'待核验'}</Tag><div>{row.compatibility_message}</div></>},
      {title:'平台上的目标模型',render:(_,row)=><><Select aria-label={`${row.platform_name} ${row.name} 对应模型`} style={{width:'100%',minWidth:240}} value={row.selected_key||undefined} placeholder="请选择对应模型目录" options={[{value:'skip',label:'本平台不更新此版本'},...row.options.map(option=>({value:option.key,disabled:option.disabled,label:`${option.name} · ${option.service} · ${option.repository}${option.gpus?.length?` · ${option.gpus.join('/')} / TRT ${option.tensorrt_versions.join('/')}`:''}${option.disabled?'（不可用）':''}`}))]} onChange={value=>setAssignments(values=>({...values,[row.platform_id+'|'+row.version_key]:value}))}/>{row.error&&<Typography.Text type="danger">{row.error}</Typography.Text>}</>},
    ]}/>}]} />}
    <Space wrap><Typography.Text>已选 {versionKeys.length} 个模型版本、{platformIds.length} 个平台，已匹配 {updateCount} 项更新{blockedCount>0?`，${blockedCount} 项待处理`:''}</Typography.Text><Button type="primary" disabled={!canPrepare||!!busy} loading={busy==='prepare'} onClick={()=>void prepare()}>一键准备所选更新</Button></Space>
  </Space>;
}
