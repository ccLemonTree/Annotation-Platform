import apiClient from './client';
import type { AttributeSchema, AttributeTemplate } from './objectAttributes';
import type { Box } from '../components/detectionEditor';

export type Operation = 'crop' | 'caption' | 'index' | 'detect_attributes' | 'attributes' | 'quality';
export type ModelWorkflowOperation = 'crop' | 'detect' | 'preannotate' | 'ai_review' | 'filter' | 'quality' | 'caption' | 'attributes';
export type BuiltinWorkflowOperation = 'branch' | 'img_filter' | 'box_clean' | 'img_transform' | 'script';
export type WorkflowOperation = ModelWorkflowOperation | BuiltinWorkflowOperation;

export interface BranchCase {
  key: string;
  name: string;
  match_mode?: 'all' | 'any';
  crop_labels?: string[];
  box_labels?: string[];
  min_boxes?: number | null;
  max_boxes?: number | null;
  attribute_key?: string;
  attribute_values?: string[];
  min_width?: number | null;
  min_height?: number | null;
}

export interface BranchConfig {
  first_match: boolean;
  branches: BranchCase[];
}

export interface ImageFilterConfig {
  min_width?: number | null;
  max_width?: number | null;
  min_height?: number | null;
  max_height?: number | null;
  min_short_edge?: number | null;
  min_long_edge?: number | null;
  min_aspect_ratio?: number | null;
  max_aspect_ratio?: number | null;
  min_sharpness?: number | null;
  min_brightness?: number | null;
  max_brightness?: number | null;
  dedup_phash_distance?: number | null;
}

export interface BoxCleanConfig {
  min_box_width?: number;
  min_box_height?: number;
  min_area_ratio?: number;
  max_area_ratio?: number;
  edge_margin_px?: number;
  keep_labels?: string[];
  drop_labels?: string[];
  rename_map?: Record<string, string>;
  nms_iou_threshold?: number | null;
  class_agnostic_nms?: boolean;
  pass_when_empty?: boolean;
  save_boxes?: boolean;
}

export interface ImageTransformConfig {
  roi_mode: 'none' | 'ratio' | 'pixels';
  roi_ratio?: [number, number, number, number] | null;
  roi_margins_px?: [number, number, number, number] | null;
  blur_labels?: string[];
  blur_all_boxes?: boolean;
  blur_radius?: number;
}

export interface ScriptConfig {
  code: string;
  timeout_seconds?: number;
  save_boxes?: boolean;
  save_attributes?: boolean;
  save_description?: boolean;
}

export interface WorkflowEdge {
  source: string;
  target: string;
  source_handle: string;
}

export interface WorkflowStep {
  node_key?: string;
  title?: string;
  operation: WorkflowOperation;
  model_id?: string | null;
  prompt?: string;
  attribute_prompt?: string;
  labels?: string[];
  confidence?: number;
  expansion?: number;
  min_crop_long_edge?: number;
  save_input_boxes?: boolean | null;
  auto_approve_confidence?: number;
  attribute_templates?: AttributeTemplate[] | null;
  branch_config?: BranchConfig | null;
  img_filter_config?: ImageFilterConfig | null;
  box_clean_config?: BoxCleanConfig | null;
  img_transform_config?: ImageTransformConfig | null;
  script_config?: ScriptConfig | null;
}

export interface WorkflowRun { id: string; name: string; steps: WorkJob[] }
export interface WorkflowHistoryConfig {
  name: string;
  created_at: string;
  image_kind?: 'originals' | 'crops' | 'all' | 'uploads' | null;
  source_job_id?: string | null;
  follow_uploads?: boolean | null;
  uploaded_crop_job_id?: string | null;
  edges?: WorkflowEdge[] | null;
  steps: (Omit<WorkflowStep, 'model_id'> & { id: string; model_name?: string; model_revision?: number; default_prompt?: string | null; attribute_schema?: AttributeSchema })[];
}
export interface WorkflowConfig {
  name: string;
  steps: WorkflowStep[];
  edges?: WorkflowEdge[];
  attribute_schema?: AttributeSchema;
}
export interface WorkflowTemplate extends WorkflowConfig { id: string }

export interface WorkflowDryRunItem {
  name: string;
  passed: boolean;
  reason: string;
  metrics?: Record<string, number | string>;
  branches?: string[];
  logs?: string[];
  boxes?: (Box & { score?: number })[];
  attributes?: Record<string, unknown>;
  description?: string;
  preview_url?: string;
  derived_previews?: string[];
  width?: number;
  height?: number;
}

export interface WorkflowDryRunNodeReport {
  node_key: string;
  title: string;
  operation: WorkflowOperation;
  duration_ms: number;
  input_count: number;
  output_count: number;
  error?: string | null;
  items: WorkflowDryRunItem[];
}

export interface WorkflowDryRunResult {
  sample_count: number;
  samples: { asset_id: string; original_name: string; width: number; height: number }[];
  edges: WorkflowEdge[];
  nodes: WorkflowDryRunNodeReport[];
}

export interface WorkflowAiGenerateResult {
  name: string;
  summary: string;
  warnings: string[];
  steps: WorkflowStep[];
  edges: WorkflowEdge[];
  orchestrator_model_id: string;
  orchestrator_model_name: string;
}

export interface WorkflowAiScriptResult {
  code: string;
  explanation: string;
  orchestrator_model_id: string;
  orchestrator_model_name: string;
}

export type Judgment = 'correct' | 'incorrect' | 'unknown';
export interface WorkStage { total: number; counts: Record<string, number> }
export interface WorkJob {
  id: string;
  node_key?: string;
  title?: string;
  workflow_routes?: { handle: string; target_job_id: string }[];
  operation: Operation | WorkflowOperation | 'match_review';
  workflow_started?: boolean;
  workflow_cancelled?: boolean;
  workflow_streaming?: boolean;
  workflow_can_follow?: boolean;
  workflow_can_route_uploaded_crops?: boolean;
  uploaded_crop_job_id?: string | null;
  batch_count?: number;
  passed_count?: number;
  attribute_skipped_count?: number;
  model_id: string | null;
  revision: number;
  total: number;
  counts: Record<string, number>;
  retrying_count?: number;
  follow_uploads?: boolean;
  created_at?: string;
  model_name?: string;
  attribute_model_name?: string;
  target_labels?: string[];
  attribute_fields?: string[];
  min_crop_long_edge?: number;
  filtered_crop_count?: number;
  waiting_reason?: string;
  quality_counts?: { passed: number; deleted: number };
  quality_requirement?: string;
  failure_reasons?: { operation: Operation | WorkflowOperation | 'match_review'; reason: string; count: number }[];
  pipeline?: { detection: WorkStage; attributes: WorkStage; generated_targets: number; preserved_annotations: number; child_batches: number };
  parent?: { job_id: string; original_name: string | null };
  example?: { asset_id: string; original_name: string; annotation_version: number };
}
export interface WorkItem {
  id: string;
  asset_id: string | null;
  original_name?: string | null;
  status: string;
  attempt: number;
  error: string | null;
  auto_retry?: boolean;
  result: {
    asset_ids?: string[];
    source_annotation_id?: string;
    source_annotation_preserved?: boolean;
    attribute_job_id?: string;
    skipped_annotated_count?: number;
    attribute_template_id?: string;
    attribute_skipped?: boolean;
    attributes?: import('./objectAttributes').AttributeValues;
    detected_count?: number;
    filtered_count?: number;
    description?: string;
    warning?: string;
    reason?: string;
    passed?: boolean;
    deleted?: boolean;
    original_name?: string;
    matched_branches?: string[];
    logs?: string[];
  } | null;
}
export interface WorkDetail extends WorkJob { items: WorkItem[]; page: number; crop_from_originals?: boolean; attribute_schema?: AttributeSchema; attribute_templates?: AttributeTemplate[]; }
export interface SmartSearchInfo { platform_id: string; platform_name: string; region: number[]; original_width: number; original_height: number; record: { timestamp?: string; deviceName?: string; confidence?: number }; }
export interface MatchHit { asset_id: string; image_id: string | null; original_name: string; source_type: string; score: number; judgment: Judgment; description?: string; labels?: string[]; smart_search?: SmartSearchInfo; review_status?: 'pending' | 'approved' | 'rejected'; ai_review?: { status: 'approved' | 'needs_review' | 'resolved'; judgment: 'correct' | 'incorrect' | 'uncertain'; reason: string; model_name: string; revision: number; reviewed_at: string }; }
export interface MatchQuery { id: string; query: { text?: string; image_id?: string; query_asset_id?: string; source?: string; platform_name?: string }; version: number; space: { revision: number; source?: string; failures?: { index: number; error: string }[] }; hits: MatchHit[]; failures?: { index: number; error: string }[]; }
export interface SmartSearchReference { reference_query_id: string; reference_asset_id: string; region: { x: number; y: number; width: number; height: number }; }
export interface SmartSearchRequest { platform_id: string; text?: string; limit: number; start_time: string; end_time: string; reference_query_id?: string; reference_asset_id?: string; region?: SmartSearchReference['region']; }

const base = (id: string) => `/api/projects/${id}`;
export const createWork = async (id: string, input: { operation: Operation; model_id: string; attribute_model_id?: string; example_asset_id?: string; example_annotation_version?: number; asset_ids: string[]; prompt: string; attribute_prompt?: string; detection_prompt?: string; labels: string[]; confidence: number; expansion: number; min_crop_long_edge?: number; follow_uploads?: boolean }): Promise<WorkJob> =>
  (await apiClient.post(base(id) + '/work/jobs', input)).data;
export const getWorkJobs = async (id: string, page = 1, crops_only = false): Promise<WorkJob[]> => (await apiClient.get(base(id) + '/work/jobs', { params: { page, roots_only: true, crops_only } })).data;
export const getWorkflows = async (id: string, page = 1): Promise<WorkflowRun[]> => (await apiClient.get(base(id) + '/work/workflows', { params: { page }, skipErrorToast: true })).data;
export const getWorkflowHistoryConfig = async (id: string, workflow: string): Promise<WorkflowHistoryConfig> =>
  (await apiClient.get(base(id) + `/work/workflows/${workflow}`, { skipErrorToast: true })).data;
export const deleteWorkflow = async (id: string, workflow: string): Promise<void> => {
  await apiClient.delete(base(id) + `/work/workflows/${workflow}`, { skipErrorToast: true });
};
export const cancelWorkflow = async (id: string, workflow: string) => (await apiClient.post(base(id) + `/work/workflows/${workflow}/cancel`)).data;
export const createWorkflow = async (id: string, input: WorkflowConfig & { image_kind: 'all' | 'originals' | 'crops' | 'uploads'; source_job_id?: string; follow_uploads?: boolean }): Promise<{ id: string }> =>
  (await apiClient.post(base(id) + '/work/workflows', input, { timeout: 120000 })).data;

export const dryRunWorkflow = async (
  id: string,
  input: Omit<WorkflowConfig, 'name'> & { image_kind: 'all' | 'originals' | 'crops'; source_job_id?: string; sample_asset_ids?: string[] },
  signal?: AbortSignal,
): Promise<WorkflowDryRunResult> =>
  (await apiClient.post(base(id) + '/work/workflows/dry-run', input, { timeout: 185000, skipErrorToast: true, signal })).data;

export const aiGenerateWorkflow = async (
  projectId: string | undefined,
  input: { prompt: string; model_id?: string | null; current_workflow?: Partial<WorkflowConfig> },
): Promise<WorkflowAiGenerateResult> => {
  const url = projectId ? `${base(projectId)}/work/workflows/ai-generate` : '/api/workflow-templates/ai-generate';
  return (await apiClient.post(url, input, { timeout: 185000, skipErrorToast: true })).data;
};

export const aiGenerateWorkflowScript = async (
  projectId: string | undefined,
  input: { prompt: string; model_id?: string | null; current_code?: string },
): Promise<WorkflowAiScriptResult> => {
  const url = projectId ? `${base(projectId)}/work/workflows/ai-script` : '/api/workflow-templates/ai-script';
  return (await apiClient.post(url, input, { timeout: 185000, skipErrorToast: true })).data;
};

const workflowTemplates = '/api/workflow-templates';
export const getWorkflowTemplates = async (): Promise<WorkflowTemplate[]> =>
  (await apiClient.get(workflowTemplates)).data;
export const saveWorkflowTemplate = async (input: WorkflowConfig, templateId?: string): Promise<WorkflowTemplate> =>
  (await (templateId ? apiClient.put(`${workflowTemplates}/${templateId}`, input) : apiClient.post(workflowTemplates, input))).data;
export const deleteWorkflowTemplate = async (templateId: string): Promise<void> => {
  await apiClient.delete(`${workflowTemplates}/${templateId}`);
};
export const getWork = async (id: string, job: string, page = 1): Promise<WorkDetail> => (await apiClient.get(base(id) + `/work/jobs/${job}`, { params: { page } })).data;
export const deleteWorkJob = async (id: string, job: string): Promise<void> => { await apiClient.delete(base(id) + `/work/jobs/${job}`); };
export const workAction = async (id: string, job: string, action: 'cancel' | 'retry') => (await apiClient.post(base(id) + `/work/jobs/${job}/${action}`)).data;
export const followWorkUploads = async (id: string, job: string, enabled: boolean, uploadedCropJobId?: string | null): Promise<{ follow_uploads: boolean }> =>
  (await apiClient.put(base(id) + `/work/jobs/${job}/follow-uploads`, { enabled, uploaded_crop_job_id: uploadedCropJobId })).data;
export const searchMatches = async (id: string, input: { model_id: string; text?: string; image_id?: string; top_k: number }): Promise<MatchQuery> =>
  (await apiClient.post(base(id) + '/matches/search', input, { timeout: 185000 })).data;
export const getSmartSearchPlatforms = async (id: string): Promise<{ id: string; name: string }[]> =>
  (await apiClient.get(base(id) + '/matches/smart-search/platforms')).data;
export const searchSmartMatches = async (id: string, input: SmartSearchRequest): Promise<MatchQuery> =>
  (await apiClient.post(base(id) + '/matches/smart-search', input, { timeout: 600000 })).data;
export const getMatchQueries = async (id: string, page = 1): Promise<MatchQuery[]> => (await apiClient.get(base(id) + '/matches', { params: { page } })).data;
export const judgeMatch = async (id: string, query: MatchQuery, asset_id: string, judgment: Judgment): Promise<MatchQuery> =>
  (await apiClient.put(base(id) + `/matches/${query.id}`, { asset_id, judgment, base_version: query.version })).data;
export const reviewMatch = async (id: string, query: MatchQuery, asset_id: string, approved: boolean): Promise<MatchQuery> =>
  (await apiClient.post(base(id) + `/matches/${query.id}/review`, { asset_id, approved, base_version: query.version })).data;
export const createMatchReviewBatch = async (id: string, input: { model_id: string; query_ids?: string[]; prompt: string; review_prompt?: string }): Promise<WorkJob[]> =>
  (await apiClient.post(base(id) + '/matches/review-jobs', input, { timeout: 120000 })).data;
