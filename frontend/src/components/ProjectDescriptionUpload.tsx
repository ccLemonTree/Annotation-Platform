import { useRef, useState } from 'react';
import { Alert, Button, Checkbox, Modal, Space } from 'antd';
import { isAxiosError } from 'axios';
import apiClient from '../api/client';

type Item={filename:string;status:'imported'|'unchanged'|'conflict'|'missing'|'invalid';message?:string};

export default function ProjectDescriptionUpload({projectId,disabled,onImported,reviewedDescriptions=false}:{
  projectId:string;disabled:boolean;onImported:()=>void;reviewedDescriptions?:boolean;
}){
  const input=useRef<HTMLInputElement>(null);
  const [open,setOpen]=useState(false),[file,setFile]=useState<File>();
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[items,setItems]=useState<Item[]>();
  const [cropFromBoxes,setCropFromBoxes]=useState(false),[cropCount,setCropCount]=useState<number>();
  const close=()=>{if(!busy)setOpen(false);};
  const submit=async()=>{
    if(!file||busy)return;
    setBusy(true);setError('');setItems(undefined);
    try{
      const body=new FormData();
      body.append('file',file);
      if(!reviewedDescriptions&&cropFromBoxes)body.append('crop_from_boxes','true');
      const endpoint=reviewedDescriptions?'description-labels':'annotation-labels';
      const response=await apiClient.postForm<{items:Item[];crop_count?:number}>(`/api/projects/${projectId}/${endpoint}`,body,
        {timeout:0,skipErrorToast:true});
      setItems(response.data.items);
      setCropCount(response.data.crop_count);
      if(response.data.items.some(item=>item.status==='imported')||response.data.crop_count)onImported();
    }catch(cause){
      const response=isAxiosError(cause)?cause.response:undefined;
      const detail=response?.data?.detail;
      setError(typeof detail==='string'?detail:response?.status===422?'服务器未收到有效的 JSON 文件，请重新选择后提交。':
        response?`导入失败（HTTP ${response.status}），请重试。`:'导入请求中断，可以重新提交同一个文件。');
    }finally{setBusy(false);}
  };
  const counts={imported:0,unchanged:0,conflict:0,missing:0,invalid:0};
  items?.forEach(item=>counts[item.status]++);
  const issues=items?.filter(item=>!['imported','unchanged'].includes(item.status)).slice(0,20)??[];
  const title=reviewedDescriptions?'上传描述标注 JSON':'上传标注 JSON';
  return <>
    <Button disabled={disabled} onClick={()=>setOpen(true)}>{title}</Button>
    <Modal open={open} title={title} okText={reviewedDescriptions?'导入为已审核通过':'导入待审核'} cancelText="关闭"
      onCancel={close} onOk={()=>void submit()} confirmLoading={busy} closable={!busy}
      keyboard={!busy} mask={{closable:!busy}} okButtonProps={{disabled:!file||disabled}} cancelButtonProps={{disabled:busy}}>
      <p>图片入库后再导入。平台按文件名匹配当前项目图片，已有标注会保留。</p>
      <p>{reviewedDescriptions?'读取 conversations 中的 gpt 回答作为描述。':
        '文件名对应列表时，为原图导入多个检测框；对应对象时，为裁剪图导入整图标注。box 使用 [左, 上, 右, 下] 像素坐标，description 可填写大模型描述。'}</p>
      {!reviewedDescriptions&&<p><Checkbox checked={cropFromBoxes} disabled={busy} onChange={event=>setCropFromBoxes(event.target.checked)}>
        根据原图检测框生成裁剪图
      </Checkbox></p>}
      <Space wrap>
        <input type="file" hidden ref={input} accept=".json,application/json" aria-label={title}
          onChange={event=>{setFile(event.target.files?.[0]);setItems(undefined);setError('');event.target.value='';}}/>
        <Button disabled={busy} onClick={()=>input.current?.click()}>选择 JSON 文件</Button>
        {file?.name}
      </Space>
      {error&&<Alert style={{marginTop:12}} type="error" title={error}/>}
      {items&&<Alert style={{marginTop:12}} type={issues.length?'warning':'success'}
        title={`已导入 ${counts.imported} · 相同 ${counts.unchanged} · 缺图 ${counts.missing} · 冲突 ${counts.conflict} · 无效 ${counts.invalid}${cropCount===undefined?'':` · 裁剪图 ${cropCount}`}`}
        description={issues.length>0&&<ul style={{margin:0,maxHeight:160,overflow:'auto'}}>{issues.map((item,index)=>
          <li key={index}>{item.filename} · {item.message}</li>)}</ul>}/>}
    </Modal>
  </>;
}
