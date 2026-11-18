// API客户端配置
import axios, { AxiosError } from 'axios';
import { appMessage } from '../ui/appFeedback';
import { redirectToLogin } from '../utils/authNavigation';

declare module 'axios' {
  interface AxiosRequestConfig {
    skipErrorToast?: boolean;
  }
}

// API基础URL
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

const UNAUTHORIZED_TOAST_COOLDOWN_MS = 3000;
let lastUnauthorizedToastAt = 0;

// 创建axios实例
const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
});

// 请求拦截器 - 添加Token
apiClient.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem('token');
    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    return config;
  },
  (error) => Promise.reject(error),
);

// 响应拦截器 - 统一错误处理
apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError<{ detail?: unknown; message?: string }>) => {
    if (axios.isCancel(error)) return Promise.reject(error);

    // 401 未授权 - 防抖提示并跳转到登录页
    if (error.response?.status === 401 && !/\/api\/auth\/login(?:\?|$)/.test(error.config?.url || '')) {
      const now = Date.now();
      if (!error.config?.skipErrorToast && now - lastUnauthorizedToastAt > UNAUTHORIZED_TOAST_COOLDOWN_MS) {
        lastUnauthorizedToastAt = now;
        appMessage.error('登录已过期，请重新登录');
      }
      redirectToLogin();
      return Promise.reject(error);
    }

    // 其他错误 - 显示错误消息
    const detail = error.response?.data?.detail;
    const errorMessage = (typeof detail === 'string' ? detail : error.response?.data?.message) || error.message || '请求失败';
    if (!error.config?.skipErrorToast) appMessage.error(errorMessage);

    return Promise.reject(error);
  },
);

export default apiClient;
