import React, { useState } from 'react';
import { ShieldCheck, ShieldAlert, Lock, Check, FileCode, ChevronDown, ChevronUp, Info, Plus } from 'lucide-react';
import { PolicyProfile } from '@/src/domain';
import { cn } from '@/src/shared/lib/cn';
import { Badge } from '@/src/shared/ui/badge';
import { Button } from '@/src/shared/ui/button';

export interface PolicyProfileSelectorProps {
  profiles: PolicyProfile[];
  selectedProfileId: string;
  onSelectProfile: (profile: PolicyProfile) => void;
  isLoading?: boolean;
  onOpenCreateModal?: () => void;
}

export const PolicyProfileSelector: React.FC<PolicyProfileSelectorProps> = ({
  profiles,
  selectedProfileId,
  onSelectProfile,
  isLoading = false,
  onOpenCreateModal,
}) => {
  const [expandedYamlId, setExpandedYamlId] = useState<string | null>(null);

  const selectedProfile = profiles.find((p) => p.id === selectedProfileId);

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <label className="text-xs font-sans font-bold uppercase tracking-wider text-text flex items-center gap-1.5">
          <ShieldCheck className="w-4 h-4 text-primary" />
          <span>Chính sách bảo mật (Policy Profile) *</span>
        </label>
        {onOpenCreateModal && (
          <button
            type="button"
            onClick={onOpenCreateModal}
            className="text-xs text-primary hover:text-primary-dark font-medium flex items-center gap-1 cursor-pointer transition-colors"
          >
            <Plus className="w-3.5 h-3.5" />
            <span>Tạo profile mới</span>
          </button>
        )}
      </div>

      {isLoading ? (
        <div className="p-4 bg-surface-subtle border border-border rounded text-xs text-text-muted animate-pulse">
          Đang tải danh mục chính sách từ máy chủ...
        </div>
      ) : profiles.length === 0 ? (
        <div className="p-4 bg-warning-soft border border-warning/30 rounded text-xs text-warning-dark">
          Chưa có cấu hình chính sách nào trên máy chủ.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {profiles.map((profile) => {
            const isSelected = profile.id === selectedProfileId;
            const allowedApps = profile.rules?.applications?.allow || [];
            const deniedApps = profile.rules?.applications?.deny || [];
            const blockedNet = profile.rules?.network?.block || [];
            const isUsbDenied = profile.rules?.devices?.usb === 'deny';
            const isYamlOpen = expandedYamlId === profile.id;

            return (
              <div
                key={profile.id}
                onClick={() => onSelectProfile(profile)}
                className={cn(
                  'relative p-3.5 rounded border transition-all cursor-pointer select-none flex flex-col justify-between gap-3 text-left',
                  isSelected
                    ? 'border-primary bg-primary-soft/30 ring-1 ring-primary/40 shadow-xs'
                    : 'border-border bg-surface hover:border-border-subtle hover:bg-surface-subtle/50'
                )}
              >
                <div>
                  <div className="flex items-start justify-between gap-2 mb-1.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-bold text-sm text-text font-sans">{profile.label}</span>
                      {profile.is_builtin ? (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-surface-subtle text-text-muted border border-border">
                          MẶC ĐỊNH
                        </span>
                      ) : (
                        <span className="px-1.5 py-0.5 rounded text-[10px] font-bold bg-primary-soft text-primary border border-primary/20">
                          TÙY BIẾN
                        </span>
                      )}
                    </div>
                    {isSelected && (
                      <div className="w-5 h-5 rounded-full bg-primary text-surface flex items-center justify-center shrink-0">
                        <Check className="w-3.5 h-3.5 stroke-[3]" />
                      </div>
                    )}
                  </div>

                  <p className="text-xs text-text-muted leading-relaxed line-clamp-2 mb-2">
                    {profile.description || 'Không có mô tả chi tiết.'}
                  </p>

                  {/* Rule summary pills */}
                  <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-medium">
                    {allowedApps.length > 0 && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-success-soft text-success-dark border border-success/20">
                        <Check className="w-3 h-3" />
                        <span>{allowedApps.length} app cho phép</span>
                      </span>
                    )}

                    {blockedNet.length > 0 && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-warning-soft text-warning-dark border border-warning/20">
                        <Lock className="w-3 h-3" />
                        <span>Chặn AI & Mạng</span>
                      </span>
                    )}

                    {isUsbDenied && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-error-soft text-error-dark border border-error/20">
                        <Lock className="w-3 h-3" />
                        <span>Khóa USB</span>
                      </span>
                    )}
                  </div>
                </div>

                {/* YAML Preview toggle */}
                {profile.yaml && (
                  <div className="pt-2 border-t border-border-subtle/80 flex items-center justify-between text-[11px]">
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        setExpandedYamlId(isYamlOpen ? null : profile.id);
                      }}
                      className="text-text-muted hover:text-text flex items-center gap-1 cursor-pointer transition-colors"
                    >
                      <FileCode className="w-3.5 h-3.5 text-primary" />
                      <span>{isYamlOpen ? 'Ẩn cấu hình YAML' : 'Xem cấu hình YAML'}</span>
                      {isYamlOpen ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                    </button>
                    <span className="font-mono text-[10px] text-text-subtle font-semibold">{profile.id}</span>
                  </div>
                )}

                {isYamlOpen && profile.yaml && (
                  <div
                    onClick={(e) => e.stopPropagation()}
                    className="mt-2 p-2.5 bg-surface-subtle border border-border rounded font-mono text-[11px] text-text leading-tight overflow-x-auto whitespace-pre select-text"
                  >
                    {profile.yaml}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
