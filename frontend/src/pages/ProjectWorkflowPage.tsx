import { lazy, Suspense, useEffect, useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { Alert, Button, Card, Space, Spin } from 'antd';
import { getProject, type Project } from '../api/projects';
import ProjectWorkflow from '../components/ProjectWorkflow';
import ProjectExport from '../components/ProjectExport';
import type { ExportScope } from '../components/ProjectExport';
import ImageAnnotationExport from '../components/ImageAnnotationExport';
import FrozenDatasetList from '../components/FrozenDatasetList';
import type { AttributeSchema } from '../api/objectAttributes';
const TrainingPage = lazy(() => import('./TrainingPage'));
const ModelPreviewPage = lazy(() => import('./ModelPreviewPage'));
const SmartEyePage = lazy(() => import('./SmartEyePage'));
const AutomationPage = lazy(() => import('./AutomationPage'));
const AutomationReviewQueue = lazy(() => import('../components/automation/AutomationReviewQueue'));

export default function ProjectWorkflowPage({stage}:{stage:'deliver'|'test'|'capture'|'automation'|'review'}) {
  const {projectId}=useParams();
  const [params,setParams]=useSearchParams();
  const imageKind=params.get('image_kind');
  const scope:ExportScope={work_job_id:params.get('work_job_id')||undefined,image_kind:imageKind==='crops'||imageKind==='originals'?imageKind:'all'};
  const scoped=!!scope.work_job_id||scope.image_kind!=='all';
  const scopeName={all:'原图与裁剪图',originals:'仅原图',crops:'仅裁剪图'}[scope.image_kind];
  const [result,setResult]=useState<{id:string;project?:Project;failed?:boolean}>(),[revision,setRevision]=useState(0);
  const [snapshotRevision,setSnapshotRevision]=useState(0);
  const [downloads,setDownloads]=useState<string[]>([]);
  const snapshotsChanged=()=>setSnapshotRevision(n=>n+1);
  const freezeQueued=(downloadJobId?:string)=>{
    if(downloadJobId)setDownloads(ids=>ids.includes(downloadJobId)?ids:[...ids,downloadJobId]);
    snapshotsChanged();
  };
  useEffect(()=>{let active=true;
    getProject(projectId!).then(project=>{if(active)setResult({id:projectId!,project});}).catch(()=>{if(active)setResult({id:projectId!,failed:true});});
    return()=>{active=false;};
  },[projectId,revision]);
  if(result&&result.id===projectId&&result.failed)return <Alert type="error" title="读取项目失败，请重试或返回项目列表" action={<Space><Button onClick={()=>{setResult(undefined);setRevision(n=>n+1);}}>重试</Button><Link to="/projects">全部项目</Link></Space>}/>;
  const project=result&&result.id===projectId?result.project:undefined;
  if(!project||project.id!==projectId)return <Spin/>;
  if(stage==='test')return <>
    <ProjectWorkflow project={project} active="test"/>
    <Suspense fallback={<div role="status"><Spin /> 正在加载测试与评估…</div>}>{['detection','caption','vector'].includes(project.task_type)?<ModelPreviewPage key={project.id} embeddedProject={project}/>:<Alert type="info" showIcon title="当前项目类型暂不支持模型测试与评估" description="目前支持目标检测、图片描述和向量检索项目。"/>}</Suspense>
  </>;
  return <>
    <ProjectWorkflow project={project} active={stage==='capture'?'images':stage==='deliver'?'deliver':stage==='review'?'review':'prepare'}/>
    <Suspense fallback={<div role="status"><Spin /> 正在加载项目内容…</div>}>{stage==='capture'?<SmartEyePage embeddedProject={project}/>:stage==='automation'?<AutomationPage embeddedProject={project}/>:stage==='review'?<AutomationReviewQueue projectId={project.id}/>:<Space orientation="vertical" size="middle" style={{width:'100%'}}>
      <Card title="导出与冻结数据" id="freeze-data"><p className="workspace-help">先确认图片范围和标注版本，再冻结数据。下方训练任务选择已经冻结的版本。</p>
        {scoped&&<Alert style={{marginBottom:12}} type="info" title={`${scope.work_job_id?`工作流步骤 ${scope.work_job_id.slice(0,8)} 的结果`:'项目图片'} · ${scopeName}`}
          action={<Button size="small" onClick={()=>setParams({})}>切换到全部项目图片</Button>}/>}
        <Space wrap>{project.task_type==='detection'?<ProjectExport projectId={project.id} scope={scope} onFrozen={freezeQueued}/>:
          project.task_type!=='segmentation'&&<ImageAnnotationExport projectId={project.id} taskType={project.task_type} scope={scope}
            onFrozen={snapshotsChanged} attributeSchema={project.task_type==='classification'?undefined:project.label_schema?.object_attributes as AttributeSchema|undefined}/>}</Space></Card>
      <FrozenDatasetList key={`snapshots-${project.id}`} projectId={project.id} revision={snapshotRevision} onDeleted={snapshotsChanged}
        downloads={downloads} onDownloadHandled={id=>setDownloads(ids=>ids.filter(jobId=>jobId!==id))}/>
      <TrainingPage key={project.id} embeddedProject={project}/>
      <Card title="检查数据与模型"><Space wrap><Link to={`/analytics?project_id=${project.id}`}>项目质量与效率</Link>
        {['detection','caption','vector'].includes(project.task_type)&&<Link to={`/projects/${project.id}/test?view=validation`}>模型测试与评估</Link>}
        <Link to={`/annotations?project_id=${project.id}${params.size?'&'+params:''}`}>返回审核</Link></Space></Card>
    </Space>}</Suspense>
  </>;
}
