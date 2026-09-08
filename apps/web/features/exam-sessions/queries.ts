import { fetchApi } from "@/lib/api-client";
import type {
  ApiHealth,
  AvailableAgent,
  ExamSession,
  PolicyProfile,
} from "@/features/exam-sessions/types";

export async function getApiHealth(): Promise<ApiHealth | null> {
  try {
    return await fetchApi<ApiHealth>("/health");
  } catch {
    return null;
  }
}

export async function getSessions(): Promise<ExamSession[] | null> {
  try {
    return await fetchApi<ExamSession[]>("/api/v1/sessions");
  } catch {
    return null;
  }
}

export async function getAvailableAgents(): Promise<AvailableAgent[] | null> {
  try {
    return await fetchApi<AvailableAgent[]>("/api/v1/agents");
  } catch {
    return null;
  }
}

export async function getPolicyProfiles(): Promise<PolicyProfile[] | null> {
  try {
    return await fetchApi<PolicyProfile[]>("/api/v1/policy-profiles");
  } catch {
    return null;
  }
}

