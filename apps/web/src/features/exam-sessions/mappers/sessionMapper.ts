import {
  ExamSession,
  Workstation,
  ActivityItem,
  PolicyConfig,
  SessionStatus,
} from '@/src/domain';

/**
 * Maps an AssignedAgentView from Backend management API into a Workstation entity for UI.
 */
function mapAgentToWorkstation(agent: any): Workstation {
  const isOnline = agent.status === 'ONLINE';
  const isApplied = agent.policy_status === 'APPLIED' || agent.policy_status === 'RESTORED';
  const isFailed = agent.policy_status === 'FAILED';

  const status: Workstation['status'] = isFailed ? 'FAILED' : isOnline ? 'READY' : 'PENDING';
  const preflight_status: Workstation['preflight_status'] = isFailed
    ? 'FAILED'
    : isApplied
    ? 'PASSED'
    : isOnline
    ? 'PASSED'
    : 'PENDING';

  return {
    id: agent.id,
    ip: agent.ip_address || '127.0.0.1',
    status,
    preflight_status,
    preflight_details: {
      os_lockdown: isApplied,
      network_firewall: isApplied,
      agent_health: isOnline,
      peripheral_check: true,
      notes: agent.policy_status ? `Chính sách: ${agent.policy_status}` : undefined,
    },
    last_heartbeat: agent.last_seen || new Date().toISOString(),
    agent_version: agent.agent_version || 'v1.0.0',
  };
}

/**
 * Maps legacy workstation dictionary format from pipeline API.
 */
function mapLegacyWorkstations(workstationsDict: Record<string, any>): Workstation[] {
  return Object.entries(workstationsDict).map(([wsId, val]) => {
    const readiness = val?.readiness || 'PENDING';
    const checks = val?.preflight_checks || [];
    const passedAll = checks.length > 0 ? checks.every((c: any) => c.passed) : readiness === 'READY';

    return {
      id: wsId,
      ip: '127.0.0.1',
      status: readiness === 'READY' ? 'READY' : readiness === 'WARNING' ? 'WARNING' : 'FAILED',
      preflight_status: passedAll ? 'PASSED' : 'FAILED',
      last_heartbeat: new Date().toISOString(),
      agent_version: 'v1.0.0',
    };
  });
}

/**
 * Maps raw policy dictionary to structured PolicyConfig.
 */
function mapPolicy(rawPolicy: any): PolicyConfig | undefined {
  if (!rawPolicy || typeof rawPolicy !== 'object') return undefined;

  const rules = rawPolicy.rules || {};
  return {
    policy_id: rawPolicy.policy_hash || rawPolicy.policy_id || 'pol-default',
    name: rawPolicy.profile || rawPolicy.name || 'Chính sách tiêu chuẩn',
    strict_mode: rules.network?.strict_mode ?? rawPolicy.strict_mode ?? true,
    network_lockdown: rules.network?.network_lockdown ?? rawPolicy.network_lockdown ?? true,
    usb_storage_blocked: rules.usb_storage_blocked ?? (rules.devices?.usb === 'deny') ?? true,
    allowed_processes: rules.applications?.allow || rawPolicy.allowed_processes || ['exam-browser.exe'],
    deployed_at: rawPolicy.deployed_at,
  };
}

/**
 * Extracts and synthesizes timeline activity logs from violations and lifecycle timestamps.
 */
function mapActivityLog(raw: any): ActivityItem[] {
  if (Array.isArray(raw.activity_log) && raw.activity_log.length > 0) {
    return raw.activity_log;
  }

  const logs: ActivityItem[] = [];

  // 1. Violations from Backend PolicyViolationView
  if (Array.isArray(raw.violations)) {
    raw.violations.forEach((v: any, idx: number) => {
      logs.push({
        id: `act-viol-${idx}`,
        timestamp: v.occurred_at || new Date().toISOString(),
        level: 'WARNING',
        message: `Máy ${v.workstation_id} vi phạm: ${v.category}${v.destination ? ` -> ${v.destination}` : ''}`,
        source: 'AGENT',
      });
    });
  }

  // 2. Lifecycle milestones
  if (raw.finished_at) {
    logs.push({
      id: `act-finish-${raw.id}`,
      timestamp: raw.finished_at,
      level: 'SUCCESS',
      message: 'Ca thi đã kết thúc.',
      source: 'TEACHER',
    });
  }

  if (raw.started_at) {
    logs.push({
      id: `act-start-${raw.id}`,
      timestamp: raw.started_at,
      level: 'SUCCESS',
      message: 'Ca thi đã bắt đầu chính thức.',
      source: 'TEACHER',
    });
  }

  if (raw.created_at) {
    logs.push({
      id: `act-init-${raw.id}`,
      timestamp: raw.created_at,
      level: 'INFO',
      message: `Hệ thống đã khởi tạo ca thi [${raw.name || raw.exam_name || raw.id}].`,
      source: 'BACKEND',
    });
  }

  return logs;
}

/**
 * Maps and normalizes raw SessionDetailView/SessionView DTOs from Backend FastAPI
 * into clean Frontend Domain/View entities.
 */
export function normalizeSession(raw: any): ExamSession {
  if (!raw) return raw;

  // Extract workstations: prefer raw.agents (Management API), then raw.workstations
  let workstations: Workstation[] = [];
  if (Array.isArray(raw.agents) && raw.agents.length > 0) {
    workstations = raw.agents.map(mapAgentToWorkstation);
  } else if (Array.isArray(raw.workstations)) {
    workstations = raw.workstations;
  } else if (raw.workstations && typeof raw.workstations === 'object') {
    workstations = mapLegacyWorkstations(raw.workstations);
  }

  const rawStatus = (raw.status || raw.state || 'CREATED').toUpperCase();
  const status: SessionStatus = rawStatus === 'COMPLETED' ? 'FINISHED' : (rawStatus as SessionStatus);

  return {
    id: raw.id,
    name: raw.name || raw.exam_name || 'Ca thi không tên',
    room: raw.room || raw.room_id || 'Chưa chỉ định',
    room_id: raw.room || raw.room_id || 'Chưa chỉ định',
    gateway_id: raw.gateway_id ?? null,
    status,
    workstations,
    agents: Array.isArray(raw.agents) ? raw.agents : undefined,
    agent_count: raw.agent_count ?? (Array.isArray(raw.agents) ? raw.agents.length : workstations.length),
    policy: mapPolicy(raw.policy),
    activity_log: mapActivityLog(raw),
    created_at: raw.created_at || new Date().toISOString(),
    updated_at: raw.updated_at || new Date().toISOString(),
  };
}
