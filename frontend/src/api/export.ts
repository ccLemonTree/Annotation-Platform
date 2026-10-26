import apiClient from './client';

export type Split = 'train' | 'val' | 'test';
export type SplitSource = Partial<Record<'parent_image_id' | 'group_id' | 'sequence_id' | 'device_id', string>>;
export interface SplitPolicy {
  phash_distance: number;
  color_distance: number;
  training_budget_per_class: number | null;
  require_source: boolean;
  require_independent: boolean;
}
export interface SplitReport {
  splits: Record<string, Split>;
  sources: Record<string, SplitSource>;
  groups: string[][];
  missing_source_asset_ids: string[];
  policy: SplitPolicy;
  images_by_split: Record<string, number>;
  images_by_label_by_split: Record<string, Record<string, number>>;
  warnings: string[];
  errors: { code: string; message: string; asset_ids?: string[] }[];
  can_freeze: boolean;
}
export interface SplitRequest {
  labels: string[];
  approved_only: boolean;
  policy: SplitPolicy;
  splits?: Record<string, Split>;
  suggest?: boolean;
}
export const previewSplits = async (projectId: string, request: SplitRequest): Promise<SplitReport> =>
  (await apiClient.post(`/api/export/projects/${projectId}/splits/preview`, request, { timeout: 300000 })).data;

export const getSplitSources = async (projectId: string): Promise<Record<string, SplitSource>> =>
  (await apiClient.get(`/api/export/projects/${projectId}/split-sources`)).data;

export const updateSplitSource = async (projectId: string, assetId: string, source: Record<keyof SplitSource, string | null>) =>
  (await apiClient.patch(`/api/export/projects/${projectId}/assets/${assetId}/split-source`, source)).data;
