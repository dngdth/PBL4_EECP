import React, { useState } from 'react';
import { AlertTriangle, CheckCircle2, ShieldAlert, SlidersHorizontal, ArrowRight, Info } from 'lucide-react';
import { ExamSession, SessionStatus } from '@/src/domain';
import { Modal } from '@/src/shared/ui/modal';
import { Button } from '@/src/shared/ui/button';
import { SESSION_STATUS_LABELS } from '@/src/shared/config/labels';
import { updateSessionStatus } from '../services/sessionApi';
import { formatApiErrorMessage } from '@/src/shared/api/errors';
import { cn } from '@/src/shared/lib/cn';

export interface InterveneStatusModalProps {
  isOpen: boolean;
  onClose: () => void;
  session: ExamSession | null;
  onSuccess: (updatedSession: ExamSession) => void;
}

const AVAILABLE_STATUSES: Array<{
  status: SessionStatus;
  description: string;
  badgeClass: string;
}> = [
  {
    status: 'CREATED',
    description: 'Khởi tạo lại trạng thái ban đầu của ca thi',
    badgeClass: 'bg-surface-subtle text-text-muted border-border',
  },
  {
    status: 'DEPLOYING',
    description: 'Chuyển ca thi sang trạng thái đang nạp chính sách xuống Gateway',
    badgeClass: 'bg-primary-soft text-primary border-primary/30',
  },
  {
    status: 'PREFLIGHT',
    description: 'Chuyển ca thi sang trạng thái đang kiểm tra tiền kiểm máy trạm',
    badgeClass: 'bg-warning-soft text-warning-dark border-warning/30',
  },
  {
    status: 'READY',
    description: 'Đánh dấu toàn bộ phòng máy đã sẵn sàng phát đề thi',
    badgeClass: 'bg-success-soft text-success-dark border-success/30',
  },
  {
    status: 'DEGRADED',
    description: 'Chuyển sang trạng thái cảnh báo / có máy lỗi cần giám sát riêng',
    badgeClass: 'bg-warning-soft text-warning-dark border-warning/50',
  },
  {
    status: 'RUNNING',
    description: 'Kích hoạt trạng thái ca thi đang diễn ra chính thức',
    badgeClass: 'bg-success-soft text-success-dark border-success/40',
  },
  {
    status: 'FINISHED',
    description: 'Kết thúc ca thi và chuẩn bị tiến hành hoàn nguyên máy trạm',
    badgeClass: 'bg-surface-subtle text-text-muted border-border',
  },
  {
    status: 'RESTORING',
    description: 'Đang gửi lệnh hoàn nguyên môi trường máy trạm về ban đầu',
    badgeClass: 'bg-primary-soft text-primary border-primary/30',
  },
  {
    status: 'NORMAL',
    description: 'Môi trường phòng máy đã hoàn nguyên hoàn tất về mặc định',
    badgeClass: 'bg-surface-subtle text-text-muted border-border',
  },
];

export const InterveneStatusModal: React.FC<InterveneStatusModalProps> = ({
  isOpen,
  onClose,
  session,
  onSuccess,
}) => {
  const [selectedStatus, setSelectedStatus] = useState<SessionStatus>(
    session?.status || 'READY'
  );
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  if (!session) return null;

  const hasGateway = Boolean(session.gateway_id);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (selectedStatus === session.status) {
      setErrorMsg('Vui lòng chọn một trạng thái khác với trạng thái hiện tại.');
      return;
    }

    setIsLoading(true);
    setErrorMsg(null);

    try {
      const res = await updateSessionStatus(session.id, selectedStatus);
      onSuccess(res.session);
      onClose();
    } catch (err: any) {
      setErrorMsg(formatApiErrorMessage(err));
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Can thiệp / Chuyển trạng thái ca thi"
      description={`Ca thi: [${session.name}] — Phòng ${session.room_id}`}
      maxWidth="lg"
    >
      <form onSubmit={handleSubmit} className="space-y-4 font-sans">
        {hasGateway ? (
          <div className="p-3.5 bg-primary-soft/40 border border-primary/30 rounded text-xs text-text space-y-1.5">
            <div className="flex items-center gap-2 text-primary font-bold">
              <Info className="w-4 h-4 shrink-0" />
              <span>Ca thi vận hành qua Gateway phòng ({session.gateway_id}):</span>
            </div>
            <p className="text-xs text-text-muted leading-relaxed">
              Theo quy chuẩn an toàn của Backend, ca thi này được điều phối tự động tuần tự qua các nút chức năng: <strong>[Triển khai chính sách] ➔ [Tiền kiểm] ➔ [Bắt đầu thi] ➔ [Kết thúc ca thi]</strong>.
            </p>
          </div>
        ) : (
          <div className="p-3 bg-warning-soft/60 border border-warning/40 rounded text-xs text-warning-dark flex items-start gap-2.5">
            <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
            <div className="space-y-1">
              <p className="font-bold">Cảnh báo quyền can thiệp đặc quyền:</p>
              <p className="text-[11px] leading-relaxed">
                Thao tác này cho phép giám thị/quản trị viên can thiệp ghi đè trạng thái vòng đời của ca thi trên máy chủ Backend. Hãy chắc chắn bạn đã nắm rõ tình trạng thực tế của phòng máy.
              </p>
            </div>
          </div>
        )}

        {errorMsg && (
          <div className="p-3.5 bg-error-soft border border-error/30 rounded text-xs text-error-dark flex items-start gap-2.5">
            <ShieldAlert className="w-4 h-4 shrink-0 mt-0.5" />
            <span className="leading-relaxed font-medium">{errorMsg}</span>
          </div>
        )}

        <div className="space-y-2">
          <label className="text-xs font-bold uppercase tracking-wider text-text block">
            Chọn trạng thái mục tiêu cần chuyển đổi:
          </label>

          <div className="space-y-1.5 max-h-[280px] overflow-y-auto pr-1">
            {AVAILABLE_STATUSES.map((item) => {
              const isCurrent = item.status === session.status;
              const isSelected = item.status === selectedStatus;

              return (
                <div
                  key={item.status}
                  onClick={() => setSelectedStatus(item.status)}
                  className={cn(
                    'p-3 rounded border transition-all cursor-pointer flex items-center justify-between gap-3 text-left select-none',
                    isSelected
                      ? 'border-primary bg-primary-soft/30 ring-1 ring-primary/40'
                      : 'border-border bg-surface hover:bg-surface-subtle'
                  )}
                >
                  <div className="space-y-0.5">
                    <div className="flex items-center gap-2">
                      <span className={cn('px-2 py-0.5 rounded text-xs font-bold border', item.badgeClass)}>
                        {SESSION_STATUS_LABELS[item.status]}
                      </span>
                      <span className="font-mono text-xs text-text font-semibold">({item.status})</span>
                      {isCurrent && (
                        <span className="text-[10px] px-1.5 py-0.2 bg-surface-subtle border border-border text-text-muted rounded font-medium">
                          HIỆN TẠI
                        </span>
                      )}
                    </div>
                    <p className="text-xs text-text-muted leading-relaxed">
                      {item.description}
                    </p>
                  </div>

                  <input
                    type="radio"
                    name="targetStatus"
                    checked={isSelected}
                    onChange={() => setSelectedStatus(item.status)}
                    className="text-primary focus:ring-primary h-4 w-4 shrink-0"
                  />
                </div>
              );
            })}
          </div>
        </div>

        <div className="flex items-center justify-end gap-2.5 pt-3 border-t border-border-subtle">
          <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={isLoading}>
            Đóng
          </Button>
          <Button type="submit" variant="primary" size="sm" isLoading={isLoading} className="font-bold">
            Xác nhận chuyển trạng thái
          </Button>
        </div>
      </form>
    </Modal>
  );
};
