import apiClient from './client';
import type { PaginatedResponse } from './assets';
import { imageBatches } from './imageBatches';

export interface LibraryImage {
  id: string; original_name: string; blob_path: string; content_hash: string;
  asset_id?: string | null;
  in_target_project?: boolean;
  file_size: number; width: number | null; height: number | null; format: string | null;
  source_type: string; batch_id: string | null; created_at: string; collection_names: string[];
  scene_labels: string[]; scene_status: string; scene_error: string | null; scene_metadata: Record<string,unknown> | null;
  has_imported_annotation?:boolean;imported_annotation_trusted?:boolean;
  device_info?:Record<string,unknown>|null;
}
export type ImageFilters = {platform_name?:string;device_name?:string;scene?: string; scene_status?: string;q?: string; collection_id?: string; source_type?: string; source_project_id?:string;label?:string;review_status?:'approved'|'approved_or_pending';batch_id?: string; created_from?: string; created_to?: string; page?: number; page_size?: number};
export async function getImageSourcePlatforms(collection_id?:string,project_id?:string):Promise<string[]> {
  return (await apiClient.get('/api/images/source-platforms',{params:{collection_id,project_id}})).data;
}
export async function getImages(params: ImageFilters & {reuse_project_id?:string}, signal?:AbortSignal): Promise<PaginatedResponse<LibraryImage>> {
  return (await apiClient.get('/api/images',{params,signal})).data;
}
export async function getImageSelection(filters: ImageFilters, exclude_project_id?: string, signal?: AbortSignal): Promise<{image_ids:string[];total:number}> {
  return (await apiClient.get('/api/images/selection',{params:{...filters,page:undefined,page_size:undefined,exclude_project_id},signal})).data;
}
export async function uploadImages(files: File[],collectionName?: string,minLongEdge?:number): Promise<{images:LibraryImage[];skipped:number}> {
  const body=new FormData(); files.forEach(file=>body.append('files',file));
  if(collectionName!==undefined)body.append('collection_name',collectionName);
  if(minLongEdge!==undefined)body.append('min_long_edge',String(minLongEdge));
  const response=await apiClient.post('/api/images/batch-upload',body,{headers:{'Content-Type':'multipart/form-data'}});
  return {images:response.data,skipped:Number(response.headers['x-images-skipped']||0)};
}
export async function associateImages(project_id: string,image_ids: string[],completed?: (ids:string[])=>void,includeImported=false,source?:{projectId:string;includeAnnotations:boolean;includePending?:boolean},uploadId?:string) {
  const results=await imageBatches(image_ids,async ids=>(await apiClient.post<{added_count:number;skipped_count?:number;asset_ids:string[];annotated?:number;reused_annotations?:number;annotation_skipped?:number;conflicts?:number;without_annotations?:number;preannotation_queued?:number;preannotation_warning?:string}>('/api/images/associate',{project_id,image_ids:ids,upload_id:uploadId,...(includeImported?{include_imported_annotations:true}:{}),...(source?{source_project_id:source.projectId,include_source_annotations:source.includeAnnotations,include_pending_source_annotations:source.includePending}:{})},{timeout:120000,skipErrorToast:!!uploadId})).data,completed);
  return {added_count:results.reduce((n,result)=>n+result.added_count,0),skipped_count:results.reduce((n,result)=>n+(result.skipped_count||0),0),asset_ids:results.flatMap(result=>result.asset_ids),annotated:results.reduce((n,result)=>n+(result.annotated||0),0),reused_annotations:results.reduce((n,result)=>n+(result.reused_annotations||0),0),annotation_skipped:results.reduce((n,result)=>n+(result.annotation_skipped||0),0),conflicts:results.reduce((n,result)=>n+(result.conflicts||0),0),without_annotations:results.reduce((n,result)=>n+(result.without_annotations||0),0),
    preannotation_queued:results.reduce((n,result)=>n+(result.preannotation_queued||0),0),
    preannotation_warning:results.find(result=>result.preannotation_warning)?.preannotation_warning};
}
export async function deleteImage(id: string) { await apiClient.delete(`/api/images/${id}`); }
export interface ImageCollection {id:string;name:string;image_count:number;ingest_config?:ImageIngestConfig;ingest_revision?:number}
export async function getImageCollections(signal?:AbortSignal):Promise<ImageCollection[]> {
  return (await apiClient.get('/api/images/collections',{signal})).data;
}
export async function createImageCollection(name:string):Promise<{id:string;name:string}> {
  return (await apiClient.post('/api/images/collections',{name})).data;
}
export interface CollectionDeletion {total_images:number;deleted_images:number;kept_images:number}
export async function previewImageCollectionDeletion(id:string):Promise<CollectionDeletion> {
  return (await apiClient.get(`/api/images/collections/${id}/delete-preview`)).data;
}
export async function deleteImageCollection(id:string):Promise<CollectionDeletion> {
  return (await apiClient.delete(`/api/images/collections/${id}`)).data;
}
export async function assignImageCollection(name:string,image_ids:string[],completed?: (ids:string[])=>void):Promise<{collection_id:string;added_count:number}> {
  const results=await imageBatches(image_ids,async ids=>(await apiClient.post<{collection_id:string;added_count:number}>('/api/images/collections/assign',{name,image_ids:ids},{timeout:120000})).data,completed);
  return {collection_id:results[0].collection_id,added_count:results.reduce((n,result)=>n+result.added_count,0)};
}


export interface SceneGroup {name:string;scenes:string[]}
export interface ImageIngestConfig {
  classification_enabled:boolean;base_url:string;model:string;scenes:string[];min_long_edge:number;revision?:number;scene_prompt?:string;
  scene_groups?:SceneGroup[];
}
export async function getImageIngestSettings(collectionId?:string):Promise<ImageIngestConfig> {
  return (await apiClient.get('/api/images/ingest-settings',{params:{collection_id:collectionId}})).data;
}
export async function saveImageIngestSettings(config:ImageIngestConfig,collectionId?:string):Promise<ImageIngestConfig> {
  return (await apiClient.put('/api/images/ingest-settings',config,{params:{collection_id:collectionId}})).data;
}
export async function testImageIngestService(config:ImageIngestConfig):Promise<{models:string[];model_available:boolean}> {
  return (await apiClient.post('/api/images/ingest-settings/test',config)).data;
}
export async function classifyImages(image_ids:string[],collection_id:string,completed?: (ids:string[])=>void):Promise<{queued_count:number}> {
  const results=await imageBatches(image_ids,async ids=>(await apiClient.post<{queued_count:number}>('/api/images/scene-classify',{image_ids:ids,collection_id},{timeout:120000})).data,completed);
  return {queued_count:results.reduce((n,result)=>n+result.queued_count,0)};
}

