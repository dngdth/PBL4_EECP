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
import { PolicyConfig, SessionStatus, Workstation } from '@/src/domain';
import { Button } from '@/src/shared/ui/button';
import { formatDateTime, formatRelativeTime } from '@/src/shared/lib/formatters';
import { UI_LABELS } from '@/src/shared/config/labels';
import { cn } from '@/src/shared/lib/cn';

export interface PolicySummaryCardProps {
  policy?: PolicyConfig;
  status: SessionStatus;
  gatewayId?: string | null;
  gatewayPolicyHash?: string | null;
  workstations?: Workstation[];
  onDeployClick?: () => void;
  canDeploy?: boolean;
}

export const PolicySummaryCard: React.FC<PolicySummaryCardProps> = ({
  policy,
  status,
  gatewayId,
  gatewayPolicyHash,
  workstations,
  onDeployClick,
  canDeploy = true,
}) => {
  const [showTechnicalDetails, setShowTechnicalDetails] = useState<boolean>(false);

  const totalWorkstations = workstations?.length ?? 0;
  const desiredPolicyHash = policy?.policy_id;

  // STRICT: Count only workstations where Backend recorded actual_policy_hash matches desired policy
  // NEVER use active_policy_hash (presence heartbeat) for ACK SUCCEEDED
  const ackedWorkstations =
    totalWorkstations > 0 && desiredPolicyHash
      ? workstations!.filter(
          (ws) =>
            (ws.actual_policy_hash && ws.actual_policy_hash === desiredPolicyHash) ||
            ws.policy_compliant === true
        ).length
      : 0;

  const isGatewayRequired = Boolean(gatewayId);
  const isGatewayAcked =
    !isGatewayRequired ||
    (Boolean(gatewayPolicyHash) && Boolean(desiredPolicyHash) && gatewayPolicyHash === desiredPolicyHash);

  const isAllWorkstationsAcked = totalWorkstations > 0 && ackedWorkstations === totalWorkstations;
  const isFullyEnforced = isAllWorkstationsAcked && isGatewayAcked;
  const isTerminatedOrRestoring = ['FINISHED', 'RESTORING', 'NORMAL'].includes(status);

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
                title={policy.network_lockdown ? 'Đang chặn danh mục AI, MXH, VPN qua file hosts' : 'Cảnh báo: Danh mục web cấm chưa bị chặn'}
              >
                {policy.network_lockdown ? (
                  <Check className="w-3 h-3 text-success stroke-[2.5]" />
                ) : (
                  <AlertTriangle className="w-3 h-3 text-warning stroke-[2]" />
                )}
                <span>{UI_LABELS.policy.blockedWeb}</span>
              </span>

              <span
                className={cn(
                  'inline-flex items-center gap-1 px-2 py-0.5 rounded-xs text-[11px] font-semibold border transition-colors',
                  policy.usb_storage_blocked
                    ? 'bg-success-soft border-success/30 text-success-dark'
                    : 'bg-warning-soft border-warning/30 text-warning-dark'
                )}
                title={policy.usb_storage_blocked ? 'Đã vô hiệu hóa lưu trữ USB' : 'Cảnh báo: Cổng USB chưa bị khóa'}
              >
                {policy.usb_storage_blocked ? (
                  <Check className="w-3 h-3 text-success stroke-[2.5]" />
                ) : (
                  <AlertTriangle className="w-3 h-3 text-warning stroke-[2]" />
                )}
                <span>{UI_LABELS.policy.blockUsb}</span>
              </span>

              <span
                className={cn(
                  'inline-flex items-center gap-1 px-2 py-0.5 rounded-xs text-[11px] font-semibold border transition-colors',
                  policy.strict_mode && (policy.denied_processes?.length ?? 0) > 0
                    ? 'bg-success-soft border-success/30 text-success-dark'
                    : 'bg-surface-subtle border-border text-text-muted'
                )}
                title={
                  policy.strict_mode && (policy.denied_processes?.length ?? 0) > 0
                    ? `Tự động quét và tắt ${policy.denied_processes!.length} ứng dụng trong danh sách cấm`
                    : 'Chưa kích hoạt tự động tắt ứng dụng cấm (danh sách cấm rỗng)'
                }
              >
                {policy.strict_mode && (policy.denied_processes?.length ?? 0) > 0 && (
                  <Check className="w-3 h-3 text-success stroke-[2.5]" />
                )}
                <span>{UI_LABELS.policy.blockedApps}</span>
              </span>
            </div>

            {/* Confirmation Status & Action Chi tiết */}
            <div className="pt-3 border-t border-border-subtle flex items-center justify-between text-xs">
              <div>
                {!policy ? (
                  <span className="text-text-muted font-medium">
                    {UI_LABELS.policy.notDeployed}
                  </span>
                ) : status === 'FINISHED' ? (
                  <span className="font-semibold text-text-muted">
                    Ca thi đã kết thúc (FINISHED)
                  </span>
                ) : status === 'RESTORING' ? (
                  <span
                    className="inline-flex items-center gap-1.5 font-semibold text-warning"
                    title="Hệ thống đang phục hồi cấu hình ban đầu trên máy trạm"
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-warning animate-pulse" />
                    <span>Đang khôi phục hệ thống (RESTORING)</span>
                  </span>
                ) : status === 'NORMAL' ? (
                  <span className="font-semibold text-text-muted">
                    Chế độ bình thường (NORMAL)
                  </span>
                ) : status === 'DEGRADED' ? (
                  <span
                    className="inline-flex items-center gap-1 font-semibold text-error"
                    title={`Lỗi áp dụng chính sách (${totalWorkstations - ackedWorkstations}/${totalWorkstations} máy trạm chưa đạt)`}
                  >
                    <AlertTriangle className="w-3.5 h-3.5 text-error stroke-[2]" />
                    <span>
                      {UI_LABELS.policy.degraded}
                      {totalWorkstations > 0 ? ` (${ackedWorkstations}/${totalWorkstations})` : ''}
                    </span>
                  </span>
                ) : totalWorkstations > 0 && isFullyEnforced ? (
                  <span
                    className="inline-flex items-center gap-1 font-semibold text-success"
                    title={`Toàn bộ ${totalWorkstations} máy trạm${isGatewayRequired ? ' và Gateway' : ''} đã xác nhận áp dụng chính sách [${policy.policy_id.slice(0, 8)}...]`}
                  >
                    <Check className="w-3.5 h-3.5 text-success stroke-[2.5]" />
                    <span>{UI_LABELS.policy.ackSucceeded} ({ackedWorkstations}/${totalWorkstations})</span>
                  </span>
                ) : totalWorkstations > 0 && isAllWorkstationsAcked && !isGatewayAcked ? (
                  <span
                    className="inline-flex items-center gap-1.5 font-semibold text-primary"
                    title={`Tất cả máy trạm đã xác nhận, đang chờ xác nhận từ Gateway`}
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
                    <span>Chờ Gateway xác nhận (AWAITING ACK) ({ackedWorkstations}/${totalWorkstations})</span>
                  </span>
                ) : totalWorkstations > 0 && ackedWorkstations > 0 ? (
                  <span
                    className="inline-flex items-center gap-1.5 font-semibold text-primary"
                    title={`Đang chờ các máy trạm còn lại xác nhận chính sách (${ackedWorkstations}/${totalWorkstations} máy đã xác nhận)`}
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
                    <span>{UI_LABELS.policy.awaitingAck} ({ackedWorkstations}/${totalWorkstations})</span>
                  </span>
                ) : status === 'DEPLOYING' || (totalWorkstations > 0 && ackedWorkstations === 0 && status !== 'CREATED') ? (
                  <span
                    className="inline-flex items-center gap-1.5 font-semibold text-primary"
                    title={`Lệnh đang phát đến Gateway & máy trạm, đang chờ xác nhận (0/${totalWorkstations} máy trạm)`}
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
                    <span>{UI_LABELS.policy.awaitingAck} (0/{totalWorkstations})</span>
                  </span>
                ) : status === 'CREATED' ? (
                  <span
                    className="inline-flex items-center gap-1.5 font-semibold text-warning"
                    title="Chính sách đã được lưu vào hàng đợi, chờ chuyển phát"
                  >
                    <span className="w-1.5 h-1.5 rounded-full bg-warning animate-pulse" />
                    <span>{UI_LABELS.policy.queued}</span>
                  </span>
                ) : (
                  <span
                    className="inline-flex items-center gap-1 font-semibold text-text-muted"
                    title="Chưa gán máy trạm cho ca thi"
                  >
                    <span>Chưa gán máy trạm</span>
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
                    {UI_LABELS.policy.allowedProcesses} ({policy.allowed_processes.length}):
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

                {policy.denied_processes && policy.denied_processes.length > 0 && (
                  <div className="pt-1">
                    <span className="text-[11px] font-bold text-text-muted block mb-1">
                      {UI_LABELS.policy.deniedProcesses} ({policy.denied_processes.length}):
                    </span>
                    <div className="flex flex-wrap gap-1">
                      {policy.denied_processes.map((proc) => (
                        <span
                          key={proc}
                          className="px-1.5 py-0.5 bg-error-soft border border-error/20 text-[11px] font-sans font-medium text-error-dark rounded"
                        >
                          {proc}
                        </span>
                      ))}
                    </div>
                  </div>
                )}

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


