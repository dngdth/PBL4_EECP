import React from 'react';
import { DoorOpen, Wifi, Terminal } from 'lucide-react';
import { UI_LABELS } from '@/src/shared/config/labels';

export interface SessionEnvironmentCardProps {
  roomId?: string;
  gatewayId?: string;
  workstationCount?: number;
  onOpenCommandQueue?: (targetId: string) => void;
}

export const SessionEnvironmentCard: React.FC<SessionEnvironmentCardProps> = ({
  roomId,
  gatewayId,
  workstationCount,
  onOpenCommandQueue,
}) => {
  if (!roomId && !gatewayId && workstationCount === undefined) return null;

  return (
    <div className="bg-surface border border-border rounded shadow-2xs overflow-hidden">
      {/* Header */}
      <div className="px-4 py-2.5 bg-surface-subtle border-b border-border-subtle flex items-center justify-between">
        <h3 className="text-xs uppercase tracking-wider font-bold text-text-muted font-sans">
          {UI_LABELS.environment.title}
        </h3>
        <span className="inline-flex items-center gap-1 text-[11px] font-sans font-medium text-success">
          <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse" />
          <span>Trực tuyến</span>
        </span>
      </div>

      {/* Main Content */}
      <div className="p-4 space-y-3">
        <div className="flex items-start justify-between gap-4">
          {/* Room Column */}
          <div className="min-w-0 flex-1">
            <div className="font-mono font-bold text-lg text-text truncate">
              {roomId || '—'}
            </div>
            <div className="text-xs text-text-muted font-sans mt-0.5">
              Phòng máy thi
            </div>
          </div>

          {/* Gateway Column */}
          <div className="min-w-0 flex-1 text-right">
            <div className="font-mono font-semibold text-sm text-text truncate">
              {gatewayId || '—'}
            </div>
            <div className="text-xs text-text-muted font-sans mt-0.5">
              Gateway phòng
            </div>
          </div>
        </div>

        {/* Divider and Actions */}
        <div className="pt-3 border-t border-border-subtle flex items-center justify-between text-xs font-sans">
          <span className="text-text font-semibold">
            {workstationCount ?? 0} máy trạm
          </span>

          {gatewayId && onOpenCommandQueue && (
            <button
              type="button"
              onClick={() => onOpenCommandQueue(gatewayId)}
              className="text-[11px] font-medium text-primary hover:text-primary-dark flex items-center gap-1 cursor-pointer transition-colors"
            >
              <Terminal className="w-3.5 h-3.5" />
              <span>Hàng đợi lệnh</span>
            </button>
          )}
        </div>
      </div>
    </div>
  );
};
