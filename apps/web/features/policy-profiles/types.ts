export type PolicyRules = {
  applications?: { allow?: string[]; deny?: string[] };
  network?: { block?: string[] };
  devices?: { usb?: "allow" | "deny" };
};

export type PolicyProfile = {
  id: string;
  label: string;
  description: string;
  rules: PolicyRules;
  is_builtin: boolean;
  yaml: string;
};

export type PolicyProfilePayload = {
  label: string;
  description: string;
  rules: PolicyRules;
};

export type CreatePolicyProfilePayload = PolicyProfilePayload & { id: string };
