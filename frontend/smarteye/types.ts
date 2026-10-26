export interface DetectionRecord {
  id: string; // generated
  platform: string;
  dataType: string; // 'alarm' | 'capture' | 'waste'
  targetType: string;
  targetColor?: 'red' | 'blue' | 'gray' | 'yellow';
  device?: string; // might be missing
  deviceId?: string; // Add deviceId
  timestamp?: string; // might be missing
  count?: number; // accumulated count
  urls?: string[]; // image urls for details
  picUrl?: string; // single image url for detail view
}

export interface PlatformOption {
  label: string;
  value: string;
  alarmTypes: Record<string, string>; // { "name": "id" }
}

export interface MenuItem {
  id: string;
  label: string;
  icon: string;
  active?: boolean;
}

export interface SmartSearchPlatform {
  plat_id: string;
  name: string;
  base_url: string;
  state: string;
}

export interface SmartSearchPlatformResult {
  plat_id: string;
  plat_name: string;
  base_url: string;
  status: string;
  msg: string;
  image_count: number;
  download_count: number;
}

export interface AiImageAsset {
  id: string;
  name: string;
  source_type: string;
  local_path?: string;
  relative_name?: string;
  origin_url?: string;
  preview_url?: string;
  url?: string;
}

export interface AiImageTaskItem {
  item_id: string;
  batch_index: number;
  asset_count: number;
  status: string;
  volc_task_status?: string;
  volc_task_id?: string;
  error_message?: string;
  assets: AiImageAsset[];
  result_urls: string[];
  result_files: string[];
  preview_urls?: string[];
}

export interface AiImageTaskRuntime {
  task_id: string;
  task_type: string;
  prompt: string;
  status: string;
  progress: number;
  message: string;
  output_count: number;
  repeat_count: number;
  batch_size: number;
  total_items: number;
  completed_items: number;
  success_items: number;
  failed_items: number;
  params: Record<string, any>;
  items: AiImageTaskItem[];
  created_at: number;
}

export interface AiImageHistoryRecord {
  task_id: string;
  task_type: string;
  prompt: string;
  task_status: string;
  output_count: number;
  repeat_count: number;
  batch_size: number;
  total_items: number;
  completed_items: number;
  success_items: number;
  failed_items: number;
  root_dir: string;
  params_json: Record<string, any>;
  request_summary_json: Record<string, any>;
  latest_error: string;
  created_at: string;
  updated_at: string;
  expires_at: string;
}
