import { useState } from 'react';
import { Link,useNavigate } from 'react-router-dom';
import { Button, message } from 'antd';
import { isAxiosError } from 'axios';
import { assetImageKind, defaultAnnotationImageKind, firstAnnotationImage, getAsset, getAssetNeighbors, readAssetImageKind } from '../api/assets';
import { readAnnotationProgress, clearAnnotationProgress } from './annotationProgress';
import type { TaskType } from '../api/projects';
import { useCurrentUser } from './currentUser';

export default function ContinueAnnotation({ projectId, taskType, reviewSearch = '' }: { projectId: string; taskType?: TaskType; reviewSearch?: string }) {
  const navigate = useNavigate();
  const admin=useCurrentUser()?.role==='admin';
  const [notice, context] = message.useMessage();
  const [busy, setBusy] = useState(false);
  const [removed, setRemoved] = useState(false);
  const progress = !removed && readAnnotationProgress(projectId);
  if(taskType==='vector')return <Button aria-label={progress?'继续标注':'开始标注'} onClick={()=>navigate(`/annotate/project/${projectId}`)}>{progress?'继续标注':'开始标注'}</Button>;
  const resume = async () => {
    setBusy(true);
    try {
      const search=new URLSearchParams(reviewSearch);
      const mode=taskType==='detection'?'detection':'image';
      search.set('kind',mode);
      const workJobId=search.get('work_job_id')||undefined;
      const explicit=search.has('image_kind');
      const preferred=explicit?readAssetImageKind(search.get('image_kind')):defaultAnnotationImageKind(mode);
      let asset;
      if(progress){
        try{const previous=await getAsset(progress.assetId,true);
          if(previous.project_id===projectId){
            const neighbors=await getAssetNeighbors(previous.id,undefined,undefined,undefined,{work_job_id:workJobId});
            if(neighbors.matched!==false)asset=previous;
          }
        }catch(error){if(!isAxiosError(error)||![404,409].includes(error.response?.status||0))throw error;}
        if(!asset&&!workJobId){clearAnnotationProgress(projectId);setRemoved(true);}
      }
      if(!asset||preferred!=='all'&&assetImageKind(asset)!==preferred){
        const first=await firstAnnotationImage(projectId,search,preferred);
        asset=first||(explicit?undefined:asset||await firstAnnotationImage(projectId,search,'all'));
      }
      if(!asset){notice.info(admin?<span>当前标注范围内暂无图片；请添加图片或完成检测切图。<Link to={`/projects/${projectId}/prepare`}>前往标注准备 →</Link></span>:'当前标注范围内暂无图片，请联系管理员添加图片或完成检测切图。');return;}
      if(!explicit)search.set('image_kind',assetImageKind(asset));
      if (reviewSearch) search.set('from',search.get('stage')==='annotate'?'annotation':'review');
      navigate(`/annotate/${asset.id}${search.size ? `?${search}` : ''}`);
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 404) {
        clearAnnotationProgress(projectId); setRemoved(true); notice.warning('上次的图片已移除，请重新选择图片');
      } else notice.error('继续位置读取失败，请重试');
    } finally { setBusy(false); }
  };
  return <>{context}<Button aria-label={progress?'继续标注':'开始标注'} title={progress?`上次打开：${progress.name}`:undefined} disabled={busy} loading={busy} onClick={resume}>{progress?'继续标注':'开始标注'}</Button></>;
}
