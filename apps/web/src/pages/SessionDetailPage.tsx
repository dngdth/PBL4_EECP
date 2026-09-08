import React, { useEffect, useState, useCallback } from 'react';
import { useParams, Link } from 'react-router-dom';
import { AlertCircle, ArrowLeft } from 'lucide-react';
import { ExamSession, Workstation, ActivityItem } from '@/src/domain';
import {
  getSession,
  SessionHeader,
  SessionLifecycleStepper,
  SessionEnvironmentCard,
  CompletedSummaryCard,
  DeployPolicyModal,
  ForceStartDialog,
  FinishSessionModal,
  InterveneStatusModal,
} from '@/src/features/exam-sessions';
import {
  WorkstationGrid,
  WorkstationInspectModal,
  CommandQueueModal,
} from '@/src/features/workstations';
import { PolicySummaryCard } from '@/src/features/security-policy';
import { ActivityFeed, ActivityDetailModal } from '@/src/features/recent-activity';
import { AlertPanel } from '@/src/features/alerts';
import { Spinner } from '@/src/shared/ui/spinner';
import { Button } from '@/src/shared/ui/button';

export const SessionDetailPage: React.FC = () => {
  const { sessionId } = useParams<{ sessionId: string }>();

  const [session, setSession] = useState<ExamSession | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Modals & Inspection
  const [isDeployModalOpen, setIsDeployModalOpen] = useState<boolean>(false);
  const [isForceStartOpen, setIsForceStartOpen] = useState<boolean>(false);
  const [isFinishModalOpen, setIsFinishModalOpen] = useState<boolean>(false);
  const [isInterveneModalOpen, setIsInterveneModalOpen] = useState<boolean>(false);
  const [inspectingWorkstation, setInspectingWorkstation] = useState<Workstation | null>(null);
  const [selectedActivity, setSelectedActivity] = useState<ActivityItem | null>(null);
  const [commandQueueTarget, setCommandQueueTarget] = useState<{
    id: string;
    type: 'GATEWAY' | 'WORKSTATION';
  } | null>(null);

  // Auto-polling toggle for active phases
  const [autoPoll] = useState<boolean>(true);

  const fetchSessionData = useCallback(async (isBackground = false) => {
    if (!sessionId) return;
    if (!isBackground) setIsRefreshing(true);
    setErrorMsg(null);

    try {
      const data = await getSession(sessionId);
      setSession(data);
    } catch (err: any) {
      if (!isBackground) {
        setErrorMsg(err.message || 'Không thể tải thông tin ca thi.');
      }
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  }, [sessionId]);

  // Initial load
  useEffect(() => {
    fetchSessionData(false);
  }, [fetchSessionData]);

  // Auto-polling when deploying or in preflight
  useEffect(() => {
    if (!autoPoll || !session) return;
    const isTransitional = session.status === 'DEPLOYING' || session.status === 'PREFLIGHT';
    if (!isTransitional) return;

    const timer = setInterval(() => {
      fetchSessionData(true);
    }, 2000);

    return () => clearInterval(timer);
  }, [autoPoll, session, fetchSessionData]);

  const handleSessionUpdated = (updated: ExamSession) => {
    setSession(updated);
    if (inspectingWorkstation) {
      const refreshedWs = updated.workstations.find((w) => w.id === inspectingWorkstation.id);
      if (refreshedWs) setInspectingWorkstation(refreshedWs);
    }
  };

  const handleInspectWorkstationById = (wsId: string) => {
    if (!session) return;
    const targetWs = session.workstations.find((w) => w.id === wsId);
    if (targetWs) {
      setInspectingWorkstation(targetWs);
    }
  };

  if (isLoading) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-8 min-h-[400px]">
        <Spinner size="lg" label="Đang tải dữ liệu ca thi..." />
      </div>
    );
  }

  if (errorMsg || !session) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center p-6 text-center">
        <div className="w-12 h-12 rounded-full bg-error-soft text-error-dark flex items-center justify-center mb-3">
          <AlertCircle className="w-6 h-6" />
        </div>
        <h2 className="text-base font-bold text-text mb-1">Lỗi tải dữ liệu</h2>
        <p className="text-xs text-text-muted mb-4 max-w-sm">{errorMsg || 'Không tìm thấy ca thi.'}</p>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={() => fetchSessionData(false)}>
            Thử lại
          </Button>
          <Link to="/sessions">
            <Button variant="outline" size="sm">
              <ArrowLeft className="w-3.5 h-3.5 mr-1" />
              Về danh sách
            </Button>
          </Link>
        </div>
      </div>
    );
  }

  const isCompleted =
    session.status === 'FINISHED' ||
    session.status === 'NORMAL';

  return (
    <div className="flex flex-col min-h-full w-full overflow-x-hidden">
      {/* 1. Session Header */}
      <SessionHeader
        session={session}
        onRefresh={() => fetchSessionData(false)}
        isRefreshing={isRefreshing}
        onOpenDeployModal={() => setIsDeployModalOpen(true)}
        onForceStart={() => setIsForceStartOpen(true)}
        onFinishExam={() => setIsFinishModalOpen(true)}
        onOpenInterveneModal={() => setIsInterveneModalOpen(true)}
      />

      {/* 2. Main Content Canvas */}
      <div className="w-full max-w-[1600px] mx-auto p-4 sm:p-6 lg:p-8 space-y-5 flex-1 min-w-0">
        {/* Session Lifecycle Progress */}
        <section className="bg-surface border border-border rounded p-4 sm:p-5 shadow-2xs">
          <SessionLifecycleStepper status={session.status} />
        </section>

        {/* Completed Summary Banner */}
        {isCompleted && (
          <CompletedSummaryCard session={session} />
        )}

        {/* Alerts & Critical Diagnostics Panel */}
        <AlertPanel
          session={session}
          onInspectWorkstation={(wsOrId) =>
            typeof wsOrId === 'string'
              ? handleInspectWorkstationById(wsOrId)
              : setInspectingWorkstation(wsOrId)
          }
        />

        {/* 2-Column Responsive Dashboard Layout (>= lg) */}
        <div className="hidden lg:grid lg:grid-cols-12 gap-5 items-start">
          {/* Left Column (25%): Context, Policy & Activity Feed */}
          <div className="lg:col-span-3 xl:col-span-3 flex flex-col gap-4 min-w-0">
            {/* 1. Môi trường */}
            <SessionEnvironmentCard
              roomId={session.room_id}
              gatewayId={session.gateway_id}
              workstationCount={session.workstations.length}
              onOpenCommandQueue={(gwId) => setCommandQueueTarget({ id: gwId, type: 'GATEWAY' })}
            />

            {/* 2. Chính sách bảo mật */}
            <PolicySummaryCard
              policy={session.policy}
              status={session.status}
              gatewayId={session.gateway_id}
              onDeployClick={() => setIsDeployModalOpen(true)}
              canDeploy={session.status !== 'RUNNING' && !isCompleted}
            />

            {/* 3. Hoạt động gần đây */}
            <ActivityFeed
              activities={session.activity_log}
              onSelectActivity={(act) => setSelectedActivity(act)}
              onInspectWorkstation={handleInspectWorkstationById}
            />
          </div>

          {/* Right Column (75%): Workstation Health & Monitor Grid */}
          <div className="lg:col-span-9 xl:col-span-9 flex flex-col min-w-0">
            <WorkstationGrid
              workstations={session.workstations}
              onInspectWorkstation={(ws) => setInspectingWorkstation(ws)}
              onRefresh={() => fetchSessionData(false)}
            />
          </div>
        </div>

        {/* Mobile & Tablet Stacked Layout (< lg) */}
        <div className="flex flex-col gap-4 lg:hidden min-w-0">
          {/* 1. Môi trường */}
          <section className="min-w-0">
            <SessionEnvironmentCard
              roomId={session.room_id}
              gatewayId={session.gateway_id}
              workstationCount={session.workstations.length}
              onOpenCommandQueue={(gwId) => setCommandQueueTarget({ id: gwId, type: 'GATEWAY' })}
            />
          </section>

          {/* 2. Chính sách bảo mật */}
          <section className="min-w-0">
            <PolicySummaryCard
              policy={session.policy}
              status={session.status}
              gatewayId={session.gateway_id}
              onDeployClick={() => setIsDeployModalOpen(true)}
              canDeploy={session.status !== 'RUNNING' && !isCompleted}
            />
          </section>

          {/* Workstations Grid */}
          <section className="min-w-0">
            <WorkstationGrid
              workstations={session.workstations}
              onInspectWorkstation={(ws) => setInspectingWorkstation(ws)}
              onRefresh={() => fetchSessionData(false)}
            />
          </section>

          {/* 3. Hoạt động gần đây */}
          <section className="min-w-0 w-full">
            <ActivityFeed
              activities={session.activity_log}
              onSelectActivity={(act) => setSelectedActivity(act)}
              onInspectWorkstation={handleInspectWorkstationById}
            />
          </section>
        </div>
      </div>

      {/* Modals */}
      {isDeployModalOpen && (
        <DeployPolicyModal
          isOpen={isDeployModalOpen}
          onClose={() => setIsDeployModalOpen(false)}
          session={session}
          onSuccess={handleSessionUpdated}
        />
      )}

      {isForceStartOpen && (
        <ForceStartDialog
          isOpen={isForceStartOpen}
          onClose={() => setIsForceStartOpen(false)}
          session={session}
          onSuccess={handleSessionUpdated}
        />
      )}

      {isFinishModalOpen && (
        <FinishSessionModal
          isOpen={isFinishModalOpen}
          onClose={() => setIsFinishModalOpen(false)}
          session={session}
          onSuccess={handleSessionUpdated}
        />
      )}

      {inspectingWorkstation && (
        <WorkstationInspectModal
          isOpen={Boolean(inspectingWorkstation)}
          onClose={() => setInspectingWorkstation(null)}
          workstation={inspectingWorkstation}
          sessionId={session.id}
          onWorkstationUpdated={handleSessionUpdated}
          onViewCommandQueue={(wsId) => setCommandQueueTarget({ id: wsId, type: 'WORKSTATION' })}
        />
      )}

      {/* Activity Event Detail Pop-up Modal */}
      <ActivityDetailModal
        activity={selectedActivity}
        isOpen={Boolean(selectedActivity)}
        onClose={() => setSelectedActivity(null)}
        onInspectWorkstation={handleInspectWorkstationById}
      />

      {/* Command Queue Modal (Gateway / Agent Commands) */}
      <CommandQueueModal
        isOpen={Boolean(commandQueueTarget)}
        targetId={commandQueueTarget?.id || ''}
        targetType={commandQueueTarget?.type}
        onClose={() => setCommandQueueTarget(null)}
      />

      {/* Intervene Status Modal (Admin Status Override) */}
      <InterveneStatusModal
        isOpen={isInterveneModalOpen}
        session={session}
        onClose={() => setIsInterveneModalOpen(false)}
        onSuccess={handleSessionUpdated}
      />
    </div>
  );
};
