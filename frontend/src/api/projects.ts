// 项目管理API
import apiClient from './client';

export type TaskType = 'detection' | 'classification' | 'segmentation' | 'caption' | 'vector';
export type ProjectStatus = 'draft' | 'active' | 'completed' | 'archived';

export interface Project {
  id: string;
  tenant_id: number;
  project_name: string;
  task_type: TaskType;
  description: string | null;
  label_schema: Record<string, unknown> | null;
  status: ProjectStatus;
  total_assets: number;
  annotated_count: number;
  reviewed_count: number;
  created_at: string;
  updated_at: string;
}
export interface ProjectCreate {
  project_name: string;
  task_type: TaskType;
  description?: string;
  label_schema?: Record<string, unknown>;
}
export interface ProjectUpdate {
  project_name?: string;
  description?: string;
  label_schema?: Record<string, unknown> | null;
  status?: ProjectStatus;
  label_renames?: Record<string, string>;
  base_label_schema?: Record<string, unknown> | null;
}
export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
export const getProjects = async (params?: {page?: number; page_size?: number; task_type?: TaskType; status?: ProjectStatus}): Promise<PaginatedResponse<Project>> => {
  return (await apiClient.get('/api/projects', {params})).data;
};
export const createProject = async (data: ProjectCreate): Promise<Project> => (await apiClient.post('/api/projects', data)).data;
export const getProject = async (id: string): Promise<Project> => (await apiClient.get(`/api/projects/${id}`)).data;
export const getAnnotationStats = async (id:string):Promise<Pick<Project,'total_assets'|'annotated_count'|'reviewed_count'>> =>
  (await apiClient.get(`/api/projects/${id}/stats`,{params:{scope:'annotation'}})).data;
export const updateProject = async (id: string, data: ProjectUpdate): Promise<Project> => (await apiClient.put(`/api/projects/${id}`, data)).data;
export const deleteProject = async (id: string): Promise<void> => { await apiClient.delete(`/api/projects/${id}`); };

