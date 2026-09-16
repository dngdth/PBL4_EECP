import React, { useState } from 'react';
import { Shield, ShieldAlert, CheckCircle2 } from 'lucide-react';
import { Modal } from '@/src/shared/ui/modal';
import { Button } from '@/src/shared/ui/button';
import { Input } from '@/src/shared/ui/input';
import { deployPolicy } from '../services/sessionApi';
import { ExamSession } from '@/src/domain';
import { UI_LABELS } from '@/src/shared/config/labels';

export interface DeployPolicyModalProps {
  isOpen: boolean;
  onClose: () => void;
  session: ExamSession;
  onSuccess: (updatedSession: ExamSession) => void;
}

export const DeployPolicyModal: React.FC<DeployPolicyModalProps> = ({
  isOpen,
  onClose,
  session,
  onSuccess,
}) => {
  const [policyName, setPolicyName] = useState<string>(
    session.policy?.name || 'StandardExamPolicy_PBL4'
  );
  const [strictMode, setStrictMode] = useState<boolean>(session.policy?.strict_mode ?? true);
  const [networkLockdown, setNetworkLockdown] = useState<boolean>(
    session.policy?.network_lockdown ?? true
  );
  const [usbBlocked, setUsbBlocked] = useState<boolean>(
    session.policy?.usb_storage_blocked ?? true
  );
  const [allowedProcesses, setAllowedProcesses] = useState<string>(
    session.policy?.allowed_processes?.join(', ') || 'exam-browser.exe, calculator.exe'
  );

  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setErrorMsg(null);

    const procs = allowedProcesses
      .split(',')
      .map((p) => p.trim())
      .filter(Boolean);

    try {
      const res = await deployPolicy(session.id, {
        policy_name: policyName,
        strict_mode: strictMode,
        network_lockdown: networkLockdown,
        usb_storage_blocked: usbBlocked,
        allowed_processes: procs,
      });

      onSuccess(res.session);
      onClose();
    } catch (err: any) {
      setErrorMsg(err.message || 'Triển khai chính sách thất bại.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Triển khai chính sách bảo mật ca thi"
      description={`Cấu hình bộ quy tắc cô lập & phát lệnh đến Gateway [${session.gateway_id || 'Mặc định'}]`}
      maxWidth="md"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        {errorMsg && (
          <div className="p-3 bg-error-soft border border-error/20 rounded-sm text-xs text-error-dark flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}

        <Input
          label="Tên cấu hình chính sách"
          value={policyName}
          onChange={(e) => setPolicyName(e.target.value)}
          required
          placeholder="VD: StandardExamPolicy_PBL4"
        />

        <div className="space-y-2.5 pt-1">
          <label className="text-xs font-sans font-bold uppercase tracking-wider text-text block">
            Quy tắc an toàn trên máy trạm
          </label>

          <div className="space-y-2 bg-surface-subtle p-3 rounded-sm border border-border">
            <label className="flex items-center gap-2.5 cursor-pointer text-xs sm:text-[13px] font-sans font-medium text-text">
              <input
                type="checkbox"
                checked={strictMode}
                onChange={(e) => setStrictMode(e.target.checked)}
                className="w-4 h-4 rounded text-primary focus:ring-primary"
              />
              <span>Chế độ Kiosk nghiêm ngặt (Chặn Alt+Tab, Taskmgr, WinKey)</span>
            </label>

            <label className="flex items-center gap-2.5 cursor-pointer text-xs sm:text-[13px] font-sans font-medium text-text">
              <input
                type="checkbox"
                checked={networkLockdown}
                onChange={(e) => setNetworkLockdown(e.target.checked)}
                className="w-4 h-4 rounded text-primary focus:ring-primary"
              />
              <span>Cô lập mạng (Chỉ cho phép kết nối máy chủ thi & Gateway)</span>
            </label>

            <label className="flex items-center gap-2.5 cursor-pointer text-xs sm:text-[13px] font-sans font-medium text-text">
              <input
                type="checkbox"
                checked={usbBlocked}
                onChange={(e) => setUsbBlocked(e.target.checked)}
                className="w-4 h-4 rounded text-primary focus:ring-primary"
              />
              <span>Vô hiệu hóa bộ nhớ ngoài USB & thiết bị lưu trữ thứ cấp</span>
            </label>
          </div>
        </div>

        <Input
          label="Danh sách tiến trình cho phép (phân cách bằng dấu phẩy)"
          value={allowedProcesses}
          onChange={(e) => setAllowedProcesses(e.target.value)}
          placeholder="exam-browser.exe, calculator.exe"
          helperText="Các tiến trình nằm ngoài danh sách sẽ bị Agent tự động ngăn chặn."
        />

        <div className="flex items-center justify-end gap-2.5 pt-3 border-t border-border-subtle">
          <Button type="button" variant="secondary" size="sm" onClick={onClose} disabled={isLoading}>
            {UI_LABELS.actions.cancel}
          </Button>
          <Button
            type="submit"
            variant="primary"
            size="sm"
            isLoading={isLoading}
            leftIcon={<Shield className="w-3.5 h-3.5" />}
          >
            Phát lệnh triển khai
          </Button>
        </div>
      </form>
    </Modal>
  );
};
