import React, { useState, useEffect } from 'react';
import { ShieldCheck, ShieldAlert, Check } from 'lucide-react';
import { Modal } from '@/src/shared/ui/modal';
import { Button } from '@/src/shared/ui/button';
import { Input } from '@/src/shared/ui/input';
import { PolicyProfile, UpdatePolicyProfileInput } from '@/src/domain';
import { updatePolicyProfile } from '../services/policyApi';

export interface EditPolicyProfileModalProps {
  isOpen: boolean;
  onClose: () => void;
  profile: PolicyProfile | null;
  onSuccess: (updatedProfile: PolicyProfile) => void;
}

export const EditPolicyProfileModal: React.FC<EditPolicyProfileModalProps> = ({
  isOpen,
  onClose,
  profile,
  onSuccess,
}) => {
  const [label, setLabel] = useState<string>('');
  const [description, setDescription] = useState<string>('');
  const [allowedApps, setAllowedApps] = useState<string>('');
  const [deniedApps, setDeniedApps] = useState<string>('');
  const [blockAi, setBlockAi] = useState<boolean>(true);
  const [blockSocial, setBlockSocial] = useState<boolean>(true);
  const [usbBlocked, setUsbBlocked] = useState<boolean>(true);

  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (profile) {
      setLabel(profile.label || '');
      setDescription(profile.description || '');

      const allowList = profile.rules?.applications?.allow || [];
      const denyList = profile.rules?.applications?.deny || [];
      setAllowedApps(allowList.join(', '));
      setDeniedApps(denyList.join(', '));

      const blockedNet = profile.rules?.network?.block || [];
      setBlockAi(blockedNet.includes('generative_ai'));
      setBlockSocial(blockedNet.includes('social_network'));

      setUsbBlocked(profile.rules?.devices?.usb === 'deny');
      setErrorMsg(null);
    }
  }, [profile]);

  if (!profile) return null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!label.trim()) {
      setErrorMsg('Vui lòng nhập Tên hiển thị của chính sách.');
      return;
    }

    setIsLoading(true);
    setErrorMsg(null);

    const allowList = allowedApps
      .split(/[\n,;]+/)
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean);

    const denyList = deniedApps
      .split(/[\n,;]+/)
      .map((s) => s.trim().toLowerCase())
      .filter(Boolean);

    const blockNetList: string[] = [];
    if (blockAi) blockNetList.push('generative_ai');
    if (blockSocial) blockNetList.push('social_network');

    const payload: UpdatePolicyProfileInput = {
      label: label.trim(),
      description: description.trim() || `Chính sách ${label.trim()}`,
      rules: {
        applications: {
          allow: allowList,
          deny: denyList,
        },
        network: {
          block: blockNetList,
        },
        devices: {
          usb: usbBlocked ? 'deny' : 'allow',
        },
      },
    };

    try {
      const updated = await updatePolicyProfile(profile.id, payload);
      onSuccess(updated);
      onClose();
    } catch (err: any) {
      setErrorMsg(err.message || 'Không thể cập nhật chính sách. Vui lòng kiểm tra lại dữ liệu.');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`Chỉnh sửa chính sách: ${profile.id}`}
      description="Cập nhật cấu hình quy tắc ứng dụng, mạng và thiết bị cho các ca thi được tạo sau này."
      maxWidth="lg"
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        {errorMsg && (
          <div className="p-3 bg-error-soft border border-error/30 rounded text-xs text-error-dark flex items-center gap-2">
            <ShieldAlert className="w-4 h-4 shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div>
            <label className="text-xs font-bold font-sans uppercase tracking-wider text-text block mb-1">
              Mã hồ sơ (Profile ID)
            </label>
            <input
              type="text"
              value={profile.id}
              disabled
              className="w-full p-2.5 bg-surface-subtle border border-border rounded text-xs font-mono text-text-muted cursor-not-allowed"
            />
          </div>
          <Input
            label="Tên hiển thị *"
            placeholder="VD: Thi Lập trình C Nâng cao"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            required
          />
        </div>

        <Input
          label="Mô tả chính sách"
          placeholder="Mô tả ngắn gọn mục đích và phạm vi áp dụng..."
          value={description}
          onChange={(e) => setDescription(e.target.value)}
        />

        <div className="space-y-3 pt-1 border-t border-border-subtle">
          <div>
            <label className="text-xs font-bold font-sans uppercase tracking-wider text-text block mb-1">
              Ứng dụng cho phép chạy (Allowlist)
            </label>
            <textarea
              rows={2}
              className="w-full p-2.5 bg-surface border border-border rounded text-xs font-mono text-text focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="vscode.exe, gcc.exe, gdb.exe"
              value={allowedApps}
              onChange={(e) => setAllowedApps(e.target.value)}
            />
            <span className="text-[11px] text-text-muted">Ngăn cách bởi dấu phẩy hoặc xuống dòng.</span>
          </div>

          <div>
            <label className="text-xs font-bold font-sans uppercase tracking-wider text-text block mb-1">
              Ứng dụng cấm chạy (Denylist)
            </label>
            <textarea
              rows={2}
              className="w-full p-2.5 bg-surface border border-border rounded text-xs font-mono text-text focus:outline-none focus:ring-1 focus:ring-primary"
              placeholder="chatgpt.exe, anydesk.exe, teamviewer.exe"
              value={deniedApps}
              onChange={(e) => setDeniedApps(e.target.value)}
            />
          </div>

          <div className="space-y-2 bg-surface-subtle p-3 rounded border border-border">
            <span className="text-xs font-bold font-sans uppercase tracking-wider text-text block mb-1.5">
              Quy tắc mạng & Thiết bị
            </span>
            <label className="flex items-center gap-2.5 text-xs text-text cursor-pointer select-none">
              <input
                type="checkbox"
                checked={blockAi}
                onChange={(e) => setBlockAi(e.target.checked)}
                className="rounded border-border text-primary focus:ring-primary h-4 w-4"
              />
              <span>Chặn Generative AI (ChatGPT, Claude, Copilot,...)</span>
            </label>
            <label className="flex items-center gap-2.5 text-xs text-text cursor-pointer select-none">
              <input
                type="checkbox"
                checked={blockSocial}
                onChange={(e) => setBlockSocial(e.target.checked)}
                className="rounded border-border text-primary focus:ring-primary h-4 w-4"
              />
              <span>Chặn Mạng xã hội (Facebook, Telegram, Discord,...)</span>
            </label>
            <label className="flex items-center gap-2.5 text-xs text-text cursor-pointer select-none">
              <input
                type="checkbox"
                checked={usbBlocked}
                onChange={(e) => setUsbBlocked(e.target.checked)}
                className="rounded border-border text-primary focus:ring-primary h-4 w-4"
              />
              <span>Khóa cổng USB lưu trữ (USB Deny)</span>
            </label>
          </div>
        </div>

        <div className="pt-3 border-t border-border flex items-center justify-end gap-2.5">
          <Button type="button" variant="secondary" size="sm" onClick={onClose}>
            Hủy
          </Button>
          <Button type="submit" variant="primary" size="sm" isLoading={isLoading}>
            Cập nhật chính sách
          </Button>
        </div>
      </form>
    </Modal>
  );
};
