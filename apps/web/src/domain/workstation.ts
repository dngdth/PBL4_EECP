export type WorkstationStatus = 'READY' | 'WARNING' | 'FAILED' | 'PENDING';

export type PreflightStatus = 'PASSED' | 'WARNING' | 'FAILED' | 'PENDING';

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
}

export type AgentOnlineStatus = 'ONLINE' | 'OFFLINE';

export interface Agent {
  id: string;
  hostname?: string | null;
  ip_address?: string | null;
  status?: AgentOnlineStatus | null;
  last_seen?: string | null;
  agent_version?: string | null;
}
