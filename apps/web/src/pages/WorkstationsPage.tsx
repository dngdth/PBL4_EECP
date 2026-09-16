import React, { useState, useEffect, useMemo, useCallback } from 'react';
import { 
  Monitor, 
  RefreshCw, 
  AlertCircle, 
  CheckCircle2, 
  XCircle, 
  Search, 
  Shield, 
  Clock 
} from 'lucide-react';
import { 
  Workstation, 
  Agent 
} from '@/src/domain';
import { 
  listAgents, 
  WorkstationGrid, 
  WorkstationInspectModal, 
  CommandQueueModal 
} from '@/src/features/workstations';
import { Button } from '@/src/shared/ui/button';
import { Spinner } from '@/src/shared/ui/spinner';
import { cn } from '@/src/shared/lib/cn';
import { UI_LABELS } from '@/src/shared/config/labels';
import { formatRelativeTime } from '@/src/shared/lib/formatters';

export const WorkstationsPage: React.FC = () => {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Inspection & Modals
  const [inspectingWorkstation, setInspectingWorkstation] = useState<Workstation | null>(null);
  const [commandQueueTargetId, setCommandQueueTargetId] = useState<string | null>(null);

  const fetchAgentData = useCallback(async (silent = false) => {
    if (!silent) setIsRefreshing(true);
    setErrorMsg(null);
    try {
      const data = await listAgents();
      setAgents(data);
    } catch (err: any) {
      setErrorMsg(err.message || 'Không thể tải danh sách máy trạm từ hệ thống.');
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, []);

  useEffect(() => {
    fetchAgentData(false);
  }, [fetchAgentData]);

  // Convert Agent to Workstation ViewModel for Grid
  const workstations: Workstation[] = useMemo(() => {
    return agents.map((agent) => {
      const isOnline = agent.status === 'ONLINE';
      return {
        id: agent.id,
        ip: agent.ip_address || '127.0.0.1',
        status: isOnline ? 'READY' : 'FAILED',
        preflight_status: isOnline ? 'PASSED' : 'FAILED',
        preflight_details: {
          os_lockdown: isOnline,
          network_firewall: isOnline,
          agent_health: isOnline,
          peripheral_check: isOnline,
          notes: isOnline ? 'Máy trạm đang trực tuyến và sẵn sàng.' : 'Máy trạm đang ngoại tuyến.',
        },
        last_heartbeat: agent.last_seen || new Date().toISOString(),
        agent_version: agent.agent_version || 'v1.0.0',
      };
    });
  }, [agents]);

  const onlineCount = useMemo(() => agents.filter((a) => a.status === 'ONLINE').length, [agents]);
  const offlineCount = useMemo(() => agents.filter((a) => a.status !== 'ONLINE').length, [agents]);

  return (
    <div className="w-full max-w-[1600px] mx-auto p-4 sm:p-6 lg:p-8 space-y-6 flex-1 min-w-0">
      {/* 1. Header & Summary Banner */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-border pb-4">
        <div>
          <div className="flex items-center gap-2 text-xs font-mono text-text-muted uppercase tracking-wider">
            <span>Hạ tầng phòng máy</span>
            <span>/</span>
            <span className="text-primary font-bold">Máy trạm Agent</span>
          </div>
          <h1 className="text-xl sm:text-2xl font-sans font-bold text-text mt-1 flex items-center gap-2.5">
            <Monitor className="w-6 h-6 text-primary" />
            Giám sát Máy trạm & Agent
          </h1>
          <p className="text-xs sm:text-sm text-text-muted mt-0.5">
            Quản lý và giám sát trạng thái nhịp tim thời gian thực của các máy trạm trong phòng máy.
          </p>
        </div>

        <div className="flex items-center gap-2 self-start sm:self-auto">
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => fetchAgentData(false)}
            isLoading={isRefreshing}
            leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
          >
            Làm mới
          </Button>
        </div>
      </div>

      {/* 2. Metric Cards */}
      <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
        <div className="bg-surface border border-border rounded-sm p-4 shadow-2xs flex items-center justify-between">
          <div>
            <div className="text-xs font-mono text-text-muted uppercase tracking-wider">Tổng máy trạm</div>
            <div className="text-2xl font-bold font-mono text-text mt-1">{agents.length}</div>
          </div>
          <div className="w-10 h-10 rounded-full bg-surface-subtle border border-border flex items-center justify-center text-text-muted">
            <Monitor className="w-5 h-5" />
          </div>
        </div>

        <div className="bg-surface border border-border rounded-sm p-4 shadow-2xs flex items-center justify-between">
          <div>
            <div className="text-xs font-mono text-text-muted uppercase tracking-wider">Trực tuyến (Online)</div>
            <div className="text-2xl font-bold font-mono text-success mt-1">{onlineCount}</div>
          </div>
          <div className="w-10 h-10 rounded-full bg-success-soft border border-success/30 flex items-center justify-center text-success-dark">
            <CheckCircle2 className="w-5 h-5" />
          </div>
        </div>

        <div className="bg-surface border border-border rounded-sm p-4 shadow-2xs flex items-center justify-between">
          <div>
            <div className="text-xs font-mono text-text-muted uppercase tracking-wider">Ngoại tuyến (Offline)</div>
            <div className="text-2xl font-bold font-mono text-error mt-1">{offlineCount}</div>
          </div>
          <div className="w-10 h-10 rounded-full bg-error-soft border border-error/30 flex items-center justify-center text-error-dark">
            <XCircle className="w-5 h-5" />
          </div>
        </div>
      </div>

      {/* 3. Error Banner */}
      {errorMsg && (
        <div className="p-4 bg-error-soft border border-error/30 rounded-sm text-xs text-error-dark flex items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{errorMsg}</span>
          </div>
          <Button
            type="button"
            variant="outline"
            size="xs"
            onClick={() => fetchAgentData(false)}
          >
            Thử lại
          </Button>
        </div>
      )}

      {/* 4. Content Area */}
      {isLoading ? (
        <div className="flex flex-col items-center justify-center p-16 bg-surface border border-border rounded-sm">
          <Spinner size="lg" className="text-primary" />
          <p className="text-xs text-text-muted mt-3 font-sans">Đang truy vấn danh sách máy trạm từ hệ thống...</p>
        </div>
      ) : workstations.length === 0 ? (
        <div className="text-center p-12 bg-surface border border-border rounded-sm space-y-3">
          <Monitor className="w-12 h-12 text-text-muted mx-auto opacity-40" />
          <h3 className="font-bold text-text text-sm">Chưa phát hiện máy trạm nào</h3>
          <p className="text-xs text-text-muted max-w-md mx-auto">
            Không tìm thấy bản ghi Agent nào trong cơ sở dữ liệu. Hãy khởi động dịch vụ Agent trên các máy trạm sinh viên để bắt đầu kết nối.
          </p>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => fetchAgentData(false)}
            leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
          >
            Kiểm tra lại
          </Button>
        </div>
      ) : (
        <div className="h-[640px]">
          <WorkstationGrid
            workstations={workstations}
            onInspectWorkstation={(ws) => setInspectingWorkstation(ws)}
            onRefresh={() => fetchAgentData(false)}
          />
        </div>
      )}

      {/* 5. Modals */}
      {inspectingWorkstation && (
        <WorkstationInspectModal
          isOpen={Boolean(inspectingWorkstation)}
          onClose={() => setInspectingWorkstation(null)}
          workstation={inspectingWorkstation}
          onViewCommandQueue={(wsId) => {
            setCommandQueueTargetId(wsId);
          }}
        />
      )}

      {commandQueueTargetId && (
        <CommandQueueModal
          isOpen={Boolean(commandQueueTargetId)}
          onClose={() => setCommandQueueTargetId(null)}
          targetId={commandQueueTargetId}
          targetType="WORKSTATION"
        />
      )}
    </div>
  );
};
