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
function mapAgentToWorkstation(agent: any, sessionWorkstation?: any): Workstation {
  const isOnline = agent.status === 'ONLINE';
  const isFailed = agent.policy_status === 'FAILED';

  const status: Workstation['status'] = isFailed ? 'FAILED' : isOnline ? 'READY' : 'PENDING';
  const readiness = sessionWorkstation?.readiness;
  const preflight_status: Workstation['preflight_status'] = isFailed || readiness === 'FAILED'
    ? 'FAILED'
    : readiness === 'READY'
      ? 'PASSED'
      : readiness === 'WARNING'
        ? 'WARNING'
        : 'PENDING';
  const checks = Array.isArray(sessionWorkstation?.preflight_checks)
    ? sessionWorkstation.preflight_checks
    : [];
  const checkPassed = (...names: string[]) => {
    const match = checks.find((check: any) => names.includes(check.name));
    return match ? Boolean(match.passed) : false;
  };
  const preflight_details = checks.length
    ? {
        os_lockdown: checkPassed('os_lockdown', 'exam_app', 'kiosk_mode'),
        network_firewall: checkPassed('network_firewall', 'dns_policy', 'policy_compliant'),
        agent_health: checkPassed('agent_health', 'agent_online'),
        peripheral_check: checkPassed('peripheral_check', 'display_peripherals'),
        notes: checks
          .filter((check: any) => check.details)
          .map((check: any) => check.details)
          .join('; ') || undefined,
      }
    : undefined;

  return {
    id: agent.id,
    ip: agent.ip_address || 'unknown',
    status,
    preflight_status,
    preflight_details,
    last_heartbeat: agent.last_seen || '',
    agent_version: agent.agent_version || 'unknown',
    gateway_id: agent.gateway_id || null,
    service_health: agent.service_health || null,
    active_policy_hash: agent.active_policy_hash || null,
    actual_policy_hash: sessionWorkstation?.actual_policy_hash || null,
    desired_policy_hash: sessionWorkstation?.desired_policy_hash || null,
    policy_compliant: Boolean(sessionWorkstation?.policy_compliant),
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
      ip: 'unknown',
      status: readiness === 'READY' ? 'READY' : readiness === 'WARNING' ? 'WARNING' : 'FAILED',
      preflight_status: checks.length ? (passedAll ? 'PASSED' : 'FAILED') : 'PENDING',
      last_heartbeat: '',
      agent_version: 'unknown',
      active_policy_hash: null,
      actual_policy_hash: val?.actual_policy_hash || null,
      desired_policy_hash: val?.desired_policy_hash || null,
      policy_compliant: Boolean(val?.policy_compliant),
    };
  });
}

/**
 * Maps raw policy dictionary to structured PolicyConfig.
 */
function mapPolicy(rawPolicy: any): PolicyConfig | undefined {
  if (!rawPolicy || typeof rawPolicy !== 'object') return undefined;

  const rules = rawPolicy.rules || {};
  const network = rules.network || {};
  const devices = rules.devices || {};
  const applications = rules.applications || {};

  const blockedCategories = network.block || network.blocked_categories || [];
  const networkLockdown =
    network.network_lockdown ??
    rawPolicy.network_lockdown ??
    (Array.isArray(blockedCategories) && blockedCategories.length > 0);

  const usbBlocked =
    rules.usb_storage_blocked ??
    devices.usb_storage_blocked ??
    (devices.usb ? devices.usb === 'deny' : devices.usb_storage === 'deny') ??
    true;

  const deniedProcesses = Array.isArray(applications.deny) ? applications.deny : [];
  // strict_mode is active only if denylist is non-empty
  const strictMode = deniedProcesses.length > 0;

  return {
    policy_id: rawPolicy.policy_hash || rawPolicy.policy_id || 'pol-default',
    name: rawPolicy.profile || rawPolicy.name || 'Chính sách tiêu chuẩn',
    strict_mode: strictMode,
    network_lockdown: networkLockdown,
    usb_storage_blocked: usbBlocked,
    allowed_processes: applications.allow || rawPolicy.allowed_processes || ['exam-browser.exe'],
    denied_processes: deniedProcesses,
    deployed_at: rawPolicy.deployed_at,
  };
}

/**
 * Extracts and synthesizes timeline activity logs from violations and lifecycle timestamps.
 */
function mapActivityLog(raw: any): ActivityItem[] {
  const logs: ActivityItem[] = Array.isArray(raw.activity_log)
    ? [...raw.activity_log]
    : [];

  if (Array.isArray(raw.security_events)) {
    raw.security_events.forEach((event: any) => {
      const actor = event.username ? ` — ${event.username}` : '';
      const process = event.process_name
        ? ` — ${event.process_name}${event.pid ? ` (PID ${event.pid})` : ''}`
        : '';
      const result = event.enforcement_result || event.recovery_status || event.action;
      logs.push({
        id: event.event_id,
        timestamp: event.occurred_at || '',
        level:
          event.severity === 'CRITICAL'
            ? 'ERROR'
            : event.severity === 'WARNING'
              ? 'WARNING'
              : 'INFO',
        message: `${event.agent_id}${actor}${process} — ${event.category} — ${result}`,
        source: event.source || 'AGENT_SERVICE',
        details: { payload: event.payload },
      });
    });
  }

  // 1. Violations from Backend PolicyViolationView
  if (Array.isArray(raw.violations)) {
    raw.violations.forEach((v: any, idx: number) => {
      const isObserved = v.action === 'OBSERVED';
      logs.push({
        id: `act-viol-${idx}`,
        timestamp: v.occurred_at || '',
        level: 'WARNING',
        message: isObserved
          ? `Audit DNS trên máy ${v.workstation_id} quan sát thấy tên miền bị cấm${v.destination ? `: ${v.destination}` : ''}`
          : `Máy ${v.workstation_id} bị chặn truy cập${v.destination ? `: ${v.destination}` : ''}`,
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

  return logs.sort((left, right) => right.timestamp.localeCompare(left.timestamp));
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
    workstations = raw.agents.map((agent: any) =>
      mapAgentToWorkstation(agent, raw.workstations?.[agent.id])
    );
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
    gateway_policy_hash: raw.gateway_policy_hash ?? null,
    status,
    workstations,
    agents: Array.isArray(raw.agents) ? raw.agents : undefined,
    agent_count: raw.agent_count ?? (Array.isArray(raw.agents) ? raw.agents.length : workstations.length),
    policy: mapPolicy(raw.policy),
    activity_log: mapActivityLog(raw),
    security_events: Array.isArray(raw.security_events) ? raw.security_events : [],
    created_at: raw.created_at || '',
    updated_at: raw.updated_at || '',
  };
}
