import {useEffect,useState} from 'react';
import {Link,useSearchParams} from 'react-router-dom';
import {Alert,Select,Space} from 'antd';
import {getProjects,type Project} from '../api/projects';
import ProjectWorkflow from '../components/ProjectWorkflow';
import AutomationReviewQueue from '../components/automation/AutomationReviewQueue';

export default function ReviewQueuePage(){
  const [params,setParams]=useSearchParams(),[projects,setProjects]=useState<Project[]>([]),[error,setError]=useState('');
  const projectId=params.get('project_id')||undefined;
  useEffect(()=>{let active=true;
    (async()=>{const items:Project[]=[];for(let page=1;;page++){const data=await getProjects({page,page_size:100,task_type:'detection'});items.push(...data.items);if(items.length>=data.total||!data.items.length)break;}if(active)setProjects(items);})().catch(()=>{if(active)setError('项目读取失败，请刷新重试');});
    return()=>{active=false;};
  },[]);
  const project=projects.find(project=>project.id===projectId);
  return <Space orientation="vertical" size="middle" style={{width:'100%',minWidth:0}}>
    {project&&<ProjectWorkflow project={project} active="review"/>}
    {error&&<Alert type="error" title={error}/>}
    <Space wrap><Select aria-label="待审队列项目" prefix="项目" placeholder="全部项目" allowClear showSearch optionFilterProp="label" style={{width:240,maxWidth:'100%'}} value={projectId} options={projects.map(project=>({value:project.id,label:project.project_name}))} onChange={id=>{const next=new URLSearchParams(params);if(id)next.set('project_id',id);else next.delete('project_id');setParams(next);}}/>
      {projectId&&<Link to={`/annotations?project_id=${projectId}`}>查看项目普通审核</Link>}
      <Link to={`/automation${projectId?`?project_id=${projectId}`:''}`}>预标注与AI审核任务</Link>
    </Space>
    <AutomationReviewQueue key={projectId||'all'} projectId={projectId}/>
  </Space>;
}
