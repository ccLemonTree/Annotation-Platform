// 认证相关API
import apiClient from './client';
import { redirectToLogin } from '../utils/authNavigation';

export interface LoginRequest {
  username: string;
  password: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

export interface User {
  id: number;
  username: string;
  email?: string;
  full_name?: string;
  role: string;
  tenant_id: number;
  is_active: boolean;
  created_at: string;
  last_login_at?: string;
}

export interface RegisterRequest {
  username: string;
  password: string;
  email?: string;
}

export interface PublicAuthConfig {
  allow_public_register: boolean;
}

// 读取公开认证配置（失败默认关闭公开注册）
export const getPublicAuthConfig = async (): Promise<PublicAuthConfig> => {
  try {
    const response = await apiClient.get<PublicAuthConfig>('/api/auth/public-config', { skipErrorToast: true });
    return { allow_public_register: response.data?.allow_public_register === true };
  } catch {
    return { allow_public_register: false };
  }
};

// 登录
export const login = async (data: LoginRequest): Promise<LoginResponse> => {
  const formData = new URLSearchParams({ username: data.username, password: data.password });

  const response = await apiClient.post<LoginResponse>('/api/auth/login', formData, {
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    skipErrorToast: true,
  });
  return response.data;
};

// 注册
export const register = async (data: RegisterRequest): Promise<User> => {
  const email = data.email?.trim();
  const payload: RegisterRequest = {
    username: data.username.trim(),
    password: data.password,
    ...(email ? { email } : {}),
  };
  const response = await apiClient.post<User>('/api/auth/register', payload, { skipErrorToast: true });
  return response.data;
};

// 获取当前用户信息
export const getCurrentUser = async (): Promise<User> => {
  const response = await apiClient.get<User>('/api/auth/me');
  return response.data;
};

// 登出（前端清除token）
export const logout = () => {
  redirectToLogin();
};
