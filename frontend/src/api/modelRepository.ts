import { isAxiosError } from 'axios';
import type { AxiosRequestConfig } from 'axios';
import apiClient, { API_BASE_URL } from './client';

export type RepositoryFormat = 'pt' | 'onnx' | 'tensorrt' | 'python' | 'huggingface';
export interface RepositoryModel {
  id: string; name: string; group_name?: string; version: string; description?: string; format: RepositoryFormat; gpu_model: string;
  tensorrt_version?: string; batch_profile?: string; entry_file: string; file_size: number;
  files: { path: string; bytes: number; sha256: string }[];
  status: 'importing' | 'ready' | 'failed' | 'interrupted'; error?: string; run_id?: string;
  source?: { kind: 'upload' | 'training'; artifact_path?: string; onnx_input?: { name: string; shape: [number | 'N', number, number, number] } };
  created_at: string; updated_at: string;
}
export interface RepositoryMetadata {
  name: string; group_name?: string; version: string; format: RepositoryFormat; gpu_model?: string; tensorrt_version?: string; batch_profile?: string;
}
export interface RepositoryDownloadFilters { q?: string; group_name?: string; format?: RepositoryFormat; gpu_model?: string; }
export type RepositoryBatchDownload = { ids: string[]; filters?: never } | { filters: RepositoryDownloadFilters; ids?: never };
export type RepositoryUpdate = Pick<RepositoryModel, 'name' | 'version'> & { description: string; group_name?: string; gpu_model?: string; tensorrt_version?: string };
export const repositoryConfigPath = (model: RepositoryModel) => model.format === 'python' ? [...model.entry_file.split('/').slice(0, -2), 'config.pbtxt'].join('/') : 'config.pbtxt';
export interface RepositorySource { id: string; name: string; kind: string; created_at: string }
export interface RepositoryArtifact {
  path: string; bytes: number; format: RepositoryFormat; gpu_model?: string; tensorrt_version?: string; batch_profile?: string;
}
export const repositoryBatchLabel = (profile?: string) => !profile ? 'batch 未记录' : profile.startsWith('fixed:') ? `固定 batch ${profile.split(':')[1]}` : `动态 batch ${profile.split(':').slice(1).join(' / ')}（min / opt / max）`;
export interface RepositoryGroup {
  name: string; version_count: number; formats: RepositoryFormat[]; gpu_models: string[]; latest_at: string; importing_count: number;
}
const root = '/api/model-repository';
export const getRepositoryModel = async (id: string, signal?: AbortSignal): Promise<RepositoryModel> =>
  (await apiClient.get(`${root}/${id}`, { signal })).data;
export const getRepositoryModels = async (params: { page: number; q?: string; format?: RepositoryFormat; gpu_model?: string; group_name?: string; run_id?: string }, signal?: AbortSignal): Promise<{
  items: RepositoryModel[]; total: number; page: number; page_size: number; gpu_models: string[];
}> => (await apiClient.get(root, { params: { ...params, page_size: 20 }, signal })).data;
export const getRepositoryGroups = async (params: { page: number; q?: string; format?: RepositoryFormat; gpu_model?: string }, signal?: AbortSignal): Promise<{
  items: RepositoryGroup[]; total: number; version_total: number; gpu_models: string[];
}> => (await apiClient.get(`${root}/groups`, { params: { ...params, page_size: 20 }, signal })).data;
export const getRepositoryConfig = async (id: string, signal?: AbortSignal): Promise<{ content: string; sha256: string }> =>
  (await apiClient.get(`${root}/${id}/config`, { signal })).data;
export const getRepositorySources = async (page: number, signal?: AbortSignal): Promise<{ items: RepositorySource[]; total: number }> =>
  (await apiClient.get(`${root}/sources`, { params: { page, page_size: 20 }, signal })).data;
export const getRepositoryArtifacts = async (id: string, signal?: AbortSignal): Promise<{ items: RepositoryArtifact[]; truncated: boolean }> =>
  (await apiClient.get(`${root}/sources/${id}/artifacts`, { signal, timeout: 360000 })).data;
export const importRepositoryModel = async (input: RepositoryMetadata & { run_id: string; artifact_path: string }): Promise<RepositoryModel> =>
  (await apiClient.post(`${root}/from-training`, input, { timeout: 360000 })).data;
export const archiveTrainingModel = async (id: string, path: string): Promise<RepositoryModel> =>
  (await apiClient.post(`${root}/sources/${id}/archive`, { run_id: id, artifact_path: path, format: 'huggingface', name: '训练模型', version: 'auto' }, { timeout: 360000 })).data;
export const retryRepositoryModel = async (id: string): Promise<RepositoryModel> =>
  (await apiClient.post(`${root}/${id}/retry`)).data;
export const updateRepositoryModel = async (id: string, input: RepositoryUpdate): Promise<RepositoryModel> =>
  (await apiClient.patch(`${root}/${id}`, input)).data;
export const uploadRepositoryModel = async (input: RepositoryMetadata & { entry_file: string }, files: File[], onProgress: (value: number) => void): Promise<RepositoryModel> => {
  const body = new FormData();
  for (const [key, value] of Object.entries(input)) if (value) body.append(key, value);
  for (const file of files) body.append('files', file, file.webkitRelativePath || file.name);
  return (await apiClient.post(`${root}/upload`, body, {
    headers: { 'Content-Type': undefined }, timeout: 0,
    onUploadProgress: event => { if (event.total) onProgress(Math.round(event.loaded / event.total * 100)); },
  })).data;
};
const browserDownloadPath = (id: string) => `${root}/${encodeURIComponent(id)}/browser-download`;

export async function repositoryDownloadCommand(model: RepositoryModel): Promise<string> {
  const { data } = await apiClient.post<{ ticket: string }>(`${root}/${encodeURIComponent(model.id)}/curl-download`, undefined, { skipErrorToast: true });
  const url = new URL(`${API_BASE_URL.replace(/\/$/, '')}${browserDownloadPath(model.id)}`, window.location.origin).href;
  const filename = model.files.length > 1 ? `model-${model.id}.zip` : model.entry_file.split('/').pop() || 'model';
  const quote = (value: string) => `'${value.replaceAll("'", "'\\''")}'`;
  return `curl --fail --output ${quote(filename)} --cookie ${quote(`repository_download=${data.ticket}`)} ${quote(url)}`;
}

function triggerNativeBrowserDownload(href: string, filename: string) {
  const link = document.createElement('a');
  link.href = href;
  link.download = filename;
  link.style.display = 'none';
  document.body.appendChild(link);
  link.click();
  link.remove();
}

async function authorizeAndStreamDownload(
  ticketPath: string,
  filename: string,
  body?: unknown,
  fallback?: () => Promise<void>,
): Promise<void> {
  try {
    const response = await apiClient.post<{ download_url?: string } | undefined>(
      ticketPath,
      body,
      { skipErrorToast: true, withCredentials: true },
    );
    const targetPath = response.data && typeof response.data === 'object' && response.data.download_url
      ? response.data.download_url
      : ticketPath;
    const href = new URL(`${API_BASE_URL.replace(/\/$/, '')}${targetPath}`, window.location.origin).href;
    triggerNativeBrowserDownload(href, filename);
  } catch (cause) {
    if (fallback && isAxiosError(cause) && (cause.response?.status === 404 || cause.response?.status === 405)) {
      return fallback();
    }
    const detail = isAxiosError(cause) ? cause.response?.data?.detail : undefined;
    if (typeof detail === 'string') throw new Error(detail);
    throw cause;
  }
}

export async function downloadRepositoryModel(model: RepositoryModel): Promise<void> {
  const filename = model.files.length > 1 ? `${model.name}-${model.version}.zip` : model.entry_file.split('/').pop() || 'model';
  return authorizeAndStreamDownload(
    browserDownloadPath(model.id),
    filename,
    undefined,
    () => saveRepositoryDownload(config => apiClient.get(`${root}/${model.id}/download`, config), filename),
  );
}

export async function downloadRepositoryBatch(input: RepositoryBatchDownload): Promise<void> {
  return authorizeAndStreamDownload(
    `${root}/download-batch/browser-download`,
    'models-batch.zip',
    input,
    () => saveRepositoryDownload(config => apiClient.post(`${root}/download-batch`, input, config), 'models-batch.zip'),
  );
}

async function saveRepositoryDownload(
  request: (config: AxiosRequestConfig) => Promise<{ data: Blob }>,
  filename: string,
): Promise<void> {
  const controller = new AbortController();
  let timer: ReturnType<typeof setTimeout> | undefined;
  const resetTimer = () => {
    clearTimeout(timer);
    timer = setTimeout(() => controller.abort(), 5 * 60 * 1000);
  };
  resetTimer();
  let data: Blob;
  try {
    data = (await request({
      responseType: 'blob', timeout: 0, skipErrorToast: true,
      signal: controller.signal, onDownloadProgress: resetTimer,
    })).data;
  } catch (cause) {
    if (controller.signal.aborted) throw Error('下载连续 5 分钟没有进度，请重试。');
    const error = cause as { response?: { data?: Blob } };
    if (error.response?.data instanceof Blob) {
      let detail: string | undefined;
      try { const body = JSON.parse(await error.response.data.text()); detail = body.detail || body.message; } catch { /* Keep the transport error. */ }
      if (detail) throw Error(detail);
    }
    throw cause;
  } finally {
    clearTimeout(timer);
  }
  const url = URL.createObjectURL(data);
  triggerNativeBrowserDownload(url, filename);
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}
