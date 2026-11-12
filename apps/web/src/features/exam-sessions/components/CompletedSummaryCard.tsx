import React from 'react';
import { AlertTriangle, CheckCircle2, ShieldCheck, Clock, MonitorCheck } from 'lucide-react';
import { ExamSession } from '@/src/domain';
import { formatDateTime } from '@/src/shared/lib/formatters';
import { Button } from '@/src/shared/ui/button';

export interface CompletedSummaryCardProps {
  session: ExamSession;
  onRetryRestore?: () => void;
  isRetrying?: boolean;
}

export const CompletedSummaryCard: React.FC<CompletedSummaryCardProps> = ({
  session, onRetryRestore, isRetrying,
}) => {
  const restoredCount = session.workstations.filter(
    (ws) => ws.restored || !session.policy
  ).length;
  const totalCount = session.workstations.length;
  const restorationComplete = restoredCount === totalCount && (
    !session.gateway_id || session.gateway_restored || !session.policy
  );
  const StatusIcon = restorationComplete ? CheckCircle2 : AlertTriangle;

  return (
    <div className={`p-4 sm:p-5 bg-surface border rounded-sm shadow-2xs ${restorationComplete ? 'border-success/30' : 'border-warning/50'}`}>
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className={`w-9 h-9 rounded-full border flex items-center justify-center shrink-0 mt-0.5 ${restorationComplete ? 'bg-success-soft border-success/30 text-success-dark' : 'bg-warning-soft border-warning/50 text-warning-dark'}`}>
            <StatusIcon className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-sm sm:text-base font-bold text-text">
              {restorationComplete ? 'Ca thi đã kết thúc và khôi phục hoàn tất' : 'Ca thi đã kết thúc, chưa khôi phục hoàn tất'}
            </h3>
            <p className="text-xs text-text-muted mt-0.5 font-sans">
              {restorationComplete
                ? 'Các máy trạm đã xác nhận hoàn nguyên chính sách.'
                : 'Máy chưa xác nhận hoàn nguyên có thể vẫn đang áp dụng chính sách của ca thi.'}
            </p>
          </div>
        </div>

        {/* Operational Indicators */}
        <div className="flex items-center gap-3 sm:gap-4 text-xs font-sans border-t sm:border-t-0 pt-2.5 sm:pt-0 border-border-subtle shrink-0">
          <div className="flex items-center gap-1.5 text-text-muted">
            <Clock className="w-3.5 h-3.5 text-primary" />
            <span>Kết thúc lúc: <strong className="text-text font-mono">{formatDateTime(session.updated_at)}</strong></span>
          </div>

          <div className="flex items-center gap-1.5 text-text-muted">
            <MonitorCheck className="w-3.5 h-3.5 text-success" />
            <span>Đã khôi phục: <strong className="text-text font-mono">{restoredCount}/{totalCount}</strong></span>
          </div>

          <div className="flex items-center gap-1.5 text-text-muted">
            <ShieldCheck className="w-3.5 h-3.5 text-primary" />
            <span>Chính sách: <strong className="text-text font-mono">{restorationComplete ? 'Đã hoàn nguyên' : 'Chờ hoàn nguyên'}</strong></span>
          </div>
          {!restorationComplete && !session.gateway_id && onRetryRestore && (
            <Button size="sm" variant="outline" onClick={onRetryRestore} isLoading={isRetrying}>
              Thử lại khôi phục
            </Button>
          )}
        </div>
      </div>
    </div>
  );
};
