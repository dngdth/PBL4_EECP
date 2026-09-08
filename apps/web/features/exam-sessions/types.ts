export type ApiHealth = {
  status: "ok";
};

export type PipelineStage = {
  state: string;
  label: string;
  description: string;
};

export type SessionStatus =
  | "CREATED"
  | "DEPLOYING"
  | "PREFLIGHT"
  | "READY"
  | "DEGRADED"
  | "RUNNING"
  | "RESTORING"
  | "NORMAL"
  | "FINISHED";

export type AssignedAgent = {
  id: string;
  hostname: string | null;
  ip_address: string | null;
  status: "ONLINE" | "OFFLINE" | null;
  last_seen: string | null;
  assigned_at: string | null;
  policy_status: "NOT_ASSIGNED" | "PENDING" | "APPLIED" | "FAILED" | "RESTORED";
};

export type AvailableAgent = {
  id: string;
  hostname: string;
  ip_address: string;
  status: "ONLINE" | "OFFLINE";
  agent_version: string;
  last_seen: string;
  created_at: string;
};

export type PolicyRules = {
  applications?: { allow?: string[]; deny?: string[] };
  network?: { block?: string[] };
  devices?: { usb?: "allow" | "deny" };
};

export type PolicyDocument = {
  profile: string;
  rules: PolicyRules;
  version: number;
  policy_hash: string;
};

export type PolicyProfile = {
  id: string;
  label: string;
  description: string;
  rules: PolicyRules;
  is_builtin: boolean;
  yaml: string;
};

export type ExamSession = {
  id: string;
  name: string;
  room: string;
  status: SessionStatus;
  gateway_id: string | null;
  created_at: string;
  updated_at: string;
  agent_count: number;
  agents: AssignedAgent[];
  policy: PolicyDocument | null;
  violations?: {
    workstation_id: string;
    destination: string | null;
    category: string;
    occurred_at: string;
  }[];
};

export type CreateSessionPayload = {
  name: string;
  room: string;
  agent_ids: string[];
  policy_profile: string;
};

