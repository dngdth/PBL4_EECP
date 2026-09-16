import React, { useState, useMemo } from 'react';
import { 
  Search, 
  Monitor, 
  LayoutGrid, 
  List, 
  Shield, 
  Network, 
  Activity, 
  Cpu, 
  Clock, 
  ChevronRight,
  ArrowUpDown
} from 'lucide-react';
import { Workstation } from '@/src/domain';
import { WorkstationCard } from './WorkstationCard';
import { WorkstationStatusBadge } from './WorkstationStatusBadge';
import { cn } from '@/src/shared/lib/cn';
import { UI_LABELS } from '@/src/shared/config/labels';
import { formatRelativeTime } from '@/src/shared/lib/formatters';

export interface WorkstationGridProps {
  workstations: Workstation[];
  onInspectWorkstation: (ws: Workstation) => void;
  onRefresh?: () => void;
}

const PAGE_SIZE = 12;

export const WorkstationGrid: React.FC<WorkstationGridProps> = ({
  workstations = [],
  onInspectWorkstation,
}) => {
  const [search, setSearch] = useState<string>('');
  const [statusFilter, setStatusFilter] = useState<'ALL' | 'READY' | 'WARNING' | 'FAILED'>('ALL');
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const [currentPage, setCurrentPage] = useState<number>(1);

  // Status Counts
  const totalCount = workstations.length;
  const readyCount = useMemo(
    () => workstations.filter((ws) => ws.status === 'READY').length,
    [workstations]
  );
  const warningCount = useMemo(
    () => workstations.filter((ws) => ws.status === 'WARNING').length,
    [workstations]
  );
  const failedCount = useMemo(
    () => workstations.filter((ws) => ws.status === 'FAILED').length,
    [workstations]
  );

  // Filtered list
  const filtered = useMemo(() => {
    return workstations.filter((ws) => {
      const matchSearch =
        ws.id.toLowerCase().includes(search.toLowerCase()) ||
        ws.ip.toLowerCase().includes(search.toLowerCase());

      if (!matchSearch) return false;

      if (statusFilter === 'READY') return ws.status === 'READY';
      if (statusFilter === 'WARNING') return ws.status === 'WARNING';
      if (statusFilter === 'FAILED') return ws.status === 'FAILED';
      return true;
    });
  }, [workstations, search, statusFilter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const activePage = Math.min(currentPage, totalPages);
  const paginatedStations = filtered.slice((activePage - 1) * PAGE_SIZE, activePage * PAGE_SIZE);

  return (
    <div className="bg-surface border border-border rounded-sm flex flex-col h-full shadow-2xs">
      {/* Header */}
      <div className="px-4 sm:px-5 py-3 border-b border-border bg-surface flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Monitor className="w-4 h-4 text-primary shrink-0" />
          <h2 className="font-sans font-bold text-sm uppercase tracking-wider text-text">
            {UI_LABELS.workstation.title}
          </h2>
          <span className="text-xs font-mono font-bold px-2 py-0.5 rounded bg-surface-subtle border border-border text-text-muted">
            {totalCount} máy
          </span>
        </div>

        {/* Dynamic Metric Counts & Interactive Filter Pills */}
        <div className="flex flex-wrap items-center gap-1.5 text-xs font-sans font-semibold">
          <button
            onClick={() => {
              setStatusFilter('ALL');
              setCurrentPage(1);
            }}
            className={cn(
              'px-2.5 py-1 rounded border transition-colors cursor-pointer',
              statusFilter === 'ALL'
                ? 'bg-text text-surface border-text font-bold shadow-2xs'
                : 'bg-surface text-text-muted border-border hover:text-text hover:bg-surface-subtle'
            )}
          >
            <span>Tất cả ({totalCount})</span>
          </button>

          <button
            onClick={() => {
              setStatusFilter(statusFilter === 'READY' ? 'ALL' : 'READY');
              setCurrentPage(1);
            }}
            className={cn(
              'px-2.5 py-1 rounded border transition-colors cursor-pointer flex items-center gap-1.5',
              statusFilter === 'READY'
                ? 'bg-success text-surface border-success font-bold shadow-2xs'
                : 'bg-surface text-text-muted border-border hover:border-success/60 hover:text-success-dark'
            )}
          >
            <span className={cn('w-2 h-2 rounded-full', statusFilter === 'READY' ? 'bg-surface' : 'bg-success')} />
            <span>Sẵn sàng ({readyCount})</span>
          </button>

          <button
            onClick={() => {
              setStatusFilter(statusFilter === 'WARNING' ? 'ALL' : 'WARNING');
              setCurrentPage(1);
            }}
            className={cn(
              'px-2.5 py-1 rounded border transition-colors cursor-pointer flex items-center gap-1.5',
              statusFilter === 'WARNING'
                ? 'bg-warning text-surface border-warning font-bold shadow-2xs'
                : 'bg-surface text-text-muted border-border hover:border-warning/60 hover:text-warning-dark'
            )}
          >
            <span className={cn('w-2 h-2 rounded-full', statusFilter === 'WARNING' ? 'bg-surface' : 'bg-warning')} />
            <span>Cảnh báo ({warningCount})</span>
          </button>

          <button
            onClick={() => {
              setStatusFilter(statusFilter === 'FAILED' ? 'ALL' : 'FAILED');
              setCurrentPage(1);
            }}
            className={cn(
              'px-2.5 py-1 rounded border transition-colors cursor-pointer flex items-center gap-1.5',
              statusFilter === 'FAILED'
                ? 'bg-error text-surface border-error font-bold shadow-2xs'
                : 'bg-surface text-text-muted border-border hover:border-error/60 hover:text-error'
            )}
          >
            <span className={cn('w-2 h-2 rounded-full', statusFilter === 'FAILED' ? 'bg-surface' : 'bg-error')} />
            <span>Lỗi ({failedCount})</span>
          </button>
        </div>
      </div>

      {/* Filter / Search Subheader + View Switcher (Grid vs List) */}
      <div className="px-4 sm:px-5 py-2.5 border-b border-border-subtle bg-surface-subtle flex items-center justify-between gap-3">
        <div className="relative flex-1">
          <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-text-muted" />
          <input
            type="text"
            placeholder={UI_LABELS.workstations.searchPlaceholder}
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setCurrentPage(1);
            }}
            className="w-full pl-9 pr-3 py-1.5 text-xs sm:text-sm bg-surface text-text border border-border rounded focus:outline-none focus:ring-1 focus:ring-primary placeholder:text-text-muted font-sans"
          />
        </div>

        <div className="flex items-center gap-2">
          {statusFilter !== 'ALL' && (
            <button
              onClick={() => {
                setStatusFilter('ALL');
                setCurrentPage(1);
              }}
              className="text-xs font-sans font-bold text-primary hover:underline whitespace-nowrap cursor-pointer"
            >
              {UI_LABELS.workstations.clearFilter}
            </button>
          )}

          {/* View Mode Switcher (Lưới / Danh sách) */}
          <div className="flex items-center border border-border rounded bg-surface p-0.5 shadow-2xs">
            <button
              type="button"
              onClick={() => setViewMode('grid')}
              title="Chế độ lưới (Grid)"
              className={cn(
                'p-1.5 rounded text-xs font-medium transition-colors cursor-pointer flex items-center gap-1',
                viewMode === 'grid'
                  ? 'bg-text text-surface font-semibold shadow-2xs'
                  : 'text-text-muted hover:text-text'
              )}
            >
              <LayoutGrid className="w-3.5 h-3.5" />
              <span className="hidden md:inline text-[11px]">Lưới</span>
            </button>
            <button
              type="button"
              onClick={() => setViewMode('list')}
              title="Chế độ danh sách (List)"
              className={cn(
                'p-1.5 rounded text-xs font-medium transition-colors cursor-pointer flex items-center gap-1',
                viewMode === 'list'
                  ? 'bg-text text-surface font-semibold shadow-2xs'
                  : 'text-text-muted hover:text-text'
              )}
            >
              <List className="w-3.5 h-3.5" />
              <span className="hidden md:inline text-[11px]">Danh sách</span>
            </button>
          </div>
        </div>
      </div>

      {/* Main Content Area: Grid Mode vs List Mode */}
      <div className="p-3.5 sm:p-5 flex-1 bg-surface">
        {paginatedStations.length === 0 ? (
          <div className="py-12 text-center text-sm font-sans text-text-muted bg-surface-subtle border border-dashed border-border rounded">
            {UI_LABELS.workstations.noMatch}
          </div>
        ) : viewMode === 'grid' ? (
          /* Grid View */
          <div className="grid grid-cols-1 sm:grid-cols-2 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-3.5 sm:gap-4">
            {paginatedStations.map((ws) => (
              <WorkstationCard
                key={ws.id}
                workstation={ws}
                onInspect={onInspectWorkstation}
              />
            ))}
          </div>
        ) : (
          /* List View */
          <div className="border border-border rounded-sm overflow-hidden bg-surface">
            <div className="overflow-x-auto custom-scrollbar">
              <table className="w-full text-left text-xs font-sans">
                <thead className="bg-surface-subtle border-b border-border text-[11px] uppercase font-bold text-text-muted tracking-wider">
                  <tr>
                    <th className="py-2.5 px-3.5">Máy trạm</th>
                    <th className="py-2.5 px-3">Địa chỉ IP</th>
                    <th className="py-2.5 px-3">Tiền kiểm an ninh (Khóa HĐH / Mạng / Giám sát / Ngoại vi)</th>
                    <th className="py-2.5 px-3">Lần phản hồi gần nhất</th>
                    <th className="py-2.5 px-3">Trạng thái</th>
                    <th className="py-2.5 px-3 text-right">Thao tác</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-border-subtle">
                  {paginatedStations.map((ws) => {
                    const details = ws.preflight_details;
                    const isReady = ws.status === 'READY';
                    const isWarning = ws.status === 'WARNING';
                    const isFailed = ws.status === 'FAILED';

                    return (
                      <tr
                        key={ws.id}
                        onClick={() => onInspectWorkstation(ws)}
                        className="hover:bg-surface-subtle/80 cursor-pointer transition-colors group"
                      >
                        {/* Machine ID */}
                        <td className="py-3 px-3.5">
                          <div className="flex items-center gap-2.5">
                            <div
                              className={cn(
                                'w-6 h-6 rounded flex items-center justify-center shrink-0 border text-xs',
                                isReady && 'bg-emerald-50 border-emerald-200 text-emerald-600',
                                isWarning && 'bg-amber-50 border-amber-200 text-amber-600',
                                isFailed && 'bg-rose-50 border-rose-200 text-rose-600'
                              )}
                            >
                              <Monitor className="w-3.5 h-3.5" />
                            </div>
                            <div>
                              <div className="font-mono font-bold text-text text-xs flex items-center gap-1.5">
                                <span>{ws.id}</span>
                                <span
                                  className={cn(
                                    'w-1.5 h-1.5 rounded-full shrink-0 animate-pulse',
                                    isReady && 'bg-emerald-500',
                                    isWarning && 'bg-amber-500',
                                    isFailed && 'bg-rose-500'
                                  )}
                                />
                              </div>
                            </div>
                          </div>
                        </td>

                        {/* IP Address */}
                        <td className="py-3 px-3 font-mono text-text-muted text-xs">
                          {ws.ip}
                        </td>

                        {/* Preflight Checks (4 Micro Badges) */}
                        <td className="py-3 px-3">
                          <div className="flex items-center gap-1.5">
                            {/* OS */}
                            <span
                              className={cn(
                                'px-2 py-0.5 rounded text-[10px] font-sans font-semibold border',
                                details?.os_lockdown
                                  ? 'bg-success-soft border-success/30 text-success-dark'
                                  : 'bg-error-soft border-error/30 text-error-dark'
                              )}
                              title="Khóa hệ điều hành"
                            >
                              Khóa HĐH
                            </span>

                            {/* NET */}
                            <span
                              className={cn(
                                'px-2 py-0.5 rounded text-[10px] font-sans font-semibold border',
                                details?.network_firewall
                                  ? 'bg-success-soft border-success/30 text-success-dark'
                                  : 'bg-error-soft border-error/30 text-error-dark'
                              )}
                              title="Tường lửa cô lập mạng"
                            >
                              Mạng
                            </span>

                            {/* AGT */}
                            <span
                              className={cn(
                                'px-2 py-0.5 rounded text-[10px] font-sans font-semibold border',
                                details?.agent_health
                                  ? 'bg-success-soft border-success/30 text-success-dark'
                                  : 'bg-error-soft border-error/30 text-error-dark'
                              )}
                              title="Giám sát phòng thi"
                            >
                              Giám sát
                            </span>

                            {/* IO */}
                            <span
                              className={cn(
                                'px-2 py-0.5 rounded text-[10px] font-sans font-semibold border',
                                details?.peripheral_check
                                  ? 'bg-success-soft border-success/30 text-success-dark'
                                  : 'bg-warning-soft border-warning/30 text-warning-dark'
                              )}
                              title="Toàn vẹn ngoại vi"
                            >
                              Ngoại vi
                            </span>
                          </div>
                        </td>

                        {/* Heartbeat */}
                        <td className="py-3 px-3 text-text-muted text-xs">
                          <span className="flex items-center gap-1">
                            <Clock className="w-3 h-3 text-text-subtle" />
                            <span>{formatRelativeTime(ws.last_heartbeat)}</span>
                          </span>
                        </td>

                        {/* Status Badge */}
                        <td className="py-3 px-3">
                          <WorkstationStatusBadge status={ws.status} size="sm" />
                        </td>

                        {/* Action */}
                        <td className="py-3 px-3 text-right">
                          <span className="inline-flex items-center gap-0.5 text-xs font-medium text-primary group-hover:underline">
                            <span>Chi tiết</span>
                            <ChevronRight className="w-3.5 h-3.5 group-hover:translate-x-0.5 transition-transform" />
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        )}
      </div>

      {/* Pagination Footer */}
      <div className="mt-auto px-4 sm:px-5 py-3 bg-surface-subtle border-t border-border">
        <div className="flex flex-col sm:flex-row justify-between items-center gap-2 text-xs">
          <span className="text-text-muted font-sans text-xs text-center sm:text-left">
            {UI_LABELS.workstations.page} <strong className="text-text font-mono">{activePage}</strong> / <strong className="text-text font-mono">{totalPages}</strong> ({filtered.length} {UI_LABELS.environment.workstations})
          </span>

          {totalPages > 1 && (
            <div className="flex items-center gap-1.5 flex-wrap justify-center font-mono">
              <button
                onClick={() => setCurrentPage((p) => Math.max(1, p - 1))}
                disabled={activePage === 1}
                className="w-7 h-7 flex items-center justify-center border border-border bg-surface rounded text-xs font-bold disabled:opacity-40 hover:bg-surface-subtle cursor-pointer"
                aria-label="Trang trước"
              >
                &lt;
              </button>
              {Array.from({ length: totalPages }, (_, i) => i + 1).map((pg) => (
                <button
                  key={pg}
                  onClick={() => setCurrentPage(pg)}
                  className={cn(
                    'w-7 h-7 flex items-center justify-center border rounded text-xs font-bold transition-colors cursor-pointer',
                    activePage === pg
                      ? 'bg-surface text-text border-text shadow-xs'
                      : 'bg-surface text-text-muted border-border hover:bg-surface-subtle'
                  )}
                >
                  {pg}
                </button>
              ))}
              <button
                onClick={() => setCurrentPage((p) => Math.min(totalPages, p + 1))}
                disabled={activePage === totalPages}
                className="w-7 h-7 flex items-center justify-center border border-border bg-surface rounded text-xs font-bold disabled:opacity-40 hover:bg-surface-subtle cursor-pointer"
                aria-label="Trang sau"
              >
                &gt;
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
