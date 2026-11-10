import React, { useCallback, useEffect, useState } from 'react';
import { apiClient } from '@/src/shared/api/client';

interface Frame {
  frame_id: string;
  evidence_id: string;
  captured_at: string;
  event_occurred_at?: string;
  kind: 'context' | 'trigger' | 'snapshot';
  width: number;
  height: number;
  url: string;
}

interface Evidence {
  id: string;
  agent_id: string;
  event_id?: string;
  status: string;
  reason?: string;
  replay_available: boolean;
  frames: Frame[];
}

interface CaptureRequest {
  id: string;
  status: string;
  response?: { reason?: string; evidence_id?: string };
}

const labels: Record<string, string> = {
  capturing: 'Đang thu ảnh', ready: 'Còn trong RAM client', uploading: 'Đang chuyển ảnh',
  available: 'Đã lưu ảnh', uploaded: 'Đã lưu ảnh', expired: 'Đã hết hạn',
  unavailable: 'Không lấy được ảnh',
};

function EvidenceImage({ frame }: { frame: Frame }) {
  const [url, setUrl] = useState<string>();
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    let objectUrl: string | undefined;
    setUrl(undefined);
    setError('');
    const token = sessionStorage.getItem('eecp_access_token');
    fetch(frame.url, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
      cache: 'no-store', signal: controller.signal,
    }).then(async response => {
      if (!response.ok) throw new Error(`Không tải được ảnh (${response.status})`);
      const blob = await response.blob();
      if (!controller.signal.aborted) {
        objectUrl = URL.createObjectURL(blob);
        setUrl(objectUrl);
      }
    }).catch(error => {
      if (!controller.signal.aborted) setError(error.message);
    });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [frame.url]);
  return <div className="space-y-2">
    <p className="text-xs text-text-muted">
      Chụp lúc {new Date(frame.captured_at).toLocaleString('vi-VN')} · {frame.width} × {frame.height}
      {' · '}{frame.kind === 'trigger' ? 'Ảnh khi phát hiện sự kiện' :
        frame.kind === 'snapshot' ? 'Ảnh chụp theo yêu cầu' : 'Ảnh ngữ cảnh'}
      {frame.event_occurred_at && <> · Sự kiện lúc {
        new Date(frame.event_occurred_at).toLocaleString('vi-VN')
      }</>}
    </p>
    {error && <p role="alert" className="text-error text-xs">{error}</p>}
    {url ? <a href={url} target="_blank" rel="noreferrer" aria-label="Mở ảnh kích thước đầy đủ">
      <img src={url} alt={`Màn hình được chụp lúc ${frame.captured_at}`}
        className="max-h-96 max-w-full mx-auto rounded border border-border" />
    </a> : !error && <p className="text-xs text-text-muted">Đang tải ảnh…</p>}
  </div>;
}

export function ScreenEvidencePanel({
  sessionId, agentId, agentIds = [], evidenceId, allowCapture = true,
}: {
  sessionId: string;
  agentId?: string;
  agentIds?: string[];
  evidenceId?: string;
  allowCapture?: boolean;
}) {
  const [agent, setAgent] = useState(agentId || '');
  const [items, setItems] = useState<Evidence[]>([]);
  const [selectedId, setSelectedId] = useState(evidenceId || '');
  const [frameIndex, setFrameIndex] = useState(0);
  const [pending, setPending] = useState<CaptureRequest>();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  useEffect(() => {
    setAgent(agentId || ''); setSelectedId(evidenceId || ''); setItems([]);
    setPending(undefined); setFrameIndex(0); setError('');
  }, [agentId, evidenceId, sessionId]);
  const refresh = useCallback(async () => {
    const values = await apiClient<Evidence[]>(
      `/api/v1/sessions/${encodeURIComponent(sessionId)}/screen/evidence`,
      { params: { agent_id: agent || undefined } },
    );
    setItems(evidenceId ? values.filter(value => value.id === evidenceId) : values);
  }, [sessionId, agent, evidenceId]);
  useEffect(() => {
    let active = true;
    const update = async () => {
      try { if (active) await refresh(); }
      catch (error: any) { if (active) setError(error.message); }
    };
    update();
    const timer = window.setInterval(update, 5000);
    return () => { active = false; window.clearInterval(timer); };
  }, [refresh]);
  useEffect(() => {
    if (!pending || !['capturing', 'uploading'].includes(pending.status)) return;
    let active = true;
    const timer = window.setInterval(async () => {
      try {
        const value = await apiClient<CaptureRequest>(`/api/v1/screen/requests/${pending.id}`);
        if (active) {
          setPending(value);
          if (value.response?.evidence_id) setSelectedId(value.response.evidence_id);
          await refresh();
        }
      } catch (error: any) { if (active) setError(error.message); }
    }, 1500);
    return () => { active = false; window.clearInterval(timer); };
  }, [pending?.id, pending?.status, refresh]);
  const selected = items.find(value => value.id === selectedId) || items[0];
  const frame = selected?.frames[Math.min(frameIndex, Math.max(0, selected.frames.length - 1))];
  const capture = async (mode: 'SNAPSHOT' | 'REPLAY') => {
    const target = mode === 'REPLAY' ? selected?.agent_id : agent;
    if (!target) return;
    setBusy(true); setError(''); setFrameIndex(0);
    try {
      const value = await apiClient<CaptureRequest>(
        `/api/v1/sessions/${encodeURIComponent(sessionId)}/agents/${encodeURIComponent(target)}/screen/requests`,
        { method: 'POST', body: JSON.stringify({
          mode, ...(mode === 'REPLAY' ? { evidence_id: selected?.id } : {}),
        }) },
      );
      setPending(value);
      if (value.response?.evidence_id) setSelectedId(value.response.evidence_id);
    } catch (error: any) { setError(error.message); }
    finally { setBusy(false); }
  };
  const waiting = busy || Boolean(pending && ['capturing', 'uploading'].includes(pending.status));
  return <section className="border border-border rounded p-3 space-y-3 bg-surface">
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h3 className="font-semibold text-sm">Ảnh màn hình và bằng chứng</h3>
      <button type="button" className="text-xs text-primary underline"
        onClick={() => refresh().catch(error => setError(error.message))}>Làm mới</button>
    </div>
    <p className="text-xs text-text-muted">Ảnh được chụp tại thời điểm ghi bên dưới. Không phải video trực tiếp.</p>
    {!agentId && agentIds.length > 0 && <label className="text-xs block">
      Máy cần chụp{' '}
      <select value={agent} className="border border-border rounded p-1 bg-surface"
        onChange={event => { setAgent(event.target.value); setSelectedId(''); setFrameIndex(0); }}>
        <option value="">Tất cả máy</option>
        {agentIds.map(id => <option key={id} value={id}>{id}</option>)}
      </select>
    </label>}
    <div className="flex flex-wrap gap-2 text-xs">
      {allowCapture && <button type="button" disabled={!agent || waiting}
        className="border border-border rounded px-2 py-1 disabled:opacity-40"
        onClick={() => capture('SNAPSHOT')}>Yêu cầu ảnh hiện tại{agent && ` · ${agent}`}</button>}
      {allowCapture && <button type="button" disabled={!selected?.replay_available || waiting}
        className="border border-border rounded px-2 py-1 disabled:opacity-40"
        onClick={() => capture('REPLAY')}>Lấy ảnh trước / sau sự kiện</button>}
    </div>
    {pending && <p role="status" className="text-xs">{
      labels[pending.status] || pending.status
    }{pending.response?.reason && ` · ${pending.response.reason}`}</p>}
    {error && <p role="alert" className="text-error text-xs">{error}</p>}
    {items.length > 0 ? <>
      <label className="text-xs block">Bằng chứng{' '}
        <select value={selected?.id || ''} className="border border-border rounded p-1 bg-surface max-w-full"
          onChange={event => { setSelectedId(event.target.value); setFrameIndex(0); }}>
          {items.map(item => <option key={item.id} value={item.id}>
            {item.agent_id} · {item.event_id || 'Theo yêu cầu'} · {labels[item.status] || item.status}
          </option>)}
        </select>
      </label>
      {selected?.reason && <p className="text-xs text-warning-dark">{selected.reason}</p>}
      {selected && <p className="text-xs text-text-muted font-mono break-all">{selected.id}</p>}
      {frame ? <>
        <EvidenceImage frame={frame} />
        {selected.frames.length > 1 && <label className="block text-xs">
          Ảnh {Math.min(frameIndex + 1, selected.frames.length)} / {selected.frames.length}
          <input type="range" min={0} max={selected.frames.length - 1}
            value={Math.min(frameIndex, selected.frames.length - 1)} className="w-full"
            aria-label="Chọn ảnh trước hoặc sau sự kiện"
            onChange={event => setFrameIndex(Number(event.target.value))} />
        </label>}
      </> : <p className="text-xs text-text-muted">{labels[selected?.status || 'unavailable']}</p>}
    </> : <p className="text-xs text-text-muted">Chưa có ảnh bằng chứng được ghi nhận.</p>}
  </section>;
}
