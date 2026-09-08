import { SessionStatus } from '@/src/domain';
import { SESSION_STATUS_LABELS } from '@/src/shared/config/labels';

export interface SessionStatusConfig {
  stepIndex: number;
  label: string;
  badgeClass: string;
  dotClass: string;
  isTerminal?: boolean;
}

export const SESSION_STATUS_CONFIG: Record<SessionStatus, SessionStatusConfig> = {
  CREATED: {
    stepIndex: 0,
    label: SESSION_STATUS_LABELS.CREATED,
    badgeClass: 'bg-surface-subtle text-text-muted border-border-subtle font-medium',
    dotClass: 'bg-text-subtle',
  },
  DEPLOYING: {
    stepIndex: 1,
    label: SESSION_STATUS_LABELS.DEPLOYING,
    badgeClass: 'bg-primary-soft text-primary border-primary/30 font-bold',
    dotClass: 'bg-primary animate-pulse',
  },
  PREFLIGHT: {
    stepIndex: 2,
    label: SESSION_STATUS_LABELS.PREFLIGHT,
    badgeClass: 'bg-warning-soft text-warning-dark border-warning/30 font-bold',
    dotClass: 'bg-warning animate-pulse',
  },
  READY: {
    stepIndex: 3,
    label: SESSION_STATUS_LABELS.READY,
    badgeClass: 'bg-success-soft/70 text-success-dark border-success/30 font-semibold',
    dotClass: 'bg-success',
  },
  DEGRADED: {
    stepIndex: 3,
    label: SESSION_STATUS_LABELS.DEGRADED,
    badgeClass: 'bg-warning-soft text-warning-dark border-warning/40 font-bold',
    dotClass: 'bg-warning',
  },
  RUNNING: {
    stepIndex: 4,
    label: SESSION_STATUS_LABELS.RUNNING,
    badgeClass: 'bg-success-soft text-success-dark border-success/40 ring-1 ring-success/20 font-bold',
    dotClass: 'bg-success animate-pulse',
  },
  FINISHED: {
    stepIndex: 5,
    label: SESSION_STATUS_LABELS.FINISHED,
    badgeClass: 'bg-surface-subtle text-text-muted border-border-subtle font-medium',
    dotClass: 'bg-text-subtle',
    isTerminal: true,
  },
  RESTORING: {
    stepIndex: 5,
    label: SESSION_STATUS_LABELS.RESTORING,
    badgeClass: 'bg-primary-soft text-primary border-primary/30 font-medium',
    dotClass: 'bg-primary animate-pulse',
  },
  NORMAL: {
    stepIndex: 5,
    label: SESSION_STATUS_LABELS.NORMAL,
    badgeClass: 'bg-surface-subtle text-text-muted border-border-subtle font-medium',
    dotClass: 'bg-text-subtle',
    isTerminal: true,
  },
};

export const LIFECYCLE_STEPS: Array<{ key: SessionStatus; label: string; index: number }> = [
  { key: 'CREATED', label: SESSION_STATUS_LABELS.CREATED, index: 0 },
  { key: 'DEPLOYING', label: SESSION_STATUS_LABELS.DEPLOYING, index: 1 },
  { key: 'PREFLIGHT', label: SESSION_STATUS_LABELS.PREFLIGHT, index: 2 },
  { key: 'READY', label: SESSION_STATUS_LABELS.READY, index: 3 },
  { key: 'RUNNING', label: SESSION_STATUS_LABELS.RUNNING, index: 4 },
  { key: 'FINISHED', label: SESSION_STATUS_LABELS.FINISHED, index: 5 },
];
