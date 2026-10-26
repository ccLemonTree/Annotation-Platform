import client from '../api/client';

export interface PlatformOption {
  platform_id: string;
  platform_name: string;
}

export interface DetectionTargetOption {
  target_id: string;
  target_name: string;
}

export interface DetectionQueryParams {
  platforms?: string[];
  data_types?: string[];
  detection_targets?: string[];
  equipment?: string;
  start_time?: string;
  end_time?: string;
  page?: number;
  page_size?: number;
}

export interface DetectionItem {
  platform: string;
  data_type: string;
  detection_target: string;
  device_id: string;
  detection_time: string;
  total_count: number;
}

export interface DetectionQueryResponse {
  total: number;
  page: number;
  page_size: number;
  items: DetectionItem[];
}

export const detectionApi = {
  // 获取平台列表
  getPlatforms: async (): Promise<PlatformOption[]> => {
    const response = await client.get('/detections/platforms');
    return response.data;
  },

  // 获取检测目标列表
  getDetectionTargets: async (): Promise<DetectionTargetOption[]> => {
    const response = await client.get('/detections/detection-targets');
    return response.data;
  },

  // 查询检测结果
  queryDetections: async (params: DetectionQueryParams): Promise<DetectionQueryResponse> => {
    const response = await client.post('/detections/query', params);
    return response.data;
  }
};
