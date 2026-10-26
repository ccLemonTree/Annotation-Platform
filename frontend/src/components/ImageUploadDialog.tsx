import { useCallback,useEffect,useRef,useState } from 'react';
import { Alert,AutoComplete,Button,Checkbox,Input,InputNumber,Modal,Progress,Space,Table,Tag } from 'antd';
import type { ImageCollection,ImageIngestConfig } from '../api/images';
import { getImageIngestSettings } from '../api/images';
import { getUploadConfig,listUploads,deleteUpload,uploadResumable } from '../api/imageUploads';
import type { UploadConfig,UploadJob } from '../api/imageUploads';

type Row={file:File;key:string;phase:string;percent:number;done?:boolean;summary?:string};
const accepted=/\.(jpe?g|png|bmp|webp|zip|tar|tar\.gz|tgz)$/i;
export default function ImageUploadDialog({open,initialName,collections,onClose,onImported,onViewCollection}:{
  open:boolean;initialName:string;collections:ImageCollection[];onClose:()=>void;onImported:(name:string)=>void;onViewCollection:(id:string)=>void;
}){
  const [name,setName]=useState(initialName),[minimum,setMinimum]=useState<number>();
  const [config,setConfig]=useState<UploadConfig>(),[defaults,setDefaults]=useState<ImageIngestConfig>();
  const [rows,setRows]=useState<Row[]>([]),[records,setRecords]=useState<UploadJob[]>([]);
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  const [annotated,setAnnotated]=useState(false),[trust,setTrust]=useState(false);
  const [admin]=useState(()=>{try{return JSON.parse(localStorage.getItem('user')||'null')?.role==='admin';}catch{return false;}});
  const abort=useRef<AbortController|null>(null),picker=useRef<HTMLInputElement>(null),folder=useRef<HTMLInputElement>(null);
  const wasOpen=useRef(false);
  const recordRequest=useRef(0);
  const refresh=useCallback(async()=>{
    const request=++recordRequest.current;
    try{const records=await listUploads();if(request===recordRequest.current)setRecords(records);}
    catch{if(request===recordRequest.current)setError('上传记录读取失败');}
  },[]);
  useEffect(()=>{
    const opening=open&&!wasOpen.current;wasOpen.current=open;
    if(opening&&!rows.length){setName(initialName);setMinimum(undefined);setError('');setNotice('');}
  },[open,initialName,rows.length]);
  // shortcut: transfers live on this page; an app-level queue is needed to survive navigation.
  useEffect(()=>()=>abort.current?.abort(),[]);
  useEffect(()=>{
    if(!open)return;
    let active=true;
    Promise.all([getUploadConfig(),getImageIngestSettings()]).then(([config,defaults])=>{
      if(active){setConfig(config);setDefaults(defaults);}
    }).catch(()=>{if(active)setError('上传配置或续传记录读取失败，请关闭后重试');});
    return ()=>{active=false;};
  },[open]);
  useEffect(()=>{
    if(!open)return;
    let active=true,timer:ReturnType<typeof setTimeout>;
    const poll=async()=>{await refresh();if(active)timer=setTimeout(poll,2000);};
    void poll();
    return()=>{active=false;recordRequest.current++;clearTimeout(timer);};
  },[open,refresh]);
  const resetRows=()=>setRows(current=>current.map(({file,key})=>({file,key,phase:'等待上传',percent:0})));
  const add=(files:File[])=>{
    const valid=files.filter(file=>annotated?/\.zip$/i.test(file.name):accepted.test(file.name));
    if(valid.length<files.length)setNotice(`已忽略 ${files.length-valid.length} 个非图片/压缩包文件`);
    const previous=new Map(rows.map(row=>[row.key,row]));
    for(const file of valid){const key=[file.webkitRelativePath||file.name,file.size,file.lastModified].join(':');
      if(!previous.has(key))previous.set(key,{file,key,phase:'等待上传',percent:0});}
    setRows([...previous.values()]);
  };
  const start=async()=>{
    if(abort.current)return;
    if(!config||!name.trim()||!rows.length){setError('请填写图片集名称并选择文件');return;}
    localStorage.setItem('image-upload-collection',name.trim());
    const controller=new AbortController();abort.current=controller;setBusy(true);setError('');
    const update=(key:string,values:Partial<Row>)=>setRows(current=>current.map(row=>row.key===key?{...row,...values}:row));
    try{
      for(const row of rows){
        if(row.done)continue;
        const result=await uploadResumable(row.file,name.trim(),minimum,config,controller.signal,
          (phase,percent)=>update(row.key,{phase,percent}),annotated?{annotation_format:'yolo',trust_annotations:trust}:undefined);
        update(row.key,{done:true,phase:'已完成',summary:`新增 ${result.result.imported}，重复 ${result.result.duplicates}，低分辨率 ${result.result.filtered}，无效 ${result.result.invalid}`+(annotated?`；导入标注 ${result.result.annotated||0}，已有相同标注 ${result.result.annotation_skipped||0}，冲突保留 ${result.result.conflicts||0}`:'')+(result.result.errors.length?'；'+result.result.errors.join('；'):'')});
        onImported(name.trim());
      }
      setNotice('所选文件已处理完成，可继续添加文件或关闭窗口');
    }catch(error){
      if(controller.signal.aborted)setNotice('已暂停传输。已收到的分片保留，后台入库任务继续执行');
      else setError(error instanceof Error?error.message:'上传中断，点击继续上传可补传缺失分片');
    }finally{setBusy(false);abort.current=null;void refresh();}
  };
  const policy=collections.find(group=>group.name===name.trim())?.ingest_config||defaults;
  return <Modal title="上传到图片库" open={open} width={900} style={{top:24}}
    styles={{body:{maxHeight:'calc(100dvh - 200px)',overflowY:'auto',paddingRight:8}}} onCancel={onClose}
    footer={<Space><Button onClick={onClose}>关闭</Button>
      {busy?<Button onClick={()=>abort.current?.abort()}>暂停上传</Button>:<Button type="primary" onClick={start} disabled={!config||!rows.length}>开始 / 继续上传</Button>}</Space>}>
    <p className="workspace-help">选择图片、文件夹或ZIP/TAR（含.tar.gz/.tgz），子目录图片加入同一目标图片集。关闭窗口会继续上传当前队列，暂停请点击“暂停上传”；离开图片管理页、刷新或断网后重选相同文件，只补传缺失分片。</p>
    {error&&<Alert type="error" title={error} showIcon style={{marginBottom:12}}/>}
    {notice&&<Alert type="info" title={notice} showIcon style={{marginBottom:12}}/>}
    {records.some(job=>job.status==='completed'&&!job.result.imported&&!job.result.duplicates)&&<Alert type="warning" showIcon style={{marginBottom:12}}
      title="部分压缩包处理完成，但没有图片入库" description="请查看下方记录的过滤、无效和忽略数量。若图片因尺寸不足被过滤，可将本次上传长边阈值设为 0，再重新选择原文件上传；图片集原有规则不会改变。"/>}
    <Checkbox checked={annotated} disabled={busy} onChange={event=>{setAnnotated(event.target.checked);setRows([]);setError('');setNotice('');}}>同时导入 YOLO 标注（ZIP）</Checkbox>
    {annotated&&<Space orientation="vertical" style={{width:'100%',margin:'12px 0'}}>
      <Alert type="info" showIcon title="标注保存在图片库，不绑定项目" description="ZIP包含 images/、labels/ 和 data.yaml（names）或 classes.txt；支持平铺同名图片/TXT。仅检测框，空TXT表示负样本，缺失TXT报错。之后添加到任意检测项目时可选择带入标注，各项目独立编辑。"/>
      <Checkbox checked={trust} disabled={busy||!admin} onChange={event=>{setTrust(event.target.checked);resetRows();}}>信任已有标注，直接用于训练/验证（管理员）</Checkbox>
      <span className="workspace-help">{trust?'带入项目后直接可用，并保留管理员导入确认；已有项目标注状态不变。':'带入项目后保持待审核，通过审核后可用于训练/验证。'} 验证数据仍需与训练数据独立。</span>
    </Space>}
    <label htmlFor="upload-collection">目标图片集</label>
    <AutoComplete value={name} onChange={value=>{if(value!==name){setName(value);setMinimum(undefined);resetRows();}}} disabled={busy}
      style={{width:'100%',marginBottom:12}} options={collections.map(group=>({value:group.name}))}>
      <Input id="upload-collection" aria-label="上传图片集名称" maxLength={200}/>
    </AutoComplete>
    <p>图片集规则：长边 ≥ {policy?.min_long_edge??'读取中'} 像素；场景分类{policy?.classification_enabled?'开启':'关闭'}。</p>
    <Space wrap style={{marginBottom:16}}><span>本次覆盖阈值（留空按图片集）</span>
      <InputNumber aria-label="本次上传长边阈值" placeholder="按图片集规则" min={0} max={32768} precision={0} value={minimum} disabled={busy}
        onChange={value=>{if((value??undefined)!==minimum){setMinimum(value??undefined);resetRows();}}}/>
      <span>任一边 ≥，0关闭过滤</span></Space>
    <input type="file" multiple hidden ref={picker} accept={annotated?'.zip':'.jpg,.jpeg,.png,.bmp,.webp,.zip,.tar,.tar.gz,.tgz'} aria-label="上传图片或压缩包"
      onChange={event=>{add(Array.from(event.target.files||[]));event.target.value='';}}/>
    <input type="file" multiple hidden ref={element=>{folder.current=element;element?.setAttribute('webkitdirectory','');}} aria-label="上传图片文件夹"
      onChange={event=>{add(Array.from(event.target.files||[]));event.target.value='';}}/>
    <Space wrap style={{marginBottom:12}}><Button disabled={busy} onClick={()=>picker.current?.click()}>选择图片或压缩包</Button>
      {!annotated&&<Button disabled={busy} onClick={()=>folder.current?.click()}>选择文件夹</Button>}
      <Button disabled={busy} onClick={()=>{setRows([]);setNotice('');}}>清空所选</Button><span>已选择 {rows.length} 个文件</span></Space>
    <div onDragOver={event=>event.preventDefault()} onDrop={event=>{event.preventDefault();if(!busy)add(Array.from(event.dataTransfer.files));}}
      style={{padding:12,border:'1px dashed var(--ui-border)',borderRadius:8,marginBottom:12}}>也可拖入图片或压缩包；文件夹请使用“选择文件夹”按钮。</div>
    <Table rowKey="key" size="small" dataSource={rows} pagination={{pageSize:5}} scroll={{x:620}}
      columns={[{title:'文件',render:(_,row:Row)=>row.file.webkitRelativePath||row.file.name},
        {title:'大小',width:90,render:(_,row:Row)=>`${(row.file.size/1024**2).toFixed(1)} MiB`},
        {title:'进度',width:260,render:(_,row:Row)=><><span>{row.phase}</span><Progress percent={row.percent} size="small"/><span>{row.summary}</span></>}]}/>
    <p className="workspace-help">{config&&`每片 ${config.chunk_size/1024**2} MiB；单图上限 ${config.max_image_size/1024**2} MiB，单包上限 ${config.max_file_size/1024**3} GiB；单包最多 ${config.max_entries} 项（含图片、标注和目录），展开上限 ${config.max_expanded_size/1024**3} GiB；未完成传输保留 ${config.retention_days} 天。`} 上传完成后后台入库，关闭窗口不影响入库；同图只保留一张并复用场景。</p>
    <Space style={{marginBottom:8}}><strong>最近上传与续传记录</strong><Button size="small" onClick={refresh}>刷新记录</Button></Space>
    <Table rowKey="id" size="small" dataSource={records} pagination={{pageSize:5}} scroll={{x:620}}
      columns={[{title:'文件',render:(_,job:UploadJob)=><>{job.filename}{job.annotation_format&&<Tag>YOLO · {job.trust_annotations?'直接可用':'待审核'}</Tag>}<div className="workspace-help">图片集：{collections.find(group=>group.id===job.collection_id)?.name||'读取中'}</div></>},{title:'状态',render:(_,job:UploadJob)=><Tag color={job.status==='completed'&&!job.result.imported&&!job.result.duplicates?'warning':undefined}>{job.status==='completed'&&!job.result.imported&&!job.result.duplicates?'无图片入库':({uploading:'等待续传',pending:'等待处理',processing:'正在处理',completed:'已完成',failed:'失败，可重试'} as Record<string,string>)[job.status]}</Tag>},
        {title:'已上传',render:(_,job:UploadJob)=>`${Math.round(job.uploaded_bytes/job.size*100)}%`},
        {title:'入库结果',render:(_,job:UploadJob)=><>
          <div>入库 {job.result.imported} 张；已存在 {job.result.duplicates} 张；尺寸过滤 {job.result.filtered} 张；无效 {job.result.invalid} 张；忽略 {job.result.ignored} 项</div>
          <div className="workspace-help">本次规则：{job.min_long_edge===0?'不过滤尺寸':`长边 ≥ ${job.min_long_edge} 像素`}</div>
          {job.annotation_format&&<div>标注 {job.result.annotated||0}；已有相同标注 {job.result.annotation_skipped||0}；冲突 {job.result.conflicts||0}</div>}
          {job.result.errors.map((text,index)=><div key={index}>{text}</div>)}
          <div>{job.error||(['pending','processing'].includes(job.status)?'后台会继续处理':job.status==='completed'?'文件已处理完成':'重新选择相同文件即可继续')}</div>
        </>},
        {title:'操作',render:(_,job:UploadJob)=><Space orientation="vertical">
          {job.status==='completed'&&<Button size="small" onClick={()=>{onViewCollection(job.collection_id);onClose();}}>查看图片集</Button>}
          <Button disabled={busy||['pending','processing'].includes(job.status)} danger size="small" onClick={async()=>{try{await deleteUpload(job.id);await refresh();}catch{setError('清除记录失败，请重试');}}}>清除记录</Button>
        </Space>}]} />
  </Modal>;
}
