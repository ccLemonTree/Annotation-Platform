import { useState } from 'react';
import { Alert,Button,Modal } from 'antd';
import { isAxiosError } from 'axios';
import { deleteAssets } from '../api/assets';

export default function DeleteSelectedImages({ids=[],loadAll,allLabel='删除全部图片',rangeLabel='当前项目图片',onDeleted,disabled=false}:{ids?:string[];loadAll?:()=>Promise<string[]>;allLabel?:string;rangeLabel?:string;onDeleted:(ids:string[])=>void;disabled?:boolean}){
  const [targets,setTargets]=useState<string[]>([]),[open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const prepare=async()=>{setError('');setOpen(true);setTargets(loadAll?[]:[...ids]);if(!loadAll)return;setBusy(true);try{
    setTargets(await loadAll());
  }catch(error){setError(isAxiosError(error)&&typeof error.response?.data?.detail==='string'?error.response.data.detail:'读取全部图片失败，请重试');}finally{setBusy(false);}};
  const remove=async()=>{setBusy(true);setError('');try{
    await deleteAssets(targets);setTargets([]);setOpen(false);onDeleted(targets);
  }catch(error){setError(isAxiosError(error)&&typeof error.response?.data?.detail==='string'?error.response.data.detail:'批量删除失败，请重试');}finally{setBusy(false);}};
  return <><Button danger disabled={(!loadAll&&!ids.length)||disabled||busy} onClick={prepare}>{loadAll?allLabel:`删除选中图片（${ids.length}）`}</Button>
    <Modal title={loadAll?'删除全部图片？':'批量删除选中图片？'} open={open} onCancel={()=>!busy&&setOpen(false)} onOk={remove} confirmLoading={busy}
      okText={loadAll?'删除全部图片':'删除选中图片'} cancelText="取消" okButtonProps={{danger:true,disabled:busy||!targets.length,'aria-label':loadAll?'确认删除全部图片':'确认批量删除'}}>
      {loadAll?<><p>范围：{rangeLabel}，包含所有分页，不依赖勾选。</p><p>{busy&&!targets.length?'正在读取全部图片…':`共 ${targets.length} 张图片，将删除其项目记录及标注。`}</p></>:<p>将删除当前项目选中的 {targets.length} 张图片及其标注，包括在其他分页中勾选的图片。</p>}
      <p>图片库原图、其他项目、磁盘文件和已导出的数据集保留。</p>{error&&<Alert type="error" title={error} action={loadAll&&!targets.length?<Button disabled={busy} onClick={prepare}>重新读取范围</Button>:undefined}/>}
    </Modal></>;
}
