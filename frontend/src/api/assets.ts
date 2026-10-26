// 资产管理API
import apiClient from './client';

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
  label?: string;
  q?: string;
  scope?: 'all' | 'annotation';
  image_kind?: 'all' | 'originals' | 'crops';
  page?: number;
  page_size?: number;
}): Promise<PaginatedResponse<Asset>> => {
  const response = await apiClient.get('/api/assets', { params });
  // Older backends ignore unknown query parameters; never present an unfiltered queue as filtered.
  if(params?.label&&response.headers['x-annotation-label-filter']!=='1')throw Error('后台尚未启用标签筛选');
  if(params?.q&&response.headers['x-asset-filename-filter']!=='1')throw Error('后台尚未启用文件名搜索');
  if(params?.review_status&&response.headers['x-annotation-review-filter']!=='1')throw Error('后台尚未启用审核状态筛选');
  return response.data;
};

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
export const getAsset = async (assetId: string): Promise<Asset> => {
  const response = await apiClient.get(`/api/assets/${assetId}`);
  return response.data;
};

export interface AssetNeighbors {
  position: number;
  total: number;
  previous_id: string | null;
  next_id: string | null;
  matched?:boolean;
}

export const getAssetNeighbors = async (assetId: string,label?:string,review_status?:'pending'|'approved'|'rejected'): Promise<AssetNeighbors> => {
  const response=await apiClient.get(`/api/assets/${assetId}/neighbors`,{params:{label,review_status}});
  if(review_status&&response.headers['x-annotation-review-filter']!=='1')throw Error('后台尚未启用审核状态筛选');
  return response.data;
};

// 删除资产
export const deleteAsset = async (assetId: string): Promise<void> => {
  await apiClient.delete(`/api/assets/${assetId}`);
};

export const deleteAssets = async (assetIds:string[]):Promise<{affected_count:number}> =>
  (await apiClient.post('/api/batch/assets/delete',assetIds,{timeout:120000})).data;

export const getAllAssetIds = async (params:{project_id:string;scope?:'all'|'annotation';image_kind?:'all'|'originals'|'crops';label?:string;q?:string;review_status?:'pending'|'approved'|'rejected'}):Promise<string[]> => {
  const response=await apiClient.get('/api/assets/selection',{params});
  if(params.q&&response.headers['x-asset-filename-filter']!=='1')throw Error('后台尚未启用文件名搜索');
  if(params.review_status&&response.headers['x-annotation-review-filter']!=='1')throw Error('后台尚未启用审核状态筛选');
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

