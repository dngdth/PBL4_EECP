import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { normalizeSession } from '../mappers/sessionMapper';
import { CompletedSummaryCard } from './CompletedSummaryCard';

function render(restored: boolean, gatewayRestored = false, gatewayId: string | null = null) {
  const session = normalizeSession({
    id: 'SES-1', status: 'FINISHED', name: 'Exam', room: 'LAB',
    gateway_id: gatewayId, gateway_restored: gatewayRestored,
    policy: { profile: 'TEST', rules: {} },
    agents: [{ id: 'PC01', status: 'ONLINE' }],
    workstations: { PC01: { readiness: 'FAILED', restored } },
    updated_at: '2026-11-13T00:00:00Z',
  });
  assert.equal(session.workstations[0].restored, restored);
  return renderToStaticMarkup(
    <CompletedSummaryCard session={session} onRetryRestore={() => {}} />
  );
}

test('online workstation without restore ACK remains incomplete and can retry', () => {
  const html = render(false);
  assert.match(html, /chưa khôi phục hoàn tất/);
  assert.match(html, /Thử lại khôi phục/);
  assert.doesNotMatch(html, /Ca thi đã kết thúc và khôi phục hoàn tất/);
});

test('confirmed restore displays completion and removes retry', () => {
  const html = render(true);
  assert.match(html, /Ca thi đã kết thúc và khôi phục hoàn tất/);
  assert.doesNotMatch(html, /Thử lại khôi phục/);
});

test('pipeline session also requires gateway restore confirmation', () => {
  assert.match(render(true, false, 'GW-A'), /chưa khôi phục hoàn tất/);
  assert.match(render(true, true, 'GW-A'), /Ca thi đã kết thúc và khôi phục hoàn tất/);
});
