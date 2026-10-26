import apiClient from './client';

export interface DeploymentServer {name:string;host:string;user:string;port:number;group?:string;description?:string}
export interface DeploymentInput {
  run_id:string;artifact_path:string;environment:'k8s'|'docker'|'docker_kafka';runtime:'triton_plan'|'triton_vllm'|'vllm';
  file_server:string;control_server:string;repository:string;model_name:string;containers:string[];
  namespace:string;workload_kind:'StatefulSet'|'Deployment';workload:string;container:string;
  triton_config:string;runtime_config:Record<string,unknown>;
  preload_playbook?:string;preload_inventory?:string;
}
export interface DeploymentInstance {name:string;uid:string;ok?:boolean;ready?:boolean;detail?:string;image?:string;image_id?:string;health?:string}
export interface ModelDeployment {
  id:string;run_id:string|null;release_id?:string;target_id?:string;batch_id?:string;kind:'model'|'image';status:string;error?:string;created_at:string;
  source:{artifact_path?:string;root?:string;name?:string;version?:string;image?:string};target:DeploymentInput;
  inspection:{check_ok?:boolean;source_info?:{bytes:number;file_count:number;gpu_name?:string};
    inventory?:{desired:number;instances:DeploymentInstance[]};instances?:DeploymentInstance[];applied?:DeploymentInstance[];
    preload_info?:{playbook:string;inventory:string;node_count:number};
    image_receipt?:{node_count?:number;digest?:string;pinned_image?:string;archive_sha256?:string;log_path?:string;nodes?:{node:string;uid:string;digest:string;archive_sha256:string;ok:boolean}[]};
    transfer?:{bytes:number;sha256:string;file_count:number}};
  logs:{at:string;message:string}[];
}
export const getDeploymentServers=async(signal?:AbortSignal):Promise<DeploymentServer[]> =>
  (await apiClient.get('/api/model-deployments/servers',{signal})).data;
export const getDeployments=async(run_id?:string,page=1,signal?:AbortSignal,batch_id?:string):Promise<{items:ModelDeployment[];has_more:boolean}> =>
  (await apiClient.get('/api/model-deployments',{params:{run_id,page,batch_id},signal})).data;
export const getDeployment=async(id:string,signal?:AbortSignal):Promise<ModelDeployment> =>
  (await apiClient.get(`/api/model-deployments/${id}`,{signal})).data;
export const createDeployment=async(input:DeploymentInput):Promise<ModelDeployment> =>
  (await apiClient.post('/api/model-deployments',input)).data;
export const deploymentAction=async(id:string,action:string):Promise<ModelDeployment> =>
  (await apiClient.post(`/api/model-deployments/${id}/${action}`,undefined,{timeout:1400000})).data;

export interface ModelRelease {
  id:string;run_id:string;name:string;version:string;runtime:DeploymentInput['runtime'];status:string;error?:string;created_at:string;
  source:{artifact_path:string};archive:{path?:string;sha256?:string;bytes?:number;archived_at?:string;source_info?:{file_count:number}};
}
export interface ServerTarget {
  id:string;name:string;kind:'model'|'image';target:DeploymentInput;
  current:{state:string;version?:string;image?:string;name?:string;release_id?:string;checked_at?:string;instances?:DeploymentInstance[]};latest?:ModelDeployment;
}
export interface BatchResult {batch_id:string;items:ModelDeployment[]}
export const getDeploymentBatch=async(id:string):Promise<BatchResult> => (await apiClient.get(`/api/model-deployments/batches/${id}`)).data;
export const getModelReleases=async(page=1,signal?:AbortSignal):Promise<{items:ModelRelease[];has_more:boolean}> =>
  (await apiClient.get('/api/model-deployments/releases',{params:{page},signal})).data;
export const archiveModel=async(input:{run_id:string;name:string;version:string;runtime:string;artifact_path:string}):Promise<ModelRelease> =>
  (await apiClient.post('/api/model-deployments/releases',input)).data;
export const retryModelArchive=async(id:string):Promise<ModelRelease> => (await apiClient.post(`/api/model-deployments/releases/${id}/retry`)).data;
export const getDistributionSources=async(page=1,signal?:AbortSignal):Promise<{items:(import('./training').TrainingRun&{runtimes:DeploymentInput['runtime'][]})[];has_more:boolean}> =>
  (await apiClient.get('/api/model-deployments/sources',{params:{page},signal})).data;
export const getServerTargets=async(signal?:AbortSignal):Promise<{items:ServerTarget[]}> => (await apiClient.get('/api/model-deployments/targets',{signal})).data;
export const saveServerTarget=async(input:{name:string;kind:string;target:Partial<DeploymentInput>},id?:string):Promise<ServerTarget> =>
  (await (id?apiClient.put(`/api/model-deployments/targets/${id}`,input):apiClient.post('/api/model-deployments/targets',input))).data;
export const inspectServerTarget=async(id:string):Promise<ServerTarget> => (await apiClient.post(`/api/model-deployments/targets/${id}/inspect`,undefined,{timeout:360000})).data;
export const createDeploymentBatch=async(input:{kind:string;release_id?:string;image?:string;target_ids:string[]}):Promise<BatchResult> =>
  (await apiClient.post('/api/model-deployments/batches',input)).data;
export const deploymentBatchAction=async(id:string,operation:string):Promise<{results:{id:string;status?:string;error?:string}[]}> =>
  (await apiClient.post(`/api/model-deployments/batches/${id}/${operation}`,undefined,{timeout:1400000})).data;
