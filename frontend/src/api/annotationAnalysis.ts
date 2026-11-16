import apiClient from './client';
import type {AttributeSchema,AttributeValues} from './objectAttributes';

export interface AnalysisModel {id:string;name:string;revision:number;unavailable_reason:string|null;prompt:string;supports_prompt:boolean}
export type AnalysisMode='attributes'|'description';
export interface ImageAnalysis {asset_id:string;model_id:string;model_revision:number;attribute_schema?:AttributeSchema;attributes?:AttributeValues;description?:string}
const base=(id:string)=>`/api/annotations/assets/${id}`;
export const getAnalysisModels=async(id:string,mode:AnalysisMode,signal?:AbortSignal):Promise<AnalysisModel[]>=>
  (await apiClient.get(base(id)+'/analysis-models',{params:{mode},signal,skipErrorToast:true})).data;
export const analyzeCurrentImage=async(id:string,model:AnalysisModel,mode:AnalysisMode,prompt:string,attributePrompt:string,signal:AbortSignal):Promise<ImageAnalysis>=>
  (await apiClient.post(base(id)+'/analyze',{model_id:model.id,model_revision:model.revision,mode,...(model.supports_prompt?{prompt,...(mode==='attributes'?{attribute_prompt:attributePrompt}:{})}:{})},{signal,timeout:185000,skipErrorToast:true})).data;
