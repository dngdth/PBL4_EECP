import React, { useState } from 'react';
import { 
  Check, 
  X, 
  ArrowRight, 
  Clock, 
  Info,
  Server,
  Pencil,
  Send,
  ShieldCheck,
  AlertTriangle
} from 'lucide-react';
import { PolicyConfig, SessionStatus } from '@/src/domain';
import { Button } from '@/src/shared/ui/button';
import { formatDateTime, formatRelativeTime } from '@/src/shared/lib/formatters';
import { UI_LABELS } from '@/src/shared/config/labels';
import { cn } from '@/src/shared/lib/cn';

export interface PolicySummaryCardProps {
  policy?: PolicyConfig;
  status: SessionStatus;
  gatewayId?: string;
  onDeployClick?: () => void;
  canDeploy?: boolean;
}

export const PolicySummaryCard: React.FC<PolicySummaryCardProps> = ({
  policy,
  status,
  gatewayId,
  onDeployClick,
  canDeploy = true,
}) => {
  const [showTechnicalDetails, setShowTechnicalDetails] = useState<boolean>(false);

  const isDeployed = Boolean(policy);
  const isDeploying = status === 'DEPLOYING';

  return (
    <div className="bg-surface border border-border rounded-sm shadow-2xs overflow-hidden flex flex-col">
      {/* Header */}
      <div className="px-4 py-2.5 bg-surface-subtle border-b border-border-subtle flex items-center justify-between">
        <h3 className="text-xs uppercase tracking-wider font-bold text-text-muted">
          {UI_LABELS.policy.title}
        </h3>

        {canDeploy && onDeployClick && policy && (
          <button
            type="button"
            onClick={onDeployClick}
            title="Chỉnh sửa chính sách"
            className="flex items-center gap-1 text-[11px] font-sans font-medium text-text-muted hover:text-text cursor-pointer transition-colors"
          >
            <Pencil className="w-3 h-3" />
            <span>Chỉnh sửa</span>
          </button>
        )}
      </div>

      {/* Main Content */}
      <div className="p-4 space-y-3 font-sans">
        {policy ? (
          <>
            {/* Primary Policy Name */}
            <div className="flex items-start justify-between gap-2">
              <div className="font-sans font-bold text-sm text-text truncate">
                {policy.name || 'Chính sách bảo mật phòng'}
              </div>
              <span className="text-[11px] text-text-muted shrink-0">
                {policy.allowed_processes.length} ứng dụng
              </span>
            </div>

            {/* Deployed At Metadata */}
            {policy.deployed_at && (
              <div className="text-xs text-text-muted flex items-center gap-1">
                <span>Áp dụng lúc {formatDateTime(policy.deployed_at)}</span>
              </div>
            )}

            {/* Inline Guard Rules Status - Uses Theme Tokens with High Contrast */}
            <div className="flex items-center gap-1.5 text-xs font-medium pt-0.5">
              <span
                className={cn(
                  'inline-flex items-center gap-1 px-2 py-0.5 rounded-xs text-[11px] font-semibold border transition-colors',
                  policy.network_lockdown
                    ? 'bg-success-soft border-success/30 text-success-dark'
                    : 'bg-warning-soft border-warning/30 text-warning-dark'
                )}
                title={policy.network_lockdown ? 'Đã kích hoạt tường lửa cô lập mạng' : 'Cảnh báo: Mạng đang mở, chưa cô lập'}
              >
                {policy.network_lockdown ? (
                  <Check className="w-3 h-3 text-success stroke-[2.5]" />
                ) : (
                  <AlertTriangle className="w-3 h-3 text-warning stroke-[2]" />
                )}
                <span>Khóa mạng</span>
              </span>

              <span
                className={cn(
                  'inline-flex items-center gap-1 px-2 py-0.5 rounded-xs text-[11px] font-semibold border transition-colors',
                  policy.usb_storage_blocked
                    ? 'bg-success-soft border-success/30 text-success-dark'
                    : 'bg-warning-soft border-warning/30 text-warning-dark'
                )}
                title={policy.usb_storage_blocked ? 'Đã khóa toàn bộ cổng USB' : 'Cảnh báo: Cổng USB chưa bị chặn'}
              >
                {policy.usb_storage_blocked ? (
                  <Check className="w-3 h-3 text-success stroke-[2.5]" />
                ) : (
                  <AlertTriangle className="w-3 h-3 text-warning stroke-[2]" />
                )}
                <span>Khóa USB</span>
              </span>

              <span
                className={cn(
                  'inline-flex items-center gap-1 px-2 py-0.5 rounded-xs text-[11px] font-semibold border transition-colors',
                  policy.strict_mode
                    ? 'bg-success-soft border-success/30 text-success-dark'
                    : 'bg-surface-subtle border-border text-text-muted'
                )}
                title={policy.strict_mode ? 'Chế độ an ninh nghiêm ngặt tối đa' : 'Chế độ an ninh tiêu chuẩn'}
              >
                {policy.strict_mode && <Check className="w-3 h-3 text-success stroke-[2.5]" />}
                <span>Nghiêm ngặt</span>
              </span>
            </div>

            {/* Confirmation Status & Action Chi tiết */}
            <div className="pt-3 border-t border-border-subtle flex items-center justify-between text-xs">
              <div>
                {isDeploying ? (
                  <span className="inline-flex items-center gap-1.5 font-semibold text-primary">
                    <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
                    <span>{UI_LABELS.policy.dispatching}</span>
                  </span>
                ) : isDeployed ? (
                  <span className="inline-flex items-center gap-1 font-semibold text-success">
                    <Check className="w-3.5 h-3.5 text-success stroke-[2.5]" />
                    <span>{UI_LABELS.policy.acknowledged}</span>
                  </span>
                ) : (
                  <span className="text-text-muted font-medium">
                    {UI_LABELS.policy.notDeployed}
                  </span>
                )}
              </div>

              <button
                type="button"
                onClick={() => setShowTechnicalDetails(!showTechnicalDetails)}
                className="inline-flex items-center gap-1 text-text-muted hover:text-text font-medium cursor-pointer transition-colors"
              >
                <span>{showTechnicalDetails ? 'Thu gọn' : 'Chi tiết'}</span>
                <ArrowRight className={cn('w-3.5 h-3.5 transition-transform', showTechnicalDetails && 'rotate-90')} />
              </button>
            </div>

            {/* Collapsible Technical Details */}
            {showTechnicalDetails && (
              <div className="pt-2.5 border-t border-border-subtle space-y-2 text-xs">
                <div>
                  <span className="text-[11px] font-bold text-text-muted block mb-1">
                    Danh sách ứng dụng cho phép chạy ({policy.allowed_processes.length}):
                  </span>
                  <div className="flex flex-wrap gap-1">
                    {policy.allowed_processes.map((proc) => (
                      <span
                        key={proc}
                        className="px-1.5 py-0.5 bg-surface-subtle border border-border-subtle text-[11px] font-sans font-medium text-text rounded"
                      >
                        {proc}
                      </span>
                    ))}
                  </div>
                </div>

                {canDeploy && onDeployClick && (
                  <div className="pt-2">
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={onDeployClick}
                      leftIcon={<Pencil className="w-3 h-3" />}
                      className="w-full justify-center"
                    >
                      Chỉnh sửa chính sách
                    </Button>
                  </div>
                )}
              </div>
            )}
          </>
        ) : (
          /* Unconfigured Policy State */
          <div className="py-2 space-y-2">
            <div className="text-text-muted text-xs">
              {UI_LABELS.policy.noPolicyDesc}
            </div>

            {canDeploy && onDeployClick && (
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={onDeployClick}
                leftIcon={<Send className="w-3 h-3" />}
                className="w-full justify-center"
              >
                {UI_LABELS.session.deployPolicy}
              </Button>
            )}
          </div>
        )}
      </div>
    </div>
  );
};


