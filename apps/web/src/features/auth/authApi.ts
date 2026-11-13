import { apiClient } from '@/src/shared/api/client';

export type UserRole = 'ADMIN' | 'EXAMINER';

export interface AuthUser {
  id: string | null;
  username: string;
  full_name: string;
  role: UserRole;
  session_scope: string[];
}

export interface UserAccount {
  id: string;
  username: string;
  full_name: string;
  role: UserRole;
  is_active: boolean;
  created_at: string;
  updated_at: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
}

export interface AuthMeResponse {
  authenticated: boolean;
  auth_enforced: boolean;
  user: AuthUser | null;
}

export interface CreateUserPayload {
  username: string;
  password: string;
  full_name: string;
  role?: UserRole;
}

export interface UpdateUserPayload {
  full_name?: string;
  password?: string;
  is_active?: boolean;
  role?: UserRole;
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  return apiClient<LoginResponse>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
}

export async function fetchMe(): Promise<AuthMeResponse> {
  return apiClient<AuthMeResponse>('/auth/me');
}

export async function refreshToken(): Promise<LoginResponse> {
  return apiClient<LoginResponse>('/auth/refresh', { method: 'POST' });
}

export async function logoutApi(): Promise<void> {
  await apiClient<{ status: string }>('/auth/logout', { method: 'POST' });
}

export async function listUsers(params?: {
  role?: UserRole;
  active_only?: boolean;
}): Promise<UserAccount[]> {
  return apiClient<UserAccount[]>('/users', { params });
}

export async function createUser(payload: CreateUserPayload): Promise<UserAccount> {
  return apiClient<UserAccount>('/users', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateUser(
  userId: string,
  payload: UpdateUserPayload,
): Promise<UserAccount> {
  return apiClient<UserAccount>(`/users/${userId}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function assignSessionExaminers(
  sessionId: string,
  examinerIds: string[],
): Promise<any> {
  return apiClient<any>(`/sessions/${sessionId}/examiners`, {
    method: 'PUT',
    body: JSON.stringify({ examiner_ids: examinerIds }),
  });
}

