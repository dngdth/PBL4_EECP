import { Workstation, Agent } from './workstation';
import { PolicyConfig, PolicyRules } from './security-policy';
import { ActivityItem } from './activity';

export type SessionStatus =
  | 'CREATED'
  | 'DEPLOYING'
  | 'PREFLIGHT'
  | 'READY'
  | 'DEGRADED'
  | 'RUNNING'
  | 'FINISHED'
  | 'RESTORING'
  | 'NORMAL';

export interface CreateSessionRequest {
  name: string;
  room?: string;
  room_id?: string;
  gateway_id?: string | null;
  agent_ids?: string[];
  workstation_ids?: string[];
  policy_name?: string;
  policy_profile?: string;
}

export interface DeployPolicyRequest {
  profile?: string;
  policy_name?: string;
  /** Legacy flag: toggles applications.deny list termination. NOT Kiosk Mode. */
  strict_mode?: boolean;
  /** Legacy flag: toggles network.block category rules. */
  network_lockdown?: boolean;
  usb_storage_blocked?: boolean;
  allowed_processes?: string[];
  denied_processes?: string[];
  rules?: PolicyRules;
}

export interface ExamSession {
  id: string;
  name: string;
  room: string;
  room_id: string;
  gateway_id: string | null;
  gateway_policy_hash?: string | null;
  status: SessionStatus;
  workstations: Workstation[];
  agents?: Agent[];
  agent_count?: number;
  policy?: PolicyConfig;
  created_at: string;
  updated_at: string;
  activity_log: ActivityItem[];
}
