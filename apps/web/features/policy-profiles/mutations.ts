import { fetchApi } from "@/lib/api-client";
import type {
  CreatePolicyProfilePayload,
  PolicyProfile,
  PolicyProfilePayload,
} from "@/features/policy-profiles/types";

export function createPolicyProfile(
  payload: CreatePolicyProfilePayload,
): Promise<PolicyProfile> {
  return fetchApi<PolicyProfile>("/api/v1/policy-profiles", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function updatePolicyProfile(
  profileId: string,
  payload: PolicyProfilePayload,
): Promise<PolicyProfile> {
  return fetchApi<PolicyProfile>(`/api/v1/policy-profiles/${profileId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function deletePolicyProfile(profileId: string): Promise<void> {
  return fetchApi<void>(`/api/v1/policy-profiles/${profileId}`, {
    method: "DELETE",
  });
}
