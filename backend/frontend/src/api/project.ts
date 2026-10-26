import request from '@/utils/request'

export interface Project {
  id: string
  project_name: string
  description?: string
  task_type: string
  status: string
  total_assets: number
  annotated_count: number
  created_at: string
}

export interface CreateProjectParams {
  project_name: string
  description?: string
  task_type: string
  label_schema: any
}

export interface Asset {
  id: string
  original_name: string
  file_size: number
  width: number
  height: number
  format: string
  annotation_status: string
  quality_score?: number
  capture_time?: string
  url: string
  source_type: string
  created_at: string
}

// 创建项目
export function createProject(data: CreateProjectParams) {
  return request({
    url: '/projects',
    method: 'post',
    data
  })
}

// 获取项目列表
export function getProjects(params?: { status?: string; limit?: number; offset?: number }) {
  return request({
    url: '/projects',
    method: 'get',
    params
  })
}

// 获取项目详情
export function getProjectDetail(projectId: string) {
  return request({
    url: `/projects/${projectId}`,
    method: 'get'
  })
}

// 获取项目图片
export function getProjectAssets(
  projectId: string,
  params?: { annotation_status?: string; limit?: number; offset?: number }
) {
  return request({
    url: `/projects/${projectId}/assets`,
    method: 'get',
    params
  })
}

// 上传图片
export function uploadImages(projectId: string, formData: FormData) {
  return request({
    url: `/projects/${projectId}/upload`,
    method: 'post',
    data: formData,
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  })
}

// 删除项目
export function deleteProject(projectId: string) {
  return request({
    url: `/projects/${projectId}`,
    method: 'delete'
  })
}
