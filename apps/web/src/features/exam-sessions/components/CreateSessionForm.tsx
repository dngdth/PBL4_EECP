import React, { useEffect, useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import { 
  Building2, 
  Network, 
  Monitor, 
  ShieldAlert, 
  Check, 
  Plus, 
  Layers, 
  ArrowLeft,
  ChevronRight,
  ShieldCheck
} from 'lucide-react';
import { Button } from '@/src/shared/ui/button';
import { Input } from '@/src/shared/ui/input';
import { createSession } from '../services/sessionApi';
import { CreateSessionRequest, PolicyProfile, Agent } from '@/src/domain';
import { 
  PolicyProfileSelector, 
  CreatePolicyProfileModal, 
  listPolicyProfiles 
} from '@/src/features/security-policy';
import { AgentSelector, listAgents } from '@/src/features/workstations';

const DEFAULT_WORKSTATIONS = [
  'PC01', 'PC02', 'PC03', 'PC04', 'PC05', 'PC06',
  'PC07', 'PC08', 'PC09', 'PC10', 'PC11', 'PC12'
];

export const CreateSessionForm: React.FC = () => {
  const navigate = useNavigate();

  // Basic Info
  const [name, setName] = useState<string>('');
  const [roomId, setRoomId] = useState<string>('A101');
  const [gatewayId, setGatewayId] = useState<string>('gw-f301');

  // Policy Profile State
  const [policyProfiles, setPolicyProfiles] = useState<PolicyProfile[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<string>('INTERNET_NO_AI');
  const [isProfilesLoading, setIsProfilesLoading] = useState<boolean>(true);
  const [isCreateProfileModalOpen, setIsCreateProfileModalOpen] = useState<boolean>(false);

  // Workstations / Agents State
  const [agents, setAgents] = useState<Agent[]>([]);
  const [isAgentsLoading, setIsAgentsLoading] = useState<boolean>(true);
  const [selectedWorkstations, setSelectedWorkstations] = useState<string[]>(DEFAULT_WORKSTATIONS);

  // Form State
  const [isLoading, setIsLoading] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Fetch Policy Profiles from Backend
  const fetchProfiles = async () => {
    setIsProfilesLoading(true);
    try {
      const list = await listPolicyProfiles();
      setPolicyProfiles(list);
      if (list.length > 0) {
        // If current selection not in list, pick the first
        const exists = list.some((p) => p.id === selectedProfileId);
        if (!exists) {
          setSelectedProfileId(list[0].id);
        }
      }
    } catch (err: any) {
      console.error('Failed to load policy profiles:', err);
    } finally {
      setIsProfilesLoading(false);
    }
  };

  // Fetch Registered Agents from Backend
  const fetchAgents = async () => {
    setIsAgentsLoading(true);
    try {
      const agentList = await listAgents();
      setAgents(agentList);
      if (agentList.length > 0) {
        // If agents exist, select online agents by default
        const onlineIds = agentList.filter((a) => a.status === 'ONLINE').map((a) => a.id);
        if (onlineIds.length > 0) {
          setSelectedWorkstations(onlineIds);
        } else {
          setSelectedWorkstations(agentList.map((a) => a.id));
        }
      }
    } catch (err: any) {
      console.error('Failed to load agents:', err);
    } finally {
      setIsAgentsLoading(false);
    }
  };

  useEffect(() => {
    fetchProfiles();
    fetchAgents();
  }, []);

  const handleProfileCreated = (newProfile: PolicyProfile) => {
    setPolicyProfiles((prev) => [newProfile, ...prev]);
    setSelectedProfileId(newProfile.id);
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!name.trim()) {
      setErrorMsg('Vui lòng nhập tên ca thi.');
      return;
    }
    if (!roomId.trim()) {
      setErrorMsg('Vui lòng nhập phòng thi.');
      return;
    }
    if (!gatewayId.trim()) {
      setErrorMsg('Vui lòng chỉ định Gateway quản trị.');
      return;
    }
    if (selectedWorkstations.length === 0) {
      setErrorMsg('Vui lòng thêm ít nhất một máy trạm cho ca thi.');
      return;
    }

    setIsLoading(true);
    setErrorMsg(null);

    const payload: CreateSessionRequest = {
      name: name.trim(),
      room_id: roomId.trim(),
      gateway_id: gatewayId.trim(),
      workstation_ids: selectedWorkstations,
      policy_name: selectedProfileId || undefined,
    };

    try {
      const newSession = await createSession(payload);
      navigate(`/sessions/${newSession.id}`);
    } catch (err: any) {
      setErrorMsg(err.message || 'Không thể tạo ca thi. Vui lòng kiểm tra lại kết nối Gateway.');
      setIsLoading(false);
    }
  };

  const selectedProfile = policyProfiles.find((p) => p.id === selectedProfileId);

  return (
    <form onSubmit={handleSubmit} className="flex flex-col min-h-full w-full overflow-x-hidden">
      {/* 1. Standardized Global Page Header */}
      <header className="bg-surface border-b border-border shadow-2xs shrink-0">
        <div className="w-full max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-3 sm:py-3.5">
          {/* Breadcrumb Navigation */}
          <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-xs text-text-muted select-none mb-1 font-sans">
            <Link to="/sessions" className="text-text-muted hover:text-primary font-medium hover:underline transition-colors">
              Ca thi
            </Link>
            <span className="text-text-subtle">/</span>
            <span className="text-text font-medium truncate">Tạo ca thi mới</span>
          </nav>

          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-4">
            <div>
              <h1 className="text-lg sm:text-xl font-bold font-sans tracking-tight text-text">
                Tạo ca thi mới
              </h1>
            </div>

            <div className="flex items-center gap-2.5 sm:gap-3 shrink-0">
              <Link to="/sessions">
                <Button variant="secondary" size="sm" type="button">
                  Hủy & quay lại
                </Button>
              </Link>
              <Button
                type="submit"
                variant="primary"
                size="sm"
                isLoading={isLoading}
                className="font-bold shadow-2xs bg-primary hover:bg-primary-dark text-surface"
              >
                Tạo ca thi ngay
              </Button>
            </div>
          </div>
        </div>
      </header>

      {/* 2. Main Content Area */}
      <div className="w-full max-w-[1600px] mx-auto p-4 sm:p-6 lg:p-8 space-y-6 flex-1 min-w-0">
        {errorMsg && (
          <div className="p-4 bg-error-soft border border-error/30 rounded text-sm text-error-dark flex items-center gap-3">
            <ShieldAlert className="w-5 h-5 shrink-0" />
            <div className="flex-1 font-medium">{errorMsg}</div>
          </div>
        )}

        <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
          {/* Left Column (8/12): Form inputs */}
          <div className="lg:col-span-8 space-y-6">
            {/* Section 1: Thông tin cơ bản */}
            <div className="bg-surface border border-border rounded p-5 sm:p-6 space-y-4 shadow-2xs">
              <h2 className="text-sm uppercase tracking-wider font-bold text-text-muted flex items-center gap-2">
                <Building2 className="w-4 h-4 text-primary" />
                <span>Thông tin ca thi</span>
              </h2>

              <Input
                label="Tên ca thi *"
                placeholder="VD: Thi giữa kỳ Hệ điều hành 2026 - Ca 1"
                value={name}
                onChange={(e) => setName(e.target.value)}
                required
              />

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <Input
                  label="Phòng thi *"
                  placeholder="VD: A101"
                  value={roomId}
                  onChange={(e) => setRoomId(e.target.value)}
                  required
                />
                <Input
                  label="Gateway quản trị (Phòng) *"
                  placeholder="VD: gw-f301"
                  value={gatewayId}
                  onChange={(e) => setGatewayId(e.target.value)}
                  required
                />
              </div>
            </div>

            {/* Section 2: Chọn Chính Sách Bảo Mật (Real Policy Profiles) */}
            <div className="bg-surface border border-border rounded p-5 sm:p-6 space-y-4 shadow-2xs">
              <PolicyProfileSelector
                profiles={policyProfiles}
                selectedProfileId={selectedProfileId}
                onSelectProfile={(p) => setSelectedProfileId(p.id)}
                isLoading={isProfilesLoading}
                onOpenCreateModal={() => setIsCreateProfileModalOpen(true)}
              />
            </div>

            {/* Section 3: Danh sách Máy trạm (Real Agents + Batch Input) */}
            <div className="bg-surface border border-border rounded p-5 sm:p-6 space-y-4 shadow-2xs">
              <AgentSelector
                agents={agents}
                selectedWorkstationIds={selectedWorkstations}
                onChange={(ids) => setSelectedWorkstations(ids)}
                isLoading={isAgentsLoading}
              />
            </div>
          </div>

          {/* Right Column (4/12): Live Configuration Summary Preview */}
          <div className="lg:col-span-4 space-y-4 sticky top-6">
            <div className="bg-surface border border-border rounded p-5 space-y-4 shadow-2xs">
              <div className="flex items-center justify-between border-b border-border-subtle pb-3">
                <h3 className="text-xs uppercase tracking-wider font-bold text-text-muted">
                  Tóm tắt cấu hình
                </h3>
                <span className="text-[11px] font-sans font-semibold text-primary">Xem trước</span>
              </div>

              <div className="space-y-3 text-xs font-sans">
                <div className="flex items-start justify-between gap-2">
                  <span className="text-text-muted">Tên ca thi:</span>
                  <span className="font-bold text-text text-right truncate max-w-[180px]">
                    {name || '—'}
                  </span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-text-muted">Phòng thi:</span>
                  <span className="font-semibold text-text">{roomId || '—'}</span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-text-muted">Gateway:</span>
                  <span className="font-mono font-semibold text-text">{gatewayId || '—'}</span>
                </div>

                <div className="flex items-center justify-between">
                  <span className="text-text-muted">Số máy trạm:</span>
                  <span className="font-bold text-primary">{selectedWorkstations.length} máy</span>
                </div>

                <div className="pt-2 border-t border-border-subtle">
                  <span className="text-text-muted block mb-1">Chính sách đã chọn:</span>
                  <div className="p-2.5 bg-surface-subtle border border-border rounded text-xs">
                    <span className="font-bold text-text block">
                      {selectedProfile?.label || selectedProfileId || 'Chưa chọn'}
                    </span>
                    <span className="text-[11px] text-text-muted font-mono block mt-0.5">
                      {selectedProfile?.id}
                    </span>
                  </div>
                </div>
              </div>

              <Button
                type="submit"
                variant="primary"
                size="md"
                isLoading={isLoading}
                className="w-full justify-center font-bold bg-primary hover:bg-primary-dark text-surface mt-2"
              >
                Xác nhận tạo ca thi
              </Button>
            </div>
          </div>
        </div>
      </div>

      {/* Modal tạo Policy Profile mới nếu cần */}
      <CreatePolicyProfileModal
        isOpen={isCreateProfileModalOpen}
        onClose={() => setIsCreateProfileModalOpen(false)}
        onSuccess={handleProfileCreated}
      />
    </form>
  );
};
