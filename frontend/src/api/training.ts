import apiClient from './client';
import type { GpuServer } from './gpuResources';

export interface TrainingMetric {
  name:string; file:string; count:number; first:number; last:number; best:number|null;
  best_epoch:number|null; best_step:number|null; last_epoch:number|null; last_step:number|null; direction:'higher'|'lower'|null;
  series:{value:number;epoch:number|null;step:number|null;index:number}[];
}
export interface TrainingEvidenceRun {id:string;name:string;kind:string;status:string;metrics:TrainingMetric[];warnings:string[];files:{path:string;bytes:number;rows:number;sha256:string}[]}
export interface TrainingEvidence {
  collected_at:string;current:TrainingEvidenceRun;previous:TrainingEvidenceRun[];limitations:string[];
  comparisons:{run_id:string;name:string;comparable:boolean;warnings:string[];metrics:{name:string;file:string;direction:string|null;current:number;previous:number;last_delta:number;best_delta:number|null}[]}[];
}
export interface TrainingReport {id:string;created_at:string;model:{name:string;model_name:string;revision:number};prompt:string;evidence:TrainingEvidence;analysis:{summary:string;findings:string[];recommendations:string[];limitations:string[]}}
export type TrainingReportSummary=Pick<TrainingReport,'id'|'created_at'|'model'>;
export const getTrainingComparisons=async(id:string,page=1):Promise<{items:TrainingRun[];total:number}>=>
  (await apiClient.get(`/api/training/runs/${id}/comparison-candidates`,{params:{page,page_size:20}})).data;
export const getTrainingReports=async(id:string):Promise<TrainingReportSummary[]>=>
  (await apiClient.get(`/api/training/runs/${id}/analysis`)).data;
export const getTrainingReport=async(id:string,reportId:string):Promise<TrainingReport>=>
  (await apiClient.get(`/api/training/runs/${id}/analysis/${reportId}`)).data;
export const collectTrainingEvidence=async(id:string,compare_ids:string[]):Promise<TrainingEvidence>=>
  (await apiClient.post(`/api/training/runs/${id}/analysis/evidence`,{compare_ids},{timeout:1400000})).data;
export const analyzeTraining=async(id:string,compare_ids:string[],model_id:string,prompt:string):Promise<TrainingReport>=>
  (await apiClient.post(`/api/training/runs/${id}/analysis`,{compare_ids,model_id,prompt},{timeout:1600000})).data;

export interface ParameterField {
  type?: string; title?: string; default?: unknown; enum?: (string | number)[];
  minimum?: number; maximum?: number; exclusiveMinimum?: number; anyOf?: ParameterField[]; const?: string | number;
}
export interface TrainingTemplate {
  name: string; image: string; defaults: Record<string, unknown>; environment: Record<string, string>;
  schema: { properties: Record<string, ParameterField> };
}
export interface TrainingInput {
  name: string; kind: string; server_id: string; image: string; gpu_uuids: string[];
  dataset_path?: string; snapshot_id?: string; model_path: string; output_root: string; output_subdir: string;
  shm_size_gb: number; accept_platform_model_license: boolean;
  selected_labels?: string[] | null;
  parameters: Record<string, unknown>; environment: Record<string, string>;
  mounts: { source: string; target: string; read_only: boolean }[];
  output?: ModelOutputInput;
  model_kind?: string;
  adapter_path?: string;
  onnx_stage?:{id:string;server:{id:string;name:string};config:TrainingInput};
}
export interface ModelOutputInput {
  format:'onnx'|'tensorrt'|'merge';server_id:string;gpu_uuid:string;source_path:string;base_model_path?:string;
  output_root:string;image:string;batch:number;dynamic_batch:boolean;min_batch:number;opt_batch:number;max_batch:number;
  image_size?:number|null;fp16:boolean;opset:number;simplify:boolean;input_name:string;output_name:string;
  trust_checkpoint:boolean;model_type:string;torch_dtype:'float16'|'bfloat16'|'float32';
  source_gpu_uuid?:string;onnx_image?:string;
}
export const createModelOutput = async(id:string,input:ModelOutputInput):Promise<TrainingRun> =>
  (await apiClient.post(`/api/training/runs/${id}/outputs`,input)).data;
export interface TrainingCheck {
  ok: boolean; checks: { name: string; ok: boolean; detail: string }[]; image_digest?: string;
}
export interface TrainingRun {
  id: string; name: string; kind: string; status: string; config: TrainingInput;
  source: { kind: string; id?: string; sha256?: string; format?: string; path?: string; project_id?: string; labels?: string[]; run_id?:string;source_path?:string;model_kind?:string };
  server: { id: string; name: string }; output_dir: string; error?: string; created_at: string;
  inspection: Partial<TrainingCheck> & { runtime?: unknown;output_phase?:'onnx'|'transferring'|'tensorrt';
    startup?:{state:'queued'|'running'|'complete'|'failed'|'interrupted'|'canceled';phase:string;steps:string[];archive_bytes?:number;events:{phase:string;message:string;time:string}[]} };
}
export interface TrainingArtifact { path: string; bytes: number; role: string }
export interface TrainingSnapshot {
  id: string; format: string; content_hash: string;
  manifest: { labels?: string[]; assets?: { split: string }[]; distribution?: { images_by_split?: Record<string, number> }; training?:{format:string;source:string;samples:number} };
  created_at: string;
}
export const getTrainingCatalog = async (signal?: AbortSignal): Promise<Record<string, TrainingTemplate>> =>
  (await apiClient.get('/api/training/catalog', { signal })).data;
export const getTrainingServers = async (signal?: AbortSignal): Promise<GpuServer[]> =>
  (await apiClient.get('/api/training/servers', { signal })).data;
export const getTrainingDefaults = async (server_id: string, kind: string, signal?: AbortSignal): Promise<{ model_path: string; output_root: string }> =>
  (await apiClient.get('/api/training/defaults', { params: { server_id, kind }, signal })).data;
export const getTrainingRuns = async (page = 1, signal?: AbortSignal): Promise<{ items: TrainingRun[]; total: number }> =>
  (await apiClient.get('/api/training/runs', { params: { page, page_size: 20 }, signal })).data;
export const getTrainingRun = async (id: string): Promise<TrainingRun> => (await apiClient.get(`/api/training/runs/${id}`)).data;
export const createTrainingRun = async (input: TrainingInput): Promise<TrainingRun> =>
  (await apiClient.post('/api/training/runs', input)).data;
export const updateTrainingRun = async (id: string, input: TrainingInput): Promise<TrainingRun> =>
  (await apiClient.put(`/api/training/runs/${id}`, input)).data;
export const deleteTrainingRun = async (id: string): Promise<void> => { await apiClient.delete(`/api/training/runs/${id}`); };
export async function downloadTrainingArtifact(id:string,path:string):Promise<void>{
  let data:Blob;
  try {data=(await apiClient.get(`/api/training/runs/${id}/artifacts/download`,{params:{path},responseType:'blob',timeout:14460000})).data;}
  catch(cause){
    const error=cause as {response?:{data?:Blob}};
    if(error.response?.data instanceof Blob){
      let detail:string|undefined;try{detail=JSON.parse(await error.response.data.text()).detail;}catch{ /* Keep the original transport error. */ }
      if(detail)throw Error(detail);
    }
    throw cause;
  }
  const url=URL.createObjectURL(data),link=document.createElement('a');link.href=url;link.download=path.split('/').pop()||'artifact';
  document.body.appendChild(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);
}
export const trainingAction = async (id: string, action: string): Promise<TrainingRun | TrainingCheck> =>
  (await apiClient.post(`/api/training/runs/${id}/${action}`, {}, { timeout: 360000 })).data;
export const getTrainingLogs = async (id: string): Promise<{ text: string }> =>
  (await apiClient.get(`/api/training/runs/${id}/logs`, { timeout: 360000 })).data;
export const getTrainingArtifacts = async (id: string): Promise<{ files: TrainingArtifact[]; truncated: boolean }> =>
  (await apiClient.get(`/api/training/runs/${id}/artifacts`, { timeout: 360000 })).data;
export const getTrainingSnapshots = async (project: string): Promise<TrainingSnapshot[]> =>
  (await apiClient.get(`/api/export/projects/${project}/snapshots`)).data;
export const freezeTrainingData = async(project:string,input:{dataset_format:string;sample_source:string;approved_only:boolean;instruction:string}):Promise<TrainingSnapshot> =>
  (await apiClient.post(`/api/export/projects/${project}/annotation-snapshots`,input,{timeout:120000})).data;
