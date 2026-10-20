export type NetworkCategory = 'generative_ai' | 'social_network' | 'vpn_proxy';
export type UsbAccess = 'allow' | 'deny';

export interface PolicyApplicationsRule {
  allow?: string[];
  deny?: string[];
}

export interface PolicyNetworkRule {
  block?: NetworkCategory[] | string[];
  blocked_categories?: NetworkCategory[] | string[];
  allow_domains?: string[];
  blocked_domains?: string[];
  blocked_ips?: string[];
  blocked_cidrs?: string[];
}

export interface PolicyDevicesRule {
  usb?: UsbAccess;
  usb_storage?: UsbAccess;
}

export interface PolicyRules {
  applications?: PolicyApplicationsRule;
  network?: PolicyNetworkRule;
  devices?: PolicyDevicesRule;
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
  /** Legacy flag: indicates whether prohibited application termination (denylist) is active. NOT Kiosk Mode. */
  strict_mode: boolean;
  /** Legacy flag: indicates whether web category domain blocking is active. */
  network_lockdown: boolean;
  usb_storage_blocked: boolean;
  allowed_processes: string[];
  denied_processes?: string[];
  deployed_at?: string;
}
