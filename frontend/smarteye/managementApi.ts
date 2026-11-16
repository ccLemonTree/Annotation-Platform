import { apiFetch } from './network';
import { getProjects } from '../src/api/projects';

export interface ManagedPlatform {
  plat_id: string; platform_name: string; base_url: string; username: string; login_url?: string;
  state: string; has_password: boolean; connection_status: string; connection_error?: string;
  checked_at?: string; device_count?: number;classified_count?:number;scene_failed_count?:number;scene_pending_count?:number;scene_unmatched_count?:number;
}
export interface Device {
  plat_id: string; device_id: string; device_name: string; platform_name: string;
  scene_labels: string[]; scene_status: string; scene_error?: string; scene_source?: string;scene_basis?:'image'|'device_name';has_image?:boolean;
  representative_url?: string; representative_status?:string;representative_error?:string;updated_at?: string;
}
export interface CaptureSchedule {
  sched_id: string; name: string; devices: {plat_id:string;device_id:string}[];
  interval_minutes: number; interval_seconds?: number; project_id?: string; collection_id?: string; enabled: boolean;
  running: boolean; last_task_id?: string; last_run?: number; next_run?: number; last_error?: string;
}
export interface ManagementTask {
  task_id:string;kind:string;status:string;progress?:number;message?:string;project_id?:string;
  platform_results?:TaskResult[];device_results?:TaskResult[];
  name?:string;created_at?:string;updated_at?:string;processed_count?:number;failed_count?:number;skipped_count?:number;unmatched_count?:number;
}
export interface TaskResult {plat_id:string;platform_name?:string;device_id?:string;device_name?:string;status:string;error?:string;count?:number;device_total?:number;image_count?:number;image_reused_count?:number;image_missing_count?:number;image_failed_count?:number;downloaded_count?:number;downloaded?:number;filtered?:number;skipped?:number;processed_count?:number;success_count?:number;failed_count?:number;skipped_count?:number;unmatched_count?:number;device_results?:TaskResult[]}
export interface PlatformCheckTask extends ManagementTask {
  total_count:number;processed_count:number;online_count:number;error_count:number;unconfigured_count:number;
}
export async function managementApi<T>(path:string,method='GET',body?:unknown):Promise<T> {
  const response=await apiFetch(`/api/smarteye${path}`,{method,headers:{'Content-Type':'application/json'},...(body===undefined?{}:{body:JSON.stringify(body)})});
  const result=await response.json();
  if(!response.ok||result.code!==0)throw new Error(result.message||result.detail||`请求失败（${response.status}）`);
  return result.data;
}
export async function allProjects() {
  const items=[];
  for(let page=1;;page++){
    const result=await getProjects({page,page_size:100});items.push(...result.items);
    if(items.length>=result.total||!result.items.length)return items;
  }
}
export const deviceKey=(device:Pick<Device,'plat_id'|'device_id'>)=>JSON.stringify([device.plat_id,device.device_id]);
export const taskKinds:Record<string,string>={'device-sync':'设备同步','device-scenes':'设备场景分类','device-capture':'设备抓图'};
export const sceneStatuses:Record<string,string>={unclassified:'未分类',pending:'待分类',processing:'分类中',completed:'已分类',unmatched:'未匹配场景',failed:'分类失败'};
export const taskStatuses:Record<string,string>={pending:'等待中',running:'运行中',processing:'处理中',completed:'已完成',failed:'失败',cancelled:'已取消',interrupted:'已中断'};
