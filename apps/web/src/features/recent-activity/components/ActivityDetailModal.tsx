import React, { useState } from 'react';
import { 
  CheckCircle2, 
  AlertTriangle, 
  Info, 
  ShieldAlert, 
  Clock, 
  Server, 
  Router, 
  Monitor, 
  ShieldCheck, 
  UserCheck, 
  Copy, 
  Check, 
  FileCode2, 
  HelpCircle, 
  ArrowRight,
  ExternalLink,
  Tag
} from 'lucide-react';
import { ActivityItem, Workstation } from '@/src/domain';
import { Modal } from '@/src/shared/ui/modal';
import { Button } from '@/src/shared/ui/button';
import { formatDateTime, formatRelativeTime } from '@/src/shared/lib/formatters';
import { cn } from '@/src/shared/lib/cn';

export interface ActivityDetailModalProps {
  activity: ActivityItem | null;
  isOpen: boolean;
  onClose: () => void;
  onInspectWorkstation?: (workstationId: string) => void;
}

export const ActivityDetailModal: React.FC<ActivityDetailModalProps> = ({
  activity,
  isOpen,
  onClose,
  onInspectWorkstation,
}) => {
  const [copiedJson, setCopiedJson] = useState(false);

  if (!activity) return null;

  const getSourceInfo = (source: string) => {
    const s = source.toUpperCase();
    if (s.includes('GATEWAY')) {
      return {
        label: 'Gateway phòng thi',
        badgeClass: 'bg-warning-soft text-warning-dark border-warning/30',
        icon: <Router className="w-4 h-4 text-warning-dark" />,
      };
    }
    if (s.includes('AGENT') || s.includes('WORKSTATION') || s.includes('PC')) {
      return {
        label: 'Agent máy trạm',
        badgeClass: 'bg-surface-subtle text-text border-border',
        icon: <Monitor className="w-4 h-4 text-text" />,
      };
    }
    if (s.includes('PREFLIGHT') || s.includes('SECURITY')) {
      return {
        label: 'Tiền kiểm an ninh',
        badgeClass: 'bg-success-soft text-success-dark border-success/30',
        icon: <ShieldCheck className="w-4 h-4 text-success-dark" />,
      };
    }
    if (s.includes('PROCTOR')) {
      return {
        label: 'Giám thị phòng máy',
        badgeClass: 'bg-surface-subtle text-text-muted border-border',
        icon: <UserCheck className="w-4 h-4 text-text-muted" />,
      };
    }
    return {
      label: 'Hệ thống máy chủ',
      badgeClass: 'bg-primary-soft text-primary border-primary/20',
      icon: <Server className="w-4 h-4 text-primary" />,
    };
  };

  const getLevelBadge = (level: ActivityItem['level']) => {
    switch (level) {
      case 'SUCCESS':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-success-soft text-success-dark border border-success/30 font-sans text-xs font-bold uppercase tracking-wider">
            <CheckCircle2 className="w-3.5 h-3.5" />
            <span>Thành công</span>
          </span>
        );
      case 'WARNING':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-warning-soft text-warning-dark border border-warning/40 font-sans text-xs font-bold uppercase tracking-wider">
            <AlertTriangle className="w-3.5 h-3.5" />
            <span>Cảnh báo</span>
          </span>
        );
      case 'ERROR':
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-error-soft text-error-dark border border-error/30 font-sans text-xs font-bold uppercase tracking-wider">
            <ShieldAlert className="w-3.5 h-3.5" />
            <span>Lỗi nghiêm trọng</span>
          </span>
        );
      default:
        return (
          <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-surface-subtle text-text border border-border font-sans text-xs font-bold uppercase tracking-wider">
            <Info className="w-3.5 h-3.5 text-primary" />
            <span>Thông tin</span>
          </span>
        );
    }
  };

  const sourceInfo = getSourceInfo(activity.source);
  
  // Extract or synthesize structured details
  const eventCode = activity.details?.code || `EVT_${activity.source.toUpperCase()}_${activity.level}`;
  const payloadData = activity.details?.payload || {
    id: activity.id,
    source: activity.source,
    level: activity.level,
    message: activity.message,
    timestamp: activity.timestamp,
  };
  const remediation = activity.details?.remediation || (
    activity.level === 'WARNING' || activity.level === 'ERROR'
      ? 'Kiểm tra máy trạm phát sinh sự cố, khắc phục các kết nối bất thường và tiến hành chạy lại tiền kiểm.'
      : 'Sự kiện vận hành thông thường được ghi nhận và lưu trữ vào sổ nhật ký giám thị.'
  );

  // Check if a workstation ID is referenced in payload or message (e.g. PC03)
  const workstationIdMatch = typeof payloadData === 'object' && payloadData?.workstation_id
    ? payloadData.workstation_id
    : activity.message.match(/PC\d+/i)?.[0];

  const handleCopy = () => {
    const jsonStr = typeof payloadData === 'string' ? payloadData : JSON.stringify(payloadData, null, 2);
    navigator.clipboard.writeText(jsonStr);
    setCopiedJson(true);
    setTimeout(() => setCopiedJson(false), 2000);
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title="Chi tiết sự kiện & Nhật ký hoạt động"
      description={`Mã sự kiện: ${eventCode} • Ghi nhận tại thời điểm ${formatDateTime(activity.timestamp)}`}
      maxWidth="lg"
    >
      <div className="space-y-4 font-sans text-xs">
        {/* Top Status & Meta Grid */}
        <div className="p-3.5 bg-surface-subtle rounded-sm border border-border flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="p-2 rounded bg-surface border border-border shadow-2xs">
              {sourceInfo.icon}
            </div>
            <div>
              <div className="flex items-center gap-2">
                <span className="font-bold text-xs text-text">{sourceInfo.label}</span>
                <span className={cn('px-1.5 py-0.5 rounded text-[10px] font-mono font-bold uppercase border', sourceInfo.badgeClass)}>
                  {activity.source}
                </span>
              </div>
              <div className="text-[11px] text-text-muted flex items-center gap-1 mt-0.5 font-mono">
                <Clock className="w-3 h-3 text-text-subtle" />
                <span>{activity.timestamp}</span>
                <span>({formatRelativeTime(activity.timestamp)})</span>
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {getLevelBadge(activity.level)}
          </div>
        </div>

        {/* Primary Message Card */}
        <div className="p-3.5 bg-surface rounded-sm border border-border shadow-2xs space-y-1.5">
          <span className="text-[10px] font-bold uppercase tracking-wider text-text-muted block">
            Nội dung thông báo / Thông điệp ghi nhận:
          </span>
          <p className="text-sm font-semibold text-text leading-relaxed">
            {activity.message}
          </p>
        </div>

        {/* Workstation Quick Action Banner (if event is linked to a workstation) */}
        {workstationIdMatch && onInspectWorkstation && (
          <div className="p-3 bg-warning-soft/50 border border-warning/40 rounded-sm flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <Monitor className="w-4 h-4 text-warning-dark shrink-0" />
              <span className="font-sans text-xs text-text">
                Sự kiện này liên quan trực tiếp đến máy trạm{' '}
                <strong className="font-mono font-bold text-text px-1.5 py-0.5 bg-surface rounded border border-warning/40">
                  {workstationIdMatch}
                </strong>
              </span>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                onClose();
                onInspectWorkstation(workstationIdMatch);
              }}
              rightIcon={<ArrowRight className="w-3.5 h-3.5" />}
              className="shrink-0 font-bold border-warning/50 text-warning-dark hover:bg-warning/10"
            >
              Kiểm tra {workstationIdMatch}
            </Button>
          </div>
        )}

        {/* Event Details Card (Human Readable) */}
        <div className="p-3.5 bg-surface rounded-sm border border-border space-y-2.5">
          <span className="text-[10px] font-bold uppercase tracking-wider text-text-muted block">
            Thông tin chi tiết sự kiện:
          </span>
          
          <div className="grid grid-cols-2 gap-3 text-xs">
            <div>
              <span className="text-text-muted block text-[11px]">Nguồn phát sinh:</span>
              <span className="font-semibold text-text">{sourceInfo.label}</span>
            </div>
            <div>
              <span className="text-text-muted block text-[11px]">Mức độ an ninh:</span>
              <span className="font-semibold text-text">{activity.level === 'SUCCESS' ? 'Bình thường' : activity.level === 'WARNING' ? 'Cần lưu ý' : 'Nghiêm trọng'}</span>
            </div>
            <div>
              <span className="text-text-muted block text-[11px]">Thời gian ghi nhận:</span>
              <span className="font-semibold text-text">{formatDateTime(activity.timestamp)}</span>
            </div>
            {workstationIdMatch && (
              <div>
                <span className="text-text-muted block text-[11px]">Máy trạm liên quan:</span>
                {onInspectWorkstation ? (
                  <button
                    type="button"
                    onClick={() => {
                      onClose();
                      onInspectWorkstation(workstationIdMatch);
                    }}
                    className="font-mono font-bold text-primary hover:underline hover:text-primary-dark inline-flex items-center gap-1 cursor-pointer transition-colors"
                    title={`Bấm để mở kiểm tra máy ${workstationIdMatch}`}
                  >
                    <span>{workstationIdMatch}</span>
                    <span className="text-[10px] font-sans font-medium px-1 bg-primary/10 text-primary rounded">Xem →</span>
                  </button>
                ) : (
                  <span className="font-mono font-bold text-primary">{workstationIdMatch}</span>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Remediation Guide */}
        {remediation && (
          <div className="p-3.5 bg-surface-subtle rounded-sm border border-border-subtle space-y-1.5">
            <div className="flex items-center gap-1.5 text-primary font-bold text-xs uppercase tracking-wider">
              <HelpCircle className="w-4 h-4 shrink-0" />
              <span>Khuyến nghị xử lý cho giám thị:</span>
            </div>
            <p className="text-text font-normal leading-relaxed text-xs whitespace-pre-line pl-4 border-l-2 border-primary bg-surface p-2.5 rounded">
              {remediation}
            </p>
          </div>
        )}

        {/* Footer Actions */}
        <div className="flex items-center justify-end pt-3 border-t border-border-subtle">
          <Button type="button" variant="primary" size="sm" onClick={onClose}>
            Đóng cửa sổ
          </Button>
        </div>
      </div>
    </Modal>
  );
};
