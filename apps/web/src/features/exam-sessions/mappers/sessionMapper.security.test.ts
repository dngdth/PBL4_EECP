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

test('handles missing, null, or corrupted timestamps without throwing localeCompare TypeError', () => {
  const session = normalizeSession({
    id: 'SES-NULL-TIMESTAMP',
    status: 'RUNNING',
    activity_log: [
      { id: 'act-1', message: 'No timestamp', level: 'INFO' },
      { id: 'act-2', timestamp: null, message: 'Null timestamp', level: 'INFO' },
      { id: 'act-3', timestamp: '2026-10-27T03:00:00Z', message: 'Valid timestamp', level: 'INFO' },
    ],
    security_events: [
      {
        event_id: 'evt-no-occurred-at',
        occurred_at: null,
        severity: 'INFO',
        action: 'LOGON',
      },
      null, // corrupted null event
    ],
    violations: [
      { workstation_id: 'PC01', occurred_at: null, destination: null },
      null, // corrupted null violation
    ],
  });

  assert.equal(session.id, 'SES-NULL-TIMESTAMP');
  assert.ok(Array.isArray(session.activity_log));
  assert.equal(session.activity_log[0].id, 'act-3');
});

test('handles corrupted checks and non-array allowed_processes safely without crash', () => {
  const session = normalizeSession({
    id: 'SES-CORRUPT-CHECKS',
    status: 'DEPLOYING',
    policy: {
      applications: {
        allow: null,
      },
    },
    agents: [
      {
        id: 'PC01',
        presence_health: 'ONLINE',
      },
      null, // corrupted agent element
    ],
    workstations: {
      PC01: {
        readiness: 'READY',
        preflight_checks: [null, { name: 'os_lockdown', passed: true }, { details: null }],
      },
    },
  });

  assert.equal(session.workstations.length, 1);
  assert.equal(session.workstations[0].preflight_details?.os_lockdown, true);
  assert.ok(Array.isArray(session.policy?.allowed_processes));
  assert.deepEqual(session.policy?.allowed_processes, ['exam-browser.exe']);
});
