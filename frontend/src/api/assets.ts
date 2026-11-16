// 资产管理API
import apiClient from './client';
import type {AttributeFilters} from './objectAttributes';
import type { AnnotationKind } from './annotations';
export type AssetAttributeFilters=Pick<AttributeFilters,'attribute_key'|'attribute_value'|'attribute_state'>;
export type AssetImageKind = 'all'|'originals'|'crops';
export const readAssetImageKind=(value:string|null):AssetImageKind=>value==='originals'||value==='crops'?value:'all';
export const defaultAnnotationImageKind=(kind:AnnotationKind):AssetImageKind=>kind==='image'?'crops':'originals';
export const assetImageKind=(asset:Asset):AssetImageKind=>asset.source_type==='model_crop'?'crops':'originals';

export interface Asset {
  id: string;
  project_id: string;
  image_id: string | null;
  batch_id?: string | null;
  original_name: string;
  blob_path: string;
  content_hash: string;
  file_size: number;
  format: string | null;
  width?: number;
  height?: number;
  annotation_status: 'pending' | 'annotating' | 'completed' | 'reviewed';
  source_type: string;
  device_info?: Record<string, unknown> | null;
  scene_labels?:string[];scene_status?:string;scene_error?:string|null;scene_metadata?:Record<string,unknown>|null;
  created_at: string;
  updated_at: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface AssetUploadResponse {
  asset_id: string;
  original_name: string;
  blob_path: string;
  file_size: number;
}

// 获取资产列表
export const getAssets = async (params?: {
  project_id?: string;
  annotation_status?: string;
  review_status?: 'pending'|'approved'|'rejected';
  annotation_kind?: 'detection'|'image';
  ai_pending?: boolean;
  collection_id?: string;
  label?: string;
  crop_label?: string[];
  scene?: string;
  q?: string;
  scope?: 'all' | 'annotation';
  image_kind?: 'all' | 'originals' | 'crops';
  crop_depth?:number;
  crop_job_id?:string;
  work_job_id?:string;
  platform_name?:string;device_name?:string;
  page?: number;
  page_size?: number;
}&AssetAttributeFilters): Promise<PaginatedResponse<Asset>> => {
  const response = await apiClient.get('/api/assets', { params,paramsSerializer:{indexes:null} });
  // Older backends ignore unknown query parameters; never present an unfiltered queue as filtered.
  if(params?.label&&response.headers['x-annotation-label-filter']!=='1')throw Error('后台尚未启用标签筛选');
  if(params?.crop_label?.length&&response.headers['x-asset-crop-label-filter']!=='1')throw Error('后台尚未启用切图标签筛选');
  if(params?.scene&&response.headers['x-asset-scene-filter']!=='1')throw Error('后台尚未启用场景筛选');
  if(params?.q&&response.headers['x-asset-filename-filter']!=='1')throw Error('后台尚未启用文件名搜索');
  if(params?.review_status&&response.headers['x-annotation-review-filter']!=='1')throw Error('后台尚未启用审核状态筛选');
  if(params?.collection_id&&response.headers['x-asset-collection-filter']!=='1')throw Error('后台尚未启用图片集筛选');
  if(params?.attribute_key&&response.headers['x-asset-attribute-filter']!=='1')throw Error('后台尚未启用目标属性筛选');
  if((params?.platform_name!==undefined||params?.device_name)&&response.headers['x-asset-source-filter']!=='1')throw Error('后台尚未启用平台和设备筛选');
  if(params?.crop_depth!==undefined&&response.headers['x-asset-crop-depth-filter']!=='1')throw Error('后台尚未启用切图轮次筛选');
  if(params?.work_job_id&&response.headers['x-asset-work-filter']!=='1')throw Error('后台尚未启用处理结果筛选');
  if(params?.crop_job_id&&response.headers['x-asset-crop-batch-filter']!=='1')throw Error('后台尚未启用切图批次筛选');
  return response.data;
};

export async function firstAnnotationImage(projectId:string,search:URLSearchParams,imageKind:AssetImageKind){
  const status=search.get('review_status');
  const attributeState=search.get('attribute_state');
  const attributes=search.get('kind')==='image'&&search.has('attribute_key')?{
    attribute_key:search.get('attribute_key')||undefined,attribute_value:search.get('attribute_value')||undefined,
    attribute_state:attributeState as AssetAttributeFilters['attribute_state']||undefined,
  }:{};
  return (await getAssets({project_id:projectId,scope:'annotation',image_kind:imageKind,work_job_id:search.get('work_job_id')||undefined,
    collection_id:search.get('collection_id')||undefined,label:search.get('label')||undefined,crop_label:search.getAll('crop_label'),
    review_status:status==='pending'||status==='approved'||status==='rejected'?status:undefined,...attributes,page:1,page_size:1})).items[0];
}

// 上传单个文件
export const uploadAsset = async (
  projectId: string,
  file: File
): Promise<AssetUploadResponse> => {
  const formData = new FormData();
  formData.append('file', file);

  const response = await apiClient.post(
    `/api/assets/upload?project_id=${projectId}`,
    formData,
    {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    }
  );
  return response.data;
};

// 批量上传文件
export const uploadAssetsBatch = async (
  projectId: string,
  files: File[]
): Promise<AssetUploadResponse[]> => {
  const formData = new FormData();
  files.forEach((file) => {
    formData.append('files', file);
  });

  const response = await apiClient.post(
    `/api/assets/batch-upload?project_id=${projectId}`,
    formData,
    {
      headers: {
        'Content-Type': 'multipart/form-data',
      },
    }
  );
  return response.data;
};

// 获取资产详情
export const getAsset = async (assetId: string,skipErrorToast=false): Promise<Asset> => {
  const response = await apiClient.get(`/api/assets/${assetId}`,{skipErrorToast});
  return response.data;
};

export interface AssetNeighbors {
  position: number;
  total: number;
  previous_id: string | null;
  next_id: string | null;
  matched?:boolean;
}

export const getAssetNeighbors = async (assetId: string,label?:string,review_status?:'pending'|'approved'|'rejected',collection_id?:string,attributes:AssetAttributeFilters&{work_job_id?:string;image_kind?:AssetImageKind;annotation_kind?:'detection'|'image';ai_pending?:boolean;crop_label?:string[]}={}): Promise<AssetNeighbors> => {
  const response=await apiClient.get(`/api/assets/${assetId}/neighbors`,{params:{label,review_status,collection_id,...attributes},paramsSerializer:{indexes:null}});
  if(review_status&&response.headers['x-annotation-review-filter']!=='1')throw Error('后台尚未启用审核状态筛选');
  if(collection_id&&response.headers['x-asset-collection-filter']!=='1')throw Error('后台尚未启用图片集筛选');
  if(attributes.work_job_id&&response.headers['x-asset-work-filter']!=='1')throw Error('后台尚未启用处理结果筛选');
  if(attributes.attribute_key&&response.headers['x-asset-attribute-filter']!=='1')throw Error('后台尚未启用目标属性筛选');
  if(attributes.crop_label?.length&&response.headers['x-asset-crop-label-filter']!=='1')throw Error('后台尚未启用切图标签筛选');
  return response.data;
};

export const getProjectAssetCollections = async(projectId:string,includeSourceCollections?:boolean):Promise<{id:string;name:string}[]> =>
  (await apiClient.get('/api/assets/collections',{params:{project_id:projectId,include_source_collections:includeSourceCollections}})).data;

// 删除资产
export const deleteAsset = async (assetId: string): Promise<void> => {
  await apiClient.delete(`/api/assets/${assetId}`);
};

export const deleteAssets = async (assetIds:string[]):Promise<{affected_count:number}> =>
  (await apiClient.post('/api/batch/assets/delete',assetIds,{timeout:120000})).data;

export const getAllAssetIds = async (params:{project_id:string;scope?:'all'|'annotation';image_kind?:'all'|'originals'|'crops';crop_depth?:number;crop_job_id?:string;work_job_id?:string;platform_name?:string;device_name?:string;label?:string;scene?:string;q?:string;collection_id?:string;review_status?:'pending'|'approved'|'rejected'}&AssetAttributeFilters):Promise<string[]> => {
  const response=await apiClient.get('/api/assets/selection',{params});
  if(params.q&&response.headers['x-asset-filename-filter']!=='1')throw Error('后台尚未启用文件名搜索');
  if(params.collection_id&&response.headers['x-asset-collection-filter']!=='1')throw Error('后台尚未启用图片集筛选');
  if(params.review_status&&response.headers['x-annotation-review-filter']!=='1')throw Error('后台尚未启用审核状态筛选');
  if(params.label&&response.headers['x-annotation-label-filter']!=='1')throw Error('后台尚未启用标签筛选');
  if(params.scene&&response.headers['x-asset-scene-filter']!=='1')throw Error('后台尚未启用场景筛选');
  if(params.attribute_key&&response.headers['x-asset-attribute-filter']!=='1')throw Error('后台尚未启用目标属性筛选');
  if((params.platform_name!==undefined||params.device_name)&&response.headers['x-asset-source-filter']!=='1')throw Error('后台尚未启用平台和设备筛选');
  if(params.crop_depth!==undefined&&response.headers['x-asset-crop-depth-filter']!=='1')throw Error('后台尚未启用切图轮次筛选');
  if(params.work_job_id&&response.headers['x-asset-work-filter']!=='1')throw Error('后台尚未启用处理结果筛选');
  if(params.crop_job_id&&response.headers['x-asset-crop-batch-filter']!=='1')throw Error('后台尚未启用切图批次筛选');
  return response.data.asset_ids;
};

// 批量更新资产状态
export const updateAssetsStatus = async (data: {
  asset_ids: string[];
  annotation_status: string;
}): Promise<{ success: boolean; affected_count: number; message: string }> => {
  const response = await apiClient.post('/api/batch/assets/update-status', data.asset_ids, { params: { new_status: data.annotation_status } });
  return response.data;
};

