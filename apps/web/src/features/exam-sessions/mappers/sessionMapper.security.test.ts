import assert from 'node:assert/strict';
import test from 'node:test';

import { normalizeSession } from './sessionMapper';

test('maps security events to INFO, WARNING and ERROR timeline entries', () => {
  const session = normalizeSession({
    id: 'SES-1',
    name: 'Exam',
    room: 'LAB',
    status: 'RUNNING',
    security_events: [
      {
        event_id: 'evt-login',
        session_id: 'SES-1',
        agent_id: 'PC01',
        event_type: 'USER_SESSION_CHANGED',
        severity: 'INFO',
        category: 'WINDOWS_SESSION',
        action: 'LOGON',
        occurred_at: '2026-10-27T03:00:00Z',
        username: 'STUDENT01',
        payload: {},
      },
      {
        event_id: 'evt-process',
        session_id: 'SES-1',
        agent_id: 'PC02',
        event_type: 'ENFORCEMENT_ACTION',
        severity: 'WARNING',
        category: 'FORBIDDEN_PROCESS',
        action: 'BLOCKED',
        occurred_at: '2026-10-27T03:00:01Z',
        process_name: 'zalo.exe',
        pid: 42,
        enforcement_result: 'TERMINATED',
        payload: {},
      },
      {
        event_id: 'evt-recovery',
        session_id: 'SES-1',
        agent_id: 'PC03',
        event_type: 'ENFORCEMENT_TAMPER',
        severity: 'CRITICAL',
        category: 'FIREWALL_INTEGRITY',
        action: 'RECOVERY_FAILED',
        occurred_at: '2026-10-27T03:00:02Z',
        recovery_status: 'RECOVERY_FAILED',
        payload: {},
      },
    ],
  });

  assert.deepEqual(
    session.activity_log.map((item) => [item.id, item.level]),
    [
      ['evt-recovery', 'ERROR'],
      ['evt-process', 'WARNING'],
      ['evt-login', 'INFO'],
    ],
  );
  assert.match(session.activity_log[1].message, /zalo\.exe.*PID 42.*TERMINATED/);
  assert.match(session.activity_log[2].message, /STUDENT01.*LOGON/);
});

test('accepts missing optional security event fields without inventing BLOCKED', () => {
  const session = normalizeSession({
    id: 'SES-1',
    status: 'RUNNING',
    security_events: [
      {
        event_id: 'evt-detected',
        session_id: 'SES-1',
        agent_id: 'PC01',
        event_type: 'FORBIDDEN_PROCESS_DETECTED',
        severity: 'WARNING',
        category: 'FORBIDDEN_PROCESS',
        action: 'DETECTED',
        occurred_at: '2026-10-27T03:00:00Z',
        payload: {},
      },
    ],
  });

  assert.match(session.activity_log[0].message, /DETECTED/);
  assert.doesNotMatch(session.activity_log[0].message, /BLOCKED/);
});
