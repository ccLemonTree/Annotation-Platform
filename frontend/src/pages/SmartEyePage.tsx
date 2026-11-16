import {useCallback,useEffect,useRef,useState} from 'react';
import {Alert,Drawer,Spin} from 'antd';
import {useSearchParams} from 'react-router-dom';
import {getProject,type Project} from '../api/projects';
import AutomationPage from './AutomationPage';

export default function SmartEyePage({embeddedProject}:{embeddedProject?:Project}={}) {
  const [params,setParams]=useSearchParams();
  const projectId=embeddedProject?.id||params.get('project_id');
  const requestedAutomation=params.get('automation')==='1';
  const frame=useRef<HTMLIFrameElement>(null),request=useRef(0);
  const [project,setProject]=useState<Project>();
  const [opening,setOpening]=useState(false),[error,setError]=useState('');
  const openAutomation=useCallback(async(id:string)=>{
    const current=++request.current;setOpening(true);setError('');
    try{
      const selected=await getProject(id);
      if(current!==request.current)return;
      if(selected.task_type!=='detection'){setError('批量预标注与AI审核当前仅支持检测项目');return;}
      setProject(selected);
    }catch{if(current===request.current)setError('项目读取失败，请重试打开预标注');}
    finally{if(current===request.current)setOpening(false);}
  },[]);
  useEffect(()=>{
    const receive=(event:MessageEvent)=>{
      if(event.origin!==location.origin||event.source!==frame.current?.contentWindow)return;
      if(event.data?.type==='smarteye:open-automation'&&typeof event.data.projectId==='string'&&event.data.projectId)
        void openAutomation(event.data.projectId);
    };
    window.addEventListener('message',receive);
    return()=>{window.removeEventListener('message',receive);request.current++;};
  },[openAutomation]);
  useEffect(()=>{if(projectId&&requestedAutomation)void openAutomation(projectId);},[projectId,requestedAutomation,openAutomation]);
  return <>
    {error&&<Alert type="error" showIcon title={error} closable afterClose={()=>setError('')}/>}
    {opening&&<Spin description="读取项目…"><div style={{height:32}}/></Spin>}
    <iframe ref={frame} title="SmartEye 数据拉取" src={`/smarteye/index.html${projectId?`?project_id=${encodeURIComponent(projectId)}`:''}`} className="smarteye-frame" />
    <Drawer title={project?`${project.project_name} · 预标注与AI审核`:''} open={!!project} size={1280} destroyOnHidden
      onClose={()=>{setProject(undefined);if(requestedAutomation){const next=new URLSearchParams(params);next.delete('automation');setParams(next,{replace:true});}}}>
      {project&&<AutomationPage key={project.id} embeddedProject={project}/>}
    </Drawer>
  </>;
}
