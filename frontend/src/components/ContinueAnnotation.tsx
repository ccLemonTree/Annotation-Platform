import { useState } from 'react';
import { Link,useNavigate } from 'react-router-dom';
import { Button, message } from 'antd';
import { isAxiosError } from 'axios';
import { getAsset, getAssetNeighbors, getAssets } from '../api/assets';
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
      let asset;
      if(progress){
        try{const previous=await getAsset(progress.assetId);
          if(previous.project_id===projectId){await getAssetNeighbors(previous.id);asset=previous;}
        }catch(error){if(!isAxiosError(error)||![404,409].includes(error.response?.status||0))throw error;}
        if(!asset){clearAnnotationProgress(projectId);setRemoved(true);}
      }
      if(!asset)asset=(await getAssets({project_id:projectId,scope:'annotation',page:1,page_size:1})).items[0];
      if(!asset){notice.info(admin?<span>当前标注范围内暂无图片；请添加图片或完成检测切图。<Link to={`/projects/${projectId}/prepare`}>前往标注准备 →</Link></span>:'当前标注范围内暂无图片，请联系管理员添加图片或完成检测切图。');return;}
      navigate(`/annotate/${asset.id}${reviewSearch ? `?from=review&${reviewSearch}` : ''}`);
    } catch (error) {
      if (isAxiosError(error) && error.response?.status === 404) {
        clearAnnotationProgress(projectId); setRemoved(true); notice.warning('上次的图片已移除，请重新选择图片');
      } else notice.error('继续位置读取失败，请重试');
    } finally { setBusy(false); }
  };
  return <>{context}<Button aria-label={progress?'继续标注':'开始标注'} title={progress?`上次打开：${progress.name}`:undefined} disabled={busy} loading={busy} onClick={resume}>{progress?'继续标注':'开始标注'}</Button></>;
}
