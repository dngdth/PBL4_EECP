import type { Agent } from "@/features/workstations/types";
import { fetchApi } from "@/lib/api-client";

export async function getAgents(): Promise<Agent[] | null> {
  try {
    return await fetchApi<Agent[]>("/api/v1/agents");
  } catch {
    return null;
  }
}
