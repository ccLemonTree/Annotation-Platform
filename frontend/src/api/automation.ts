import apiClient from './client';
import type { PaginatedResponse } from './projects';

export type AutomationKind = 'preannotate' | 'review';
export type AutomationJobStatus = 'pending' | 'running' | 'completed' | 'canceled' | 'failed';
export type AutomationItemStatus = 'pending' | 'running' | 'approved' | 'needs_review' | 'failed' | 'skipped' | 'canceled';
export interface AutomationConfig {
  confidence: number;
  iou: number;
  auto_approve_confidence: number;
  crop_expansion: number;
  auto_review: boolean;
  label_mapping: Record<string, string>;
  labels: string[];
  prompt: string;
}
export interface AutomationJobCreate {
  project_id: string;
  kind: AutomationKind;
  asset_ids?: string[];
  detector_id?: string;
  reviewer_id?: string;
  config: AutomationConfig;
}
export interface AutomationJob {
  id: string;
  project_id: string;
  kind: AutomationKind;
  status: AutomationJobStatus;
  total: number;
  processed: number;
  approved_count: number;
  needs_review_count: number;
  failed_count: number;
  config: AutomationConfig;
  created_at: string;
  error?: string | null;
}
export interface AutomationItem {
  id: string;
  job_id: string;
  asset_id: string;
  annotation_id?: string | null;
  annotation_version?: number | null;
  status: AutomationItemStatus;
  reason?: string | null;
  evidence: Record<string, unknown>;
  result: Record<string, unknown>;
  created_at: string;
}
export interface AutomationListParams { project_id?: string; page?: number; page_size?: number }

export async function getAutomationJobs(params: AutomationListParams, signal?: AbortSignal): Promise<PaginatedResponse<AutomationJob>> {
  return (await apiClient.get('/api/automation/jobs', { params, signal })).data;
}
export async function createAutomationJob(data: AutomationJobCreate): Promise<AutomationJob> {
  return (await apiClient.post('/api/automation/jobs', data)).data;
}
export async function cancelAutomationJob(id: string): Promise<void> {
  await apiClient.post(`/api/automation/jobs/${encodeURIComponent(id)}/cancel`);
}
export async function retryAutomationJob(id: string): Promise<void> {
  await apiClient.post(`/api/automation/jobs/${encodeURIComponent(id)}/retry`);
}
export async function getAutomationItems(id: string, params: { page?: number; page_size?: number; status?: AutomationItemStatus }, signal?: AbortSignal): Promise<PaginatedResponse<AutomationItem>> {
  return (await apiClient.get(`/api/automation/jobs/${encodeURIComponent(id)}/items`, { params, signal })).data;
}
export async function getAutomationReviewQueue(params: AutomationListParams, signal?: AbortSignal): Promise<PaginatedResponse<AutomationItem>> {
  return (await apiClient.get('/api/automation/review-queue', { params, signal })).data;
}
export async function resolveAutomationItem(id: string, data: { approved: boolean; comment: string }): Promise<void> {
  await apiClient.post(`/api/automation/items/${encodeURIComponent(id)}/resolve`, data);
}
