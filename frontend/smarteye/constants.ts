import type { DetectionRecord } from './types';

// In development, we use proxy (configured in vite.config.ts)
// In production (served by Flask), relative path works
export const API_BASE_URL = '/api/smarteye';
export const getBackendBaseUrl = () => window.location.origin;

export const MOCK_DATA: DetectionRecord[] = [];
