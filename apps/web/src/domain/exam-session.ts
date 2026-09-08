import { Workstation } from './workstation';
import { PolicyConfig } from './security-policy';
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
  room_id: string;
  gateway_id: string;
  workstation_ids: string[];
  policy_name?: string;
}

export interface DeployPolicyRequest {
  policy_name?: string;
  strict_mode?: boolean;
  network_lockdown?: boolean;
  usb_storage_blocked?: boolean;
  allowed_processes?: string[];
}

export interface ExamSession {
  id: string;
  name: string;
  room_id: string;
  gateway_id: string;
  status: SessionStatus;
  workstations: Workstation[];
  policy?: PolicyConfig;
  created_at: string;
  updated_at: string;
  activity_log: ActivityItem[];
}
