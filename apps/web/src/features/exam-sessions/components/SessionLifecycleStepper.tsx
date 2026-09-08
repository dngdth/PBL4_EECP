import React from 'react';
import { SessionStatus } from '@/src/domain';
import { LIFECYCLE_STEPS, SESSION_STATUS_CONFIG } from '../model/lifecycle';
import { cn } from '@/src/shared/lib/cn';
import { Check } from 'lucide-react';

export interface SessionLifecycleStepperProps {
  status: SessionStatus;
  updatedAt?: string;
}

export const SessionLifecycleStepper: React.FC<SessionLifecycleStepperProps> = ({
  status,
}) => {
  const currentConfig = SESSION_STATUS_CONFIG[status] || { stepIndex: 0 };
  const activeIndex = currentConfig.stepIndex;

  return (
    <nav aria-label="Vòng đời ca thi" className="bg-surface border border-border rounded-sm px-4 py-3 sm:px-6 sm:py-4 w-full shadow-2xs">
      <div className="overflow-x-auto no-scrollbar py-1">
        <ol className="flex items-center justify-between min-w-[580px] sm:min-w-0 w-full gap-1.5 list-none m-0 p-0">
          {LIFECYCLE_STEPS.map((step, idx) => {
            const isPassed = idx < activeIndex;
            const isCurrent = idx === activeIndex;
            const isLast = idx === LIFECYCLE_STEPS.length - 1;

            return (
              <li key={step.key} className="flex-1 flex items-center min-w-0 last:flex-initial">
                <div className="flex items-center gap-2 sm:gap-2.5 shrink-0 select-none">
                  {/* Indicator Dot */}
                  {isPassed ? (
                    <span className="w-6 h-6 rounded-full bg-success text-surface flex items-center justify-center shrink-0 shadow-2xs ring-2 ring-success/20">
                      <Check className="w-3.5 h-3.5 stroke-[3]" />
                    </span>
                  ) : isCurrent ? (
                    <span className="w-6 h-6 rounded-full bg-primary text-surface flex items-center justify-center shrink-0 ring-4 ring-primary/25 shadow-xs animate-pulse">
                      <span className="w-2 h-2 rounded-full bg-surface" />
                    </span>
                  ) : (
                    <span className="w-6 h-6 rounded-full border-2 border-border bg-surface-subtle flex items-center justify-center shrink-0">
                      <span className="w-1.5 h-1.5 rounded-full bg-text-subtle/40" />
                    </span>
                  )}

                  {/* Step Label */}
                  <span
                    className={cn(
                      'text-xs sm:text-sm tracking-tight whitespace-nowrap',
                      isCurrent
                        ? 'text-primary font-bold'
                        : isPassed
                        ? 'text-text font-semibold'
                        : 'text-text-muted font-normal'
                    )}
                  >
                    {step.label}
                  </span>
                </div>

                {/* Connecting Line - prominent green/orange path */}
                {!isLast && (
                  <div className="flex-1 mx-2 sm:mx-3 h-1.5 bg-border/60 rounded-full overflow-hidden min-w-[20px]">
                    <div
                      className={cn(
                        'h-full rounded-full transition-all duration-300',
                        idx < activeIndex - 1
                          ? 'w-full bg-success'
                          : idx === activeIndex - 1
                          ? 'w-full bg-gradient-to-r from-success via-primary/80 to-primary'
                          : isCurrent
                          ? 'w-1/4 bg-primary/40'
                          : 'w-0 bg-transparent'
                      )}
                    />
                  </div>
                )}
              </li>
            );
          })}
        </ol>
      </div>
    </nav>
  );
};
