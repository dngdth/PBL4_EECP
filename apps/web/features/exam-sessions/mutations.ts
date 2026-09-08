import { fetchApi } from "@/lib/api-client";
import type { CreateSessionPayload, ExamSession } from "@/features/exam-sessions/types";

export function createSession(payload: CreateSessionPayload): Promise<ExamSession> {
  return fetchApi<ExamSession>("/api/v1/sessions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export function updateSessionStatus(
  sessionId: string,
  status: "READY" | "RUNNING" | "FINISHED",
): Promise<ExamSession> {
  return fetchApi<ExamSession>(`/api/v1/sessions/${sessionId}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
}
