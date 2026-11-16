import {useRef,useState} from 'react';
import {Alert,Button,Modal,Radio,Space,Typography} from 'antd';
import {isAxiosError} from 'axios';
import apiClient from '../api/client';

const isClasses=(name:string)=>/^(data\.ya?ml|classes\.txt)$/i.test(name);
type Result={filename:string;status:'imported'|'unchanged'|'conflict'|'missing'|'invalid';message?:string};

export default function ProjectYoloLabelUpload({projectId,disabled,onImported}:{projectId:string;disabled:boolean;onImported:()=>void}){
  const [open,setOpen]=useState(false),[mode,setMode]=useState<'folder'|'zip'>('folder');
  const [files,setFiles]=useState<File[]>([]),[classes,setClasses]=useState<File>(),[zip,setZip]=useState<File>();
  const [busy,setBusy]=useState(false),[processed,setProcessed]=useState(0),[error,setError]=useState('');
  const [summary,setSummary]=useState<{imported:number;unchanged:number;issues:number;examples:Result[]}>();
  const folder=useRef<HTMLInputElement>(null),archive=useRef<HTMLInputElement>(null),classPicker=useRef<HTMLInputElement>(null);
  const uploadBatch=useRef<string>(undefined);
  const ready=mode==='zip'?!!zip:files.length>0;
  const close=()=>{if(!busy)setOpen(false);};
  const start=async()=>{
    if(busy||!ready)return;
    const selected=mode==='folder'?files:[];
    const selectedZip=mode==='zip'?zip:undefined;
    if(new Set(selected.map(file=>file.name.toLowerCase())).size!==selected.length){
      setError('标签文件夹内存在同名 TXT，请先处理重名文件');return;
    }
    setBusy(true);setProcessed(0);setError('');setSummary(undefined);
    const result={imported:0,unchanged:0,issues:0,examples:[] as Result[]};
    const batchId=uploadBatch.current??=Array.from(crypto.getRandomValues(new Uint8Array(16)),value=>value.toString(16).padStart(2,'0')).join('');
    try{
      for(let index=0;index<(selectedZip?1:selected.length);index+=16){
        const data=new FormData();
        data.append('batch_id',batchId);
        if(classes)data.append('classes',classes,classes.name);
        if(selectedZip)data.append('archive',selectedZip);
        else for(const file of selected.slice(index,index+16))data.append('files',file,file.name);
        const {data:{items}}=await apiClient.post<{items:Result[]}>(`/api/projects/${projectId}/yolo-labels`,data,
          {headers:{'Content-Type':'multipart/form-data'},timeout:0,skipErrorToast:true});
        for(const item of items){
          if(item.status==='imported'||item.status==='unchanged')result[item.status]++;
          else{result.issues++;if(result.examples.length<20)result.examples.push(item);}
        }
        setProcessed(count=>count+items.length);
      }
    }catch(cause){
      const detail=isAxiosError(cause)?cause.response?.data?.detail:undefined;
      setError(typeof detail==='string'?detail:'导入请求中断。可重试，已保存的相同标注会跳过。');
    }finally{
      setBusy(false);setSummary(result);if(result.imported)onImported();
    }
  };
  const chooseFolder=(picked:File[])=>{
    uploadBatch.current=undefined;
    const tables=picked.filter(file=>isClasses(file.name));
    setClasses(tables.length===1?tables[0]:undefined);
    setFiles(picked.filter(file=>/\.txt$/i.test(file.name)&&!isClasses(file.name)));
    setSummary(undefined);setError('');
  };
  return <>
    <Button disabled={disabled} onClick={()=>setOpen(true)}>单独上传 YOLO 标签</Button>
    <Modal open={open} title="单独上传 YOLO 标签" onCancel={close} onOk={()=>void start()}
      okText="导入待审核标注" cancelText="关闭" confirmLoading={busy} cancelButtonProps={{disabled:busy}}
      okButtonProps={{disabled:!ready}}>
      <Typography.Paragraph>先上传图片。系统按同名 TXT 匹配本项目已入库的图片，空 TXT 表示负样本，已有标注不会被覆盖。</Typography.Paragraph>
      <Space orientation="vertical" size="middle" style={{width:'100%'}}>
        <Radio.Group aria-label="YOLO 标签来源" value={mode} disabled={busy}
          onChange={event=>{uploadBatch.current=undefined;setMode(event.target.value);setClasses(undefined);setSummary(undefined);setError('');}}
          options={[{value:'folder',label:'标签文件夹'},{value:'zip',label:'标签 ZIP'}]}/>
        <input type="file" multiple hidden ref={element=>{folder.current=element;element?.setAttribute('webkitdirectory','');}}
          aria-label="YOLO 标签文件夹" onChange={event=>{chooseFolder(Array.from(event.target.files||[]));event.target.value='';}}/>
        <input type="file" hidden ref={archive} accept=".zip" aria-label="YOLO 标签 ZIP"
          onChange={event=>{uploadBatch.current=undefined;setZip(event.target.files?.[0]);setSummary(undefined);setError('');event.target.value='';}}/>
        <input type="file" hidden ref={classPicker} accept=".yaml,.yml,.txt" aria-label="YOLO 类别表"
          onChange={event=>{setClasses(event.target.files?.[0]);event.target.value='';}}/>
        <Space wrap>
          {mode==='folder'?<><Button disabled={busy} onClick={()=>folder.current?.click()}>选择标签文件夹</Button><span>{files.length} 个 TXT</span></>:
            <><Button disabled={busy} onClick={()=>archive.current?.click()}>选择标签 ZIP</Button><span>{zip?.name}</span></>}
        </Space>
        <Space wrap><Button disabled={busy} onClick={()=>classPicker.current?.click()}>选择类别表（可选）</Button>
          <span>{classes?.name||(mode==='zip'?'优先读取 ZIP 内的类别表，缺省可在标注页设置':'可留空，在标注页设置类别')}</span>
          {classes&&<Button disabled={busy} type="link" onClick={()=>setClasses(undefined)}>清除</Button>}
        </Space>
        {busy&&<span role="status">{mode==='zip'?'正在导入标签 ZIP…':`已处理 ${processed} / ${files.length}`}</span>}
        {error&&<Alert type="error" title={error}/>}
        {summary&&<Alert type={summary.issues?'warning':'info'}
          title={`已导入 ${summary.imported} · 已有相同标注 ${summary.unchanged} · 未导入 ${summary.issues}`}
          description={summary.examples.length>0&&<ul style={{margin:0,maxHeight:160,overflow:'auto'}}>{summary.examples.map((item,index)=>
            <li key={index}>{item.filename} · {item.message}</li>)}</ul>}/>}
      </Space>
    </Modal>
  </>;
}
