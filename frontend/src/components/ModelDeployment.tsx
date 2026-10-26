import { useState } from 'react';
import { Alert,Button,Col,Collapse,Descriptions,Form,Input,Modal,Row,Select,Space,Table,Tag,Typography } from 'antd';
import type { FormInstance } from 'antd';
import { deploymentAction } from '../api/modelDeployments';
import type { DeploymentServer,ModelDeployment as Record } from '../api/modelDeployments';
import {deploymentBusy,deploymentError,deploymentNames} from '../utils/modelDeployment';
export function DeploymentFields({form,servers,kind}:{form:FormInstance;servers:DeploymentServer[];kind:'model'|'image'}){
  const environment=Form.useWatch('environment',form),runtime=Form.useWatch('runtime',form);
  const options=servers.map(server=>({value:server.name,label:`${server.name} · ${server.host}:${server.port}`}));
  return <>
    <Row gutter={16}><Col xs={24} md={12}><Form.Item name="environment" label="部署环境" rules={[{required:true}]}><Select options={[{value:'k8s',label:'K8s'},{value:'docker',label:'Docker'},{value:'docker_kafka',label:'Docker Kafka worker'}]}/></Form.Item></Col>
      {kind==='model'&&<Col xs={24} md={12}><Form.Item name="runtime" label="推理方式" rules={[{required:true}]}><Select options={[{value:'triton_plan',label:'小模型 · Triton TensorRT'},{value:'triton_vllm',label:'大模型 · Triton vLLM backend'},{value:'vllm',label:'向量 · vLLM'}]}/></Form.Item></Col>}</Row>
    <Row gutter={16}>{kind==='model'&&<Col xs={24} md={12}><Form.Item name="file_server" label="模型文件服务器" rules={[{required:true}]}><Select showSearch optionFilterProp="label" options={options}/></Form.Item></Col>}
      {(kind==='image'||environment==='k8s')&&<Col xs={24} md={12}><Form.Item name="control_server" label={environment==='k8s'?'K8s控制服务器':'SSH服务器'} rules={[{required:true}]}><Select showSearch optionFilterProp="label" options={options}/></Form.Item></Col>}</Row>
    {kind==='model'&&<><Form.Item name="repository" label="服务器模型仓库路径" rules={[{required:true}]} extra="填写真实宿主机 / NFS路径；向量模型填写模型目录的父目录。"><Input/></Form.Item>
      <Form.Item name="model_name" label="目标模型名 / 目录名" rules={[{required:true},{pattern:/^[A-Za-z0-9][A-Za-z0-9_-]{0,99}$/,message:'仅字母、数字、下划线和短横线'}]}><Input/></Form.Item></>}
    {environment==='k8s'?<><Row gutter={16}><Col xs={24} md={12}><Form.Item name="namespace" label="命名空间" rules={[{required:true}]}><Input/></Form.Item></Col><Col xs={24} md={12}><Form.Item name="workload_kind" label="工作负载类型"><Select options={['StatefulSet','Deployment'].map(value=>({value,label:value}))}/></Form.Item></Col></Row>
      <Form.Item name="workload" label="工作负载名称" rules={[{required:true}]}><Input/></Form.Item>
      <Form.Item name="container" label={kind==='image'?'待更新镜像的容器名':'推理容器名称'} rules={[{required:true}]}><Input placeholder={kind==='image'?'例如 nexusai 或 triton-server':'triton-server'}/></Form.Item>
      {kind==='image'&&<><Form.Item name="preload_playbook" label="Ansible预分发剧本（可选）" extra="留空自动查找该环境的 /opt/*/ansible/preload-ai-images.yml；例如嘉兴 /opt/jx_k8s/ansible/preload-ai-images.yml。"><Input/></Form.Item>
        <Form.Item name="preload_inventory" label="Ansible节点清单（可选）" extra="留空自动查找覆盖全部实时节点的inventory。准备时控制机拉取，分批4台分发，逐节点核验后才能生效。"><Input/></Form.Item></>}</>:
      <Form.Item name="containers" label={kind==='image'?'待更新镜像的全部容器':'全部目标推理容器'} rules={[{required:true,type:'array',min:1}]} extra="填写明确的Docker容器名，按回车添加。镜像更新只覆盖所选容器。"><Select mode="tags" tokenSeparators={[',','，']}/></Form.Item>}
    {kind==='model'&&runtime!=='vllm'&&<Form.Item name="triton_config" label="Triton config.pbtxt（可选）" extra="留空沿用目标现有配置；新模型请核对输入输出与GPU配置。"><Input.TextArea rows={4}/></Form.Item>}
    {kind==='model'&&runtime==='triton_vllm'&&<Form.Item name="runtime_options" label="vLLM model.json参数（可选）" extra="JSON对象；模型路径自动匹配挂载。"><Input.TextArea rows={4}/></Form.Item>}
  </>;
}

export default function ModelDeployment({record,onChange}:{record:Record;onChange:(row:Record)=>void}){
  const [busy,setBusy]=useState(''),[error,setError]=useState(''),[confirm,setConfirm]=useState<'activate'|'rollback'>();
  const image=record.kind==='image',disabled=!!busy||deploymentBusy.includes(record.status);
  const act=async(action:string)=>{setBusy(action);setError('');try{onChange(await deploymentAction(record.id,action));setConfirm(undefined);}catch(cause){setError(deploymentError(cause));}finally{setBusy('');}};
  const instances=record.inspection.instances?.length?record.inspection.instances:record.inspection.inventory?.instances||[];
  return <Space orientation="vertical" size="middle" style={{width:'100%',minWidth:0,overflowWrap:'anywhere'}} styles={{item:{minWidth:0,maxWidth:'100%'}}}>
    <Typography.Title level={5}>{record.source.name||record.source.image||record.target.model_name} <Tag>{deploymentNames[record.status]||record.status}</Tag></Typography.Title>
    <Descriptions column={1} size="small" bordered items={[{key:'id',label:'记录ID',children:record.id},{key:'version',label:image?'目标镜像':'模型版本',children:record.source.image||record.source.version||'历史来源任务'},
      {key:'target',label:'目标',children:`${record.target.control_server} · ${record.target.environment==='k8s'?`${record.target.namespace}/${record.target.workload} · ${record.target.container}`:record.target.containers.join('、')}`},
      ...(!image?[{key:'path',label:'模型路径',children:`${record.target.file_server}:${record.target.repository}/${record.target.model_name}`}]:[]),
      ...(image?[{key:'previous',label:'原镜像',children:[...new Set(record.inspection.inventory?.instances.map(row=>`${row.image} · ${row.image_id}`))].join('；')}]:[]),
      ...(record.inspection.preload_info?[{key:'preload',label:'Ansible预分发',children:<>{record.inspection.preload_info.playbook}<br/>{record.inspection.preload_info.inventory}<br/>实时节点：{record.inspection.preload_info.node_count}；已验证：{record.inspection.image_receipt?.node_count||0}</>}]:[]),
      ...(record.inspection.image_receipt?.pinned_image?[{key:'digest',label:'已分发的固定镜像',children:<>{record.inspection.image_receipt.pinned_image}<br/>生效使用本地缓存（Never），避免节点并发回源。<br/>包SHA256：{record.inspection.image_receipt.archive_sha256}</>}]:[]),
      {key:'sha',label:image?'镜像核验':'模型传输校验',children:record.inspection.transfer?`SHA256 ${record.inspection.transfer.sha256} · ${record.inspection.transfer.file_count}个文件`:image?'镜像准备后由运行实例核验实际镜像ID':'尚未传输'}]}/>
    <Space wrap><Button disabled={disabled||!['draft','checked','transfer_failed'].includes(record.status)} loading={busy==='precheck'} onClick={()=>void act('precheck')}>预检查</Button>
      <Button disabled={disabled||!['draft','checked','transfer_failed'].includes(record.status)} onClick={()=>void act('transfer')}>{image?'准备镜像':'传输并校验'}</Button>
      <Button type="primary" disabled={disabled||!['staged','activation_failed','unknown'].includes(record.status)} onClick={()=>setConfirm('activate')}>生效</Button>
      <Button danger disabled={disabled||!['active','activation_failed','rollback_failed','unknown'].includes(record.status)} onClick={()=>setConfirm('rollback')}>恢复旧版本</Button>
      <Button disabled={!!busy} loading={busy==='refresh'} onClick={()=>void act('refresh')}>核对远端状态</Button></Space>
    {(error||record.error)&&<Alert type="error" showIcon title={error||record.error}/>}
    {instances.length>0&&<Table size="small" rowKey="uid" dataSource={instances} pagination={{pageSize:10}} scroll={{x:650}} columns={[{title:'实例',dataIndex:'name'},{title:'结果',render:(_,row)=>row.ok===undefined?'待核验':row.ok?'已核验':'未通过'},
      {title:'运行镜像',render:(_,row)=><><div>{row.image}</div><Typography.Text type="secondary" copyable={!!row.image_id}>{row.image_id}</Typography.Text></>},{title:'详情',dataIndex:'detail'}]}/>}
    {!!record.inspection.image_receipt?.nodes?.length&&<Collapse items={[{key:'nodes',label:`查看全部 ${record.inspection.image_receipt.nodes.length} 个节点的预分发结果`,children:<Table rowKey="uid" size="small" pagination={{pageSize:10}} scroll={{x:650}} dataSource={record.inspection.image_receipt.nodes} columns={[{title:'节点',dataIndex:'node'},{title:'结果',render:(_,row)=>row.ok?'摘要已验证':'未通过'},{title:'镜像摘要',dataIndex:'digest'}]}/>}]} />}
    <Typography.Paragraph style={{whiteSpace:'pre-wrap',maxHeight:260,overflow:'auto'}}>{record.logs.map(log=>`${new Date(log.at).toLocaleTimeString('zh-CN')}  ${log.message}`).join('\n')||'创建记录不会变更远程服务。'}</Typography.Paragraph>
    {confirm&&<Modal open title={confirm==='rollback'?'恢复此记录前的旧版本？':image?'更新全部指定实例的镜像？':'使此模型在全部指定实例生效？'} okText={confirm==='rollback'?'确认恢复':'确认生效'} cancelText="返回" confirmLoading={!!busy}
      okButtonProps={{danger:confirm==='rollback'}} onCancel={()=>{if(!busy)setConfirm(undefined);}} onOk={()=>void act(confirm)}>
      <Typography.Paragraph>{record.target.control_server} · {record.target.workload||record.target.containers.join('、')}</Typography.Paragraph>
      <Typography.Paragraph>{image?'将重建所选Docker容器或滚动更新指定K8s容器镜像，并核对全部实例。旧镜像和恢复记录保留。':'将切换已校验模型并保留旧目录，逐实例加载或重启推理服务。'}期间可能影响服务，失败会保留现场，需核对后继续或恢复。</Typography.Paragraph>
      {error&&<Alert type="error" title={error}/>}</Modal>}
  </Space>;
}
