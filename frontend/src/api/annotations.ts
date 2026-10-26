// 标注管理API
import apiClient from './client';

export interface LabelRemovalPreview {
  project_id:string;project_name:string;label:string;scope:'all'|'crops';total_images:number;
  affected_images:number;removed_boxes:number;empty_images:number;reviewed_images:number;preview_token:string;
}
export const previewProjectLabelRemoval=async(projectId:string,label:string):Promise<LabelRemovalPreview>=>
  (await apiClient.post(`/api/projects/${projectId}/annotations/remove-label/preview`,{label})).data;
export const removeProjectLabel=async(projectId:string,preview:LabelRemovalPreview):Promise<LabelRemovalPreview>=>
  (await apiClient.post(`/api/projects/${projectId}/annotations/remove-label`,{label:preview.label,preview_token:preview.preview_token},{timeout:300000})).data;

export interface Annotation {
  id: string;
  asset_id: string;
  annotator_id: number | null;
  reviewer_id: number | null;
  annotation_source: string | null;
  annotation_data: any;
  version: number;
  is_latest: boolean;
  review_status: 'pending' | 'approved' | 'rejected';
  review_comment?: string;
  reviewed_at?: string;
  created_at: string;
  updated_at: string;
}

export interface AnnotationCreate {
  asset_id: string;
  annotation_data: any;
  base_version?: number;
}

export interface AnnotationUpdate {
  annotation_data: any;
  base_version: number;
}

export interface AnnotationReview {
  approved: boolean;
  review_comment?: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

// 获取标注列表
export const getAnnotations = async (params?: {
  asset_id?: string;
  project_id?: string;
  scope?: 'all' | 'annotation';
  latest_only?: boolean;
  review_status?: string;
  page?: number;
  page_size?: number;
}): Promise<PaginatedResponse<Annotation>> => {
  const response = await apiClient.get('/api/annotations', { params });
  return response.data;
};

// 创建标注
export const createAnnotation = async (
  data: AnnotationCreate
): Promise<Annotation> => {
  const response = await apiClient.post('/api/annotations', data);
  return response.data;
};

// 获取标注详情
export const getAnnotation = async (annotationId: string): Promise<Annotation> => {
  const response = await apiClient.get(`/api/annotations/${annotationId}`);
  return response.data;
};

// 更新标注
export const updateAnnotation = async (
  annotationId: string,
  data: AnnotationUpdate
): Promise<Annotation> => {
  const response = await apiClient.put(`/api/annotations/${annotationId}`, data);
  return response.data;
};

// 删除标注
export const deleteAnnotation = async (annotationId: string): Promise<void> => {
  await apiClient.delete(`/api/annotations/${annotationId}`);
};

// 审核标注
export const reviewAnnotation = async (
  annotationId: string,
  data: AnnotationReview
): Promise<Annotation> => {
  const response = await apiClient.post(
    `/api/annotations/${annotationId}/review`,
    data
  );
  return response.data;
};

// 批量审核标注
export const batchReviewAnnotations = async (data: {
  annotation_ids: string[];
  review_status: string;
  review_comment?: string;
}): Promise<{ success: boolean; affected_count: number; message: string }> => {
  const action = data.review_status === 'approved' ? 'approve' : 'reject';
  const response = await apiClient.post(`/api/batch/annotations/${action}`, data.annotation_ids, { params: { reason: data.review_comment } });
  return response.data;
};
