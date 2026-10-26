import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import { Alert, Button, Card, Space, Spin } from 'antd';
import { getProject, type Project } from '../api/projects';
import ProjectWorkflow from '../components/ProjectWorkflow';
import ProjectAnnotationTools from '../components/ProjectAnnotationTools';
import ProjectExport from '../components/ProjectExport';
import ImageAnnotationExport from '../components/ImageAnnotationExport';

export default function ProjectWorkflowPage({stage}:{stage:'prepare'|'deliver'}) {
  const {projectId}=useParams();
  const [result,setResult]=useState<{id:string;project?:Project;failed?:boolean}>(),[revision,setRevision]=useState(0);
  useEffect(()=>{let active=true;
    getProject(projectId!).then(project=>{if(active)setResult({id:projectId!,project});}).catch(()=>{if(active)setResult({id:projectId!,failed:true});});
    return()=>{active=false;};
  },[projectId,revision]);
  if(result&&result.id===projectId&&result.failed)return <Alert type="error" title="读取项目失败，请重试或返回项目列表" action={<Space><Button onClick={()=>{setResult(undefined);setRevision(n=>n+1);}}>重试</Button><Link to="/projects">全部项目</Link></Space>}/>;
  const project=result&&result.id===projectId?result.project:undefined;
  if(!project||project.id!==projectId)return <Spin/>;
  const detection=['detection','segmentation'].includes(project.task_type);
  return <>
    <ProjectWorkflow project={project} active={stage}/>
    {stage==='prepare'?<Card title="标注准备">
      <p className="workspace-help">按需检测切图、批量生成描述或建立检索索引，然后进入标注。已有图片可直接标注。</p>
      {project.task_type==='detection'&&<p><Link to={`/automation?project_id=${project.id}`}>批量检测预标注 / AI审核 →</Link></p>}
      <ProjectAnnotationTools key={project.id} project={project} onRefresh={()=>setRevision(n=>n+1)}/>
    </Card>:<Space orientation="vertical" size="middle" style={{width:'100%'}}>
      <Card title="导出标注数据"><p className="workspace-help">确认审核结果后导出。修改标注后重新导出或冻结；已冻结版本保持不变。</p>
        {detection?<ProjectExport projectId={project.id}/>:<ImageAnnotationExport projectId={project.id} taskType={project.task_type}/>}</Card>
      <Card title="使用平台数据训练"><p className="workspace-help">{detection?'先在导出窗口划分并冻结 train/val，再创建训练任务选择冻结版本。':'创建训练任务时已预选本项目，可冻结审核通过的标注用于训练。'}保存配置后仍需预检查并手动启动。</p>
        <Link className="dashboard-primary-link" to={`/training?project_id=${project.id}`}>前往模型训练 →</Link></Card>
      <Card title="检查数据与模型"><Space wrap><Link to={`/analytics?project_id=${project.id}`}>项目质量与效率</Link>
        {project.task_type==='detection'&&<Link to={`/validation?project_id=${project.id}`}>创建验证集 / 验证训练权重</Link>}
        <Link to={`/annotations?project_id=${project.id}`}>返回审核</Link></Space></Card>
    </Space>}
  </>;
}
