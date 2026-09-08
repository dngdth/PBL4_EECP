import React, { useState } from 'react';
import { Monitor, CheckCircle2, AlertTriangle, XCircle, RefreshCw, Clock, Network, Cpu, ShieldCheck, Terminal } from 'lucide-react';
import { Modal } from '@/src/shared/ui/modal';
import { Button } from '@/src/shared/ui/button';
import { WorkstationStatusBadge, PreflightStatusBadge } from './WorkstationStatusBadge';
import { retryWorkstationPreflight } from '../services/workstationApi';
import { Workstation, ExamSession } from '@/src/domain';
import { formatDateTime, formatRelativeTime } from '@/src/shared/lib/formatters';
import { UI_LABELS } from '@/src/shared/config/labels';

export interface WorkstationInspectModalProps {
  isOpen: boolean;
  onClose: () => void;
  workstation: Workstation | null;
  sessionId: string;
  onWorkstationUpdated: (updatedSession: ExamSession) => void;
  onViewCommandQueue?: (workstationId: string) => void;
}

export const WorkstationInspectModal: React.FC<WorkstationInspectModalProps> = ({
  isOpen,
  onClose,
  workstation,
  sessionId,
  onWorkstationUpdated,
  onViewCommandQueue,
}) => {
  const [isRetrying, setIsRetrying] = useState<boolean>(false);
  const [feedback, setFeedback] = useState<string | null>(null);

  if (!workstation) return null;

  const handleRetry = async () => {
    setIsRetrying(true);
    setFeedback(null);
    try {
      const res = await retryWorkstationPreflight(sessionId, workstation.id);
      setFeedback('Kiểm tra tiền kiểm lại hoàn tất thành công.');
      onWorkstationUpdated(res.session);
    } catch (err: any) {
      setFeedback(`Kiểm tra lại thất bại: ${err.message}`);
    } finally {
      setIsRetrying(false);
    }
  };

  const details = workstation.preflight_details;

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`Kiểm tra máy trạm — ${workstation.id}`}
      description={`Dữ liệu giám sát Agent & Chẩn đoán tiền kiểm`}
      maxWidth="md"
    >
      <div className="space-y-4">
        {feedback && (
          <div className="p-3 bg-success-soft border border-success/20 rounded-sm text-xs text-success-dark flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 shrink-0" />
            <span>{feedback}</span>
          </div>
        )}

        {/* Primary Status Card */}
        <div className="grid grid-cols-2 gap-3 p-3.5 bg-surface-subtle border border-border rounded-sm">
          <div>
            <div className="text-xs text-text-muted font-sans uppercase font-bold tracking-wider">Trạng thái máy trạm</div>
            <div className="mt-1.5">
              <WorkstationStatusBadge status={workstation.status} size="md" />
            </div>
          </div>
          <div>
            <div className="text-xs text-text-muted font-sans uppercase font-bold tracking-wider">Xác thực tiền kiểm</div>
            <div className="mt-1.5">
              <PreflightStatusBadge status={workstation.preflight_status} size="md" />
            </div>
          </div>
        </div>

        {/* Diagnostic Spec Items */}
        <div className="space-y-2.5">
          <h4 className="text-xs sm:text-sm font-sans font-bold uppercase tracking-wider text-text">Ma trận chẩn đoán tiền kiểm</h4>
          
          <div className="border border-border rounded-sm divide-y divide-border-subtle bg-surface text-xs sm:text-[13px] font-sans">
            <div className="flex items-center justify-between p-3">
              <div className="flex items-center gap-2.5">
                <ShieldCheck className="w-4 h-4 text-primary shrink-0" />
                <div>
                  <span className="font-bold text-text">Chế độ Kiosk & Khóa HĐH</span>
                  <p className="text-xs text-text-muted font-sans mt-0.5">Chặn chuyển ứng dụng, clipboard, phím tắt</p>
                </div>
              </div>
              {details?.os_lockdown ? (
                <span className="text-success-dark font-bold text-xs flex items-center gap-1">
                  <CheckCircle2 className="w-4 h-4" /> Đạt
                </span>
              ) : (
                <span className="text-text-muted font-bold text-xs flex items-center gap-1">
                  <Clock className="w-4 h-4" /> Đang chờ
                </span>
              )}
            </div>

            <div className="flex items-center justify-between p-3">
              <div className="flex items-center gap-2.5">
                <Network className="w-4 h-4 text-primary shrink-0" />
                <div>
                  <span className="font-bold text-text">Cô lập tường lửa mạng</span>
                  <p className="text-xs text-text-muted font-sans mt-0.5">Chỉ kết nối máy chủ thi được cấp phép</p>
                </div>
              </div>
              {details?.network_firewall ? (
                <span className="text-success-dark font-bold text-xs flex items-center gap-1">
                  <CheckCircle2 className="w-4 h-4" /> Đạt
                </span>
              ) : (
                <span className="text-text-muted font-bold text-xs flex items-center gap-1">
                  <Clock className="w-4 h-4" /> Đang chờ
                </span>
              )}
            </div>

            <div className="flex items-center justify-between p-3">
              <div className="flex items-center gap-2.5">
                <Cpu className="w-4 h-4 text-primary shrink-0" />
                <div>
                  <span className="font-bold text-text">Tình trạng Agent Daemon</span>
                  <p className="text-xs text-text-muted font-sans mt-0.5">Nhịp tim Agent và giám sát tiến trình</p>
                </div>
              </div>
              {details?.agent_health ? (
                <span className="text-success-dark font-bold text-xs flex items-center gap-1">
                  <CheckCircle2 className="w-4 h-4" /> Hoạt động tốt
                </span>
              ) : (
                <span className="text-error font-bold text-xs flex items-center gap-1">
                  <XCircle className="w-4 h-4" /> Không phản hồi
                </span>
              )}
            </div>

            <div className="flex items-center justify-between p-3">
              <div className="flex items-center gap-2.5">
                <Monitor className="w-4 h-4 text-primary shrink-0" />
                <div>
                  <span className="font-bold text-text">Toàn vẹn màn hình & ngoại vi</span>
                  <p className="text-xs text-text-muted font-sans mt-0.5">Kiểm tra màn hình đơn, không thiết bị trái phép</p>
                </div>
              </div>
              {details?.peripheral_check ? (
                <span className="text-success-dark font-bold text-xs flex items-center gap-1">
                  <CheckCircle2 className="w-4 h-4" /> Đạt
                </span>
              ) : (
                <span className="text-warning-dark font-bold text-xs flex items-center gap-1">
                  <AlertTriangle className="w-4 h-4" /> Cảnh báo
                </span>
              )}
            </div>
          </div>
        </div>

        {details?.notes && (
          <div className="p-3.5 bg-surface-subtle border border-border rounded-sm text-xs sm:text-[13px] font-sans">
            <span className="font-bold text-text">Ghi chú chẩn đoán Agent:</span>
            <p className="text-text-muted mt-1 font-sans">{details.notes}</p>
          </div>
        )}

        {/* Machine Metadata Info */}
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-xs text-text-muted bg-surface-subtle p-3.5 rounded-sm border border-border">
          <div>Địa chỉ IP: <span className="text-text font-mono font-semibold">{workstation.ip}</span></div>
          <div>Phiên bản Agent: <span className="text-text font-mono">{workstation.agent_version}</span></div>
          <div>Nhịp tim gần nhất: <span className="text-text font-sans">{formatRelativeTime(workstation.last_heartbeat)}</span></div>
          <div>Báo cáo lúc: <span className="text-text font-mono">{formatDateTime(workstation.last_heartbeat)}</span></div>
        </div>

        {/* Actions */}
        <div className="flex items-center justify-between pt-2 border-t border-border-subtle gap-2 flex-wrap">
          <div className="flex items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleRetry}
              isLoading={isRetrying}
              leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
            >
              Chạy lại tiền kiểm
            </Button>

            {onViewCommandQueue && (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => onViewCommandQueue(workstation.id)}
                leftIcon={<Terminal className="w-3.5 h-3.5" />}
              >
                Hàng đợi lệnh
              </Button>
            )}
          </div>

          <Button type="button" variant="secondary" size="sm" onClick={onClose}>
            {UI_LABELS.actions.close}
          </Button>
        </div>
      </div>
    </Modal>
  );
};
