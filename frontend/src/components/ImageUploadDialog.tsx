import { useCallback,useEffect,useRef,useState } from 'react';
import { Alert,AutoComplete,Button,Checkbox,Input,InputNumber,Modal,Progress,Radio,Select,Space,Spin,Table,Tag } from 'antd';
import { isAxiosError } from 'axios';
import { Link } from 'react-router-dom';
import type { ImageCollection,ImageIngestConfig } from '../api/images';
import { getImageIngestSettings } from '../api/images';
import { getUploadConfig,getUploadBatches,getUploadImages,listUploads,deleteUpload,markUploadBatchFailure,saveUploadBatch,uploadResumable } from '../api/imageUploads';
import {associateImages} from '../api/images';
import type { ArchiveImportProgress,UploadBatch,UploadConfig,UploadImageKind,UploadJob,UploadResult } from '../api/imageUploads';
import {sha256} from '../utils/sha256';
import ImageIngestSettings from './ImageIngestSettings';
import ProjectYoloLabelUpload from './ProjectYoloLabelUpload';
import ProjectDescriptionUpload from './ProjectDescriptionUpload';

type Row={file:File;key:string;transferred:number;prepared?:number;preparing?:boolean;done?:boolean;active?:boolean;failed?:boolean;result?:UploadResult};
const archiveExtensions=['.zip','.tar','.tar.gz','.tgz'];
const imageExtensions=['.jpg','.jpeg','.png','.bmp','.webp'];
const parallelFiles=16;
const completionPercent=(completed:number,total:number)=>total?Math.floor(completed*1000/total)/10:0;
function ArchiveProgress({progress}:{progress:ArchiveImportProgress}){
  const {processed,total}=progress;
  return <>
    {total?<Progress aria-label="压缩包入库进度" size="small" status="active" percent={completionPercent(processed,total)}/>:null}
    <Space size="small" className="workspace-help" role="status">
      {!total&&<Spin size="small"/>}
      {total?`压缩包入库中 · 已处理 ${processed} / ${total} 项`:
        processed?`压缩包入库中 · 已处理 ${processed} 项，正在确认总数`:'正在准备压缩包入库'}
    </Space>
  </>;
}
export default function ImageUploadDialog({open,initialName,collections,onClose,onImported,onViewCollection,projectId,projectName,taskType,onBusyChange}:{
  open:boolean;initialName:string;collections:ImageCollection[];onClose:()=>void;onImported:(name:string)=>void|Promise<void>;onViewCollection:(id:string)=>void;
  projectId?:string;projectName?:string;taskType?:string;
  onBusyChange?:(busy:boolean)=>void;
}){
  const [name,setName]=useState(initialName),[minimum,setMinimum]=useState<number>();
  const [imageKind,setImageKind]=useState<UploadImageKind>('originals');
  const [config,setConfig]=useState<UploadConfig>(),[defaults,setDefaults]=useState<ImageIngestConfig>();
  const [savedPolicy,setSavedPolicy]=useState<{name:string;config:ImageIngestConfig}>();
  const [rows,setRows]=useState<Row[]>([]),[records,setRecords]=useState<UploadJob[]>([]);
  const [historyError,setHistoryError]=useState(false),[historyOpen,setHistoryOpen]=useState(false);
  const [batches,setBatches]=useState<UploadBatch[]>([]),[summaryError,setSummaryError]=useState(false);
  const [batchId,setBatchId]=useState<string>(),[viewBatchId,setViewBatchId]=useState<string>();
  const [busy,setBusy]=useState(false),[error,setError]=useState(''),[notice,setNotice]=useState('');
  useEffect(()=>{onBusyChange?.(busy);return()=>onBusyChange?.(false);},[busy,onBusyChange]);
  const [annotated,setAnnotated]=useState(false),[trust,setTrust]=useState(false);
  const [admin]=useState(()=>{try{return JSON.parse(localStorage.getItem('user')||'null')?.role==='admin';}catch{return false;}});
  const abort=useRef<AbortController|null>(null),picker=useRef<HTMLInputElement>(null),folder=useRef<HTMLInputElement>(null);
  const history=useRef<HTMLElement>(null);
  const wasOpen=useRef(false);
  const recordRequest=useRef(0);
  const refresh=useCallback(async()=>{
    const request=++recordRequest.current;
    const [summary,details]=await Promise.allSettled([getUploadBatches(projectId),
      historyOpen?listUploads(projectId):Promise.resolve(undefined)]);
    if(request!==recordRequest.current)return;
    setSummaryError(summary.status==='rejected');
    if(summary.status==='fulfilled')setBatches(summary.value);
    setHistoryError(details.status==='rejected');
    if(details.status==='fulfilled'&&details.value)setRecords(details.value);
  },[projectId,historyOpen]);
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
    const poll=async()=>{await refresh();if(active)timer=setTimeout(poll,4000);};
    void poll();
    return()=>{active=false;recordRequest.current++;clearTimeout(timer);};
  },[open,refresh]);
  const resetRows=()=>{setBatchId(undefined);setRows(current=>current.map(({file,key})=>({file,key,transferred:0})));};
  const uploadExtensions=annotated?archiveExtensions:[...imageExtensions,...archiveExtensions];
  const add=(files:File[])=>{
    const valid=files.filter(file=>uploadExtensions.some(ext=>file.name.toLowerCase().endsWith(ext)));
    if(valid.length<files.length)setNotice(`已忽略 ${files.length-valid.length} 个不支持的文件`);
    const names=new Set(valid.map(file=>file.webkitRelativePath||file.name));
    const newBatch=rows.length>0&&rows.every(row=>row.done);
    if(newBatch)setBatchId(undefined);
    setRows(current=>{
      if(newBatch)current=[];
      const previous=new Map(current.filter(row=>!names.has(row.file.webkitRelativePath||row.file.name)).map(row=>[row.key,row]));
      for(const file of valid){const key=[file.webkitRelativePath||file.name,file.size,file.lastModified].join(':');
        previous.set(key,{file,key,transferred:0});}
      return [...previous.values()];
    });
    if(valid.length)setError('');
  };
  const start=async()=>{
    if(abort.current)return;
    if(!config||!name.trim()||!rows.length){setError('请填写图片集名称并选择文件');return;}
    const collection=name.trim();
    try{localStorage.setItem('image-upload-collection',collection);}catch{/* Remembering the last target is optional. */}
    const controller=new AbortController();abort.current=controller;setBusy(true);setError('');
    setNotice('');
    let savedBatchId:string,fileKeys:string[],savedCompleted:Set<string>;
    try{
      fileKeys=await Promise.all(rows.map(row=>sha256(new TextEncoder().encode(row.key).buffer)));
      const batch=await saveUploadBatch({project_id:projectId,batch_id:batchId,collection_name:collection,
        image_kind:projectId?imageKind:'originals',file_keys:fileKeys,min_long_edge:minimum,
        ...(annotated?{annotation_format:'yolo',trust_annotations:trust}:{})},controller.signal);
      savedBatchId=batch.id;setBatchId(batch.id);setViewBatchId(batch.id);
      savedCompleted=new Set(batch.completed_keys);
    }catch(error){
      const detail=isAxiosError(error)?error.response?.data?.detail:undefined;
      if(!controller.signal.aborted)setError(typeof detail==='string'?detail:'上传批次保存失败，请重试');
      setBusy(false);abort.current=null;return;
    }
    const update=(index:number,values:Partial<Row>)=>setRows(current=>{
      const next=current.slice();
      next[index]={...next[index],...values};
      return next;
    });
    const refreshImages=async()=>{
      try{await onImported(collection);}
      catch{setNotice('上传结果已保存，可以手动刷新图片列表');}
    };
    let cursor=0,firstFailure='',lastRefresh=0;
    const worker=async()=>{
      while(!controller.signal.aborted&&cursor<rows.length){
        const index=cursor++,row=rows[index];
        if(row.done||savedCompleted.has(fileKeys[index])){
          if(!row.done)update(index,{done:true,failed:false,transferred:row.file.size});
          continue;
        }
        update(index,{active:true,failed:false,preparing:true,prepared:0});
        try{
          const result=await uploadResumable(row.file,collection,minimum,config,controller.signal,
            (bytes,phase)=>{
              if(!controller.signal.aborted)update(index,phase==='preparing'?{prepared:bytes}:{preparing:false,transferred:bytes});
            },annotated?{annotation_format:'yolo',trust_annotations:trust}:undefined,projectId,projectId?imageKind:undefined,
            {batch_id:savedBatchId,file_key:fileKeys[index]});
          const imported=projectId&&!result.result.project_association_complete?await attachUpload(result,controller.signal):result.result;
          update(index,{done:true,active:false,transferred:row.file.size,result:imported});
          if(Date.now()-lastRefresh>=2500){
            lastRefresh=Date.now();
            await refreshImages();
          }
        }catch(error){
          if(controller.signal.aborted){update(index,{active:false});return;}
          const body=isAxiosError(error)?error.response?.data:undefined;
          const summary=typeof body?.detail==='string'?body.detail:body?.message||(error instanceof Error?error.message:'上传中断，可继续上传');
          firstFailure ||= `${row.file.webkitRelativePath||row.file.name}：${summary}`;
          update(index,{active:false,failed:true});
          try{await markUploadBatchFailure(savedBatchId,fileKeys[index],controller.signal);}catch{/* A later resume can sync the receipt. */}
        }
      }
    };
    try{
      await Promise.all(Array.from({length:Math.min(rows.length,parallelFiles)},worker));
      if(controller.signal.aborted)setNotice('已暂停传输。已收到的分片保留，后台入库任务继续执行');
      else if(firstFailure)setError(firstFailure);
      else setNotice('所选文件已处理完成，可继续添加文件或关闭窗口');
      await refreshImages();
    }finally{setBusy(false);abort.current=null;void refresh();}
  };
  const attachUpload=async(job:UploadJob,signal?:AbortSignal)=>{
    if(!projectId)return job.result;
    let added=0,duplicates=0,conflicts=0,annotated=0,queued=0,warning='';
    for(let page=1;;page++){
      signal?.throwIfAborted();
      const data=await getUploadImages(job.id,page,signal);
      if(data.image_ids.length){
        const result=await associateImages(projectId,data.image_ids,undefined,job.annotation_format==='yolo'&&taskType==='detection',undefined,job.id);
        added+=result.added_count;duplicates+=result.skipped_count;conflicts+=result.conflicts;annotated+=result.annotated;
        queued+=result.preannotation_queued;warning ||= result.preannotation_warning||'';
      }
      if(page*data.page_size>=data.total||!data.image_ids.length)break;
    }
    return {...job.result,added_count:added,project_duplicates:duplicates,project_conflicts:conflicts,project_annotated:annotated,
      queued_count:queued,queue_warning:warning};
  };
  const projectResult=(job:UploadJob)=>`；项目新增 ${job.result.added_count||0} 张，已存在 ${job.result.project_duplicates||0} 张，冲突 ${job.result.project_conflicts||0} 张`+
    (job.annotation_format==='yolo'?`；带入项目标注 ${job.result.project_annotated||0} 张`:'')+
    (job.result.queued_count?`；${job.result.queued_count} 张已加入自动处理`:'')+
    (job.result.queue_warning?`；${job.result.queue_warning}`:'');
  const selectedCollection=collections.find(group=>group.name===name.trim());
  const policy=savedPolicy?.name===name.trim()?savedPolicy.config:selectedCollection
    ?selectedCollection.ingest_config||defaults:defaults&&{...defaults,classification_enabled:false};
  let completed=0,active=0,failed=0,imported=0,duplicates=0,filtered=0,invalid=0,added=0,queued=0,annotatedCount=0;
  let totalBytes=0,preparedBytes=0,transferredBytes=0,preparing=false;
  let queueWarning='',importWarning='';
  for(const row of rows){
    if(row.done)completed++;if(row.active)active++;if(row.failed)failed++;
    totalBytes+=row.file.size;
    preparedBytes+=row.done?row.file.size:row.prepared??0;
    transferredBytes+=row.transferred;
    if(row.active&&row.preparing)preparing=true;
    if(row.result){
      imported+=row.result.imported;duplicates+=row.result.duplicates;filtered+=row.result.filtered;invalid+=row.result.invalid;
      added+=row.result.added_count||0;queued+=row.result.queued_count||0;annotatedCount+=row.result.annotated||0;
      queueWarning ||=row.result.queue_warning||'';importWarning ||=row.result.errors[0]||'';
    }
  }
  const visibleBatch=batches.find(batch=>batch.id===viewBatchId)??batches[0];
  const localBatch=rows.length>0&&visibleBatch?.id===batchId;
  const savedBatch=batches.find(batch=>batch.id===batchId);
  // On resume, the browser may still be matching files already counted by the server.
  const savedProgress=savedBatch?.total===rows.length&&savedBatch.completed>completed?savedBatch:undefined;
  const progressCompleted=savedProgress?.completed??completed;
  const progressFailed=savedProgress?.failed??failed;
  const batchLabel=(batch:UploadBatch)=>`${new Date(batch.created_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})} · ${batch.image_kind==='crops'?'裁剪图':'原图'} · ${batch.total} 个文件`;
  return <Modal title={projectId?`上传到项目 ${projectName}`:'上传到图片库'} open={open} width={900} style={{top:24}}
    styles={{body:{maxHeight:'calc(100dvh - 200px)',overflowY:'auto',paddingRight:8}}} onCancel={onClose}
    footer={<Space wrap><Button onClick={onClose}>关闭</Button>
      {busy?<Button onClick={()=>abort.current?.abort()}>暂停上传</Button>:<Button type="primary" onClick={start} disabled={!config||completed===rows.length}>{failed?'重试失败 / 继续上传':'开始 / 继续上传'}</Button>}</Space>}>
    <p className="workspace-help">选择图片、文件夹或ZIP/TAR（含.tar.gz/.tgz），子目录图片加入同一目标图片集。关闭窗口会继续上传当前队列，暂停请点击“暂停上传”；离开当前页面、刷新或断网后重选相同文件，只补传缺失分片。</p>
    <Space wrap style={{marginBottom:8}}>
      <Button type="link" style={{paddingInline:0}} onClick={()=>{setHistoryOpen(!historyOpen);if(!historyOpen)history.current?.scrollIntoView({block:'start'});}}>
        {historyOpen?'收起旧记录':'续传与旧记录'}</Button>
      {projectId&&<Link to={`/projects/${projectId}/prepare`} target="_blank" rel="noopener">在新标签页批量处理 ↗</Link>}
    </Space>
    <section aria-label="上传批次记录" style={{marginBottom:12}}>
      {summaryError&&<Alert type="error" showIcon title="上传批次读取失败"
        action={<Button size="small" onClick={refresh}>刷新记录</Button>}/>}
      {!summaryError&&visibleBatch&&<>
        <Space wrap><strong>上传批次</strong>
          <Select aria-label="查看上传批次" value={visibleBatch.id} onChange={setViewBatchId}
            options={batches.map(batch=>({value:batch.id,label:batchLabel(batch)}))} style={{minWidth:255}}/>
          {visibleBatch.can_resume&&!visibleBatch.archive_progress&&(visibleBatch.failed>0||visibleBatch.remaining>0)&&<Button size="small" disabled={busy||!!rows.length||batchId===visibleBatch.id}
            onClick={()=>{setBatchId(visibleBatch.id);setName(visibleBatch.collection_name);setImageKind(visibleBatch.image_kind);
              setMinimum(visibleBatch.min_long_edge??undefined);setAnnotated(visibleBatch.annotation_format==='yolo');
              setTrust(visibleBatch.trust_annotations);setError('');}}>
            {batchId===visibleBatch.id?'已选择此批续传':'选择此批续传'}</Button>}
          {batchId&&!rows.length&&<Button size="small" disabled={busy} onClick={()=>setBatchId(undefined)}>改为新建批次</Button>}
        </Space>
        {!localBatch&&<>
          {visibleBatch.archive_progress?<ArchiveProgress progress={visibleBatch.archive_progress}/>:
            <Progress aria-label="批次入库进度" percent={completionPercent(visibleBatch.completed,visibleBatch.total)}
              size="small" status={visibleBatch.failed?'exception':undefined}/>}
          <p className="workspace-help" role="status">总数 {visibleBatch.total} · 已完成 {visibleBatch.completed} · 失败 {visibleBatch.failed} · 剩余 {visibleBatch.remaining}</p>
        </>}
        {batchId===visibleBatch.id&&!rows.length&&!visibleBatch.archive_progress&&<p className="workspace-help">继续上传此批次时，重新选择原来的 {visibleBatch.total} 个文件。</p>}
      </>}
    </section>
    {projectId&&<Alert type="info" title={`完成后仅将本次上传批次的实际图片关联到“${projectName}”，同集旧图不会整集加入。`} description="重复图片共用原图；带入标注时只保留当前项目标签匹配的内容，全部不匹配则保持未标注。关联失败可继续上传重试或从已完成记录添加，目标已有标注不覆盖。" style={{marginBottom:12}}/>}
    {error&&!failed&&<Alert type="error" title={error} showIcon style={{marginBottom:12}}/>}
    {notice&&<Alert type="info" title={notice} showIcon style={{marginBottom:12}}/>}
    {projectId&&<div style={{marginBottom:12}}><Space wrap><span>本次上传类型</span><Radio.Group aria-label="本次上传类型" value={imageKind} disabled={busy}
      options={[{value:'originals',label:'原图'},{value:'crops',label:'裁剪图'}]}
      onChange={event=>{setImageKind(event.target.value);resetRows();}}/></Space>
      {imageKind==='crops'&&<p className="workspace-help">按裁剪图入库，可直接标注。上传文件没有原图关联时，仅显示裁剪图。</p>}</div>}
    <Space wrap>
      <Checkbox checked={annotated} disabled={busy||!!projectId&&taskType!=='detection'} onChange={event=>{setAnnotated(event.target.checked);setBatchId(undefined);setRows([]);setError('');setNotice('');}}>同时导入 YOLO 标注（ZIP/TAR）</Checkbox>
      {projectId&&taskType==='detection'&&<ProjectYoloLabelUpload key={projectId} projectId={projectId} disabled={busy} onImported={()=>void onImported(name.trim())}/>}
      {projectId&&<ProjectDescriptionUpload key={`annotations-${projectId}`} projectId={projectId} disabled={busy} onImported={()=>void onImported(name.trim())}/>}
      {projectId&&taskType==='caption'&&<ProjectDescriptionUpload key={`descriptions-${projectId}`} projectId={projectId} disabled={busy} reviewedDescriptions onImported={()=>void onImported(name.trim())}/>}
    </Space>
    {annotated&&<Space orientation="vertical" style={{width:'100%',margin:'12px 0'}}>
      <Alert type="info" showIcon title={projectId?'标注入库后关联当前检测项目':'标注保存在图片库，不绑定项目'} description="支持 ZIP、TAR、TAR.GZ 和 TGZ。包内可用 images/ 和 labels/，或平铺同名图片和 TXT。类别表可选，未提供时保留 YOLO 编号，导入为待审核，可在标注页按批次设置类别。空 TXT 表示负样本，缺失 TXT 的图片会报错。"/>
      <Checkbox checked={trust} disabled={busy||!admin} onChange={event=>{setTrust(event.target.checked);resetRows();}}>信任已有标注，直接用于训练/验证（管理员）</Checkbox>
      <span className="workspace-help">{trust?'完整兼容的标注带入后直接可用；类别过滤或目标包含新类别时仍待审核，并保留管理员导入确认。':'带入项目后保持待审核，通过审核后可用于训练/验证。'} 验证数据仍需与训练数据独立。</span>
    </Space>}
    <label htmlFor="upload-collection">目标图片集</label>
    <AutoComplete value={name} onChange={value=>{if(value!==name){setName(value);setMinimum(undefined);resetRows();}}} disabled={busy}
      style={{width:'100%',marginBottom:12}} options={collections.map(group=>({value:group.name}))}>
      <Input id="upload-collection" aria-label="上传图片集名称" maxLength={200}/>
    </AutoComplete>
    <Space wrap style={{marginBottom:12}}>
      <span>图片集规则：长边 ≥ {policy?.min_long_edge??'读取中'} 像素；场景分类{policy?policy.classification_enabled?'开启':'关闭':'读取中'}。</span>
      {admin&&<ImageIngestSettings key={name.trim()} collectionId={selectedCollection?.id} collectionName={name.trim()}
        createNamed disabled={busy||!name.trim()} onSaved={config=>setSavedPolicy({name:name.trim(),config})}/>}
    </Space>
    <Space wrap style={{marginBottom:16}}><span>{projectId&&imageKind==='crops'?'本次裁剪图长边阈值':'本次覆盖阈值（留空按图片集）'}</span>
      <InputNumber aria-label="本次上传长边阈值" placeholder={projectId&&imageKind==='crops'?'默认不限制':'按图片集规则'} min={0} max={32768} precision={0} value={minimum} disabled={busy}
        onChange={value=>{if((value??undefined)!==minimum){setMinimum(value??undefined);resetRows();}}}/>
      <span>任一边 ≥，0关闭过滤</span></Space>
    <input type="file" multiple hidden ref={picker} accept={uploadExtensions.join(',')} aria-label="上传图片或压缩包"
      onChange={event=>{add(Array.from(event.target.files||[]));event.target.value='';}}/>
    <input type="file" multiple hidden ref={element=>{folder.current=element;element?.setAttribute('webkitdirectory','');}} aria-label="上传图片文件夹"
      onChange={event=>{add(Array.from(event.target.files||[]));event.target.value='';}}/>
    <Space wrap style={{marginBottom:12}}><Button disabled={busy} onClick={()=>picker.current?.click()}>选择图片或压缩包</Button>
      {!annotated&&<Button disabled={busy} onClick={()=>folder.current?.click()}>选择文件夹</Button>}
      <Button disabled={busy} onClick={()=>{setRows([]);setBatchId(undefined);setNotice('');}}>清空所选</Button><span>已选择 {rows.length} 个文件</span></Space>
    <div onDragOver={event=>event.preventDefault()} onDrop={event=>{event.preventDefault();if(!busy)add(Array.from(event.dataTransfer.files));}}
      style={{padding:12,border:'1px dashed var(--ui-border)',borderRadius:8,marginBottom:12}}>也可拖入图片或压缩包；文件夹请使用“选择文件夹”按钮。</div>
    {!!rows.length&&<section aria-label="本批上传进度">
      {busy&&preparing&&<div>
        <strong>正在校验文件</strong>
        <Progress aria-label="文件校验进度" percent={completionPercent(preparedBytes,totalBytes)} status="active"/>
      </div>}
      {(busy||transferredBytes>0)&&<div>
        <strong>文件传输</strong>
        <Progress aria-label="文件传输进度" percent={completionPercent(transferredBytes,totalBytes)}/>
      </div>}
      <strong>本批入库进度</strong>
      {savedBatch?.archive_progress&&completed<rows.length?<ArchiveProgress progress={savedBatch.archive_progress}/>:
        <Progress aria-label="本批入库进度" percent={completionPercent(progressCompleted,rows.length)}
          status={progressFailed?'exception':busy?'active':undefined}/>}
      <p className="workspace-help" role="status">已完成 {progressCompleted} / {rows.length} 个文件 · {savedProgress?
        `剩余 ${savedProgress.remaining}`:`处理中 ${active} · 待处理 ${rows.length-completed-active-failed}`} · 失败 {progressFailed}</p>
      {!!completed&&<p className="workspace-help">
        {projectId?`项目新增 ${added} 张 · 已入工作流 ${queued} 张`:`图片库新增 ${imported} 张`} · 重复 {duplicates} 张 · 尺寸过滤 {filtered} 张 · 无效 {invalid} 张
        {annotated?` · 导入标注 ${annotatedCount} 张`:''}
      </p>}
      {failed>0&&error&&<Alert type="error" showIcon title={`本批 ${failed} 个文件失败`} description={error}/>}
      {(queueWarning||importWarning)&&<Alert type="warning" showIcon title={queueWarning||importWarning}/>}
      {!busy&&completed>0&&!imported&&!duplicates&&<Alert type="warning" showIcon title="本批未入库图片"
        description="请查看本批的尺寸过滤和无效数量。若因尺寸不足被过滤，可将本次长边阈值设为 0 后重新上传。"/>}
    </section>}
    <p className="workspace-help">{config&&`每片 ${config.chunk_size/1024**2} MiB；单图上限 ${config.max_image_size/1024**2} MiB，单包上限 ${config.max_file_size/1024**3} GiB；单包最多 ${config.max_entries} 项（含图片、标注和目录），展开上限 ${config.max_expanded_size/1024**3} GiB；未完成传输保留 ${config.retention_days} 天。`} 上传完成后后台入库，关闭窗口不影响入库；同图只保留一张并复用场景。</p>
    <section ref={history} aria-label={projectId?'当前项目上传记录':'图片库上传记录'}>
    {historyOpen&&<>
    <Space style={{marginBottom:8}}><strong>{projectId?'当前项目上传记录':'最近上传与续传记录'}</strong><Button size="small" onClick={refresh}>刷新记录</Button></Space>
    <p className="workspace-help">此处显示服务器收到的最近 20 条记录。还未提交到服务器的文件仅在选中文件的电脑上显示。</p>
    {historyError&&<Alert type="error" showIcon title="上传记录读取失败，请刷新记录"/>}
    <Table rowKey="id" size="small" dataSource={records} pagination={{pageSize:5}} scroll={{x:710}}
      locale={{emptyText:historyError?'读取失败':projectId?'暂无按项目保存的上传记录。旧记录可从图片库查看，重选原文件继续上传后会归入当前项目。':'暂无上传记录'}}
      columns={[{title:'文件',render:(_,job:UploadJob)=><>{job.filename}{job.project_id&&<Tag>{job.image_kind==='crops'?'裁剪图':'原图'}</Tag>}{job.annotation_format&&<Tag>YOLO · {job.trust_annotations?'直接可用':'待审核'}</Tag>}<div className="workspace-help">图片集：{collections.find(group=>group.id===job.collection_id)?.name||'读取中'}</div></>},{title:'状态',render:(_,job:UploadJob)=><Tag color={job.status==='completed'&&!job.result.imported&&!job.result.duplicates?'warning':undefined}>{job.status==='completed'&&!job.result.imported&&!job.result.duplicates?'无图片入库':({uploading:'传输未完成',pending:'等待处理',processing:'正在处理',completed:'已完成',failed:'失败，可重试'} as Record<string,string>)[job.status]}</Tag>},
        {title:'大小',width:92,render:(_,job:UploadJob)=>`${(job.size/1024**2).toFixed(1)} MiB`},
        {title:'传输进度',width:132,render:(_,job:UploadJob)=><Progress percent={Math.round(job.uploaded_bytes/job.size*100)} size="small"/>},
        {title:'入库结果',render:(_,job:UploadJob)=><>
          <div>入库 {job.result.imported} 张；已存在 {job.result.duplicates} 张；尺寸过滤 {job.result.filtered} 张；无效 {job.result.invalid} 张；忽略 {job.result.ignored} 项</div>
          <div className="workspace-help">本次规则：{job.min_long_edge===0?'不过滤尺寸':`长边 ≥ ${job.min_long_edge} 像素`}</div>
          {job.annotation_format&&<div>标注 {job.result.annotated||0}；已有相同标注 {job.result.annotation_skipped||0}；冲突 {job.result.conflicts||0}</div>}
          {job.result.added_count!==undefined&&<div>{projectResult(job).slice(1)}</div>}
          {job.result.errors.map((text,index)=><div key={index}>{text}</div>)}
          <div>{job.error||(['pending','processing'].includes(job.status)?'后台会继续处理':job.status==='completed'?'文件已处理完成':'重新选择相同文件即可继续')}</div>
        </>},
        {title:'操作',render:(_,job:UploadJob)=>job.can_manage===false?'仅查看':<Space orientation="vertical">
          {job.status==='completed'&&projectId&&!job.result.project_association_complete&&<Button size="small" disabled={busy} onClick={async()=>{setBusy(true);setError('');try{setNotice(projectResult({...job,result:await attachUpload(job)}).slice(1));await onImported(name.trim());}catch(error){setError(error instanceof Error?error.message:'关联项目失败，可重试');}finally{setBusy(false);}}}>添加到当前项目</Button>}
          {job.status==='completed'&&!projectId&&<Button size="small" onClick={()=>{onViewCollection(job.collection_id);onClose();}}>查看图片集</Button>}
          <Button disabled={busy||['pending','processing'].includes(job.status)} danger size="small" onClick={async()=>{try{await deleteUpload(job.id);await refresh();}catch{setError('清除记录失败，请重试');}}}>清除记录</Button>
        </Space>}]} />
    </>}
    </section>
  </Modal>;
}
