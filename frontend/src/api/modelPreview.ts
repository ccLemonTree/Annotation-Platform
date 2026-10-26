import api from './client';
import type { PaginatedResponse } from './projects';
import type { CheckpointInput,ValidationRun,ValidationItem } from './validation';

export type ModelPreview=ValidationRun&{items:ValidationItem[];page:number;page_size:number};
export type PreviewConfig={max_images:number;max_upload_size:number;max_expanded_size:number;max_image_size:number};
export type PreviewInput={confidence?:number;iou?:number}&({model_id:string;labels?:string[];checkpoint?:never}|{checkpoint:CheckpointInput;model_id?:never});
const root='/api/model-previews';
export const getPreviewConfig=async():Promise<PreviewConfig>=>(await api.get(root+'/config')).data;
export const listModelPreviews=async(signal?:AbortSignal):Promise<PaginatedResponse<ValidationRun>>=>(await api.get(root,{params:{page:1,page_size:100},signal})).data;
export const getModelPreview=async(id:string,signal?:AbortSignal,page=1):Promise<ModelPreview>=>(await api.get(`${root}/${id}`,{signal,params:{page,page_size:20}})).data;
export async function createModelPreview(files:File[],input:PreviewInput,signal?:AbortSignal,onProgress?:(percent:number)=>void):Promise<ValidationRun>{
  // shortcut: interrupted multipart uploads restart from zero; add chunked staging when cross-session resume is needed.
  const data=new FormData();data.append('request',JSON.stringify(input));files.forEach(file=>data.append('files',file,file.webkitRelativePath||file.name));
  return (await api.post(root,data,{headers:{'Content-Type':undefined},timeout:0,signal,onUploadProgress:event=>{if(event.total)onProgress?.(Math.round(event.loaded/event.total*100));}})).data;
}
export const changeModelPreview=async(id:string,action:'cancel'|'retry'):Promise<ValidationRun>=>(await api.post(`${root}/${id}/${action}`,undefined,{timeout:320000})).data;
export const deleteModelPreview=async(id:string):Promise<void>=>{await api.delete(`${root}/${id}`,{timeout:320000});};
export const getPreviewImage=async(id:string,item:string,signal?:AbortSignal):Promise<Blob>=>(await api.get(`${root}/${id}/items/${item}/image`,{responseType:'blob',signal})).data;
