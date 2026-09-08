import React, { useState } from 'react';
import { ChevronDown, ChevronUp } from 'lucide-react';
import { ActivityItem } from '@/src/domain';
import { cn } from '@/src/shared/lib/cn';
import { formatTimeOnly } from '@/src/shared/lib/formatters';
import { UI_LABELS } from '@/src/shared/config/labels';
import { ActivityDetailModal } from './ActivityDetailModal';

export interface ActivityFeedProps {
  activities?: ActivityItem[];
  maxItems?: number;
  onSelectActivity?: (activity: ActivityItem) => void;
  onInspectWorkstation?: (workstationId: string) => void;
}

export const ActivityFeed: React.FC<ActivityFeedProps> = ({
  activities = [],
  maxItems = 10,
  onSelectActivity,
  onInspectWorkstation,
}) => {
  const [isVisible, setIsVisible] = useState<boolean>(true);
  const [expandedList, setExpandedList] = useState<boolean>(false);
  const [internalSelectedActivity, setInternalSelectedActivity] = useState<ActivityItem | null>(null);

  const displayList = expandedList ? activities : activities.slice(0, maxItems);

  const handleItemClick = (item: ActivityItem) => {
    if (onSelectActivity) {
      onSelectActivity(item);
    } else {
      setInternalSelectedActivity(item);
    }
  };

  const getSourceLabel = (source: string) => {
    const s = source.toUpperCase();
    if (s.includes('PROCTOR')) return 'GIÁM THỊ';
    if (s.includes('GATEWAY')) return 'GATEWAY';
    if (s.includes('AGENT') || s.includes('WORKSTATION') || s.includes('PC')) return 'AGENT';
    if (s.includes('PREFLIGHT') || s.includes('SECURITY')) return 'TIỀN KIỂM';
    return 'HỆ THỐNG';
  };

  return (
    <div className="bg-surface border border-border rounded-sm shadow-2xs overflow-hidden flex flex-col">
      {/* Header: HOẠT ĐỘNG GẦN ĐÂY                    Ẩn/Hiện */}
      <div className="px-4 py-2.5 bg-surface-subtle border-b border-border-subtle flex items-center justify-between">
        <h3 className="text-xs uppercase tracking-wider font-bold text-text-muted">
          {UI_LABELS.activity.title}
        </h3>

        <button
          type="button"
          onClick={() => setIsVisible(!isVisible)}
          className="text-xs font-sans text-text-muted hover:text-text font-medium cursor-pointer transition-colors select-none"
        >
          {isVisible ? 'Ẩn' : 'Hiện'}
        </button>
      </div>

      {/* Activity List */}
      {isVisible && (
        <>
          <div className="divide-y divide-border-subtle max-h-[380px] overflow-y-auto custom-scrollbar">
            {activities.length === 0 ? (
              <div className="py-6 text-center text-xs text-text-muted font-sans">
                {UI_LABELS.activity.empty}
              </div>
            ) : (
              displayList.map((item) => {
                const sourceLabel = getSourceLabel(item.source);
                const isWarning = item.level === 'WARNING';
                const isError = item.level === 'ERROR';

                return (
                  <div
                    key={item.id}
                    onClick={() => handleItemClick(item)}
                    className={cn(
                      'px-3.5 sm:px-4 py-2.5 hover:bg-surface-subtle transition-colors cursor-pointer flex items-center justify-between gap-2.5 text-xs font-sans group border-l-2',
                      isError
                        ? 'border-l-error bg-error-soft/30 hover:bg-error-soft/60'
                        : isWarning
                        ? 'border-l-warning bg-warning-soft/30 hover:bg-warning-soft/60'
                        : 'border-l-transparent'
                    )}
                  >
                    {/* Main Event Row Information */}
                    <div className="flex items-center gap-2.5 min-w-0 flex-1">
                      {/* Visual Status Indicator Dot */}
                      <span
                        className={cn(
                          'w-2 h-2 rounded-full shrink-0',
                          isError
                            ? 'bg-error shadow-[0_0_4px_rgba(201,76,76,0.6)] animate-pulse'
                            : isWarning
                            ? 'bg-warning shadow-[0_0_4px_rgba(217,154,34,0.6)]'
                            : 'bg-success'
                        )}
                        title={isError ? 'Lỗi nghiêm trọng' : isWarning ? 'Cảnh báo an ninh' : 'Thông tin bình thường'}
                      />

                      {/* Timestamp */}
                      <span className="font-mono text-text-muted shrink-0 text-xs">
                        {formatTimeOnly(item.timestamp)}
                      </span>

                      {/* Message Content - Clean, High Legibility, Takes Full Width */}
                      <p
                        className={cn(
                          'truncate flex-1 min-w-0 text-xs',
                          isError && 'font-medium text-error-dark',
                          isWarning && 'font-medium text-warning-dark',
                          !isError && !isWarning && 'text-text'
                        )}
                      >
                        {item.message}
                      </p>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          {/* Expand more items if needed */}
          {activities.length > maxItems && (
            <div className="px-4 py-2 bg-surface-subtle border-t border-border-subtle text-center">
              <button
                type="button"
                onClick={() => setExpandedList(!expandedList)}
                className="inline-flex items-center gap-1 text-xs font-sans font-medium text-text-muted hover:text-text cursor-pointer select-none"
              >
                <span>{expandedList ? 'Thu gọn' : `Xem thêm (${activities.length - maxItems} sự kiện)`}</span>
                {expandedList ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
              </button>
            </div>
          )}
        </>
      )}

      {/* Fallback Internal Modal if parent did not attach onSelectActivity */}
      {!onSelectActivity && (
        <ActivityDetailModal
          activity={internalSelectedActivity}
          isOpen={Boolean(internalSelectedActivity)}
          onClose={() => setInternalSelectedActivity(null)}
          onInspectWorkstation={onInspectWorkstation}
        />
      )}
    </div>
  );
};

