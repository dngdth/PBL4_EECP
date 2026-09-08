import React from 'react';
import { Loader2 } from 'lucide-react';
import { cn } from '../lib/cn';

export interface SpinnerProps {
  size?: 'sm' | 'md' | 'lg';
  className?: string;
  label?: string;
}

export const Spinner: React.FC<SpinnerProps> = ({ size = 'md', className, label }) => {
  const sizeMap = {
    sm: 'w-4 h-4',
    md: 'w-6 h-6',
    lg: 'w-8 h-8',
  };

  return (
    <div className="flex flex-col items-center justify-center gap-2 p-4">
      <Loader2 className={cn('animate-spin text-primary', sizeMap[size], className)} />
      {label && <p className="text-xs text-text-muted font-medium">{label}</p>}
    </div>
  );
};
