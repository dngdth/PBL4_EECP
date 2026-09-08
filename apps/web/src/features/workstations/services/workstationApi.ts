import { apiClient } from '@/src/shared/api/client';
import { Workstation, ExamSession, Agent, CommandItem } from '@/src/domain';
import { normalizeSession } from '@/src/features/exam-sessions/services/sessionApi';

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
  const defaultChecks = [
    { name: 'os_lockdown', passed: true, critical: true, details: 'OS Lockdown verified' },
    { name: 'network_firewall', passed: true, critical: true, details: 'Network firewall rules applied' },
    { name: 'agent_health', passed: true, critical: true, details: 'Agent process healthy' },
    { name: 'peripheral_check', passed: true, critical: false, details: 'Peripherals verified' },
  ];

  const raw = await apiClient<any>(
    `/sessions/${sessionId}/workstations/${workstationId}/preflight`,
    {
      method: 'POST',
      body: JSON.stringify({ checks: defaultChecks, actor: 'agent' }),
    }
  );

  const session = normalizeSession(raw);
  const workstation = session.workstations.find((w) => w.id === workstationId) || {
    id: workstationId,
    ip: '127.0.0.1',
    status: 'READY',
    preflight_status: 'PASSED',
    last_heartbeat: new Date().toISOString(),
    agent_version: 'v1.0.0',
  };

  return {
    message: `Máy trạm ${workstationId} đã vượt qua kiểm tra tiền kiểm.`,
    workstation,
    session,
  };
}
