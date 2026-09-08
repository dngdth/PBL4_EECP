import {
  ExamSession,
  Workstation,
  ActivityItem,
  PolicyConfig,
  SessionStatus,
} from '@/src/domain';

/**
 * Maps and normalizes raw SessionDetailView/SessionView DTOs from Backend FastAPI
 * into clean Frontend Domain/View entities.
 */
export function normalizeSession(raw: any): ExamSession {
  if (!raw) return raw;

  // 1. Extract workstations array from backend dictionary / map
  let workstations: Workstation[] = [];
  if (Array.isArray(raw.workstations)) {
    workstations = raw.workstations;
  } else if (raw.workstations && typeof raw.workstations === 'object') {
    workstations = Object.entries(raw.workstations).map(([wsId, val]: [string, any]) => {
      const agent = (raw.agents || []).find((a: any) => a.id === wsId);
      const readiness = val?.readiness || 'PENDING';
      let status: Workstation['status'] = 'PENDING';
      let preflight_status: Workstation['preflight_status'] = 'PENDING';

      if (readiness === 'READY') {
        status = 'READY';
        preflight_status = 'PASSED';
      } else if (readiness === 'WARNING') {
        status = 'WARNING';
        preflight_status = 'WARNING';
      } else if (readiness === 'FAILED') {
        status = 'FAILED';
        preflight_status = 'FAILED';
      }

      const checks = val?.preflight_checks || [];
      const preflight_details =
        checks.length > 0
          ? {
              os_lockdown: checks.some((c: any) => c.name?.toLowerCase().includes('os') && c.passed),
              network_firewall: checks.some(
                (c: any) => c.name?.toLowerCase().includes('firewall') && c.passed
              ),
              agent_health: checks.some(
                (c: any) => c.name?.toLowerCase().includes('agent') && c.passed
              ),
              peripheral_check: checks.some(
                (c: any) =>
                  (c.name?.toLowerCase().includes('peripheral') ||
                    c.name?.toLowerCase().includes('device')) &&
                  c.passed
              ),
              notes: checks
                .map(
                  (c: any) =>
                    `${c.name}: ${c.passed ? 'PASSED' : 'FAILED'}${c.details ? ` (${c.details})` : ''}`
                )
                .join('; '),
            }
          : undefined;

      return {
        id: wsId,
        ip: agent?.ip_address || '127.0.0.1',
        status,
        preflight_status,
        preflight_details,
        last_heartbeat:
          agent?.last_seen || raw.updated_at || raw.created_at || new Date().toISOString(),
        agent_version: agent?.agent_version || 'v1.0.0',
      };
    });
  }

  // 2. Extract policy details
  let policy: PolicyConfig | undefined = undefined;
  if (raw.policy && typeof raw.policy === 'object') {
    policy = {
      policy_id: raw.policy.policy_hash || raw.policy.policy_id || 'pol-default',
      name: raw.policy.profile || raw.policy.name || 'Chính sách tiêu chuẩn',
      strict_mode: raw.policy.rules?.network?.strict_mode ?? raw.policy.strict_mode ?? true,
      network_lockdown:
        raw.policy.rules?.network?.network_lockdown ?? raw.policy.network_lockdown ?? true,
      usb_storage_blocked:
        raw.policy.rules?.usb_storage_blocked ?? raw.policy.usb_storage_blocked ?? true,
      allowed_processes:
        raw.policy.rules?.applications?.allow ||
        raw.policy.allowed_processes || ['exam-browser.exe'],
      deployed_at: raw.policy.deployed_at || raw.updated_at,
    };
  }

  // 3. Extract activity logs & incidents
  const activity_log: ActivityItem[] = Array.isArray(raw.activity_log) ? raw.activity_log : [];

  if (activity_log.length === 0) {
    if (raw.created_at) {
      activity_log.push({
        id: `act-init-${raw.id}`,
        timestamp: raw.created_at,
        level: 'INFO',
        message: `Hệ thống đã khởi tạo ca thi [${raw.name || raw.exam_name || raw.id}].`,
        source: 'BACKEND',
      });
    }
    if (raw.started_at) {
      activity_log.unshift({
        id: `act-start-${raw.id}`,
        timestamp: raw.started_at,
        level: 'SUCCESS',
        message: `Ca thi đã bắt đầu chính thức.`,
        source: 'TEACHER',
      });
    }
    if (raw.finished_at) {
      activity_log.unshift({
        id: `act-finish-${raw.id}`,
        timestamp: raw.finished_at,
        level: 'SUCCESS',
        message: `Ca thi đã kết thúc.`,
        source: 'TEACHER',
      });
    }
    if (Array.isArray(raw.violations)) {
      raw.violations.forEach((v: any, idx: number) => {
        activity_log.unshift({
          id: `act-viol-${idx}`,
          timestamp: v.occurred_at || new Date().toISOString(),
          level: 'WARNING',
          message: `Máy ${v.workstation_id} vi phạm chính sách: ${v.category} -> ${v.destination || 'N/A'}`,
          source: 'AGENT',
        });
      });
    }
  }

  const rawStatus = (raw.status || raw.state || 'CREATED').toUpperCase() as SessionStatus;

  return {
    id: raw.id,
    name: raw.name || raw.exam_name || 'Ca thi không tên',
    room_id: raw.room || raw.room_id || 'Chưa chỉ định',
    gateway_id: raw.gateway_id || 'gw-default',
    status: rawStatus === ('COMPLETED' as any) ? 'FINISHED' : rawStatus,
    workstations,
    policy,
    created_at: raw.created_at || new Date().toISOString(),
    updated_at: raw.updated_at || new Date().toISOString(),
    activity_log,
  };
}
