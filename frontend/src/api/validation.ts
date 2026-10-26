import axios from 'axios';
import apiClient from './client';
import type { PaginatedResponse } from './projects';
import type { Box } from '../components/detectionEditor';

export interface ValidationSet {
  id: string; name: string; project_id: string | null; labels: string[]; total: number;
  source: Record<string, unknown>; created_at: string;
}
export interface ValidationSetCreate {
  name: string; project_id: string; snapshot_id?: string; split: 'val' | 'test'; asset_ids?: string[];
}
export interface ValidationConfig {
  inference_confidence: number; confidence: number; match_iou: number; nms_iou: number;
  target_overlap?: number;
  label_mapping: Record<string, string>; labels: string[];
}
export interface CheckpointInput {
  server_id: string; training_run_id?: string; kind: string; weight_path: string; image: string;
  device: string; class_names: string[]; accept_platform_model_license: boolean;
  base_model_path?:string; model_type?:string;
}
export interface ClassMetrics {
  label: string; truth_count: number; prediction_count: number; tp: number; fp: number; fn: number;
  precision: number | null; recall: number | null; f1: number | null; ap50: number | null; ap50_95: number | null;
}
export interface ValidationMetrics {
  tp: number | null; fp: number | null; fn: number | null; precision: number | null; recall: number | null;
  f1: number | null; map50: number | null; map50_95: number | null; per_class: ClassMetrics[];
  images: number; negative_images: number; false_positive_negative_images: number;
  merged_predictions?: number;
  confusion_matrix: { labels: string[]; matrix: number[][] };
  coverage: { total: number; succeeded: number; failed: number; pending: number; canceled: number; complete: boolean };
}
export interface ValidationRun {
  id: string; set_id: string; name: string; status: string; total: number; processed: number; failed_count: number;
  model: { id: string; revision: number; name: string; score_type?: string; source?: string; weight_path?: string; weight_sha256?: string; image_digest?: string; kind?: string; device?: string }; config: ValidationConfig; metrics: Partial<ValidationMetrics> | null;
  remote_active?: boolean;
  target_metrics?: Partial<ValidationMetrics>;
  labels?: string[];
  error: string | null; created_at: string; updated_at: string;
}
export type ValidationErrorType = 'false_positive' | 'false_negative' | 'class_confusion' | 'failed' | 'correct';
export interface ValidationItem {
  id: string; asset_id: string; original_name: string; width: number; height: number; status: string;
  truth: Box[]; predictions: (Box & { score?: number })[];
  evaluation: { tp?: number; fp?: number; fn?: number; ignored_duplicates?: number[]; unmatched_truth?: number[]; unmatched_predictions?: number[] } | null; error: string | null;
}
export interface ValidationView { mode: 'standard' | 'target'; target_overlap?: number }
export interface ValidationSnapshot {
  id: string; format: string; created_at: string;
  manifest: { labels: string[]; assets: { split: string }[]; split_evidence?: unknown };
}
export const validationStatusLabels: Record<string, string> = { pending: '等待中', running: '执行中', completed: '已完成', failed: '失败', canceled: '已取消' };
export const activeValidationRun = (run: ValidationRun) => run.status === 'pending' || run.status === 'running';
const root = '/api/validation';
export const getValidationSets = async (params: { page: number; page_size: number }, signal?: AbortSignal): Promise<PaginatedResponse<ValidationSet>> =>
  (await apiClient.get(`${root}/sets`, { params, signal })).data;
export const createValidationSet = async (data: ValidationSetCreate): Promise<ValidationSet> => (await apiClient.post(`${root}/sets`, data, { timeout: 120000 })).data;
export const refreshValidationSet = async(id:string):Promise<ValidationSet> => (await apiClient.post(`${root}/sets/${encodeURIComponent(id)}/refresh`, undefined, { timeout: 120000 })).data;
export const deleteValidationSet = async(id:string):Promise<void> => {await apiClient.delete(`${root}/sets/${encodeURIComponent(id)}`);};
export const getValidationSnapshots = async (projectId: string, signal?: AbortSignal): Promise<ValidationSnapshot[]> =>
  (await apiClient.get(`/api/export/projects/${encodeURIComponent(projectId)}/snapshots`, { signal })).data;
export const getValidationRuns = async (params: { set_id?: string; page: number; page_size: number }, signal?: AbortSignal): Promise<PaginatedResponse<ValidationRun>> =>
  (await apiClient.get(`${root}/runs`, { params, signal })).data;
export const createValidationRun = async (data: { set_id: string; model_id?: string; checkpoint?: CheckpointInput; name?: string; config: ValidationConfig }): Promise<ValidationRun> =>
  (await apiClient.post(`${root}/runs`, data)).data;
export const getValidationRun = async (id: string, signal?: AbortSignal, view?: ValidationView): Promise<ValidationRun> =>
  (await apiClient.get(`${root}/runs/${encodeURIComponent(id)}`, { signal, params:view })).data;
export const changeValidationRun = async (id: string, action: 'cancel' | 'retry'): Promise<ValidationRun> =>
  (await apiClient.post(`${root}/runs/${encodeURIComponent(id)}/${action}`)).data;
export const deleteValidationRun = async (id: string): Promise<void> => {
  await apiClient.delete(`${root}/runs/${encodeURIComponent(id)}`);
};
export const getValidationItems = async (id: string, params: { page: number; page_size: number; error_type?: ValidationErrorType; label?: string } & Partial<ValidationView>, signal?: AbortSignal): Promise<PaginatedResponse<ValidationItem>> =>
  (await apiClient.get(`${root}/runs/${encodeURIComponent(id)}/items`, { params, signal })).data;
export const getValidationImage = async (runId: string, itemId: string, signal?: AbortSignal): Promise<Blob> =>
  (await apiClient.get(`${root}/runs/${encodeURIComponent(runId)}/items/${encodeURIComponent(itemId)}/image`, { responseType: 'blob', signal })).data;
export const getValidationReport = async (id: string, view?: ValidationView): Promise<Blob> =>
  (await apiClient.get(`${root}/runs/${encodeURIComponent(id)}/report`, { responseType: 'blob', params:view })).data;
export function validationError(error: unknown, fallback: string): string {
  if (axios.isAxiosError(error) && (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT')) return `${fallback}：请求超时，后台可能仍在处理，请刷新列表确认后再重试。`;
  if (axios.isAxiosError(error) && typeof error.response?.data?.detail === 'string') return `${fallback}：${error.response.data.detail}`;
  return fallback;
}
export function validationErrorLabels(item: ValidationItem, type: 'false_negative' | 'false_positive'): string {
  const truth=type==='false_negative',boxes=truth?item.truth:item.predictions;
  const indices=(truth?item.evaluation?.unmatched_truth:item.evaluation?.unmatched_predictions)||[];
  const counts=new Map<string,number>();
  for(const index of indices){const label=boxes[index]?.label;if(label)counts.set(label,(counts.get(label)||0)+1);}
  return [...counts].map(([label,count])=>`${label} ×${count}`).join('、')||'—';
}
