import api from './client';
import { isAxiosError } from 'axios';
import { sha256 } from '../utils/sha256';

export type UploadResult={imported:number;duplicates:number;filtered:number;invalid:number;ignored:number;errors:string[];annotated?:number;annotation_skipped?:number;conflicts?:number};
export type UploadJob={id:string;filename:string;size:number;collection_id:string;min_long_edge:number;annotation_format?:'yolo';trust_annotations?:boolean;status:string;uploaded_parts:number[];uploaded_bytes:number;chunk_size:number;cursor:number;result:UploadResult;error:string|null};
export type UploadConfig={chunk_size:number;max_file_size:number;max_image_size:number;max_expanded_size:number;max_entries:number;retention_days:number};
export const getUploadConfig=async():Promise<UploadConfig>=>(await api.get('/api/image-uploads/config')).data;
export const listUploads=async():Promise<UploadJob[]>=>(await api.get('/api/image-uploads')).data;
export const deleteUpload=async(id:string)=>{await api.delete('/api/image-uploads/'+id);};

function waitForUpload(ms:number,signal:AbortSignal){
  return new Promise<void>((resolve,reject)=>{
    signal.throwIfAborted();
    const done=()=>{signal.removeEventListener('abort',abort);resolve();};
    const timer=setTimeout(done,ms);
    const abort=()=>{clearTimeout(timer);reject(signal.reason);};
    signal.addEventListener('abort',abort,{once:true});
  });
}

export async function uploadResumable(file:File,collection:string,minimum:number|undefined,config:UploadConfig,
  signal:AbortSignal,onProgress:(phase:string,percent:number)=>void,annotation?:{annotation_format:'yolo';trust_annotations:boolean}):Promise<UploadJob>{
  const archive=/\.(zip|tar|tar\.gz|tgz)$/i.test(file.name);
  const maximum=archive?config.max_file_size:config.max_image_size;
  if(!file.size||file.size>maximum)throw Error(`文件为空或超过单${archive?'包':'图'}上限 ${(maximum/1024**2).toFixed(0)} MiB`);
  const hashes:string[]=[];
  for(let offset=0;offset<file.size;offset+=config.chunk_size){
    signal.throwIfAborted();
    const bytes=await file.slice(offset,offset+config.chunk_size).arrayBuffer();
    hashes.push(await sha256(bytes));
    onProgress('校验文件',Math.min(100,Math.round((offset+bytes.byteLength)/file.size*100)));
  }
  signal.throwIfAborted();
  let job:UploadJob=(await api.post('/api/image-uploads',{filename:file.name,size:file.size,chunk_hashes:hashes,
    collection_name:collection,min_long_edge:minimum,...annotation},{signal})).data;
  if(job.status==='completed'){onProgress('已完成，复用已有上传',100);return job;}
  if(['uploading','failed'].includes(job.status)){
    const uploaded=new Set(job.uploaded_parts);
    let received=job.uploaded_bytes;
    for(let index=0;index<hashes.length;index++){
      if(uploaded.has(index))continue;
      signal.throwIfAborted();
      const part=file.slice(index*config.chunk_size,(index+1)*config.chunk_size);
      for(let attempt=0;;attempt++){
        try{
          await api.put(`/api/image-uploads/${job.id}/parts/${index}`,part,{signal,timeout:120000,skipErrorToast:true,
            headers:{'Content-Type':'application/octet-stream'},onUploadProgress:event=>onProgress('上传分片',Math.min(99,Math.round((received+(event.loaded||0))/file.size*100)))});
          break;
        }catch(error){
          signal.throwIfAborted();
          if(!isAxiosError(error)||!(['ERR_NETWORK','ECONNABORTED','ETIMEDOUT'].includes(error.code||'')||[502,503,504].includes(error.response?.status||0)))throw error;
          if(attempt===2){
            onProgress('传输中断，可继续上传',Math.floor(received/file.size*100));
            throw Error('分片上传连接中断，已重试2次；已接收的分片保留，点击“开始 / 继续上传”可续传。');
          }
          onProgress(`连接中断，正在重试分片（${attempt+1}/2）`,Math.floor(received/file.size*100));
          await waitForUpload((attempt+1)*1000,signal);
        }
      }
      received+=part.size;onProgress('上传分片',Math.round(received/file.size*100));
    }
    job=(await api.post(`/api/image-uploads/${job.id}/complete`,null,{signal})).data;
  }
  while(job.status==='pending'||job.status==='processing'){
    signal.throwIfAborted();onProgress(`后台入库，已处理 ${job.cursor} 项`,100);
    await waitForUpload(1000,signal);
    job=(await api.get('/api/image-uploads/'+job.id,{signal})).data;
  }
  if(job.status!=='completed')throw Error(job.error||'入库失败，可继续或清除任务后重新上传');
  onProgress('已完成',100);return job;
}
