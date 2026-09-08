import React, { useState } from 'react';
import { ShieldAlert, Play, AlertTriangle } from 'lucide-react';
import { Modal } from '@/src/shared/ui/modal';
import { Button } from '@/src/shared/ui/button';
import { forceStartSession } from '../services/sessionApi';
import { ExamSession } from '@/src/domain';
import { UI_LABELS } from '@/src/shared/config/labels';

export interface ForceStartDialogProps {
  isOpen: boolean;
  onClose: () => void;
  session: ExamSession;
  onSuccess: (updatedSession: ExamSession) => void;
}

export const ForceStartDialog: React.FC<ForceStartDialogProps> = ({
  isOpen,
  onClose,
  session,
  onSuccess,
}) => {
  const [reason, setReason] = useState<string>('Giám thị xác nhận máy trạm đạt yêu cầu phòng thi.');
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const nonReadyCount = session.workstations.filter((w) => w.status !== 'READY').length;

  const handleConfirm = async () => {
    setIsLoading(true);
    setErrorMsg(null);
    try {
      const res = await forceStartSession(session.id, reason);
      onSuccess(res.session);
      onClose();
    } catch (err: any) {
      setErrorMsg(err.message || 'Không thể bắt đầu ca thi.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={session.status === 'READY' ? 'Xác nhận bắt đầu ca thi' : 'Bắt đầu cưỡng chế ca thi'}
      description={`Ca thi: ${session.name} (${session.id})`}
      maxWidth="sm"
    >
      <div className="space-y-4 text-xs sm:text-[13px] font-sans">
        {errorMsg && (
          <div className="p-3 bg-error-soft border border-error/20 rounded-sm text-xs text-error-dark flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}

        {nonReadyCount > 0 && session.status !== 'READY' ? (
          <div className="p-3 bg-warning-soft border border-warning/30 rounded-sm text-warning-dark space-y-1">
            <div className="flex items-center gap-1.5 font-bold">
              <ShieldAlert className="w-4 h-4 shrink-0" />
              <span>Cảnh báo tiền kiểm ({nonReadyCount} máy chưa sẵn sàng)</span>
            </div>
            <p className="text-xs">
              Hiện có {nonReadyCount} máy trạm đang ở trạng thái Cảnh báo hoặc Lỗi. Việc cưỡng chế bắt đầu sẽ chuyển thẳng sang giai đoạn thi.
            </p>
          </div>
        ) : (
          <p className="text-text-muted">
            Xác nhận phát lệnh khóa màn hình & phân phát đề thi tới toàn bộ máy trạm trong phòng thi {session.room_id}.
          </p>
        )}

        {session.status !== 'READY' && (
          <div>
            <label className="text-xs font-sans font-bold uppercase tracking-wider text-text-muted block mb-1">
              Lý do cưỡng chế (Ghi nhận nhật ký giám thị)
            </label>
            <textarea
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={2}
              className="w-full text-xs font-sans bg-surface text-text border border-border rounded p-2 focus:outline-none focus:ring-1 focus:ring-primary"
            />
          </div>
        )}

        <div className="flex items-center justify-end gap-2.5 pt-3 border-t border-border-subtle">
          <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={isLoading}>
            {UI_LABELS.actions.cancel}
          </Button>
          <Button
            type="button"
            variant="primary"
            size="sm"
            onClick={handleConfirm}
            isLoading={isLoading}
            leftIcon={<Play className="w-3.5 h-3.5 fill-current" />}
          >
            {session.status === 'READY' ? 'Bắt đầu ca thi' : 'Xác nhận bắt đầu'}
          </Button>
        </div>
      </div>
    </Modal>
  );
};
