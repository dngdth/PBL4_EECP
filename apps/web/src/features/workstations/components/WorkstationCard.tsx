import React from 'react';
import { Monitor, Clock, Shield, Network, Activity, Cpu } from 'lucide-react';
import { Workstation } from '@/src/domain';
import { cn } from '@/src/shared/lib/cn';
import { formatRelativeTime } from '@/src/shared/lib/formatters';

export interface WorkstationCardProps {
  workstation: Workstation;
  onInspect: (ws: Workstation) => void;
}

export const WorkstationCard: React.FC<WorkstationCardProps> = ({
  workstation,
  onInspect,
}) => {
  const isWarning = workstation.status === 'WARNING';
  const isFailed = workstation.status === 'FAILED';
  const isReady = workstation.status === 'READY';

  const details = workstation.preflight_details;

  return (
    <div
      onClick={() => onInspect(workstation)}
      className="group cursor-pointer select-none flex flex-col items-center transition-transform duration-150 hover:-translate-y-1"
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          onInspect(workstation);
        }
      }}
      aria-label={`Máy trạm ${workstation.id} - ${workstation.status}`}
    >
      {/* 1. Computer Monitor Unit (Màn hình máy tính màu trắng/sáng) */}
      <div
        className={cn(
          'w-full bg-slate-100 dark:bg-surface rounded-t-xl rounded-b-md border-2 p-1.5 sm:p-2 shadow-2xs transition-all duration-200 flex flex-col justify-between relative overflow-hidden',
          isReady && 'border-slate-300 dark:border-border group-hover:border-primary group-hover:shadow-[0_4px_16px_rgba(59,130,246,0.15)]',
          isWarning && 'border-amber-400 bg-amber-50/20 group-hover:border-amber-500 group-hover:shadow-[0_4px_16px_rgba(245,158,11,0.18)]',
          isFailed && 'border-rose-400 bg-rose-50/20 group-hover:border-rose-500 group-hover:shadow-[0_4px_16px_rgba(244,63,94,0.18)]'
        )}
      >
        {/* Top Bezel: Sleek minimalist frame with webcam dot */}
        <div className="flex items-center justify-center px-1.5 pb-1">
          {/* Webcam lens */}
          <div className="w-1.5 h-1.5 rounded-full bg-slate-400 ring-1 ring-slate-300" />
        </div>

        {/* 2. Inner Computer Screen (Mặt hiển thị màn hình nền trắng) */}
        <div
          className={cn(
            'w-full rounded-sm bg-white dark:bg-surface border border-slate-200 dark:border-border text-text p-2.5 sm:p-3 flex flex-col justify-between transition-colors space-y-2.5 shadow-2xs',
            'group-hover:border-border-hover'
          )}
        >
          {/* Screen Header / Large Workstation ID & Status LED */}
          <div className="flex items-center justify-between border-b border-border-subtle pb-2">
            <div className="flex items-center gap-2.5 min-w-0">
              <div
                className={cn(
                  'w-7 h-7 rounded flex items-center justify-center shrink-0 border',
                  isReady && 'bg-success-soft border-success/30 text-success-dark',
                  isWarning && 'bg-warning-soft border-warning/30 text-warning-dark',
                  isFailed && 'bg-error-soft border-error/30 text-error-dark'
                )}
              >
                <Monitor className="w-4 h-4" />
              </div>
              <div className="flex flex-col min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-mono font-black text-base sm:text-lg text-text tracking-tight leading-none truncate">
                    {workstation.id}
                  </span>
                  {/* Status LED Dot */}
                  <span
                    className={cn(
                      'w-2 h-2 rounded-full shrink-0 animate-pulse',
                      isReady && 'bg-success shadow-[0_0_6px_rgba(79,138,98,0.8)]',
                      isWarning && 'bg-warning shadow-[0_0_6px_rgba(217,154,34,0.8)]',
                      isFailed && 'bg-error shadow-[0_0_6px_rgba(201,76,76,0.8)]'
                    )}
                    title={isReady ? 'Sẵn sàng' : isWarning ? 'Cảnh báo' : 'Lỗi tiền kiểm'}
                  />
                </div>
                <span className="font-mono text-[11px] text-text-muted truncate mt-0.5">
                  {workstation.ip}
                </span>
              </div>
            </div>

            <div className="text-right">
              <span className={cn(
                'inline-block px-1.5 py-0.5 rounded text-[10px] font-sans font-semibold border',
                isReady && 'text-success-dark bg-success-soft border-success/30',
                isWarning && 'text-warning-dark bg-warning-soft border-warning/30',
                isFailed && 'text-error-dark bg-error-soft border-error/30'
              )}>
                {isReady ? 'Sẵn sàng' : isWarning ? 'Cảnh báo' : 'Lỗi'}
              </span>
            </div>
          </div>

          {/* Preflight Diagnostics Micro-Grid (Flat, Clean, High Legibility) */}
          <div className="space-y-1.5">
            <div className="text-[10px] uppercase tracking-wider font-bold text-text-muted font-sans flex items-center justify-between">
              <span>TIỀN KIỂM AN NINH</span>
            </div>

            <div className="grid grid-cols-4 gap-1.5">
              {/* 1. OS Lockdown */}
              <div
                className={cn(
                  'flex flex-col items-center justify-center py-1 px-0.5 rounded-xs text-[10px] font-sans font-semibold transition-colors border',
                  details?.os_lockdown
                    ? 'bg-success-soft border-success/30 text-success-dark'
                    : 'bg-error-soft border-error/30 text-error-dark'
                )}
                title="Khóa hệ điều hành"
              >
                <Shield className="w-3 h-3 mb-0.5" />
                <span>Khóa HĐH</span>
              </div>

              {/* 2. Network Firewall */}
              <div
                className={cn(
                  'flex flex-col items-center justify-center py-1 px-0.5 rounded-xs text-[10px] font-sans font-semibold transition-colors border',
                  details?.network_firewall
                    ? 'bg-success-soft border-success/30 text-success-dark'
                    : 'bg-error-soft border-error/30 text-error-dark'
                )}
                title="Tường lửa cô lập mạng"
              >
                <Network className="w-3 h-3 mb-0.5" />
                <span>Mạng</span>
              </div>

              {/* 3. Agent Health */}
              <div
                className={cn(
                  'flex flex-col items-center justify-center py-1 px-0.5 rounded-xs text-[10px] font-sans font-semibold transition-colors border',
                  details?.agent_health
                    ? 'bg-success-soft border-success/30 text-success-dark'
                    : 'bg-error-soft border-error/30 text-error-dark'
                )}
                title="Giám sát phòng thi"
              >
                <Activity className="w-3 h-3 mb-0.5" />
                <span>Giám sát</span>
              </div>

              {/* 4. Peripheral Check */}
              <div
                className={cn(
                  'flex flex-col items-center justify-center py-1 px-0.5 rounded-xs text-[10px] font-sans font-semibold transition-colors border',
                  details?.peripheral_check
                    ? 'bg-success-soft border-success/30 text-success-dark'
                    : 'bg-warning-soft border-warning/30 text-warning-dark'
                )}
                title="Toàn vẹn ngoại vi"
              >
                <Cpu className="w-3 h-3 mb-0.5" />
                <span>Ngoại vi</span>
              </div>
            </div>
          </div>

          {/* Screen Bottom Bar: Heartbeat & Click hint */}
          <div className="pt-2 border-t border-border-subtle flex items-center justify-between text-[10px] text-text-muted font-sans">
            <span className="flex items-center gap-1">
              <Clock className="w-3 h-3 text-text-muted" />
              <span className="truncate">{formatRelativeTime(workstation.last_heartbeat)}</span>
            </span>

            <span className="font-semibold text-primary group-hover:underline flex items-center gap-0.5">
              <span>Chi tiết</span>
              <span>→</span>
            </span>
          </div>
        </div>

        {/* Monitor Chin: Bottom bezel with centered status power LED */}
        <div className="h-2.5 flex items-center justify-center pt-0.5">
          <div
            className={cn(
              'w-2 h-0.5 rounded-full transition-all',
              isReady && 'bg-emerald-500 shadow-[0_0_4px_rgba(16,185,129,0.7)]',
              isWarning && 'bg-amber-500 shadow-[0_0_4px_rgba(245,158,11,0.7)]',
              isFailed && 'bg-rose-500 shadow-[0_0_4px_rgba(244,63,94,0.7)]'
            )}
          />
        </div>
      </div>

      {/* 3. Monitor Stand Neck (Cổ chân đế màn hình) */}
      <div className="w-6 sm:w-8 h-2 sm:h-2.5 bg-slate-300 dark:bg-slate-700 border-x border-slate-400/60 dark:border-slate-600 shadow-inner" />

      {/* 4. Monitor Stand Base (Chân đế để bàn) */}
      <div className="w-20 sm:w-28 h-1.5 sm:h-2 bg-slate-200 dark:bg-slate-700 border border-slate-300 dark:border-slate-600 rounded-sm shadow-2xs flex items-center justify-center group-hover:bg-slate-300 dark:group-hover:bg-slate-600 transition-colors">
        <div className="w-8 h-0.5 bg-slate-400 dark:bg-slate-500 rounded-full" />
      </div>
    </div>
  );
};

