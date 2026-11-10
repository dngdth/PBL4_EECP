import assert from 'node:assert/strict';
import test from 'node:test';
import { frameTimeOffset, frameTimingLabel } from './screen-evidence-time';

test('distinguishes historical evidence from a later trigger capture', () => {
  const frame = {
    kind: 'event_context' as const,
    captured_at: '2026-11-10T09:00:00Z',
    event_occurred_at: '2026-11-10T09:00:01.500Z',
  };
  assert.match(frameTimingLabel(frame), /trước sự kiện/);
  assert.equal(frameTimeOffset(frame), '1.5 giây trước sự kiện');
  const delayed = { ...frame, kind: 'trigger' as const, captured_at: '2026-11-10T09:00:04Z' };
  assert.match(frameTimingLabel(delayed), /sau khi Agent nhận/);
  assert.equal(frameTimeOffset(delayed), '2.5 giây sau sự kiện');
});

test('does not invent event timing for snapshots or malformed timestamps', () => {
  assert.equal(frameTimeOffset({ kind: 'snapshot', captured_at: '2026-11-10T09:00:00Z' }), null);
  assert.equal(frameTimeOffset({
    kind: 'trigger', captured_at: 'invalid', event_occurred_at: '2026-11-10T09:00:00Z',
  }), null);
});
