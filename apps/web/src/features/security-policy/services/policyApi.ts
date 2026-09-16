import { apiClient } from '@/src/shared/api/client';
import {
  PolicyProfile,
  CreatePolicyProfileInput,
  UpdatePolicyProfileInput,
} from '@/src/domain';

export async function listPolicyProfiles(): Promise<PolicyProfile[]> {
  const res = await apiClient<any>('/policy-profiles');
  const items = Array.isArray(res) ? res : res.policy_profiles || [];
  return items.map((item: any) => ({
    id: item.id,
    label: item.label || item.id,
    description: item.description || '',
    rules: item.rules || {},
    is_builtin: Boolean(item.is_builtin),
    yaml: item.yaml,
  }));
}

export async function createPolicyProfile(
  data: CreatePolicyProfileInput
): Promise<PolicyProfile> {
  const payload = {
    id: data.id.trim().toUpperCase(),
    label: data.label.trim(),
    description: data.description.trim(),
    rules: data.rules,
  };
  return apiClient<PolicyProfile>('/policy-profiles', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updatePolicyProfile(
  id: string,
  data: UpdatePolicyProfileInput
): Promise<PolicyProfile> {
  return apiClient<PolicyProfile>(`/policy-profiles/${encodeURIComponent(id)}`, {
    method: 'PUT',
    body: JSON.stringify(data),
  });
}

export async function deletePolicyProfile(id: string): Promise<void> {
  await apiClient<void>(`/policy-profiles/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
}
