import React, { useState } from 'react';
import { Monitor, Plus, Trash2, CheckCircle2, AlertCircle, Wifi, WifiOff, Check } from 'lucide-react';
import { Agent } from '@/src/domain';
import { cn } from '@/src/shared/lib/cn';
import { Button } from '@/src/shared/ui/button';

export interface AgentSelectorProps {
  agents: Agent[];
  selectedWorkstationIds: string[];
  onChange: (ids: string[]) => void;
  isLoading?: boolean;
}

export const AgentSelector: React.FC<AgentSelectorProps> = ({
  agents,
  selectedWorkstationIds,
  onChange,
  isLoading = false,
}) => {
  const [newWsInput, setNewWsInput] = useState<string>('');

  const onlineAgents = agents.filter((a) => a.status === 'ONLINE');

  const handleToggle = (id: string) => {
    if (selectedWorkstationIds.includes(id)) {
      onChange(selectedWorkstationIds.filter((w) => w !== id));
    } else {
      onChange([...selectedWorkstationIds, id]);
    }
  };

  const handleSelectAllOnline = () => {
    const onlineIds = onlineAgents.map((a) => a.id);
    onChange(Array.from(new Set([...selectedWorkstationIds, ...onlineIds])));
  };

  const handleSelectAll = () => {
    const allIds = agents.map((a) => a.id);
    onChange(Array.from(new Set([...selectedWorkstationIds, ...allIds])));
  };

  const handleClearAll = () => {
    onChange([]);
  };

  const handleAddManual = () => {
    const trimmed = newWsInput.trim().toUpperCase();
    if (!trimmed) return;
    if (!selectedWorkstationIds.includes(trimmed)) {
      onChange([...selectedWorkstationIds, trimmed]);
    }
    setNewWsInput('');
  };

  const handleQuickAddBatch = (prefix: string, count: number) => {
    const batch = Array.from({ length: count }, (_, i) => {
      const num = String(i + 1).padStart(2, '0');
      return `${prefix}${num}`;
    });
    onChange(Array.from(new Set([...selectedWorkstationIds, ...batch])));
  };

  return (
    <div className="space-y-4">
      {/* Header with Quick Actions */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <label className="text-xs font-sans font-bold uppercase tracking-wider text-text flex items-center gap-1.5">
            <Monitor className="w-4 h-4 text-primary" />
            <span>Danh sách máy trạm tham gia thi ({selectedWorkstationIds.length}) *</span>
          </label>
        </div>

        <div className="flex items-center gap-1.5 flex-wrap">
          {agents.length > 0 && (
            <>
              {onlineAgents.length > 0 && (
                <button
                  type="button"
                  onClick={handleSelectAllOnline}
                  className="px-2 py-1 bg-success-soft text-success-dark hover:bg-success-soft/80 border border-success/30 rounded text-[11px] font-bold cursor-pointer transition-colors"
                >
                  Chọn {onlineAgents.length} máy Online
                </button>
              )}
              <button
                type="button"
                onClick={handleSelectAll}
                className="px-2 py-1 bg-surface-subtle hover:bg-border/40 border border-border rounded text-[11px] font-medium text-text cursor-pointer transition-colors"
              >
                Chọn tất cả Agent
              </button>
            </>
          )}

          <button
            type="button"
            onClick={() => handleQuickAddBatch('PC', 12)}
            className="px-2 py-1 bg-surface-subtle hover:bg-border/40 border border-border rounded text-[11px] font-medium text-text cursor-pointer transition-colors"
          >
            +12 PC mẫu
          </button>
          <button
            type="button"
            onClick={() => handleQuickAddBatch('PC', 24)}
            className="px-2 py-1 bg-surface-subtle hover:bg-border/40 border border-border rounded text-[11px] font-medium text-text cursor-pointer transition-colors"
          >
            +24 PC mẫu
          </button>
          {selectedWorkstationIds.length > 0 && (
            <button
              type="button"
              onClick={handleClearAll}
              className="px-2 py-1 text-error-dark hover:bg-error-soft/50 rounded text-[11px] font-medium cursor-pointer transition-colors"
            >
              Xóa hết
            </button>
          )}
        </div>
      </div>

      {/* Real Agents Grid (if registered on backend) */}
      {isLoading ? (
        <div className="p-3 bg-surface-subtle border border-border rounded text-xs text-text-muted animate-pulse">
          Đang kết nối danh sách máy trạm (Agent Daemon)...
        </div>
      ) : agents.length > 0 ? (
        <div className="space-y-2">
          <div className="text-[11px] text-text-muted flex items-center justify-between font-sans">
            <span>Máy trạm đã cài Agent phát hiện trên hệ thống ({agents.length}):</span>
            <span className="font-semibold text-success">{onlineAgents.length} Online</span>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2 max-h-[220px] overflow-y-auto p-2 bg-surface-subtle/40 border border-border rounded">
            {agents.map((agent) => {
              const isSelected = selectedWorkstationIds.includes(agent.id);
              const isOnline = agent.status === 'ONLINE';

              return (
                <div
                  key={agent.id}
                  onClick={() => handleToggle(agent.id)}
                  className={cn(
                    'p-2 rounded border transition-all cursor-pointer select-none text-left flex flex-col justify-between gap-1',
                    isSelected
                      ? 'border-primary bg-primary-soft/40 ring-1 ring-primary/40'
                      : 'border-border bg-surface hover:bg-surface-subtle'
                  )}
                >
                  <div className="flex items-center justify-between gap-1">
                    <span className="font-bold text-xs font-mono text-text">{agent.id}</span>
                    <span
                      className={cn(
                        'w-2 h-2 rounded-full',
                        isOnline ? 'bg-success animate-pulse' : 'bg-text-subtle'
                      )}
                      title={isOnline ? 'Agent trực tuyến' : 'Agent ngoại tuyến'}
                    />
                  </div>
                  <span className="text-[10px] text-text-muted font-mono truncate">
                    {agent.ip_address || agent.hostname || 'No IP'}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {/* Manual Input Bar */}
      <div className="flex items-center gap-2">
        <input
          type="text"
          placeholder="Nhập mã máy thủ công (VD: PC01, LAB02-PC15) và nhấn Enter..."
          value={newWsInput}
          onChange={(e) => setNewWsInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              handleAddManual();
            }
          }}
          className="flex-1 p-2 bg-surface border border-border rounded text-xs text-text font-mono focus:outline-none focus:ring-1 focus:ring-primary"
        />
        <Button type="button" variant="secondary" size="sm" onClick={handleAddManual}>
          <Plus className="w-3.5 h-3.5" />
          <span>Thêm</span>
        </Button>
      </div>

      {/* Selected Tags Display */}
      {selectedWorkstationIds.length > 0 ? (
        <div className="flex flex-wrap gap-1.5 p-3 bg-surface border border-border rounded max-h-[160px] overflow-y-auto">
          {selectedWorkstationIds.map((id) => (
            <span
              key={id}
              className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-surface-subtle border border-border rounded text-xs font-mono font-semibold text-text"
            >
              <span>{id}</span>
              <button
                type="button"
                onClick={() => onChange(selectedWorkstationIds.filter((w) => w !== id))}
                className="text-text-muted hover:text-error cursor-pointer transition-colors"
                title="Xóa máy"
              >
                <Trash2 className="w-3 h-3" />
              </button>
            </span>
          ))}
        </div>
      ) : (
        <div className="p-4 bg-surface-subtle border border-dashed border-border rounded text-center text-xs text-text-muted">
          Chưa chọn máy trạm nào. Hãy chọn từ danh sách Agent hoặc bấm "+12 PC mẫu".
        </div>
      )}
    </div>
  );
};
