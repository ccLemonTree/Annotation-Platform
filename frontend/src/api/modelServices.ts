import apiClient from './client';
import type { Box } from '../components/detectionEditor';

export type ModelKind = 'detection' | 'caption' | 'embedding' | 'reranker';
export type ModelProtocol = 'model_service' | 'openai_chat' | 'openai_embedding' | 'openai_rerank';
export interface ChatPrompts { caption: string; review: string; match_review?:string; training_analysis?:string }
export interface PromptDefaults extends ChatPrompts { scene: string }
export interface ModelServiceConfig {
  key: string;
  name: string;
  base_url: string;
  endpoint: string;
  protocol: ModelProtocol;
  kind: ModelKind;
  model_name: string;
  enabled: boolean;
  timeout_seconds: number;
  confidence: number;
  iou: number;
  labels: string[];
  label_mapping: Record<string, string>;
  api_key_env?: string;
  chat_prompts?: ChatPrompts;
}
export interface ModelService extends ModelServiceConfig { id: string; revision: number }
export const hasDetectionConfidence = (model?: ModelServiceConfig): boolean =>
  !(model?.protocol === 'model_service' && model.endpoint === '/detect/grounding');
export const hasDetectionNms = (model?: ModelServiceConfig): boolean =>
  hasDetectionConfidence(model) && !model?.endpoint?.endsWith('/sam3') && !model?.endpoint?.endsWith('_sahi');
export function supportsModelPrompt(model?: ModelServiceConfig): boolean {
  return !!model && (model.protocol !== 'model_service' || model.kind === 'detection' && model.endpoint === '/detect/grounding');
}
export interface PredictionResult {
  kind: ModelKind;
  boxes: (Box & { score?: number })[];
  caption?: string;
  embedding_dimensions?: number;
  embedding_preview?: number[];
  model_service_id: string;
  config_revision: number;
  warnings: string[];
}
export type PredictionInput = ({ asset_id: string; image_id?: never } | { image_id: string; asset_id?: never }) & { prompt?: string };

export async function getModelServices(params?: { kind?: ModelKind; enabled?: boolean }): Promise<ModelService[]> {
  return (await apiClient.get('/api/model-services', { params })).data;
}
export async function getPromptDefaults(): Promise<PromptDefaults> {
  return (await apiClient.get('/api/model-services/prompt-defaults')).data;
}
export async function createModelService(config: ModelServiceConfig): Promise<ModelService> {
  return (await apiClient.post('/api/model-services', config)).data;
}
export async function updateModelService(id: string, config: ModelServiceConfig): Promise<ModelService> {
  return (await apiClient.put(`/api/model-services/${id}`, config)).data;
}
export async function deleteModelService(id: string): Promise<void> {
  await apiClient.delete(`/api/model-services/${id}`);
}
export async function addDefaultModelServices(): Promise<ModelService[]> {
  return (await apiClient.post('/api/model-services/defaults')).data;
}
export async function testModelService(id: string): Promise<{ ok: boolean; detail: string; models?: string[] }> {
  return (await apiClient.post(`/api/model-services/${id}/test`, undefined, { timeout: 185000 })).data;
}
export async function predictModelService(id: string, input: PredictionInput, signal?: AbortSignal): Promise<PredictionResult> {
  if (Boolean(input.asset_id) === Boolean(input.image_id)) throw new Error('请选择且只选择一个图片 ID');
  return (await apiClient.post(`/api/model-services/${id}/predict`, input, { timeout: 185000, signal })).data;
}
