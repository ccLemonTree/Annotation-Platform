import request from '@/utils/request'

export interface Platform {
  id: string
  platform_name: string
  plat_id: string
  description?: string
}

export interface SmartEyeTask {
  id: string
  task_name: string
  platform_id: string
  status: string
  progress: number
  total_count: number
  downloaded_count: number
  created_at: string
}

export interface CreateTaskParams {
  platform_id: string
  task_name: string
  start_time: string
  end_time: string
  device_id?: string
  alarm_type?: string
}

export interface ImportToProjectParams {
  task_id: string
  project_id: string
  device_ids?: string[]
  date_range?: any
  quality_min?: number
}

// 获取平台列表
export function getPlatforms() {
  return request({
    url: '/smarteye/platforms',
    method: 'get'
  })
}

// 创建下载任务
export function createTask(data: CreateTaskParams) {
  return request({
    url: '/smarteye/tasks',
    method: 'post',
    data
  })
}

// 获取任务列表
export function getTasks(params?: { status?: string; limit?: number; offset?: number }) {
  return request({
    url: '/smarteye/tasks',
    method: 'get',
    params
  })
}

// 获取任务详情
export function getTaskDetail(taskId: string) {
  return request({
    url: `/smarteye/tasks/${taskId}`,
    method: 'get'
  })
}

// 导入到标注项目（零拷贝）
export function importToProject(data: ImportToProjectParams) {
  return request({
    url: '/smarteye/import-to-project',
    method: 'post',
    data
  })
}

// 获取下载记录
export function getDownloads(taskId: string, params?: { limit?: number; offset?: number }) {
  return request({
    url: `/smarteye/tasks/${taskId}/downloads`,
    method: 'get',
    params
  })
}
