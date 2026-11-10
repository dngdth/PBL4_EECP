export interface FrameTiming {
  kind: 'context' | 'event_context' | 'trigger' | 'snapshot';
  captured_at: string;
  event_occurred_at?: string;
}

export function frameTimingLabel(frame: FrameTiming): string {
  switch (frame.kind) {
    case 'event_context': return 'Ảnh trong bộ đệm trước sự kiện';
    case 'trigger': return 'Ảnh chụp sau khi Agent nhận sự kiện';
    case 'snapshot': return 'Ảnh chụp theo yêu cầu';
    default: return 'Ảnh ngữ cảnh';
  }
}

export function frameTimeOffset(frame: FrameTiming): string | null {
  if (!frame.event_occurred_at) return null;
  const delta = Date.parse(frame.captured_at) - Date.parse(frame.event_occurred_at);
  if (!Number.isFinite(delta)) return null;
  if (delta === 0) return 'Cùng mốc thời gian sự kiện';
  return `${(Math.abs(delta) / 1000).toFixed(1)} giây ${delta < 0 ? 'trước' : 'sau'} sự kiện`;
}
