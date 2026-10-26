import apiClient, { API_BASE_URL } from './client';

export type WorkActivity = 'annotation' | 'review';
export interface ActivityHeartbeat {
  project_id: string; asset_id: string; session_id: string; sequence: number; activity: WorkActivity;
  started_at: string; ended_at: string;
}
export interface ActivityReceipt { duplicate: boolean; accepted_seconds: number }
export interface EfficiencyUser {
  user_id: number; username: string; active_seconds: number | null;
  annotation_seconds: number | null; review_seconds: number | null;
  annotation_events: number; review_events: number;
  timed_annotation_events: number; timed_review_events: number;
  annotation_per_hour: number | null; review_per_hour: number | null;
}
export interface Analytics {
  quality: {
    total: number; approved: number; rejected: number; pending: number; negative_samples: number;
    sources: { manual: number; model: number; unknown: number };
    review_origins: { human: number; automatic: number; unknown: number };
    process_pass_rate: number | null;
    categories: { label: string; boxes: number; assets: number }[];
  };
  workload: {
    human_annotation_events: number; automatic_annotation_events: number;
    human_review_events: number; automatic_review_events: number; rework_events: number; unknown_events: number;
  };
  efficiency: {
    timing_status: 'collected' | 'not_collected'; active_seconds: number | null;
    annotation_seconds: number | null; review_seconds: number | null;
    timed_annotation_events: number; timed_review_events: number;
    annotation_per_hour: number | null; review_per_hour: number | null; users: EfficiencyUser[];
  };
  definitions: { quality: string; workload: string; timing: string; efficiency: string };
}
export interface AnalyticsFilters { project_id?: string; start?: string; end?: string }

export async function getAnalytics(params: AnalyticsFilters, signal?: AbortSignal): Promise<Analytics> {
  return (await apiClient.get('/api/analytics', { params, signal })).data;
}

export async function postActivity(data: ActivityHeartbeat): Promise<ActivityReceipt> {
  // keepalive also delivers the final short interval on navigation/pagehide.
  const response = await fetch(`${API_BASE_URL}/api/analytics/activity`, {
    method: 'POST', keepalive: true,
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${localStorage.getItem('token') || ''}` },
    body: JSON.stringify(data),
  });
  if (!response.ok) throw new Error('活跃计时未保存');
  return response.json();
}
