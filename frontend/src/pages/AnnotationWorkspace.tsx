import { useEffect,useState } from 'react';
import { Link,useParams } from 'react-router-dom';
import { Alert,Spin } from 'antd';
import { getAsset,type Asset } from '../api/assets';
import { getProject,type Project } from '../api/projects';
import DetectionWorkspace from './DetectionWorkspace';
import ImageAnnotationWorkspace from './ImageAnnotationWorkspace';
import ProjectWorkflow from '../components/ProjectWorkflow';
import { useCurrentUser } from '../components/currentUser';

export default function AnnotationWorkspace(){
  const admin=useCurrentUser()?.role==='admin';
  const {assetId}=useParams();const [data,setData]=useState<{asset:Asset;project:Project}>(),[error,setError]=useState<{id:string;message:string}>();
  useEffect(()=>{let active=true;
    getAsset(assetId!).then(async asset=>({asset,project:await getProject(asset.project_id)})).then(result=>{if(active)setData(result);})
      .catch(()=>{if(active)setError({id:assetId!,message:'图片或项目读取失败，请返回项目重新打开'});});return()=>{active=false;};
  },[assetId]);
  if(error&&error.id===assetId&&data?.project.task_type!=='detection')return <Alert type="error" title={error.message} action={<Link to="/annotations">返回标注项目</Link>}/>;
  if(!data||data.asset.id!==assetId&&data.project.task_type!=='detection')return <Spin/>;
  if(data.asset.id===assetId&&data.project.label_schema?.annotation_scope==='crops'&&data.asset.source_type!=='model_crop')return <Alert type="info" title="此项目仅标注裁剪图，原图不在标注队列中" action={<Link to={admin?`/projects/${data.project.id}/images`:'/annotations'}>{admin?'选择项目裁剪图':'返回标注项目'}</Link>}/>;
  return <><ProjectWorkflow project={data.project} active="annotate"/>{['classification','caption','vector'].includes(data.project.task_type)?<ImageAnnotationWorkspace key={assetId} {...data}/>:<DetectionWorkspace/>}</>;
}
