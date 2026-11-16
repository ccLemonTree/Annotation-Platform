import { useEffect,useState } from 'react';
import type {ReactNode} from 'react';
import { Link,useParams } from 'react-router-dom';
import { Alert,Spin } from 'antd';
import { getAsset,type Asset } from '../api/assets';
import { getProject,type Project } from '../api/projects';
import DetectionWorkspace from './DetectionWorkspace';
import ImageAnnotationWorkspace from './ImageAnnotationWorkspace';
import ProjectWorkflow from '../components/ProjectWorkflow';
import { useCurrentUser } from '../components/currentUser';
import {getAttributeOptions,getCropLabels} from '../api/objectAttributes';
import AnnotationStage,{AnnotationFullscreenButton} from '../components/AnnotationStage';
import useAnnotationImageView from '../components/useAnnotationImageView';

export default function AnnotationWorkspace(){
  const admin=useCurrentUser()?.role==='admin';
  const [imageView,setImageView]=useAnnotationImageView(),[detectionView,setDetectionView]=useAnnotationImageView();
  const {assetId}=useParams();const [data,setData]=useState<{asset:Asset;project:Project}>(),[error,setError]=useState<{id:string;message:string}>();
  const [options,setOptions]=useState<{projectId:string;values:Record<string,string[]>;error?:string}>(),[optionsReload,setOptionsReload]=useState(0);
  const [cropCategories,setCropCategories]=useState<{projectId:string;labels:string[]}>();
  const projectId=data?.project.id,attributeSchema=JSON.stringify(data?.project.label_schema?.object_attributes);
  const taskType=data?.project.task_type;
  const mode=taskType==='detection'?'detection':'image';
  const cropLabels=cropCategories?.projectId===projectId?cropCategories?.labels||[]:[];
  useEffect(()=>{
    if(!projectId||taskType==='segmentation')return;
    let active=true;
    getCropLabels(projectId).then(labels=>{
      if(active)setCropCategories({projectId,labels:[...new Set(labels.map(label=>label.toLowerCase().replace(/^sam3_/,'')))].filter(Boolean).sort()});
    }).catch(()=>{if(active)setCropCategories({projectId,labels:[]});});
    return()=>{active=false;};
  },[projectId,taskType]);
  useEffect(()=>{
    if(!projectId||!attributeSchema||taskType==='detection'||taskType==='classification')return;
    let active=true;
    getAttributeOptions(projectId).then(values=>{if(active)setOptions({projectId,values});})
      .catch(()=>{if(active)setOptions({projectId,values:{},error:'候选值读取失败，仍可手动填写。请点击更新候选值重试。'});});
    return()=>{active=false;};
  },[projectId,taskType,attributeSchema,optionsReload]);
  useEffect(()=>{let active=true;
    getAsset(assetId!).then(async asset=>({asset,project:await getProject(asset.project_id)})).then(result=>{if(active)setData(result);})
      .catch(()=>{if(active)setError({id:assetId!,message:'图片或项目读取失败，请返回项目重新打开'});});return()=>{active=false;};
  },[assetId]);
  let content:ReactNode;
  if(error&&error.id===assetId)content=<div className="annotation-stage-status"><Alert type="error" title={error.message} action={<Link to="/annotations">返回标注项目</Link>}/><AnnotationFullscreenButton/></div>;
  else if(!data||data.asset.id!==assetId&&mode!=='detection')content=<div className="annotation-stage-status"><Spin/><AnnotationFullscreenButton/></div>;
  else if(data.asset.id===assetId&&data.project.label_schema?.annotation_scope==='crops'&&data.asset.source_type!=='model_crop')content=<div className="annotation-stage-status"><Alert type="info" title="此项目仅标注裁剪图，原图不在标注队列中" action={<Link to={admin?`/projects/${data.project.id}/images`:'/annotations'}>{admin?'选择项目裁剪图':'返回标注项目'}</Link>}/><AnnotationFullscreenButton/></div>;
  else if(taskType==='segmentation')content=<Alert type="info" title="图像分割标注暂不支持"/>;
  else content=<><ProjectWorkflow project={data.project} active="annotate"/>
    {mode==='image'?<ImageAnnotationWorkspace key={assetId} {...data} cropLabels={cropLabels} imageView={imageView} setImageView={setImageView} suggestions={options?.projectId===projectId?options?.values:{}} suggestionsError={options?.projectId===projectId?options?.error:undefined} onRefreshSuggestions={()=>setOptionsReload(v=>v+1)}/>:<DetectionWorkspace initial={data} cropLabels={cropLabels} imageView={detectionView} setImageView={setDetectionView}/>}</>;
  return <AnnotationStage>{content}</AnnotationStage>;
}
