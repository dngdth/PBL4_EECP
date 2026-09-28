import { apiClient } from '@/src/shared/api/client';
import { Workstation, ExamSession, Agent, CommandItem } from '@/src/domain';
import { normalizeSession } from '@/src/features/exam-sessions';

export async function listAgents(): Promise<Agent[]> {
  const res = await apiClient<any>('/agents');
  const items = Array.isArray(res) ? res : res.agents || [];
  return items.map((a: any) => ({
    id: a.id,
    hostname: a.hostname || null,
    ip_address: a.ip_address || null,
    status: (a.status || 'OFFLINE') as Agent['status'],
    last_seen: a.last_seen || null,
    agent_version: a.agent_version || null,
    presence_health: a.presence_health || null,
    service_health: a.service_health || null,
    active_policy_hash: a.active_policy_hash || null,
    gateway_id: a.gateway_id || null,
    latest_incident: a.latest_incident || null,
  }));
}

export async function listTargetCommands(targetId: string): Promise<CommandItem[]> {
  const res = await apiClient<any>(`/agents/${encodeURIComponent(targetId)}/commands`);
  const items = Array.isArray(res) ? res : res.commands || [];
  return items.map((c: any) => ({
    id: c.id,
    session_id: c.session_id,
    target_id: c.target_id,
    type: c.type,
    payload: c.payload || {},
    status: c.status,
    created_at: c.created_at,
    attempt_count: c.attempt_count ?? 0,
    last_attempt_at: c.last_attempt_at || null,
    next_retry_at: c.next_retry_at || null,
    expires_at: c.expires_at || null,
  }));
}

export async function retryWorkstationPreflight(
  sessionId: string,
  workstationId: string
): Promise<{ message: string; workstation: Workstation; session: ExamSession }> {
  // The Dashboard cannot attest endpoint checks. The Agent must submit them.
  const raw = await apiClient<any>(`/sessions/${sessionId}`);

  const session = normalizeSession(raw);
  const workstation = session.workstations.find((w) => w.id === workstationId);
  if (!workstation) throw new Error(`Backend did not return workstation ${workstationId}`);

  return {
    message: `Đã làm mới trạng thái tiền kiểm thực tế của ${workstationId}.`,
    workstation,
    session,
  };
}
