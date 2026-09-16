import React, { useState } from 'react';
import { StopCircle, AlertTriangle } from 'lucide-react';
import { Modal } from '@/src/shared/ui/modal';
import { Button } from '@/src/shared/ui/button';
import { finishSession } from '../services/sessionApi';
import { ExamSession } from '@/src/domain';
import { UI_LABELS } from '@/src/shared/config/labels';

export interface FinishSessionModalProps {
  isOpen: boolean;
  onClose: () => void;
  session: ExamSession;
  onSuccess: (updatedSession: ExamSession) => void;
}

export const FinishSessionModal: React.FC<FinishSessionModalProps> = ({
  isOpen,
  onClose,
  session,
  onSuccess,
}) => {
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleFinish = async () => {
    setIsLoading(true);
    setErrorMsg(null);
    try {
      const res = await finishSession(session.id);
      onSuccess(res.session);
      onClose();
    } catch (err: any) {
      setErrorMsg(err.message || 'Không thể kết thúc ca thi.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Kết thúc ca thi"
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

        <div className="p-3 bg-surface-subtle border border-border rounded text-text">
          <p className="font-semibold text-text">Bạn có chắc chắn muốn kết thúc ca thi này?</p>
          <p className="text-xs text-text-muted mt-1 font-sans">
            Thao tác này sẽ đóng phiên thi, thu hồi chính sách khóa máy và chuyển ca thi sang trạng thái <strong>ĐÃ KẾT THÚC (FINISHED)</strong>.
          </p>
        </div>

        <div className="flex items-center justify-end gap-2.5 pt-3 border-t border-border-subtle">
          <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={isLoading}>
            {UI_LABELS.actions.cancel}
          </Button>
          <Button
            type="button"
            variant="primary"
            size="sm"
            onClick={handleFinish}
            isLoading={isLoading}
            leftIcon={<StopCircle className="w-3.5 h-3.5 text-error" />}
            className="bg-error hover:bg-error-dark text-surface font-bold border-error"
          >
            Xác nhận kết thúc
          </Button>
        </div>
      </div>
    </Modal>
  );
};
