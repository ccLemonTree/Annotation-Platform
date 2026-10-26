// API客户端配置
import axios, { AxiosError } from 'axios';
import { message } from 'antd';

declare module 'axios' {
  interface AxiosRequestConfig { skipErrorToast?: boolean; }
}

// API基础URL
export const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '';

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
  (error) => {
    return Promise.reject(error);
  }
);

// 响应拦截器 - 统一错误处理
apiClient.interceptors.response.use(
  (response) => {
    return response;
  },
  (error: AxiosError<any>) => {
    if (axios.isCancel(error)) return Promise.reject(error);
    // 401 未授权 - 跳转到登录页
    if (error.response?.status === 401) {
      localStorage.removeItem('token');
      localStorage.removeItem('user');
      message.error('登录已过期，请重新登录');
      window.location.href = '/login';
      return Promise.reject(error);
    }

    // 其他错误 - 显示错误消息
    const detail = error.response?.data?.detail;
    const errorMessage = (typeof detail === 'string' ? detail : error.response?.data?.message) || error.message || '请求失败';
    if (!error.config?.skipErrorToast) message.error(errorMessage);

    return Promise.reject(error);
  }
);

export default apiClient;
