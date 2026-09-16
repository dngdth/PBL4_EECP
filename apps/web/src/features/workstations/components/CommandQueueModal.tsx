import React, { useEffect, useState } from 'react';
import { Terminal, RefreshCw, CheckCircle2, Clock, AlertTriangle, FileCode } from 'lucide-react';
import { CommandItem } from '@/src/domain';
import { Modal } from '@/src/shared/ui/modal';
import { Button } from '@/src/shared/ui/button';
import { Spinner } from '@/src/shared/ui/spinner';
import { cn } from '@/src/shared/lib/cn';
import { listTargetCommands } from '../services/workstationApi';

export interface CommandQueueModalProps {
  isOpen: boolean;
  onClose: () => void;
  targetId: string;
  targetType?: 'GATEWAY' | 'WORKSTATION';
}

export const CommandQueueModal: React.FC<CommandQueueModalProps> = ({
  isOpen,
  onClose,
  targetId,
  targetType = 'GATEWAY',
}) => {
  const [commands, setCommands] = useState<CommandItem[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const fetchCommands = async (silent = false) => {
    if (!targetId) return;
    if (!silent) setIsRefreshing(true);
    setErrorMsg(null);
    try {
      const data = await listTargetCommands(targetId);
      setCommands(data);
    } catch (err: any) {
      setErrorMsg(err.message || 'Không thể lấy hàng đợi lệnh của thiết bị.');
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    if (isOpen && targetId) {
      setIsLoading(true);
      fetchCommands(false);
    }
  }, [isOpen, targetId]);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={`Hàng đợi lệnh: ${targetId} (${targetType})`}
      description="Danh sách các bản tin chỉ thị điều phối môi trường được phát từ máy chủ xuống Gateway/Agent."
      maxWidth="lg"
    >
      <div className="space-y-4">
        <div className="flex items-center justify-between">
          <span className="text-xs text-text-muted font-sans">
            Tổng số lệnh: <strong className="text-text">{commands.length}</strong>
          </span>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => fetchCommands(false)}
            isLoading={isRefreshing}
            leftIcon={<RefreshCw className="w-3 h-3" />}
          >
            Làm mới
          </Button>
        </div>

        {errorMsg && (
          <div className="p-3 bg-error-soft border border-error/30 rounded text-xs text-error-dark">
            {errorMsg}
          </div>
        )}

        {isLoading ? (
          <div className="py-12 flex items-center justify-center">
            <Spinner size="md" label="Đang tải danh sách lệnh..." />
          </div>
        ) : commands.length === 0 ? (
          <div className="p-8 text-center bg-surface-subtle border border-dashed border-border rounded text-xs text-text-muted space-y-1">
            <Terminal className="w-8 h-8 text-text-subtle mx-auto mb-2" />
            <p className="font-semibold text-text">Không có lệnh nào đang chờ</p>
            <p className="text-[11px]">Hàng đợi lệnh của thiết bị này hiện đang trống hoặc tất cả lệnh đã được xử lý.</p>
          </div>
        ) : (
          <div className="space-y-2.5 max-h-[380px] overflow-y-auto pr-1">
            {commands.map((cmd) => {
              const isAck = cmd.status === 'ACKNOWLEDGED';
              const isPending = cmd.status === 'PENDING';

              return (
                <div
                  key={cmd.id}
                  className="p-3 bg-surface-subtle border border-border rounded text-xs font-sans space-y-2"
                >
                  <div className="flex items-center justify-between gap-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono font-bold text-text">{cmd.type}</span>
                      <span
                        className={cn(
                          'px-2 py-0.5 rounded text-[10px] font-bold border',
                          isAck
                            ? 'bg-success-soft text-success-dark border-success/30'
                            : isPending
                              ? 'bg-warning-soft text-warning-dark border-warning/30 animate-pulse'
                              : 'bg-surface text-text-muted border-border'
                        )}
                      >
                        {cmd.status}
                      </span>
                    </div>
                    <span className="font-mono text-[10px] text-text-muted">
                      {new Date(cmd.created_at).toLocaleTimeString('vi-VN')}
                    </span>
                  </div>

                  <div className="grid grid-cols-2 gap-2 text-[11px] text-text-muted pt-1 border-t border-border/60">
                    <div>Mã lệnh: <span className="font-mono text-text font-semibold">{cmd.id}</span></div>
                    <div>Thử lại: <span className="font-mono text-text">{cmd.attempt_count ?? 0} lần</span></div>
                  </div>

                  {cmd.payload && Object.keys(cmd.payload).length > 0 && (
                    <div className="pt-1">
                      <span className="text-[10px] font-bold text-text-muted uppercase tracking-wider block mb-1">
                        Payload dữ liệu:
                      </span>
                      <pre className="p-2 bg-surface border border-border rounded font-mono text-[10px] text-text overflow-x-auto select-text leading-tight">
                        {JSON.stringify(cmd.payload, null, 2)}
                      </pre>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}

        <div className="flex justify-end pt-2 border-t border-border-subtle">
          <Button variant="secondary" size="sm" onClick={onClose}>
            Đóng
          </Button>
        </div>
      </div>
    </Modal>
  );
};
