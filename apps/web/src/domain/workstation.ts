export type WorkstationStatus =
  | 'READY'
  | 'WARNING'
  | 'FAILED'
  | 'PENDING'
  | 'ONLINE'
  | 'DEGRADED'
  | 'OFFLINE'
  | 'UNKNOWN';

export type PreflightStatus = 'PASSED' | 'WARNING' | 'FAILED' | 'PENDING' | 'UNKNOWN';

export interface LatestIncident {
  id: string;
  session_id: string;
  category: string;
  severity: string;
  status: string;
  created_at: string;
}

export interface PreflightDetails {
  os_lockdown: boolean;
  network_firewall: boolean;
  agent_health: boolean;
  peripheral_check: boolean;
  notes?: string;
}

export interface Workstation {
  id: string;
  ip: string;
  status: WorkstationStatus;
  preflight_status: PreflightStatus;
  preflight_details?: PreflightDetails;
  last_heartbeat: string;
  agent_version: string;
  gateway_id?: string | null;
  service_health?: 'HEALTHY' | 'DEGRADED' | 'UNAVAILABLE' | null;
  active_policy_hash?: string | null;
  latest_incident?: LatestIncident | null;
}

export type AgentOnlineStatus = 'ONLINE' | 'DEGRADED' | 'OFFLINE';

export interface Agent {
  id: string;
  hostname?: string | null;
  ip_address?: string | null;
  status?: AgentOnlineStatus | null;
  last_seen?: string | null;
  agent_version?: string | null;
  presence_health?: AgentOnlineStatus | null;
  service_health?: 'HEALTHY' | 'DEGRADED' | 'UNAVAILABLE' | null;
  active_policy_hash?: string | null;
  gateway_id?: string | null;
  latest_incident?: LatestIncident | null;
}
