import { useState } from 'react';
import { Alert,Button,Modal } from 'antd';
import { isAxiosError } from 'axios';
import { deleteImageCollection,previewImageCollectionDeletion,type ImageCollection,type CollectionDeletion } from '../api/images';

export default function DeleteImageCollection({collection,disabled,onDeleted}:{collection?:ImageCollection;disabled?:boolean;onDeleted:(collection:ImageCollection,result:CollectionDeletion)=>void}) {
  const [target,setTarget]=useState<ImageCollection>(),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [plan,setPlan]=useState<CollectionDeletion>(),[loading,setLoading]=useState(false);
  const fail=(cause:unknown)=>setError(isAxiosError(cause)&&typeof cause.response?.data?.detail==='string'?cause.response.data.detail:'图片集删除核对失败，请重试');
  const preview=async(group:ImageCollection)=>{
    setTarget(group);setPlan(undefined);setError('');setLoading(true);
    try{setPlan(await previewImageCollectionDeletion(group.id));}catch(cause){fail(cause);}finally{setLoading(false);}
  };
  const remove=async()=>{
    if(!target||!plan||busy||loading)return;
    setBusy(true);setError('');
    try{const result=await deleteImageCollection(target.id);onDeleted(target,result);setTarget(undefined);}
    catch(cause){fail(cause);}
    finally{setBusy(false);}
  };
  return <>
    <Button danger disabled={!collection||disabled||busy} onClick={()=>{if(collection)void preview(collection);}}>删除图片集</Button>
    <Modal title="删除图片集及独占图片？" open={!!target} okText="确认删除图片集" cancelText="取消" okButtonProps={{danger:true,disabled:!plan||loading}} confirmLoading={busy}
      closable={!busy&&!loading} mask={{closable:!busy&&!loading}} onCancel={()=>{if(!busy&&!loading)setTarget(undefined);}} onOk={remove}>
      <p>删除“{target?.name}”及其独占图片的图库记录，同时移除该集的设置、归属和已完成上传记录。</p>
      {loading?<p>正在核对图片和项目引用…</p>:plan&&<p>共 {plan.total_images} 张图片：预计删除 {plan.deleted_images} 张独占图片，保留 {plan.kept_images} 张共享图片。</p>}
      <p>其他图片集或项目正在使用的图片会保留，项目标注与冻结数据不变。原始文件继续遵循平台的存储保留策略。</p>
      <p>如有未完成上传或场景分类，请先处理完成再删除。</p>
      {error&&<Alert type="error" showIcon title={error} action={<Button disabled={busy||loading} onClick={()=>{if(target)void preview(target);}}>重新核对</Button>}/>}
    </Modal>
  </>;
}
