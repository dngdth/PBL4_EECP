export type CommandType =
  | 'APPLY_POLICY'
  | 'RESTORE_BASELINE'
  | 'DEPLOY_POLICY'
  | 'RUN_PREFLIGHT'
  | 'START_EXAM'
  | 'TERMINATE';

export type CommandStatus =
  | 'PENDING'
  | 'DELIVERED'
  | 'ACKNOWLEDGED'
  | 'FAILED'
  | 'TIMED_OUT'
  | 'DISPATCHED';

export interface CommandItem {
  id: string;
  session_id: string;
  target_id: string;
  type: CommandType;
  payload: Record<string, any>;
  status: CommandStatus;
  created_at: string;
  attempt_count?: number;
  last_attempt_at?: string | null;
  next_retry_at?: string | null;
  expires_at?: string | null;
}
