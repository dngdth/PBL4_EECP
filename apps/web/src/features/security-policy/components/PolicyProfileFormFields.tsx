import React from 'react';
import { Input } from '@/src/shared/ui/input';

export const parseProcessList = (raw: string): string[] =>
  raw
    .split(/[\n,;]+/)
    .map((s) => s.trim().toLowerCase())
    .filter(Boolean);

export const formatPolicyProfileError = (
  raw?: string,
  fallback = 'Không thể lưu chính sách. Vui lòng kiểm tra lại dữ liệu.'
): string => {
  const msg = (raw || '').trim();
  const lower = msg.toLowerCase();
  if (!msg) return fallback;
  if (lower.includes('already exists') || lower.includes('conflict')) {
    return 'Mã hồ sơ đã tồn tại. Vui lòng chọn mã khác.';
  }
  if (lower.includes('forbidden') || lower.includes('permission') || lower.includes('admin')) {
    return 'Bạn không có quyền thực hiện thao tác này.';
  }
  if (lower.includes('not found')) {
    return 'Không tìm thấy hồ sơ chính sách.';
  }
  if (lower.includes('failed to fetch') || lower.includes('network')) {
    return 'Không thể kết nối tới máy chủ. Vui lòng thử lại.';
  }
  return msg;
};

export interface PolicyProfileFormFieldsProps {
  description: string;
  onDescriptionChange: (value: string) => void;
  allowedApps: string;
  onAllowedAppsChange: (value: string) => void;
  deniedApps: string;
  onDeniedAppsChange: (value: string) => void;
  blockAi: boolean;
  onBlockAiChange: (checked: boolean) => void;
  blockSocial: boolean;
  onBlockSocialChange: (checked: boolean) => void;
  usbBlocked: boolean;
  onUsbBlockedChange: (checked: boolean) => void;
}

export const PolicyProfileFormFields: React.FC<PolicyProfileFormFieldsProps> = ({
  description,
  onDescriptionChange,
  allowedApps,
  onAllowedAppsChange,
  deniedApps,
  onDeniedAppsChange,
  blockAi,
  onBlockAiChange,
  blockSocial,
  onBlockSocialChange,
  usbBlocked,
  onUsbBlockedChange,
}) => (
  <>
    <Input
      label="Mô tả chính sách"
      placeholder="Mô tả ngắn gọn mục đích và phạm vi áp dụng..."
      value={description}
      onChange={(e) => onDescriptionChange(e.target.value)}
    />

    <div className="space-y-3 pt-1 border-t border-border-subtle">
      <div>
        <label className="text-xs font-bold font-sans uppercase tracking-wider text-text block mb-1">
          Ứng dụng cho phép chạy
        </label>
        <textarea
          rows={2}
          className="w-full p-2.5 bg-surface border border-border rounded text-xs font-mono text-text focus:outline-none focus:ring-1 focus:ring-primary"
          placeholder="vscode.exe, gcc.exe, gdb.exe"
          value={allowedApps}
          onChange={(e) => onAllowedAppsChange(e.target.value)}
        />
        <span className="text-[11px] text-text-muted">Ngăn cách bởi dấu phẩy hoặc xuống dòng.</span>
      </div>

      <div>
        <label className="text-xs font-bold font-sans uppercase tracking-wider text-text block mb-1">
          Ứng dụng cấm chạy
        </label>
        <textarea
          rows={2}
          className="w-full p-2.5 bg-surface border border-border rounded text-xs font-mono text-text focus:outline-none focus:ring-1 focus:ring-primary"
          placeholder="chatgpt.exe, anydesk.exe, teamviewer.exe"
          value={deniedApps}
          onChange={(e) => onDeniedAppsChange(e.target.value)}
        />
        <span className="text-[11px] text-text-muted">Ngăn cách bởi dấu phẩy hoặc xuống dòng.</span>
      </div>

      <div className="space-y-2 bg-surface-subtle p-3 rounded border border-border">
        <span className="text-xs font-bold font-sans uppercase tracking-wider text-text block mb-1.5">
          Quy tắc mạng & Thiết bị
        </span>
        <label className="flex items-center gap-2.5 text-xs text-text cursor-pointer select-none">
          <input
            type="checkbox"
            checked={blockAi}
            onChange={(e) => onBlockAiChange(e.target.checked)}
            className="rounded border-border text-primary focus:ring-primary h-4 w-4"
          />
          <span>Chặn công cụ AI (ChatGPT, Claude, Copilot,...)</span>
        </label>
        <label className="flex items-center gap-2.5 text-xs text-text cursor-pointer select-none">
          <input
            type="checkbox"
            checked={blockSocial}
            onChange={(e) => onBlockSocialChange(e.target.checked)}
            className="rounded border-border text-primary focus:ring-primary h-4 w-4"
          />
          <span>Chặn Mạng xã hội (Facebook, Telegram, Discord,...)</span>
        </label>
        <label className="flex items-center gap-2.5 text-xs text-text cursor-pointer select-none">
          <input
            type="checkbox"
            checked={usbBlocked}
            onChange={(e) => onUsbBlockedChange(e.target.checked)}
            className="rounded border-border text-primary focus:ring-primary h-4 w-4"
          />
          <span>Khóa cổng USB lưu trữ</span>
        </label>
      </div>
    </div>
  </>
);

