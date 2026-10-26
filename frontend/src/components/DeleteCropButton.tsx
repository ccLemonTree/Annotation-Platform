import { useState } from 'react';
import { Alert,Button,Modal } from 'antd';
import { isAxiosError } from 'axios';
import { deleteAsset } from '../api/assets';

export default function DeleteCropButton({assetId,name,onDeleted,disabled=false}:{assetId:string;name:string;onDeleted:()=>void;disabled?:boolean}){
  const [open,setOpen]=useState(false),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const remove=async()=>{setBusy(true);setError('');try{
    await deleteAsset(assetId);setOpen(false);onDeleted();
  }catch(error){setError(isAxiosError(error)&&typeof error.response?.data?.detail==='string'?error.response.data.detail:'裁剪图删除失败，请重试');}finally{setBusy(false);}};
  return <><Button type="link" danger disabled={disabled||busy} aria-label={`删除裁剪图 ${name}`} onClick={()=>{setError('');setOpen(true);}}>删除裁剪图</Button>
    <Modal title="删除这张裁剪图？" open={open} onCancel={()=>!busy&&setOpen(false)} onOk={remove} confirmLoading={busy}
      okText="删除裁剪图" cancelText="取消" okButtonProps={{danger:true,disabled:busy,'aria-label':'确认删除裁剪图'}}>
      <p style={{overflowWrap:'anywhere'}}>{name}</p><p>删除当前项目中的裁剪图及其标注，原图、其他项目和已导出的数据集保留。</p>
      {error&&<Alert type="error" title={error}/>}
    </Modal></>;
}
