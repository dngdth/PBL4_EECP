import React, { useCallback, useEffect, useState } from 'react';
import { AlertCircle, Network, RefreshCw, Server } from 'lucide-react';
import { listGateways, GatewayOperationalState } from '@/src/features/gateways/services/gatewayApi';
import { Button } from '@/src/shared/ui/button';
import { Card } from '@/src/shared/ui/card';
import { formatDateTime } from '@/src/shared/lib/formatters';

const value = (item: unknown) => item === null || item === undefined || item === '' ? 'Unavailable' : String(item);
const age = (seconds: number | null) => seconds === null ? 'Unavailable' : `${Math.round(seconds)}s`;
const bufferLabel = (status: GatewayOperationalState['buffer_status']) =>
  status === 'ONLINE' ? 'NORMAL' : status || 'Unavailable';

export const GatewaysPage: React.FC = () => {
  const [gateways, setGateways] = useState<GatewayOperationalState[]>([]);
  const [selected, setSelected] = useState<GatewayOperationalState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try { setGateways(await listGateways()); }
    catch (reason: any) { setError(reason.message || 'Backend unavailable'); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);

  return (
    <div className="p-4 sm:p-6 lg:p-8 space-y-5 max-w-[1600px] mx-auto">
      <header className="flex justify-between items-start gap-4">
        <div><h1 className="text-2xl font-bold flex gap-2 items-center"><Server className="w-6 h-6" />Local Gateways</h1><p className="text-sm text-text-muted">Operational facts reported through the Backend.</p></div>
        <Button variant="secondary" onClick={() => void refresh()} isLoading={loading} leftIcon={<RefreshCw className="w-4 h-4" />}>Refresh</Button>
      </header>
      {error && <div className="border border-error/30 bg-error-soft text-error p-3 flex gap-2"><AlertCircle className="w-4 h-4" />{error}</div>}
      {!loading && !error && gateways.length === 0 && <Card><p className="text-text-muted">No Gateway state is available.</p></Card>}
      <div className="grid lg:grid-cols-2 gap-4">
        {gateways.map((gateway) => <Card key={gateway.gateway_id} variant="interactive" onClick={() => setSelected(gateway)}>
          <div className="flex justify-between"><div><h2 className="font-bold">{gateway.gateway_id}</h2><p className="text-xs text-text-muted">Room {gateway.room_id} / v{gateway.version}</p></div><span className="font-mono text-xs">{gateway.status}</span></div>
          <dl className="grid grid-cols-2 gap-3 mt-4 text-sm">
            <div><dt className="text-text-muted">Backend uplink</dt><dd>{gateway.backend_uplink_status}</dd></div>
            <div><dt className="text-text-muted">Connected Agents</dt><dd>{value(gateway.connected_agent_count)}</dd></div>
            <div><dt className="text-text-muted">Pending events</dt><dd>{value(gateway.pending_event_count)}</dd></div>
            <div><dt className="text-text-muted">Buffer</dt><dd>{bufferLabel(gateway.buffer_status)}</dd></div>
            <div><dt className="text-text-muted">Oldest pending</dt><dd>{age(gateway.oldest_pending_event_age)}</dd></div>
            <div><dt className="text-text-muted">Last seen</dt><dd>{gateway.last_seen ? formatDateTime(gateway.last_seen) : 'Unavailable'}</dd></div>
          </dl>
        </Card>)}
      </div>
      {selected && <div className="fixed inset-0 bg-black/40 grid place-items-center p-4 z-50" onClick={() => setSelected(null)}><Card className="w-full max-w-xl" onClick={(event) => event.stopPropagation()}>
        <div className="flex justify-between"><h2 className="font-bold flex gap-2"><Network className="w-5 h-5" />{selected.gateway_id}</h2><Button size="xs" variant="secondary" onClick={() => setSelected(null)}>Close</Button></div>
        <dl className="grid sm:grid-cols-2 gap-3 mt-4 text-sm">
          <div><dt className="text-text-muted">Room</dt><dd>{selected.room_id}</dd></div><div><dt className="text-text-muted">Version</dt><dd>{selected.version}</dd></div>
          <div><dt className="text-text-muted">Health</dt><dd>{selected.status}</dd></div><div><dt className="text-text-muted">Uplink</dt><dd>{selected.backend_uplink_status}</dd></div>
          <div><dt className="text-text-muted">Connected Agents</dt><dd>{value(selected.connected_agent_count)}</dd></div><div><dt className="text-text-muted">Pending</dt><dd>{value(selected.pending_event_count)}</dd></div>
          <div><dt className="text-text-muted">Oldest event</dt><dd>{age(selected.oldest_pending_event_age)}</dd></div><div><dt className="text-text-muted">Buffer state</dt><dd>{bufferLabel(selected.buffer_status)}</dd></div>
          <div><dt className="text-text-muted">Last flush</dt><dd>{selected.last_flush_success_at ? formatDateTime(selected.last_flush_success_at) : 'Unavailable'}</dd></div>
          <div className="sm:col-span-2"><dt className="text-text-muted">Last flush error</dt><dd className="break-words">{value(selected.last_flush_error)}</dd></div>
        </dl>
      </Card></div>}
    </div>
  );
};
