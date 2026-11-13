import React, { useEffect, useState } from 'react';
import { ShieldAlert } from 'lucide-react';
import { Modal } from '@/src/shared/ui/modal';
import { Button } from '@/src/shared/ui/button';
import { Input } from '@/src/shared/ui/input';
import { PolicyProfile, CreatePolicyProfileInput } from '@/src/domain';
import { createPolicyProfile } from '../services/policyApi';
import {
  PolicyProfileFormFields,
  formatPolicyProfileError,
  parseProcessList,
} from './PolicyProfileFormFields';

export interface CreatePolicyProfileModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSuccess: (newProfile: PolicyProfile) => void;
}

const DEFAULT_ALLOWED_APPS = 'vscode.exe, gcc.exe, gdb.exe';
const DEFAULT_DENIED_APPS = 'chatgpt.exe, anydesk.exe, teamviewer.exe';
const PROFILE_ID_REGEX = /^[A-Z0-9_-]+$/;

export const CreatePolicyProfileModal: React.FC<CreatePolicyProfileModalProps> = ({
  isOpen,
  onClose,
  onSuccess,
}) => {
  const [profileId, setProfileId] = useState<string>('');
  const [label, setLabel] = useState<string>('');
  const [description, setDescription] = useState<string>('');
  const [allowedApps, setAllowedApps] = useState<string>(DEFAULT_ALLOWED_APPS);
  const [deniedApps, setDeniedApps] = useState<string>(DEFAULT_DENIED_APPS);
  const [blockAi, setBlockAi] = useState<boolean>(true);
  const [blockSocial, setBlockSocial] = useState<boolean>(true);
  const [usbBlocked, setUsbBlocked] = useState<boolean>(true);

  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  useEffect(() => {
    if (isOpen) {
      setProfileId('');
      setLabel('');
      setDescription('');
      setAllowedApps(DEFAULT_ALLOWED_APPS);
      setDeniedApps(DEFAULT_DENIED_APPS);
      setBlockAi(true);
      setBlockSocial(true);
      setUsbBlocked(true);
      setErrorMsg(null);
    }
  }, [isOpen]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    const normalizedId = profileId.trim().toUpperCase();
    if (!normalizedId) {
      setErrorMsg('Vui lòng nhập Mã hồ sơ.');
      return;
    }
    if (!PROFILE_ID_REGEX.test(normalizedId)) {
      setErrorMsg('Mã hồ sơ chỉ được chứa chữ in hoa, số, dấu gạch dưới (_) hoặc gạch ngang (-).');
      return;
    }
    if (!label.trim()) {
      setErrorMsg('Vui lòng nhập Tên hiển thị của chính sách.');
      return;
    }

    setIsLoading(true);
    setErrorMsg(null);

    const blockNetList: string[] = [];
    if (blockAi) blockNetList.push('generative_ai');
    if (blockSocial) blockNetList.push('social_network');

    const payload: CreatePolicyProfileInput = {
      id: normalizedId,
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
      const created = await createPolicyProfile(payload);
      onSuccess(created);
      onClose();
    } catch (err: any) {
      setErrorMsg(
        formatPolicyProfileError(
          err?.message,
          'Không thể tạo chính sách. Vui lòng kiểm tra lại dữ liệu.'
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
      title="Tạo hồ sơ chính sách bảo mật mới"
      description="Định nghĩa bộ quy tắc quản trị ứng dụng, mạng và thiết bị lưu trữ trong hệ thống."
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
          <Input
            label="Mã hồ sơ"
            placeholder="VD: C_EXAM_RESTRICTED"
            value={profileId}
            onChange={(e) => setProfileId(e.target.value.toUpperCase().replace(/\s+/g, '_'))}
            required
          />
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
            Lưu & Thêm vào danh mục
          </Button>
        </div>
      </form>
    </Modal>
  );
};
