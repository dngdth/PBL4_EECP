import React from 'react';
import { CheckCircle2, ShieldCheck, Clock, MonitorCheck } from 'lucide-react';
import { ExamSession } from '@/src/domain';
import { formatDateTime } from '@/src/shared/lib/formatters';

export interface CompletedSummaryCardProps {
  session: ExamSession;
}

export const CompletedSummaryCard: React.FC<CompletedSummaryCardProps> = ({ session }) => {
  const readyCount = session.workstations.filter((ws) => ws.status === 'READY').length;
  const totalCount = session.workstations.length;

  return (
    <div className="p-4 sm:p-5 bg-surface border border-success/30 rounded-sm shadow-2xs">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-full bg-success-soft border border-success/30 text-success-dark flex items-center justify-center shrink-0 mt-0.5">
            <CheckCircle2 className="w-5 h-5" />
          </div>
          <div>
            <h3 className="text-sm sm:text-base font-bold text-text">
              Ca thi đã hoàn tất an toàn
            </h3>
            <p className="text-xs text-text-muted mt-0.5 font-sans">
              Toàn bộ dữ liệu nhật ký, kết quả tiền kiểm và hồ sơ máy trạm đã được lưu trữ an toàn.
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
            <span>Máy trạm: <strong className="text-text font-mono">{readyCount}/{totalCount}</strong></span>
          </div>

          <div className="flex items-center gap-1.5 text-text-muted">
            <ShieldCheck className="w-3.5 h-3.5 text-primary" />
            <span>Chính sách: <strong className="text-text font-mono">{session.policy?.name ? 'Đã áp dụng' : 'Mặc định'}</strong></span>
          </div>
        </div>
      </div>
    </div>
  );
};
