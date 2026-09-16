import React from 'react';
import { WorkstationStatus, PreflightStatus } from '@/src/domain';
import { cn } from '@/src/shared/lib/cn';
import { WORKSTATION_STATUS_LABELS, PREFLIGHT_STATUS_LABELS } from '@/src/shared/config/labels';

export interface WorkstationStatusBadgeProps {
  status: WorkstationStatus;
  className?: string;
  size?: 'sm' | 'md';
}

export const WorkstationStatusBadge: React.FC<WorkstationStatusBadgeProps> = ({
  status,
  className,
  size = 'sm',
}) => {
  const config = {
    READY: {
      label: WORKSTATION_STATUS_LABELS.READY,
      badgeClass: 'bg-success-soft text-success-dark border-success/30',
      dotClass: 'bg-success',
    },
    WARNING: {
      label: WORKSTATION_STATUS_LABELS.WARNING,
      badgeClass: 'bg-warning-soft text-warning-dark border-warning/30',
      dotClass: 'bg-warning',
    },
    FAILED: {
      label: WORKSTATION_STATUS_LABELS.FAILED,
      badgeClass: 'bg-error-soft text-error-dark border-error/30',
      dotClass: 'bg-error',
    },
    PENDING: {
      label: WORKSTATION_STATUS_LABELS.PENDING,
      badgeClass: 'bg-surface-subtle text-text-muted border-border',
      dotClass: 'bg-text-subtle',
    },
  }[status] || {
    label: status,
    badgeClass: 'bg-surface-subtle text-text-muted border-border',
    dotClass: 'bg-text-subtle',
  };

  const sizeClasses = {
    sm: 'text-[11px] px-2 py-0.5 gap-1.5',
    md: 'text-xs px-2.5 py-1 gap-2',
  };

  return (
    <span
      className={cn(
        'inline-flex items-center font-sans font-bold uppercase tracking-wider rounded border select-none',
        sizeClasses[size],
        config.badgeClass,
        className
      )}
    >
      <span className={cn('w-1.5 h-1.5 rounded-full shrink-0', config.dotClass)} />
      <span>{config.label}</span>
    </span>
  );
};

export interface PreflightStatusBadgeProps {
  status: PreflightStatus;
  className?: string;
  size?: 'sm' | 'md';
}

export const PreflightStatusBadge: React.FC<PreflightStatusBadgeProps> = ({
  status,
  className,
  size = 'sm',
}) => {
  const config = {
    PASSED: {
      label: PREFLIGHT_STATUS_LABELS.PASSED,
      badgeClass: 'bg-success-soft text-success-dark border-success/30',
      dotClass: 'bg-success',
    },
    WARNING: {
      label: PREFLIGHT_STATUS_LABELS.WARNING,
      badgeClass: 'bg-warning-soft text-warning-dark border-warning/30',
      dotClass: 'bg-warning',
    },
    FAILED: {
      label: PREFLIGHT_STATUS_LABELS.FAILED,
      badgeClass: 'bg-error-soft text-error-dark border-error/30',
      dotClass: 'bg-error',
    },
    PENDING: {
      label: PREFLIGHT_STATUS_LABELS.PENDING,
      badgeClass: 'bg-surface-subtle text-text-muted border-border',
      dotClass: 'bg-text-subtle',
    },
  }[status] || {
    label: status,
    badgeClass: 'bg-surface-subtle text-text-muted border-border',
    dotClass: 'bg-text-subtle',
  };

  const sizeClasses = {
    sm: 'text-[11px] px-2 py-0.5 gap-1.5',
    md: 'text-xs px-2.5 py-1 gap-2',
  };

  return (
    <span
      className={cn(
        'inline-flex items-center font-sans font-bold uppercase tracking-wider rounded border select-none',
        sizeClasses[size],
        config.badgeClass,
        className
      )}
    >
      <span className={cn('w-1.5 h-1.5 rounded-full shrink-0', config.dotClass)} />
      <span>{config.label}</span>
    </span>
  );
};
