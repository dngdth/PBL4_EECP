import React from 'react';
import { cn } from '../lib/cn';

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: 'neutral' | 'success' | 'warning' | 'error' | 'orange' | 'info';
  size?: 'sm' | 'md';
  dot?: boolean;
}

export const Badge: React.FC<BadgeProps> = ({
  children,
  className,
  variant = 'neutral',
  size = 'md',
  dot = false,
  ...props
}) => {
  const baseStyles = 'inline-flex items-center font-bold uppercase rounded-sm tracking-wider select-none font-sans';

  const sizeStyles = {
    sm: 'text-[11px] px-2 py-0.5 gap-1.5 leading-tight',
    md: 'text-xs px-2.5 py-1 gap-1.5 leading-tight',
  };

  const variantStyles = {
    neutral: 'bg-background text-text-muted border border-border',
    success: 'bg-success-soft text-success border border-success/20',
    warning: 'bg-warning-soft text-warning-dark border border-warning/30',
    error: 'bg-error-soft text-error border border-error/30',
    orange: 'bg-primary-soft text-primary border border-primary/30',
    info: 'bg-surface-subtle text-text-muted border border-border',
  };

  const dotColors = {
    neutral: 'bg-text-muted',
    success: 'bg-success',
    warning: 'bg-warning',
    error: 'bg-error',
    orange: 'bg-primary',
    info: 'bg-text-muted',
  };

  return (
    <span className={cn(baseStyles, sizeStyles[size], variantStyles[variant], className)} {...props}>
      {dot && <span className={cn('w-1.5 h-1.5 rounded-full shrink-0', dotColors[variant])} />}
      {children}
    </span>
  );
};
