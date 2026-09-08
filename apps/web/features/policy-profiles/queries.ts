import { fetchApi } from "@/lib/api-client";
import type { PolicyProfile } from "@/features/policy-profiles/types";

export async function getPolicyProfiles(): Promise<PolicyProfile[] | null> {
  try {
    return await fetchApi<PolicyProfile[]>("/api/v1/policy-profiles");
  } catch {
    return null;
  }
}
