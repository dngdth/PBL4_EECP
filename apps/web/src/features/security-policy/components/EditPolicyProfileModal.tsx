import React, { useState, useEffect } from 'react';
import { ShieldAlert } from 'lucide-react';
import { Modal } from '@/src/shared/ui/modal';
import { Button } from '@/src/shared/ui/button';
import { Input } from '@/src/shared/ui/input';
import { PolicyProfile, UpdatePolicyProfileInput } from '@/src/domain';
import { updatePolicyProfile } from '../services/policyApi';
import {
  PolicyProfileFormFields,
  formatPolicyProfileError,
  parseProcessList,
} from './PolicyProfileFormFields';

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

    const blockNetList: string[] = [];
    if (blockAi) blockNetList.push('generative_ai');
    if (blockSocial) blockNetList.push('social_network');

    const payload: UpdatePolicyProfileInput = {
      label: label.trim(),
      description: description.trim() || `Chính sách ${label.trim()}`,
      rules: {
        applications: {
          allow: parseProcessList(allowedApps),
          deny: parseProcessList(deniedApps),
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
      setErrorMsg(
        formatPolicyProfileError(
          err?.message,
          'Không thể cập nhật chính sách. Vui lòng kiểm tra lại dữ liệu.'
        )
      );
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
              Mã hồ sơ
            </label>
            <input
              type="text"
              value={profile.id}
              disabled
              className="w-full p-2.5 bg-surface-subtle border border-border rounded text-xs font-mono text-text-muted cursor-not-allowed"
            />
          </div>
          <Input
            label="Tên hiển thị"
            placeholder="VD: Thi Lập trình C Nâng cao"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            required
          />
        </div>

        <PolicyProfileFormFields
          description={description}
          onDescriptionChange={setDescription}
          allowedApps={allowedApps}
          onAllowedAppsChange={setAllowedApps}
          deniedApps={deniedApps}
          onDeniedAppsChange={setDeniedApps}
          blockAi={blockAi}
          onBlockAiChange={setBlockAi}
          blockSocial={blockSocial}
          onBlockSocialChange={setBlockSocial}
          usbBlocked={usbBlocked}
          onUsbBlockedChange={setUsbBlocked}
        />

        <div className="pt-3 border-t border-border flex items-center justify-end gap-2.5">
          <Button type="button" variant="secondary" size="sm" onClick={onClose}>
            Hủy
          </Button>
          <Button
            type="submit"
            variant="primary"
            size="sm"
            isLoading={isLoading}
            className="font-bold bg-primary hover:bg-primary-dark text-surface shadow-2xs"
          >
            Cập nhật chính sách
          </Button>
        </div>
      </form>
    </Modal>
  );
};
