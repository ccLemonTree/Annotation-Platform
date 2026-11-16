import api from './client';
import { isAxiosError } from 'axios';
import { sha256 } from '../utils/sha256';

export type UploadResult={imported:number;duplicates:number;filtered:number;invalid:number;ignored:number;errors:string[];annotated?:number;annotation_skipped?:number;conflicts?:number;project_association_complete?:boolean;added_count?:number;project_duplicates?:number;project_conflicts?:number;project_annotated?:number;queued_count?:number;queue_warning?:string};
export type UploadImageKind='originals'|'crops';
export type UploadJob={id:string;filename:string;size:number;collection_id:string;project_id?:string|null;image_kind?:UploadImageKind;can_manage?:boolean;min_long_edge:number;annotation_format?:'yolo';trust_annotations?:boolean;status:string;uploaded_parts:number[];uploaded_bytes:number;chunk_size:number;cursor:number;result:UploadResult;error:string|null};
export type UploadConfig={chunk_size:number;max_file_size:number;max_image_size:number;max_expanded_size:number;max_entries:number;retention_days:number};
export type ArchiveImportProgress={processed:number;total:number|null};
export type UploadBatch={id:string;created_at:string;collection_name:string;image_kind:UploadImageKind;total:number;completed:number;failed:number;remaining:number;can_resume:boolean;min_long_edge:number|null;annotation_format:'yolo'|null;trust_annotations:boolean;archive_progress?:ArchiveImportProgress|null};
export const getUploadConfig=async():Promise<UploadConfig>=>(await api.get('/api/image-uploads/config')).data;
export const listUploads=async(projectId?:string):Promise<UploadJob[]>=>(await api.get('/api/image-uploads',{params:{project_id:projectId},skipErrorToast:true})).data;
export const getUploadBatches=async(projectId?:string):Promise<UploadBatch[]>=>
  (await api.get('/api/image-uploads/batches',{params:{project_id:projectId},skipErrorToast:true})).data;
export const saveUploadBatch=async(input:{project_id?:string;batch_id?:string;collection_name:string;image_kind:UploadImageKind;file_keys:string[];min_long_edge?:number;annotation_format?:'yolo';trust_annotations?:boolean},signal:AbortSignal):Promise<{id:string;completed_keys:string[]}>=>
  (await api.post('/api/image-uploads/batches',input,{signal,skipErrorToast:true})).data;
export const markUploadBatchFailure=async(id:string,key:string,signal:AbortSignal)=>
  api.put(`/api/image-uploads/batches/${id}/files/${key}/failed`,null,{signal,skipErrorToast:true});
export const deleteUpload=async(id:string)=>{await api.delete('/api/image-uploads/'+id);};
export const getUploadImages=async(id:string,page=1,signal?:AbortSignal):Promise<{image_ids:string[];total:number;page:number;page_size:number}>=>
  (await api.get(`/api/image-uploads/${encodeURIComponent(id)}/images`,{params:{page,page_size:1000},signal,skipErrorToast:true})).data;

function waitForUpload(ms:number,signal:AbortSignal){
  return new Promise<void>((resolve,reject)=>{
    signal.throwIfAborted();
    const done=()=>{signal.removeEventListener('abort',abort);resolve();};
    const timer=setTimeout(done,ms);
    const abort=()=>{clearTimeout(timer);reject(signal.reason);};
    signal.addEventListener('abort',abort,{once:true});
  });
}

async function retryUpload<T>(request:()=>Promise<T>,signal:AbortSignal):Promise<T>{
  for(let attempt=0;;attempt++){
    signal.throwIfAborted();
    try{return await request();}
    catch(error){
      signal.throwIfAborted();
      if(attempt===2||!isAxiosError(error)||!(['ERR_NETWORK','ECONNABORTED','ETIMEDOUT'].includes(error.code||'')
        ||[500,502,503,504].includes(error.response?.status||0)))throw error;
      await waitForUpload((attempt+1)*1000,signal);
    }
  }
}

export async function uploadResumable(file:File,collection:string,minimum:number|undefined,config:UploadConfig,
  signal:AbortSignal,onProgress:(bytes:number)=>void,annotation?:{annotation_format:'yolo';trust_annotations:boolean},projectId?:string,imageKind?:UploadImageKind,batch?:{batch_id:string;file_key:string}):Promise<UploadJob>{
  const archive=/\.(zip|tar|tar\.gz|tgz)$/i.test(file.name);
  const maximum=archive?config.max_file_size:config.max_image_size;
  if(!file.size||file.size>maximum)throw Error(`文件为空或超过单${archive?'包':'图'}上限 ${(maximum/1024**2).toFixed(0)} MiB`);
  const hashes:string[]=[];
  for(let offset=0;offset<file.size;offset+=config.chunk_size){
    signal.throwIfAborted();
    const bytes=await file.slice(offset,offset+config.chunk_size).arrayBuffer().catch(()=>{
      throw Error(`无法读取“${file.name}”：文件可能已被删除或重新生成。请重新选择该文件后继续上传。`);
    });
    hashes.push(await sha256(bytes));
  }
  const requestConfig={signal,skipErrorToast:true};
  let job:UploadJob=(await retryUpload(()=>api.post('/api/image-uploads',{filename:file.name,size:file.size,chunk_hashes:hashes,
    collection_name:collection,project_id:projectId,min_long_edge:minimum,image_kind:imageKind,...annotation,...batch},requestConfig),signal)).data;
  if(job.status==='completed'){onProgress(file.size);return job;}
  onProgress(job.uploaded_bytes);
  if(['uploading','failed'].includes(job.status)){
    const uploaded=new Set(job.uploaded_parts);
    let received=job.uploaded_bytes;
    for(let index=0;index<hashes.length;index++){
      if(uploaded.has(index))continue;
      signal.throwIfAborted();
      const part=file.slice(index*config.chunk_size,(index+1)*config.chunk_size);
      await retryUpload(()=>api.put(`/api/image-uploads/${job.id}/parts/${index}`,part,{...requestConfig,timeout:120000,
        headers:{'Content-Type':'application/octet-stream'},onUploadProgress:event=>onProgress(Math.min(file.size,received+(event.loaded||0)))}),signal);
      received+=part.size;onProgress(received);
    }
    job=(await retryUpload(()=>api.post(`/api/image-uploads/${job.id}/complete`,null,requestConfig),signal)).data;
  }
  while(job.status==='pending'||job.status==='processing'){
    signal.throwIfAborted();onProgress(file.size);
    await waitForUpload(1000,signal);
    job=(await retryUpload(()=>api.get('/api/image-uploads/'+job.id,requestConfig),signal)).data;
  }
  if(job.status!=='completed')throw Error(job.error||'入库失败，可继续或清除任务后重新上传');
  onProgress(file.size);return job;
}
