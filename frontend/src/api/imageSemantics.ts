import apiClient from './client';

export type SemanticStatus = 'pending' | 'running' | 'completed' | 'failed' | 'cancelled';
export interface SemanticItem {
  id: string; image_id: string | null; original_name: string; status: SemanticStatus;
  attempt: number; caption: string | null; error: string | null;
}
export interface SemanticJob {
  id: string; collection_id: string | null; caption_model_id: string | null; embedding_model_id: string | null;
  dimensions: number; preprocessing: string; created_at: string; total: number;
  counts: Partial<Record<SemanticStatus, number>>;
}
export interface SemanticDetail extends SemanticJob { items: SemanticItem[]; page: number; page_size: number }
export interface SemanticHit { image_id: string; original_name: string; caption: string | null; score: number }
export interface SemanticResults {
  items: SemanticHit[]; indexed_count: number; limit: number; warning: string;
  space: { model_id: string; revision: number; dimensions: number; preprocessing: string };
}
export interface SemanticJobInput {
  image_ids?: string[]; collection_id?: string; caption_model_id?: string; embedding_model_id?: string;
}
export interface SemanticSearchInput {
  embedding_model_id: string; collection_id?: string; text?: string; image_id?: string; top_k: number;
}
export async function createSemanticJob(input: SemanticJobInput): Promise<SemanticJob> {
  return (await apiClient.post('/api/image-semantics/jobs', input)).data;
}
export async function getSemanticJobs(collection_id?: string, page = 1, signal?: AbortSignal): Promise<SemanticJob[]> {
  return (await apiClient.get('/api/image-semantics/jobs', { params: { collection_id, page, page_size: 20 }, signal })).data;
}
export async function getSemanticJob(id: string, page = 1, signal?: AbortSignal): Promise<SemanticDetail> {
  return (await apiClient.get(`/api/image-semantics/jobs/${id}`, { params: { page, page_size: 20 }, signal })).data;
}
export async function semanticJobAction(id: string, action: 'cancel' | 'retry', item_ids?: string[]): Promise<{ changed_count: number }> {
  return (await apiClient.post(`/api/image-semantics/jobs/${id}/${action}`, { item_ids })).data;
}
export async function searchSemantics(input: SemanticSearchInput, signal?: AbortSignal): Promise<SemanticResults> {
  return (await apiClient.post('/api/image-semantics/search', input, { signal, timeout: 185000 })).data;
}
