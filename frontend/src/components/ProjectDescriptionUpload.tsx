import { useRef, useState } from 'react';
import { Alert, Button, Modal, Space } from 'antd';
import { isAxiosError } from 'axios';
import apiClient from '../api/client';

type Item={filename:string;status:'imported'|'unchanged'|'conflict'|'missing'|'invalid';message?:string};

export default function ProjectDescriptionUpload({projectId,disabled,onImported}:{
  projectId:string;disabled:boolean;onImported:()=>void;
}){
  const input=useRef<HTMLInputElement>(null);
  const [open,setOpen]=useState(false),[file,setFile]=useState<File>();
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[items,setItems]=useState<Item[]>();
  const close=()=>{if(!busy)setOpen(false);};
  const submit=async()=>{
    if(!file||busy)return;
    setBusy(true);setError('');setItems(undefined);
    try{
      const body=new FormData();
      body.append('file',file);
      const response=await apiClient.postForm<{items:Item[]}>(`/api/projects/${projectId}/description-labels`,body,
        {timeout:0,skipErrorToast:true});
      setItems(response.data.items);
      if(response.data.items.some(item=>item.status==='imported'))onImported();
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
  return <>
    <Button disabled={disabled} onClick={()=>setOpen(true)}>上传描述标注 JSON</Button>
    <Modal open={open} title="上传已审核的描述标注" okText="导入为已审核通过" cancelText="关闭"
      onCancel={close} onOk={()=>void submit()} confirmLoading={busy} closable={!busy}
      keyboard={!busy} mask={{closable:!busy}} okButtonProps={{disabled:!file||disabled}} cancelButtonProps={{disabled:busy}}>
      <p>图片入库后再导入。平台按文件名匹配当前项目图片，读取 conversations 中的 gpt 回答作为描述，已有标注会保留。</p>
      <Space wrap>
        <input type="file" hidden ref={input} accept=".json,application/json" aria-label="描述标注 JSON"
          onChange={event=>{setFile(event.target.files?.[0]);setItems(undefined);setError('');event.target.value='';}}/>
        <Button disabled={busy} onClick={()=>input.current?.click()}>选择 JSON 文件</Button>
        {file?.name}
      </Space>
      {error&&<Alert style={{marginTop:12}} type="error" title={error}/>}
      {items&&<Alert style={{marginTop:12}} type={issues.length?'warning':'success'}
        title={`已导入 ${counts.imported} · 相同 ${counts.unchanged} · 缺图 ${counts.missing} · 冲突 ${counts.conflict} · 无效 ${counts.invalid}`}
        description={issues.length>0&&<ul style={{margin:0,maxHeight:160,overflow:'auto'}}>{issues.map((item,index)=>
          <li key={index}>{item.filename} · {item.message}</li>)}</ul>}/>}
    </Modal>
  </>;
}
