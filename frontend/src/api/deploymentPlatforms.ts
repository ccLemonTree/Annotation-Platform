import apiClient from './client';
import type {BatchResult,DeploymentInput} from './modelDeployments';
export interface PlatformConfig {name?:string;environment:DeploymentInput['environment'];control_server:string;file_server:string;namespace:string;services:Partial<DeploymentInput>[]}
export interface RuntimeEnvironment {checked_at:string;expires_at:string;error?:string;instances?:{name:string;gpus:string[];tensorrt:string}[]}
export interface DeploymentPlatform {id:string;name:string;config:PlatformConfig;models:{key:string;name:string;target:DeploymentInput;environment?:RuntimeEnvironment}[];checked_at?:string;error?:string;stale:boolean;needs_runtime_refresh?:boolean;warnings?:string[];versions:{[key:string]:{state:string;version?:string;name?:string}}}
export interface PlatformPlan {platform_ids:string[];versions:({repository_id:string}|{release_id:string})[];assignments:{platform_id:string;version_key:string;model_key:string}[]}
export type Compatibility='compatible'|'incompatible'|'unknown'|'not_required'|'skipped';
export interface PlanItem {platform_id:string;platform_name:string;version_key:string;name:string;version:string;built_gpu?:string;built_trt?:string;selected_key:string|null;error?:string;compatibility:Compatibility;compatibility_message:string;auto_skipped:boolean;options:{key:string;name:string;repository:string;service:string;disabled:boolean;compatibility:Compatibility;message:string;gpus:string[];tensorrt_versions:string[];checked_at?:string}[]}
export const getDeploymentPlatforms=async(signal?:AbortSignal):Promise<{items:DeploymentPlatform[]}> => (await apiClient.get('/api/model-deployments/platforms',{signal})).data;
export const saveDeploymentPlatform=async(input:PlatformConfig&{name:string},id?:string):Promise<DeploymentPlatform> => (await (id?apiClient.put(`/api/model-deployments/platforms/${id}`,input):apiClient.post('/api/model-deployments/platforms',input))).data;
export const discoverPlatformModels=async(id:string):Promise<DeploymentPlatform> => (await apiClient.post(`/api/model-deployments/platforms/${id}/discover`,undefined,{timeout:360000})).data;
export const importDeploymentPlatforms=async():Promise<{imported:number}> => (await apiClient.post('/api/model-deployments/platforms/import-targets')).data;
export const previewPlatformPlan=async(input:PlatformPlan,signal?:AbortSignal):Promise<{items:PlanItem[]}> => (await apiClient.post('/api/model-deployments/platforms/preview',input,{signal})).data;
export const createPlatformBatch=async(input:PlatformPlan):Promise<BatchResult> => (await apiClient.post('/api/model-deployments/platforms/batches',input,{timeout:120000})).data;
