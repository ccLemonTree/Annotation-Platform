import axios from 'axios';

export function connectionError(error: unknown, fallback: string) {
  const detail = axios.isAxiosError(error) ? error.response?.data?.detail : undefined;
  return typeof detail === 'string' && detail.trim() ? detail : fallback;
}

export function forbidden(error: unknown) {
  return axios.isAxiosError(error) && error.response?.status === 403;
}
