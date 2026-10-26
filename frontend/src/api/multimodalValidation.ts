import api from './client';
import type { CheckpointInput,ValidationRun,ValidationSet } from './validation';

export type MultimodalKind='caption'|'embedding';
export type ReviewVerdict='correct'|'incorrect'|'uncertain';
export interface MultimodalItem {
  id:string;asset_id:string;original_name:string;width:number;height:number;status:string;error?:string;
  truth:{description?:string}[];evaluation:{caption?:string;dimensions?:number;review?:{verdict:ReviewVerdict;comment:string}};
}
export interface RetrievalQuery {
  id:string;kind:'text'|'image';text:string;asset_id?:string;excluded_reason?:string;
  first_positive_rank:number|null;positive_count:number;
  hits:{asset_id:string;item_id:string;original_name:string;score:number;judgment:string}[];
}
export interface MultimodalMetrics {
  task_type:MultimodalKind;reviewed?:number;correct?:number;incorrect?:number;uncertain?:number;pass_rate?:number|null;
  text?:Record<string,number|null>;image?:Record<string,number|null>;queries?:RetrievalQuery[];
  coverage?:{succeeded:number;failed:number;pending:number;canceled:number;complete:boolean;total:number};
}
export type MultimodalRun=Omit<ValidationRun,'config'|'metrics'>&{config:{prompt:string;max_tokens:number};metrics:MultimodalMetrics;items?:MultimodalItem[];page?:number};
export interface MultimodalInput {model_id?:string;checkpoint?:CheckpointInput;task_type:MultimodalKind;prompt?:string;max_tokens?:number;query_text?:string;query_image_index?:number}
export const listMultimodalSets=async(kind:MultimodalKind,page:number,signal:AbortSignal):Promise<{items:ValidationSet[];total:number}>=>(await api.get('/api/validation/sets',{params:{task_type:kind,page,page_size:20},signal})).data;
export const listMultimodalRuns=async(kind:MultimodalKind,preview:boolean,page:number,signal:AbortSignal):Promise<{items:MultimodalRun[];total:number}>=>(await api.get(preview?'/api/model-previews':'/api/validation/runs',{params:{task_type:kind,page,page_size:20},signal})).data;
export const multimodalRoot=(preview:boolean)=>preview?'/api/model-previews':'/api/validation/runs';
export async function multimodalDetail(id:string,preview:boolean,page:number,signal:AbortSignal){
  const root=multimodalRoot(preview);
  const run:MultimodalRun=(await api.get(`${root}/${id}`,{params:{page,page_size:20},signal})).data;
  if(preview)return {run,items:run.items||[]};
  const items=(await api.get(`${root}/${id}/items`,{params:{page,page_size:20},signal})).data;
  return {run,items:items.items as MultimodalItem[]};
}
export async function startMultimodal(input:MultimodalInput,preview:boolean,files:File[],setId?:string,onUpload?:(value:number)=>void){
  if(!preview)return (await api.post('/api/validation/runs',{set_id:setId,model_id:input.model_id,checkpoint:input.checkpoint,config:{prompt:input.prompt,max_tokens:input.max_tokens}},{timeout:600000})).data as MultimodalRun;
  const data=new FormData();data.append('request',JSON.stringify(input));files.forEach(file=>data.append('files',file,file.webkitRelativePath||file.name));
  return (await api.post('/api/model-previews',data,{headers:{'Content-Type':undefined},timeout:0,onUploadProgress:event=>{if(event.total)onUpload?.(Math.round(event.loaded/event.total*100));}})).data as MultimodalRun;
}
export const reviewCaption=async(run:string,item:string,verdict:ReviewVerdict,comment:string)=>api.post(`/api/validation/runs/${run}/items/${item}/review`,{verdict,comment});
