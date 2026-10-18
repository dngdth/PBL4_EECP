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
  const [blockProhibitedApps, setBlockProhibitedApps] = useState<boolean>(
    session.policy?.strict_mode ?? true
  );
  const [networkBlock, setNetworkBlock] = useState<boolean>(
    session.policy?.network_lockdown ?? true
  );
  const [usbBlocked, setUsbBlocked] = useState<boolean>(
    session.policy?.usb_storage_blocked ?? true
  );
  const [deniedProcesses, setDeniedProcesses] = useState<string>(
    session.policy?.denied_processes?.join(', ') ||
      'chatgpt.exe, anydesk.exe, teamviewer.exe, ultraviewer.exe'
  );
  const [allowedProcesses, setAllowedProcesses] = useState<string>(
    session.policy?.allowed_processes?.join(', ') || 'exam-browser.exe'
  );

  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setIsLoading(true);
    setErrorMsg(null);

    const denied = deniedProcesses
      .split(',')
      .map((p) => p.trim())
      .filter(Boolean);

    const allowed = allowedProcesses
      .split(',')
      .map((p) => p.trim())
      .filter(Boolean);

    const normalizeApp = (app: string) => {
      const clean = app.trim().toLowerCase();
      if (!clean) return '';
      return clean.endsWith('.exe') ? clean : `${clean}.exe`;
    };

    const normAllowed = Array.from(new Set(allowed.map(normalizeApp).filter(Boolean)));
    const normDenied = Array.from(new Set(denied.map(normalizeApp).filter(Boolean)));

    if (blockProhibitedApps) {
      const allowedSet = new Set(normAllowed);
      const overlap = normDenied.filter((p) => allowedSet.has(p));
      if (overlap.length > 0) {
        setErrorMsg(
          `Tiến trình [${overlap.join(', ')}] không thể vừa nằm trong danh sách cho phép vừa nằm trong danh sách cấm.`
        );
        setIsLoading(false);
        return;
      }
    }

    try {
      const res = await deployPolicy(session.id, {
        policy_name: policyName,
        strict_mode: blockProhibitedApps,
        network_lockdown: networkBlock,
        usb_storage_blocked: usbBlocked,
        allowed_processes: allowed,
        denied_processes: denied,
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
      description={`Cấu hình quy tắc bảo mật & phát lệnh đến Gateway [${session.gateway_id || 'Mặc định'}] và máy trạm`}
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
            <label className="flex items-start gap-2.5 cursor-pointer text-xs sm:text-[13px] font-sans font-medium text-text">
              <input
                type="checkbox"
                checked={blockProhibitedApps}
                onChange={(e) => setBlockProhibitedApps(e.target.checked)}
                className="w-4 h-4 mt-0.5 rounded text-primary focus:ring-primary"
              />
              <div>
                <span className="block font-semibold">Tự động tắt ứng dụng gian lận (taskkill)</span>
                <span className="text-[11px] text-text-muted font-normal block">
                  Agent sẽ định kỳ rà soát và cưỡng chế tắt các tiến trình trong danh sách cấm (AnyDesk, ChatGPT...).
                </span>
              </div>
            </label>

            <label className="flex items-start gap-2.5 cursor-pointer text-xs sm:text-[13px] font-sans font-medium text-text">
              <input
                type="checkbox"
                checked={networkBlock}
                onChange={(e) => setNetworkBlock(e.target.checked)}
                className="w-4 h-4 mt-0.5 rounded text-primary focus:ring-primary"
              />
              <div>
                <span className="block font-semibold">Chặn danh mục web cấm (AI, Mạng xã hội, VPN/Proxy)</span>
                <span className="text-[11px] text-text-muted font-normal block">
                  Điều hướng các tên miền bị cấm về 127.0.0.1 qua file hosts trên máy trạm.
                </span>
              </div>
            </label>

            <label className="flex items-start gap-2.5 cursor-pointer text-xs sm:text-[13px] font-sans font-medium text-text">
              <input
                type="checkbox"
                checked={usbBlocked}
                onChange={(e) => setUsbBlocked(e.target.checked)}
                className="w-4 h-4 mt-0.5 rounded text-primary focus:ring-primary"
              />
              <div>
                <span className="block font-semibold">Vô hiệu hóa bộ nhớ ngoài USB & thiết bị lưu trữ thứ cấp</span>
                <span className="text-[11px] text-text-muted font-normal block">
                  Khóa registry dịch vụ USBSTOR trên hệ điều hành Windows của máy trạm.
                </span>
              </div>
            </label>
          </div>
        </div>

        {blockProhibitedApps && (
          <Input
            label="Danh sách tiến trình cấm (tự động tắt khi phát hiện)"
            value={deniedProcesses}
            onChange={(e) => setDeniedProcesses(e.target.value)}
            placeholder="chatgpt.exe, anydesk.exe, teamviewer.exe, ultraviewer.exe"
            helperText="Tên file thực thi (.exe) mà Agent sẽ tự động taskkill nếu phát hiện đang chạy."
          />
        )}

        <Input
          label="Danh sách ứng dụng thi cử cho phép"
          value={allowedProcesses}
          onChange={(e) => setAllowedProcesses(e.target.value)}
          placeholder="exam-browser.exe"
          helperText="Tiến trình ứng dụng thi cử chính thức phục vụ lưu vết và giám sát hệ thống."
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
