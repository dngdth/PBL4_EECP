import { apiClient } from '@/src/shared/api/client';
import {
  ExamSession,
  CreateSessionRequest,
  DeployPolicyRequest,
  SessionStatus,
} from '@/src/domain';
import { normalizeSession } from '../mappers/sessionMapper';

export { normalizeSession };

export async function getSession(sessionId: string): Promise<ExamSession> {
  const raw = await apiClient<any>(`/sessions/${sessionId}`);
  return normalizeSession(raw);
}

export async function listSessions(): Promise<{ total: number; sessions: ExamSession[] }> {
  const res = await apiClient<any>('/sessions');
  const items = Array.isArray(res) ? res : res.sessions || [];
  const normalized = items.map(normalizeSession);
  return {
    total: normalized.length,
    sessions: normalized,
  };
}

export async function createSession(data: CreateSessionRequest): Promise<ExamSession> {
  const payload = {
    exam_name: data.name,
    room_id: data.room_id,
    gateway_id: data.gateway_id || 'gw-f301',
    workstation_ids: data.workstation_ids,
    actor: 'teacher',
  };
  const raw = await apiClient<any>('/sessions', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
  return normalizeSession(raw);
}

export async function updateSessionStatus(
  sessionId: string,
  newStatus: SessionStatus
): Promise<{ message: string; session: ExamSession }> {
  const payload = {
    status: newStatus,
    actor: 'teacher',
  };

  const raw = await apiClient<any>(`/sessions/${sessionId}/status`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });

  return {
    message: `Đã cập nhật trạng thái ca thi sang [${newStatus}].`,
    session: normalizeSession(raw),
  };
}

export async function deployPolicy(
  sessionId: string,
  data: DeployPolicyRequest
): Promise<{ message: string; session: ExamSession; command_id: string }> {
  const payload = {
    profile: data.policy_name || 'STANDARD_EXAM_POLICY',
    rules: {
      applications: {
        allow: data.allowed_processes || ['exam-browser.exe'],
        deny: [],
      },
      network: {
        strict_mode: data.strict_mode ?? true,
        network_lockdown: data.network_lockdown ?? true,
      },
      usb_storage_blocked: data.usb_storage_blocked ?? true,
    },
    actor: 'teacher',
  };

  const raw = await apiClient<any>(`/sessions/${sessionId}/policy/deploy`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

  const session = normalizeSession(raw);
  return {
    message: 'Đã triển khai chính sách thành công.',
    session,
    command_id: `cmd-deploy-${Date.now()}`,
  };
}

export async function forceStartSession(
  sessionId: string,
  reason?: string
): Promise<{ message: string; session: ExamSession }> {
  const payload = {
    actor: 'teacher',
    force: true,
    reason: reason || 'Giám thị kích hoạt bắt đầu thi cưỡng chế',
  };

  const raw = await apiClient<any>(`/sessions/${sessionId}/start`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

  return {
    message: 'Đã bắt đầu ca thi.',
    session: normalizeSession(raw),
  };
}

export async function startSession(
  sessionId: string
): Promise<{ message: string; session: ExamSession }> {
  const payload = {
    actor: 'teacher',
    force: false,
  };

  const raw = await apiClient<any>(`/sessions/${sessionId}/start`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

  return {
    message: 'Đã bắt đầu ca thi thành công.',
    session: normalizeSession(raw),
  };
}

export async function finishSession(
  sessionId: string
): Promise<{ message: string; session: ExamSession }> {
  const payload = {
    actor: 'teacher',
  };

  const raw = await apiClient<any>(`/sessions/${sessionId}/finish`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });

  return {
    message: 'Đã kết thúc ca thi thành công.',
    session: normalizeSession(raw),
  };
}
