"use server";

import { revalidatePath } from "next/cache";
import {
  createPolicyProfile,
  deletePolicyProfile,
  updatePolicyProfile,
} from "@/features/policy-profiles/mutations";
import type { PolicyProfilePayload } from "@/features/policy-profiles/types";
import { ApiError } from "@/lib/api-client";

export type PolicyProfileActionState = {
  error: string | null;
  success: string | null;
};

const initialError = "Không thể kết nối đến máy chủ EECP.";

export async function createPolicyProfileAction(
  _previous: PolicyProfileActionState,
  formData: FormData,
): Promise<PolicyProfileActionState> {
  const id = value(formData, "id");
  const payload = profilePayload(formData);
  if (!id || !payload.label || !payload.description) {
    return { error: "Vui lòng nhập ID, tên và mô tả policy.", success: null };
  }

  try {
    await createPolicyProfile({ id, ...payload });
  } catch (error) {
    return failure(error);
  }

  refreshPolicyPages();
  return { error: null, success: `Đã tạo policy ${id.toUpperCase()}.` };
}

export async function updatePolicyProfileAction(
  profileId: string,
  _previous: PolicyProfileActionState,
  formData: FormData,
): Promise<PolicyProfileActionState> {
  const payload = profilePayload(formData);
  if (!payload.label || !payload.description) {
    return { error: "Tên và mô tả policy không được để trống.", success: null };
  }

  try {
    await updatePolicyProfile(profileId, payload);
  } catch (error) {
    return failure(error);
  }

  refreshPolicyPages();
  return { error: null, success: `Đã cập nhật ${profileId}.` };
}

export async function deletePolicyProfileAction(
  profileId: string,
  _previous: PolicyProfileActionState,
  _formData: FormData,
): Promise<PolicyProfileActionState> {
  try {
    await deletePolicyProfile(profileId);
  } catch (error) {
    return failure(error);
  }

  refreshPolicyPages();
  return { error: null, success: `Đã xóa ${profileId}.` };
}

function profilePayload(formData: FormData): PolicyProfilePayload {
  return {
    label: value(formData, "label"),
    description: value(formData, "description"),
    rules: {
      applications: {
        allow: list(formData, "applications_allow"),
        deny: list(formData, "applications_deny"),
      },
      network: { block: list(formData, "network_block") },
      devices: { usb: value(formData, "usb") === "allow" ? "allow" : "deny" },
    },
  };
}

function value(formData: FormData, name: string): string {
  return formData.get(name)?.toString().trim() ?? "";
}

function list(formData: FormData, name: string): string[] {
  return value(formData, name)
    .split(/[\n,]+/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function failure(error: unknown): PolicyProfileActionState {
  return {
    error: error instanceof ApiError ? error.detail : initialError,
    success: null,
  };
}

function refreshPolicyPages(): void {
  revalidatePath("/policies");
  revalidatePath("/sessions/create");
}
