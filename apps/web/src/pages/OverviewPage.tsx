import React, { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { 
  PlusCircle, 
  Layers, 
  Monitor, 
  CheckCircle2, 
  AlertTriangle, 
  ShieldAlert, 
  RefreshCw, 
  ArrowRight, 
  Activity,
  Play,
  CheckCircle,
  Router,
  DoorOpen
} from 'lucide-react';
import { Button } from '@/src/shared/ui/button';
import { Spinner } from '@/src/shared/ui/spinner';
import { ExamSession } from '@/src/domain';
import { listSessions, SessionStatusBadge } from '@/src/features/exam-sessions';
import { ActivityFeed } from '@/src/features/recent-activity';
import { formatDateTime, formatRelativeTime } from '@/src/shared/lib/formatters';
import { UI_LABELS } from '@/src/shared/config/labels';
import { cn } from '@/src/shared/lib/cn';

export const OverviewPage: React.FC = () => {
  const [sessions, setSessions] = useState<ExamSession[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const fetchSessions = async (silent = false) => {
    if (!silent) setIsRefreshing(true);
    try {
      const res = await listSessions();
      setSessions(res.sessions || []);
      setErrorMsg(null);
    } catch (err: any) {
      if (!silent) {
        setErrorMsg(err.message || 'Không thể tải danh sách ca thi.');
      }
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    fetchSessions(false);
    const interval = setInterval(() => {
      fetchSessions(true);
    }, 5000);
    return () => clearInterval(interval);
  }, []);

  // Aggregated KPIs
  const totalSessions = sessions.length;
  const activeSessions = sessions.filter((s) => s.status === 'RUNNING');
  const readySessions = sessions.filter((s) => s.status === 'READY');
  const pendingOrPreflightSessions = sessions.filter(
    (s) => s.status === 'CREATED' || s.status === 'DEPLOYING' || s.status === 'PREFLIGHT'
  );

  const allWorkstations = sessions.flatMap((s) => s.workstations);
  const totalWorkstations = allWorkstations.length;
  const readyWorkstations = allWorkstations.filter((w) => w.status === 'READY').length;
  const warningWorkstations = allWorkstations.filter((w) => w.status === 'WARNING').length;
  const failedWorkstations = allWorkstations.filter((w) => w.status === 'FAILED').length;

  const aggregatedActivities = sessions
    .flatMap((s) => s.activity_log || [])
    .sort((a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime())
    .slice(0, 10);

  if (isLoading) {
    return (
      <div className="py-24 flex flex-col items-center justify-center">
        <Spinner size="lg" label="Đang kết nối trung tâm điều phối..." />
      </div>
    );
  }

  return (
    <div className="flex flex-col min-h-full w-full overflow-x-hidden">
      {/* 1. Standardized Global Page Header */}
      <header className="bg-surface border-b border-border shadow-2xs shrink-0">
        <div className="w-full max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-3.5 sm:py-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-4">
            <div>
              <h1 className="text-lg sm:text-xl font-bold font-sans tracking-tight text-text">
                Bảng điều khiển trung tâm
              </h1>
            </div>

            <div className="flex items-center gap-2.5 sm:gap-3 shrink-0">
              <Button
                variant="outline"
                size="sm"
                onClick={() => fetchSessions(false)}
                isLoading={isRefreshing}
                leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
              >
                Làm mới
              </Button>
              <Link to="/sessions/new">
                <Button
                  variant="primary"
                  size="sm"
                  leftIcon={<PlusCircle className="w-3.5 h-3.5" />}
                  className="font-bold shadow-2xs"
                >
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
            <ShieldAlert className="w-5 h-5 shrink-0 text-error" />
            <span>{errorMsg}</span>
          </div>
        )}

        {/* KPI Metric Cards */}
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
          {/* Metric 1: Ca thi đang diễn ra */}
          <div className="bg-surface border border-border rounded-sm p-4 shadow-2xs">
            <div className="flex items-center justify-between">
              <span className="text-xs uppercase font-bold tracking-wider text-text-muted font-sans">
                Ca thi đang chạy
              </span>
              <div className="w-7 h-7 rounded bg-success-soft text-success-dark flex items-center justify-center">
                <Play className="w-4 h-4 fill-current" />
              </div>
            </div>
            <div className="mt-2 flex items-baseline gap-2">
              <span className="text-2xl font-bold font-mono text-text">{activeSessions.length}</span>
              <span className="text-xs text-text-muted">/ {totalSessions} tổng ca</span>
            </div>
          </div>

          {/* Metric 2: Máy trạm sẵn sàng */}
          <div className="bg-surface border border-border rounded-sm p-4 shadow-2xs">
            <div className="flex items-center justify-between">
              <span className="text-xs uppercase font-bold tracking-wider text-text-muted font-sans">
                Máy trạm sẵn sàng
              </span>
              <div className="w-7 h-7 rounded bg-success-soft text-success-dark flex items-center justify-center">
                <CheckCircle2 className="w-4 h-4" />
              </div>
            </div>
            <div className="mt-2 flex items-baseline gap-2">
              <span className="text-2xl font-bold font-mono text-text">{readyWorkstations}</span>
              <span className="text-xs text-text-muted font-mono">/ {totalWorkstations} máy</span>
            </div>
          </div>

          {/* Metric 3: Cảnh báo bất thường */}
          <div className="bg-surface border border-border rounded-sm p-4 shadow-2xs">
            <div className="flex items-center justify-between">
              <span className="text-xs uppercase font-bold tracking-wider text-text-muted font-sans">
                Máy cần chú ý
              </span>
              <div className={cn(
                'w-7 h-7 rounded flex items-center justify-center',
                warningWorkstations + failedWorkstations > 0 ? 'bg-warning-soft text-warning-dark' : 'bg-surface-subtle text-text-muted'
              )}>
                <AlertTriangle className="w-4 h-4" />
              </div>
            </div>
            <div className="mt-2 flex items-baseline gap-2">
              <span className={cn('text-2xl font-bold font-mono', warningWorkstations + failedWorkstations > 0 ? 'text-warning-dark' : 'text-text')}>
                {warningWorkstations + failedWorkstations}
              </span>
              <span className="text-xs text-text-muted">máy trạm</span>
            </div>
          </div>

          {/* Metric 4: Ca thi chuẩn bị */}
          <div className="bg-surface border border-border rounded-sm p-4 shadow-2xs">
            <div className="flex items-center justify-between">
              <span className="text-xs uppercase font-bold tracking-wider text-text-muted font-sans">
                Ca đang tiền kiểm
              </span>
              <div className="w-7 h-7 rounded bg-primary-soft text-primary flex items-center justify-center">
                <Layers className="w-4 h-4" />
              </div>
            </div>
            <div className="mt-2 flex items-baseline gap-2">
              <span className="text-2xl font-bold font-mono text-text">{pendingOrPreflightSessions.length}</span>
              <span className="text-xs text-text-muted">ca thi</span>
            </div>
          </div>
        </div>

        {/* Main 2-Column Split: Active Sessions List (70%) + Recent Activity (30%) */}
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          {/* Left: Active Exam Sessions Table */}
          <div className="lg:col-span-8 bg-surface border border-border rounded-sm shadow-2xs overflow-hidden flex flex-col">
            <div className="p-4 border-b border-border bg-surface flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Layers className="w-4 h-4 text-primary" />
                <h2 className="font-sans font-bold text-xs uppercase tracking-wider text-text">
                  Danh sách ca thi đang hoạt động ({sessions.length})
                </h2>
              </div>
              <Link
                to="/sessions"
                className="text-xs font-sans font-semibold text-primary hover:underline flex items-center gap-1"
              >
                <span>Xem tất cả</span>
                <ArrowRight className="w-3.5 h-3.5" />
              </Link>
            </div>

            <div className="divide-y divide-border-subtle">
              {sessions.length === 0 ? (
                <div className="py-12 text-center text-xs text-text-muted font-sans">
                  Chưa có ca thi nào được tạo.
                </div>
              ) : (
                sessions.map((sess) => {
                  const totalWs = sess.workstations.length;
                  const readyWs = sess.workstations.filter((w) => w.status === 'READY').length;
                  const warnWs = sess.workstations.filter((w) => w.status === 'WARNING').length;
                  const failWs = sess.workstations.filter((w) => w.status === 'FAILED').length;
                  const isCompleted =
                    sess.status === 'FINISHED' || sess.status === 'NORMAL';

                  return (
                    <Link
                      key={sess.id}
                      to={`/sessions/${sess.id}`}
                      className={cn(
                        'p-4 hover:bg-surface-subtle transition-colors flex flex-col sm:flex-row sm:items-center justify-between gap-3 group',
                        isCompleted && 'bg-surface-subtle/40 opacity-85 hover:opacity-100'
                      )}
                    >
                      <div className="space-y-1.5 min-w-0">
                        <div className="flex items-center gap-2.5 flex-wrap">
                          <span className={cn(
                            'font-sans font-bold text-sm truncate transition-colors',
                            isCompleted ? 'text-text-muted group-hover:text-text font-medium' : 'text-text group-hover:text-primary'
                          )}>
                            {sess.name}
                          </span>
                        </div>

                        <div className="flex items-center gap-3 text-xs text-text-muted font-sans flex-wrap">
                          <span className="flex items-center gap-1">
                            <DoorOpen className={cn('w-3.5 h-3.5', isCompleted ? 'text-text-subtle' : 'text-primary')} />
                            <span>Phòng: <strong className="text-text font-medium">{sess.room_id}</strong></span>
                          </span>

                          <span className="flex items-center gap-1 font-mono">
                            <Router className={cn('w-3.5 h-3.5', isCompleted ? 'text-text-subtle' : 'text-primary')} />
                            <span>{sess.gateway_id}</span>
                          </span>

                          <span>• Cập nhật {formatRelativeTime(sess.updated_at)}</span>
                        </div>
                      </div>

                      <div className="flex items-center gap-3 shrink-0 self-start sm:self-center">
                        {/* Workstation micro-pills / dot indicator */}
                        <div className="flex items-center gap-2 text-xs font-mono">
                          {warnWs === 0 && failWs === 0 ? (
                            <div className="flex items-center gap-1.5 text-text-muted" title={`${readyWs}/${totalWs} máy sẵn sàng`}>
                              <span className={cn('w-2 h-2 rounded-full shrink-0', isCompleted ? 'bg-text-subtle' : 'bg-success')} />
                              <span className="font-semibold text-text">{readyWs}/{totalWs}</span>
                            </div>
                          ) : (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-warning-soft text-warning-dark border border-warning/40 font-bold shadow-2xs">
                              <AlertTriangle className="w-3 h-3 text-warning-dark" />
                              <span>{warnWs + failWs} lỗi</span>
                            </span>
                          )}
                        </div>

                        <SessionStatusBadge status={sess.status} size="sm" />

                        <ArrowRight className="w-4 h-4 text-text-subtle group-hover:text-primary group-hover:translate-x-0.5 transition-all" />
                      </div>
                    </Link>
                  );
                })
              )}
            </div>
          </div>

          {/* Right: Aggregated System Activity Feed */}
          <div className="lg:col-span-4 min-w-0">
            <ActivityFeed activities={aggregatedActivities} />
          </div>
        </div>
      </div>
    </div>
  );
};
