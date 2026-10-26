import { useEffect,useRef,useState } from 'react';
import { Alert,Button,Checkbox,Select,Space,Tag,Typography } from 'antd';
import { Link } from 'react-router-dom';
import { getDeploymentLogs } from '../api/modelDeployments';
import type { DeploymentLogSnapshot } from '../api/modelDeployments';
import { deploymentError,deploymentNames } from '../utils/modelDeployment';

export function DeploymentSshLinks({control,file}:{control:string;file?:string}){
  return <Space wrap>{[...new Set([control,file].filter((value):value is string=>!!value))].map(name=>
    <Link key={name} to={`/gpu-terminal?ssh_server=${encodeURIComponent(name)}`} target="_blank" rel="noopener noreferrer">SSH调试：{name}</Link>)}</Space>;
}

export default function DeploymentLogs({id,target=false}:{id:string;target?:boolean}){
  const [source,setSource]=useState<'updates'|'service'>('updates'),[instance,setInstance]=useState<string>();
  const [automatic,setAutomatic]=useState(true),[follow,setFollow]=useState(true),[reload,setReload]=useState(0);
  const [snapshot,setSnapshot]=useState<DeploymentLogSnapshot>(),[error,setError]=useState(''),[loading,setLoading]=useState(false);
  const output=useRef<HTMLPreElement>(null);
  const lastRead=useRef('');
  useEffect(()=>{
    const key=JSON.stringify([id,target,source,instance,reload]);
    if(!automatic&&lastRead.current===key){setLoading(false);return;}
    lastRead.current=key;
    const controller=new AbortController();let timer:ReturnType<typeof setTimeout>;
    const read=async()=>{
      setLoading(true);
      try{
        const value=await getDeploymentLogs(id,target,source,source==='service'?instance:undefined,controller.signal);
        if(!controller.signal.aborted){setSnapshot(value);setError('');}
      }catch(cause){if(!controller.signal.aborted)setError(deploymentError(cause));}
      finally{if(!controller.signal.aborted){setLoading(false);if(automatic)timer=setTimeout(()=>void read(),2000);}}
    };
    setError('');void read();
    return()=>{controller.abort();clearTimeout(timer);};
  },[id,target,source,instance,automatic,reload]);
  useEffect(()=>{if(follow&&output.current)output.current.scrollTop=output.current.scrollHeight;},[snapshot,follow]);
  return <Space orientation="vertical" style={{width:'100%',minWidth:0}} aria-label="实时更新日志">
    <Space wrap>
      <Select aria-label="日志来源" value={source} style={{width:180}} onChange={value=>{setSource(value);setInstance(undefined);setSnapshot(undefined);}}
        options={[{value:'updates',label:'更新过程日志'},{value:'service',label:'服务运行日志'}]}/>
      {source==='service'&&<Select aria-label="日志实例" value={instance||''} style={{minWidth:200,maxWidth:'100%'}}
        onChange={value=>{setInstance(value||undefined);setSnapshot(undefined);}} options={[{value:'',label:'默认实例'},...(snapshot?.instances||[]).map(row=>({value:row.name,label:row.name}))]}/>}
      <Checkbox checked={automatic} onChange={event=>setAutomatic(event.target.checked)}>自动刷新（2秒）</Checkbox>
      <Checkbox checked={follow} onChange={event=>setFollow(event.target.checked)}>跟随最新</Checkbox>
      <Button loading={loading} onClick={()=>setReload(value=>value+1)}>刷新日志</Button>
    </Space>
    {snapshot&&<Space wrap><Tag>{deploymentNames[snapshot.status]||snapshot.status}</Tag><Typography.Text type="secondary">
      {snapshot.server}{snapshot.selected_instance?' · '+snapshot.selected_instance:''} · {new Date(snapshot.collected_at).toLocaleTimeString('zh-CN',{timeZone:'Asia/Shanghai'})}
    </Typography.Text>{snapshot.truncated&&<Tag>仅显示最近日志（最多64KiB）</Tag>}</Space>}
    {error&&<Alert type="error" showIcon title="日志读取失败" description={error}/>}
    <pre ref={output} aria-label="服务器日志内容" aria-live="off" style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere',overflow:'auto',maxHeight:420,minHeight:160,background:'#111827',color:'#e5e7eb',padding:12,borderRadius:8,margin:0}}>
      {snapshot?.content|| (loading?'正在读取日志…':error?'暂未读取到日志':'暂无日志；任务尚未开始或当前阶段没有输出。')}
    </pre>
    <Typography.Text type="secondary">仅查看日志，不会启动更新或重启服务。服务日志显示所选实例最近200行。</Typography.Text>
  </Space>;
}
