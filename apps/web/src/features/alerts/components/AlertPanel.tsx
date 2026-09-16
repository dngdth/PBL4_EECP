import React from 'react';
import { ShieldAlert, ArrowRight } from 'lucide-react';
import { Workstation, ExamSession } from '@/src/domain';

export interface AlertPanelProps {
  workstations?: Workstation[];
  session?: ExamSession | null;
  gatewayId?: string;
  onInspectWorkstation: (wsOrId: Workstation | string) => void;
}

export const AlertPanel: React.FC<AlertPanelProps> = ({
  workstations,
  session,
  onInspectWorkstation,
}) => {
  const wsList = workstations || session?.workstations || [];
  const warningWorkstations = wsList.filter((w) => w.status === 'WARNING');
  const failedWorkstations = wsList.filter((w) => w.status === 'FAILED');

  const totalAnomalies = warningWorkstations.length + failedWorkstations.length;

  if (totalAnomalies === 0) return null;

  return (
    <div className="p-4 sm:p-4.5 bg-warning-soft/50 border-2 border-warning/50 rounded shadow-xs space-y-3">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2 border-b border-warning/20 pb-2.5">
        <div className="flex items-center gap-2 text-warning-dark">
          <ShieldAlert className="w-4 h-4 shrink-0 text-warning-dark" />
          <h3 className="font-sans font-bold text-xs uppercase tracking-wider">
            Phát hiện {totalAnomalies} máy trạm cần chú ý ({failedWorkstations.length} lỗi, {warningWorkstations.length} cảnh báo)
          </h3>
        </div>
        <span className="text-[11px] text-text-muted font-sans">
          Hãy kiểm tra và chạy lại tiền kiểm trước khi phát đề
        </span>
      </div>

      <div className="flex flex-wrap gap-2.5 pt-0.5">
        {failedWorkstations.map((ws) => (
          <button
            key={ws.id}
            type="button"
            onClick={() => onInspectWorkstation(ws)}
            className="group flex items-center justify-between gap-3 px-3.5 py-2 rounded bg-surface hover:bg-error-soft/30 border-2 border-error text-error-dark shadow-xs hover:shadow transition-all duration-150 cursor-pointer active:scale-[0.99] text-left"
          >
            <div className="flex items-center gap-2">
              <span className="px-2 py-0.5 rounded bg-error text-surface font-mono text-xs font-bold shrink-0">
                {ws.id}
              </span>
              <span className="font-sans text-xs font-medium text-text">
                {ws.preflight_details?.notes || 'Lỗi kiểm tra tiền kiểm'}
              </span>
            </div>

            <div className="flex items-center gap-1 text-xs font-bold text-error underline underline-offset-4 group-hover:text-error-dark shrink-0 ml-2">
              <span>Khắc phục ngay</span>
              <ArrowRight className="w-3.5 h-3.5 group-hover:translate-x-1 transition-transform" />
            </div>
          </button>
        ))}

        {warningWorkstations.map((ws) => (
          <button
            key={ws.id}
            type="button"
            onClick={() => onInspectWorkstation(ws)}
            className="group flex items-center justify-between gap-3 px-3.5 py-2 rounded bg-surface hover:bg-warning-soft/40 border-2 border-warning/70 hover:border-warning text-text shadow-xs hover:shadow transition-all duration-150 cursor-pointer active:scale-[0.99] text-left"
          >
            <div className="flex items-center gap-2">
              <span className="px-2 py-0.5 rounded bg-warning/20 text-warning-dark border border-warning/40 font-mono text-xs font-bold shrink-0">
                {ws.id}
              </span>
              <span className="font-sans text-xs font-medium text-text">
                {ws.preflight_details?.notes || 'Cảnh báo bất thường'}
              </span>
            </div>

            <div className="flex items-center gap-1 text-xs font-bold text-primary underline underline-offset-4 group-hover:text-primary-dark shrink-0 ml-2">
              <span>Xem chi tiết</span>
              <ArrowRight className="w-3.5 h-3.5 group-hover:translate-x-1 transition-transform" />
            </div>
          </button>
        ))}
      </div>
    </div>
  );
};
