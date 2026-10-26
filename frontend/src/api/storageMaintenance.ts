import apiClient from './client';

export interface CleanupFile {
  path: string;
  size: number;
  modified_at: number;
  reasons: string[];
}

export interface CleanupPreview {
  items: CleanupFile[];
  candidate_count: number;
  candidate_bytes: number;
  scanned_count: number;
  protected_counts: Record<string, number>;
  active_uploads: number;
  active_downloads: number;
  grace_days: number;
  truncated: boolean;
  execution_available: false;
  blocked_reason: string;
  message: string;
}

export async function getCleanupPreview(): Promise<CleanupPreview> {
  return (await apiClient.get('/api/storage-maintenance/preview')).data;
}

// The server rechecks current references and returns 409 until all writers share a maintenance guard.
export async function executeCleanup(paths: string[]): Promise<never> {
  return (await apiClient.post<never>('/api/storage-maintenance/execute', { paths, confirmation: 'QUARANTINE' })).data;
}
