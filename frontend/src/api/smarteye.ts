// SmartEye API
import apiClient from './client';

export interface PlatformInfo {
  platform_id: string;
  platform_name: string;
  supported: boolean;
}

export interface SmartEyeTask {
  id: number;
  task_name: string;
  platform: string;
  keyword: string;
  target_count: number;
  status: 'pending' | 'running' | 'completed' | 'failed';
  downloaded_count: number;
  failed_count: number;
  created_at: string;
  started_at?: string;
  completed_at?: string;
}

export interface CreateTaskRequest {
  tenant_id: number;
  task_name: string;
  platform: string;
  keyword: string;
  target_count: number;
}

export interface ImportRequest {
  task_id: number;
  project_id: string;
  download_ids?: number[];
}

export interface ImportResponse {
  success: boolean;
  imported_count: number;
  skipped_count: number;
  message: string;
}

// 获取支持的平台列表
export const getPlatforms = async (): Promise<PlatformInfo[]> => {
  const response = await apiClient.get('/api/smarteye/platforms');
  return response.data;
};

// 创建下载任务
export const createTask = async (
  data: CreateTaskRequest
): Promise<SmartEyeTask> => {
  const response = await apiClient.post('/api/smarteye/tasks', data);
  return response.data;
};

// 获取任务列表
export const getTasks = async (params?: {
  tenant_id?: number;
  status?: string;
  platform?: string;
  skip?: number;
  limit?: number;
}): Promise<SmartEyeTask[]> => {
  const response = await apiClient.get('/api/smarteye/tasks', { params });
  return response.data;
};

// 获取任务详情
export const getTask = async (taskId: number): Promise<SmartEyeTask> => {
  const response = await apiClient.get(`/api/smarteye/tasks/${taskId}`);
  return response.data;
};

// 获取任务的下载记录
export const getTaskDownloads = async (
  taskId: number,
  params?: { skip?: number; limit?: number }
): Promise<{
  total: number;
  items: any[];
}> => {
  const response = await apiClient.get(`/api/smarteye/tasks/${taskId}/downloads`, {
    params,
  });
  return response.data;
};

// 零拷贝导入到项目
export const importToProject = async (
  data: ImportRequest
): Promise<ImportResponse> => {
  const response = await apiClient.post('/api/smarteye/import-to-project', data);
  return response.data;
};
