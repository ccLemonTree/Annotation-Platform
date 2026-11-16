import apiClient from './client';
import type { User } from './auth';
import type { PaginatedResponse } from './projects';

export const getUsers = async (page:number):Promise<PaginatedResponse<User>> => (await apiClient.get('/api/users',{params:{page,page_size:100}})).data;
