import apiClient from './client';

export interface GpuResource {
  index: number;
  uuid: string;
  name: string;
  driver_version: string;
  memory_total_mib: number | null;
  memory_used_mib: number | null;
  memory_free_mib: number | null;
  utilization_percent: number | null;
  temperature_c: number | null;
  power_draw_w: number | null;
  power_limit_w: number | null;
  mig_mode: string;
}

export interface GpuProcess {
  gpu_uuid: string;
  pid: number;
  name: string;
  memory_used_mib: number | null;
}

export interface GpuResources {
  server_name: string;
  collected_at: string;
  cached: boolean;
  processes_complete: boolean;
  warnings: string[];
  gpus: GpuResource[];
  processes: GpuProcess[];
}

export async function getGpuResources(signal?: AbortSignal, serverId = 'default'): Promise<GpuResources> {
  const url = serverId === 'default' ? '/api/gpu-resources' : `/api/gpu-resources/servers/${encodeURIComponent(serverId)}/resources`;
  return (await apiClient.get(url, { timeout: 45000, signal })).data;
}

export interface GpuServer {
  id: string;
  name: string;
  host: string | null;
  port: number | null;
  username: string | null;
  enabled: boolean;
  has_password: boolean;
  revision: number;
  source: 'ssh_manager' | 'password';
  created_at?: string;
}

export interface GpuServerInput {
  name: string;
  host: string;
  port: number;
  username: string;
  password?: string;
  enabled: boolean;
  clear_password?: boolean;
}

export async function getGpuServers(signal?: AbortSignal): Promise<GpuServer[]> {
  return (await apiClient.get('/api/gpu-resources/servers', { signal })).data;
}

export async function createGpuServer(input: GpuServerInput): Promise<GpuServer> {
  return (await apiClient.post('/api/gpu-resources/servers', input)).data;
}

export async function updateGpuServer(id: string, input: Partial<GpuServerInput>): Promise<GpuServer> {
  return (await apiClient.patch(`/api/gpu-resources/servers/${encodeURIComponent(id)}`, input)).data;
}

export async function deleteGpuServer(id: string): Promise<void> {
  await apiClient.delete(`/api/gpu-resources/servers/${encodeURIComponent(id)}`);
}

export async function testGpuServer(input: Omit<GpuServerInput, 'enabled' | 'clear_password'>, signal?: AbortSignal): Promise<GpuResources> {
  return (await apiClient.post('/api/gpu-resources/servers/test', input, { timeout: 45000, signal })).data;
}

export type GpuReleaseTarget = { pid: number; gpu_uuid: string } | { task_kind: 'training' | 'validation'; task_id: string };
export interface GpuReleaseTask { kind: 'training' | 'validation'; id: string; name: string; status: string; gpu_uuids: string[] }
export interface GpuReleasePreview {
  token: string; server_name: string;
  target: { kind: 'process' | 'training' | 'validation'; name: string; pid?: number; gpu_uuids: string[]; container?: { name: string } | null };
}
const releaseUrl = (serverId: string) => `/api/gpu-resources/servers/${encodeURIComponent(serverId)}/release`;
export async function getGpuReleaseTasks(serverId: string, signal?: AbortSignal): Promise<GpuReleaseTask[]> {
  return (await apiClient.get(releaseUrl(serverId) + '/tasks', { signal })).data;
}
export async function previewGpuRelease(serverId: string, target: GpuReleaseTarget): Promise<GpuReleasePreview> {
  return (await apiClient.post(releaseUrl(serverId) + '/preview', target, { timeout: 360000 })).data;
}
export async function confirmGpuRelease(serverId: string, token: string, force: boolean): Promise<{ stopped: boolean; message: string }> {
  return (await apiClient.post(releaseUrl(serverId) + '/confirm', { token, force }, { timeout: 360000 })).data;
}
