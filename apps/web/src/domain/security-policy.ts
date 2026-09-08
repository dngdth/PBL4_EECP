export interface PolicyApplicationsRule {
  allow?: string[];
  deny?: string[];
}

export interface PolicyNetworkRule {
  block?: string[];
  allow_domains?: string[];
  strict_mode?: boolean;
  network_lockdown?: boolean;
}

export interface PolicyDevicesRule {
  usb?: 'allow' | 'deny';
  usb_storage_blocked?: boolean;
}

export interface PolicyRules {
  applications?: PolicyApplicationsRule;
  network?: PolicyNetworkRule;
  devices?: PolicyDevicesRule;
  [key: string]: any;
}

export interface PolicyProfile {
  id: string;
  label: string;
  description: string;
  rules: PolicyRules;
  is_builtin: boolean;
  yaml?: string;
}

export interface CreatePolicyProfileInput {
  id: string;
  label: string;
  description: string;
  rules: PolicyRules;
}

export interface UpdatePolicyProfileInput {
  label?: string;
  description?: string;
  rules?: PolicyRules;
}

export interface PolicyConfig {
  policy_id: string;
  name: string;
  strict_mode: boolean;
  network_lockdown: boolean;
  usb_storage_blocked: boolean;
  allowed_processes: string[];
  deployed_at?: string;
}
