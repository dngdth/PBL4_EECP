export type Agent = {
  id: string;
  hostname: string;
  ip_address: string;
  status: "ONLINE" | "OFFLINE";
  agent_version: string;
  last_seen: string;
  created_at: string;
};
