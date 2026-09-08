import React, { useEffect, useState, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { Plus, Search, RefreshCw, Layers, ArrowRight, Clock, DoorOpen, Router, Monitor, AlertTriangle } from 'lucide-react';
import { listSessions, SessionStatusBadge } from '@/src/features/exam-sessions';
import { ExamSession } from '@/src/domain';
import { Button } from '@/src/shared/ui/button';
import { Spinner } from '@/src/shared/ui/spinner';
import { formatDateTime, formatRelativeTime } from '@/src/shared/lib/formatters';
import { cn } from '@/src/shared/lib/cn';

export const SessionListPage: React.FC = () => {
  const [sessions, setSessions] = useState<ExamSession[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [search, setSearch] = useState<string>('');
  const [selectedStatus, setSelectedStatus] = useState<string>('ALL');

  const fetchSessions = async () => {
    setIsLoading(true);
    setErrorMsg(null);
    try {
      const res = await listSessions();
      setSessions(res.sessions || []);
    } catch (err: any) {
      setErrorMsg(err.message || 'Không thể tải danh mục ca thi.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchSessions();
  }, []);

  const filteredSessions = useMemo(() => {
    return sessions.filter((s) => {
      const matchSearch =
        s.name.toLowerCase().includes(search.toLowerCase()) ||
        s.room_id.toLowerCase().includes(search.toLowerCase()) ||
        s.gateway_id.toLowerCase().includes(search.toLowerCase()) ||
        s.id.toLowerCase().includes(search.toLowerCase());

      if (!matchSearch) return false;

      if (selectedStatus !== 'ALL' && s.status !== selectedStatus) {
        return false;
      }

      return true;
    });
  }, [sessions, search, selectedStatus]);

  const statuses: Array<{ key: string; label: string }> = [
    { key: 'ALL', label: 'Tất cả ca thi' },
    { key: 'CREATED', label: 'Đã tạo' },
    { key: 'DEPLOYING', label: 'Đang triển khai' },
    { key: 'PREFLIGHT', label: 'Kiểm tra' },
    { key: 'READY', label: 'Sẵn sàng' },
    { key: 'RUNNING', label: 'Đang thi' },
    { key: 'FINISHED', label: 'Đã kết thúc' },
  ];

  return (
    <div className="flex flex-col min-h-full w-full overflow-x-hidden">
      {/* 1. Standardized Global Page Header */}
      <header className="bg-surface border-b border-border shadow-2xs shrink-0">
        <div className="w-full max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-3.5 sm:py-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-4">
            <div>
              <h1 className="text-lg sm:text-xl font-bold font-sans tracking-tight text-text">
                Danh mục ca thi
              </h1>
            </div>

            <div className="flex items-center gap-2.5 sm:gap-3 shrink-0">
              <Button
                variant="outline"
                size="sm"
                onClick={fetchSessions}
                isLoading={isLoading}
                leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
              >
                Làm mới
              </Button>

              <Link to="/sessions/new">
                <Button variant="primary" size="sm" leftIcon={<Plus className="w-4 h-4" />} className="font-bold shadow-2xs">
                  Tạo ca thi mới
                </Button>
              </Link>
            </div>
          </div>
        </div>
      </header>

      {/* 2. Main Content Area */}
      <div className="w-full max-w-[1600px] mx-auto p-4 sm:p-6 lg:p-8 space-y-6 flex-1 min-w-0">
        {errorMsg && (
          <div className="p-4 bg-error-soft border border-error/30 rounded-sm text-sm text-error-dark flex items-center gap-3">
            <span>{errorMsg}</span>
          </div>
        )}

        {/* Filter & Search Bar */}
        <div className="bg-surface border border-border rounded-sm p-3.5 sm:p-4 space-y-3 shadow-2xs">
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
            <div className="relative flex-1 w-full">
              <Search className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-text-subtle" />
              <input
                type="text"
                placeholder="Tìm theo tên ca thi, phòng máy..."
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="w-full pl-9 pr-3.5 py-2 text-xs bg-surface-subtle focus:bg-surface text-text border border-border rounded focus:outline-none focus:ring-1 focus:ring-primary placeholder:text-text-subtle font-sans"
              />
            </div>

            <div className="flex items-center gap-1.5 overflow-x-auto w-full sm:w-auto pb-1 sm:pb-0 no-scrollbar">
              {statuses.map((st) => (
                <button
                  key={st.key}
                  onClick={() => setSelectedStatus(st.key)}
                  className={cn(
                    'px-2.5 sm:px-3 py-1.5 rounded text-xs font-semibold uppercase tracking-wider transition-colors whitespace-nowrap border cursor-pointer shrink-0 font-sans',
                    selectedStatus === st.key
                      ? 'bg-text text-surface border-text shadow-2xs'
                      : 'bg-surface text-text-muted border-border hover:bg-surface-subtle hover:text-text'
                  )}
                >
                  {st.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {/* Sessions List */}
        {isLoading ? (
          <div className="bg-surface border border-border rounded-sm p-12 flex justify-center shadow-2xs">
            <Spinner label="Đang tải danh sách ca thi..." />
          </div>
        ) : filteredSessions.length === 0 ? (
          <div className="bg-surface border border-border rounded-sm p-8 sm:p-12 text-center space-y-3 shadow-2xs">
            <div className="w-12 h-12 rounded-full bg-surface-subtle border border-border mx-auto flex items-center justify-center text-text-subtle">
              <Layers className="w-6 h-6" />
            </div>
            <div>
              <h3 className="text-sm font-bold text-text font-sans">Không tìm thấy ca thi nào</h3>
              <p className="text-xs text-text-muted mt-1 font-sans">
                Hãy thử điều chỉnh từ khóa tìm kiếm hoặc tạo ca thi mới.
              </p>
            </div>
            <Link to="/sessions/new">
              <Button variant="primary" size="sm" leftIcon={<Plus className="w-4 h-4" />} className="font-bold shadow-2xs">
                Tạo ca thi mới
              </Button>
            </Link>
          </div>
        ) : (
          <div className="space-y-3">
            {filteredSessions.map((session) => {
              const issueCount = session.workstations.filter(
                (w) => w.status === 'WARNING' || w.status === 'FAILED'
              ).length;
              const isCompleted =
                session.status === 'FINISHED' || session.status === 'NORMAL';

              return (
                <Link
                  key={session.id}
                  to={`/sessions/${session.id}`}
                  className="block group"
                >
                  <div className={cn(
                    'bg-surface border border-border rounded-sm p-4 sm:p-5 hover:border-text transition-all shadow-2xs',
                    isCompleted && 'bg-surface-subtle/40 opacity-85 hover:opacity-100 hover:border-border-dark'
                  )}>
                    <div className="flex flex-col md:flex-row md:items-center justify-between gap-3 sm:gap-4">
                      {/* Left: Info */}
                      <div className="space-y-2 min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className={cn(
                            'font-bold text-sm sm:text-base truncate max-w-[220px] sm:max-w-none font-sans transition-colors',
                            isCompleted ? 'text-text-muted group-hover:text-text font-medium' : 'text-text group-hover:text-primary'
                          )}>
                            {session.name}
                          </span>
                          <SessionStatusBadge status={session.status} size="sm" />
                        </div>

                        <div className="flex flex-wrap items-center gap-y-1 gap-x-4 sm:gap-x-5 text-xs text-text-muted font-sans">
                          <div className="flex items-center gap-1">
                            <DoorOpen className={cn('w-3.5 h-3.5', isCompleted ? 'text-text-subtle' : 'text-primary')} />
                            <span>Phòng: <strong className="text-text font-medium">{session.room_id}</strong></span>
                          </div>

                          <div className="flex items-center gap-1.5 font-mono">
                            <span className={cn('w-2 h-2 rounded-full shrink-0', issueCount > 0 ? 'bg-warning' : isCompleted ? 'bg-text-subtle' : 'bg-success')} />
                            <span className="text-text font-medium">{session.workstations.length} máy</span>
                            {issueCount > 0 && (
                              <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded bg-warning-soft text-warning-dark border border-warning/30 font-bold text-[11px]">
                                <AlertTriangle className="w-3 h-3 text-warning-dark" />
                                {issueCount} lỗi
                              </span>
                            )}
                          </div>
                        </div>
                      </div>

                      {/* Right: Timestamp & Arrow */}
                      <div className="flex items-center justify-between md:justify-end gap-3 sm:gap-4 text-xs font-mono text-text-subtle pt-2 md:pt-0 border-t md:border-t-0 border-border-subtle shrink-0">
                        <div className="flex items-center gap-1">
                          <Clock className="w-3.5 h-3.5" />
                          <span title={formatDateTime(session.created_at)}>
                            {formatRelativeTime(session.created_at)}
                          </span>
                        </div>
                        <ArrowRight className="w-4 h-4 text-text-subtle group-hover:text-primary group-hover:translate-x-0.5 transition-all" />
                      </div>
                    </div>
                  </div>
                </Link>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
