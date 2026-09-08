"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import {
  createSession,
  updateSessionStatus,
} from "@/features/exam-sessions/mutations";
import { ApiError } from "@/lib/api-client";

export type CreateSessionActionState = { error: string | null };
export type TransitionSessionActionState = { error: string | null };

const connectionError = "Không thể kết nối đến máy chủ EECP.";

export async function createSessionAction(
  _previous: CreateSessionActionState,
  formData: FormData,
): Promise<CreateSessionActionState> {
  const name = formData.get("name")?.toString().trim() ?? "";
  const room = formData.get("room")?.toString().trim() ?? "";
  const agentIds = formData
    .getAll("agent_ids")
    .filter((value): value is string => typeof value === "string")
    .map((value) => value.trim())
    .filter(Boolean);
  const policyProfile =
    formData.get("policy_profile")?.toString().trim() ?? "";

  if (!name || !room || !policyProfile || agentIds.length === 0) {
    return {
      error: "Vui lòng nhập đủ thông tin, chọn policy và ít nhất một máy trạm.",
    };
  }

  try {
    await createSession({
      name,
      room,
      agent_ids: agentIds,
      policy_profile: policyProfile,
    });
  } catch (error) {
    return {
      error: error instanceof ApiError ? error.detail : connectionError,
    };
  }

  revalidatePath("/sessions");
  redirect("/sessions");
}

export async function transitionSessionAction(
  sessionId: string,
  target: "READY" | "RUNNING" | "FINISHED",
  _previous: TransitionSessionActionState,
  _formData: FormData,
): Promise<TransitionSessionActionState> {
  try {
    await updateSessionStatus(sessionId, target);
  } catch (error) {
    return {
      error: error instanceof ApiError ? error.detail : connectionError,
    };
  }

  revalidatePath("/sessions");
  return { error: null };
}
