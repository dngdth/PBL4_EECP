import { apiClient } from '@/src/shared/api/client';

export type OperationalHealth = 'ONLINE' | 'DEGRADED' | 'OFFLINE';

export interface GatewayOperationalState {
  gateway_id: string;
  room_id: string;
  status: OperationalHealth;
  version: string;
  last_seen: string;
  connected_agent_count: number;
  backend_uplink_status: OperationalHealth;
  pending_event_count: number | null;
  oldest_pending_event_age: number | null;
  buffer_status: OperationalHealth | null;
  last_flush_success_at: string | null;
  last_flush_error: string | null;
}

export async function listGateways(): Promise<GatewayOperationalState[]> {
  const result = await apiClient<unknown>('/api/v2/gateways');
  return Array.isArray(result) ? (result as GatewayOperationalState[]) : [];
}
