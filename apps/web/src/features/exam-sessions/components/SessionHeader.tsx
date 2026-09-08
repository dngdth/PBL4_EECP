import React from 'react';
import { RefreshCw, Send, Play, ShieldAlert, StopCircle, CheckCircle2, DoorOpen, SlidersHorizontal } from 'lucide-react';
import { Link } from 'react-router-dom';
import { Button } from '@/src/shared/ui/button';
import { SessionStatusBadge } from './SessionStatusBadge';
import { ExamSession } from '@/src/domain';
import { UI_LABELS } from '@/src/shared/config/labels';

export interface SessionHeaderProps {
  session: ExamSession;
  onRefresh: () => void;
  isRefreshing?: boolean;
  onOpenDeployModal: () => void;
  onForceStart: () => void;
  onFinishExam?: () => void;
  onOpenInterveneModal?: () => void;
  isStarting?: boolean;
}

export const SessionHeader: React.FC<SessionHeaderProps> = ({
  session,
  onRefresh,
  isRefreshing = false,
  onOpenDeployModal,
  onForceStart,
  onFinishExam,
  onOpenInterveneModal,
  isStarting = false,
}) => {
  const isCompleted =
    session.status === 'FINISHED' ||
    session.status === 'NORMAL';

  return (
    <header className="bg-surface border-b border-border shadow-2xs shrink-0">
      <div className="w-full max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-3 sm:py-3.5">
        {/* Clean Breadcrumb Navigation */}
        <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-xs text-text-muted select-none mb-1 font-sans">
          <Link
            to="/sessions"
            className="hover:text-primary transition-colors font-medium text-text-muted hover:underline"
          >
            Ca thi
          </Link>
          <span className="text-text-subtle">/</span>
          <span className="text-text font-medium truncate max-w-[300px]">
            {session.name}
          </span>
        </nav>

        {/* Identity & Action Bar */}
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3 sm:gap-4">
          {/* Left: Identity, Title, Status & Key Operational Badges */}
          <div className="flex flex-col sm:flex-row sm:items-center gap-2 sm:gap-3.5 min-w-0">
            <div className="flex items-center gap-2.5 min-w-0">
              <h1 className="text-lg sm:text-xl font-bold tracking-tight text-text truncate">
                {session.name}
              </h1>
            </div>

            <div className="flex items-center gap-2 flex-wrap">
              <SessionStatusBadge status={session.status} size="sm" />

              {session.room_id && (
                <span className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded bg-surface-subtle border border-border text-xs text-text font-sans font-medium">
                  <DoorOpen className="w-3.5 h-3.5 text-primary shrink-0" />
                  <span>Phòng thi: <strong className="font-semibold text-text">{session.room_id}</strong></span>
                </span>
              )}
            </div>
          </div>

          {/* Right: Operational Actions */}
          <div className="flex items-center gap-2 sm:gap-2.5 shrink-0 self-start sm:self-auto w-full sm:w-auto flex-wrap">
            <Button
              variant="outline"
              size="sm"
              onClick={onRefresh}
              isLoading={isRefreshing}
              leftIcon={<RefreshCw className="w-3.5 h-3.5 text-text-muted" />}
              className="flex-1 sm:flex-initial justify-center"
            >
              {UI_LABELS.session.refresh}
            </Button>

            {onOpenInterveneModal && (
              <Button
                variant="outline"
                size="sm"
                onClick={onOpenInterveneModal}
                leftIcon={<SlidersHorizontal className="w-3.5 h-3.5 text-text-muted" />}
                className="flex-1 sm:flex-initial justify-center text-text-muted hover:text-text"
                title="Can thiệp / Đổi trạng thái thủ công"
              >
                Can thiệp
              </Button>
            )}

            {session.status === 'CREATED' && (
              <Button
                variant="primary"
                size="sm"
                onClick={onOpenDeployModal}
                leftIcon={<Send className="w-3.5 h-3.5" />}
                className="flex-1 sm:flex-initial justify-center bg-primary hover:bg-primary-dark font-bold text-surface"
              >
                {UI_LABELS.session.deployPolicy}
              </Button>
            )}

            {session.status === 'DEPLOYING' && (
              <div className="flex-1 sm:flex-initial justify-center px-3 py-1.5 bg-primary-soft text-primary border border-primary/30 rounded text-xs font-sans font-bold uppercase tracking-wider flex items-center gap-1.5 select-none">
                <span className="w-2 h-2 rounded-full bg-primary animate-pulse shrink-0" />
                <span className="truncate">{UI_LABELS.status.deploying}...</span>
              </div>
            )}

            {session.status === 'PREFLIGHT' && (
              <>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={onOpenDeployModal}
                  leftIcon={<Send className="w-3.5 h-3.5 text-primary" />}
                  className="hidden sm:inline-flex justify-center"
                >
                  {UI_LABELS.session.deployPolicy}
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={onForceStart}
                  isLoading={isStarting}
                  leftIcon={<ShieldAlert className="w-3.5 h-3.5 text-warning" />}
                  className="flex-1 sm:flex-initial justify-center bg-primary hover:bg-primary-dark font-bold text-surface"
                >
                  {UI_LABELS.session.forceStart}
                </Button>
              </>
            )}

            {session.status === 'READY' && (
              <Button
                variant="primary"
                size="sm"
                onClick={onForceStart}
                isLoading={isStarting}
                leftIcon={<Play className="w-3.5 h-3.5 fill-current" />}
                className="flex-1 sm:flex-initial justify-center bg-primary hover:bg-primary-dark font-bold text-surface"
              >
                {UI_LABELS.session.startExam}
              </Button>
            )}

            {session.status === 'RUNNING' && (
              <>
                <div className="hidden sm:flex items-center px-3 py-1.5 bg-success-soft text-success-dark border border-success/30 rounded text-xs font-sans font-bold uppercase tracking-wider gap-1.5 select-none">
                  <span className="w-2 h-2 rounded-full bg-success animate-pulse shrink-0" />
                  <span className="truncate">{UI_LABELS.session.activeExam}</span>
                </div>
                {onFinishExam && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={onFinishExam}
                    leftIcon={<StopCircle className="w-3.5 h-3.5 text-error" />}
                    className="flex-1 sm:flex-initial justify-center border-error/30 hover:border-error hover:bg-error-soft text-error-dark font-bold"
                  >
                    Kết thúc ca thi
                  </Button>
                )}
              </>
            )}

            {isCompleted && (
              <div className="hidden sm:flex items-center px-3 py-1.5 bg-surface-subtle text-text-muted border border-border rounded text-xs font-sans font-semibold gap-1.5 select-none">
                <CheckCircle2 className="w-3.5 h-3.5 text-success" />
                <span>Đã lưu trữ kết quả</span>
              </div>
            )}
          </div>
        </div>
      </div>
    </header>
  );
};
