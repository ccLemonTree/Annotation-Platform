// 认证相关API
import apiClient from './client';

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
  email: string;
}

// 登录
export const login = async (data: LoginRequest): Promise<LoginResponse> => {
  const formData = new URLSearchParams({ username: data.username, password: data.password });

  const response = await apiClient.post('/api/auth/login', formData, {
    headers: {
      'Content-Type': 'application/x-www-form-urlencoded',
    },
  });
  return response.data;
};

// 注册
export const register = async (data: RegisterRequest): Promise<User> => {
  const response = await apiClient.post('/api/auth/register', data);
  return response.data;
};

// 获取当前用户信息
export const getCurrentUser = async (): Promise<User> => {
  const response = await apiClient.get('/api/auth/me');
  return response.data;
};

// 登出（前端清除token）
export const logout = () => {
  localStorage.removeItem('token');
  localStorage.removeItem('user');
  window.location.href = '/login';
};
