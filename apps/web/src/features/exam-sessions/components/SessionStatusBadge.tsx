import React from 'react';
import { SessionStatus } from '@/src/domain';
import { cn } from '@/src/shared/lib/cn';
import { SESSION_STATUS_CONFIG } from '../model/lifecycle';

export interface SessionStatusBadgeProps {
  status: SessionStatus;
  className?: string;
  size?: 'sm' | 'md';
}

export const SessionStatusBadge: React.FC<SessionStatusBadgeProps> = ({
  status,
  className,
  size = 'md',
}) => {
  const config = SESSION_STATUS_CONFIG[status] || {
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
