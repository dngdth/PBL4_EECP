import React, { useEffect, useState } from 'react';
import { 
  ShieldCheck, 
  Plus, 
  Trash2, 
  RefreshCw, 
  Lock, 
  Check, 
  FileCode, 
  ChevronDown, 
  ChevronUp, 
  AlertCircle, 
  Layers, 
  BookOpen,
  Pencil
} from 'lucide-react';
import { PolicyProfile } from '@/src/domain';
import { 
  listPolicyProfiles, 
  deletePolicyProfile, 
  CreatePolicyProfileModal,
  EditPolicyProfileModal
} from '@/src/features/security-policy';
import { Button } from '@/src/shared/ui/button';
import { Spinner } from '@/src/shared/ui/spinner';
import { cn } from '@/src/shared/lib/cn';

export const PolicyProfilesPage: React.FC = () => {
  const [profiles, setProfiles] = useState<PolicyProfile[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const [isCreateModalOpen, setIsCreateModalOpen] = useState<boolean>(false);
  const [editingProfile, setEditingProfile] = useState<PolicyProfile | null>(null);
  const [expandedYamlId, setExpandedYamlId] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);

  const fetchProfiles = async (silent = false) => {
    if (!silent) setIsRefreshing(true);
    setErrorMsg(null);
    try {
      const data = await listPolicyProfiles();
      setProfiles(data);
    } catch (err: any) {
      setErrorMsg(err.message || 'Không thể tải danh sách chính sách bảo mật.');
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    fetchProfiles(false);
  }, []);

  const handleDelete = async (profileId: string) => {
    if (!window.confirm(`Bạn có chắc chắn muốn xóa chính sách "${profileId}" không?`)) {
      return;
    }

    setDeletingId(profileId);
    setErrorMsg(null);
    setSuccessMsg(null);

    try {
      await deletePolicyProfile(profileId);
      setSuccessMsg(`Đã xóa thành công chính sách ${profileId}.`);
      setProfiles((prev) => prev.filter((p) => p.id !== profileId));
    } catch (err: any) {
      if (err.status === 409) {
        setErrorMsg(`Không thể xóa "${profileId}" vì chính sách này đã được áp dụng trong ca thi trước đó (Policy in use).`);
      } else {
        setErrorMsg(err.message || `Lỗi khi xóa chính sách "${profileId}".`);
      }
    } finally {
      setDeletingId(null);
    }
  };

  const handleProfileCreated = (newProfile: PolicyProfile) => {
    setProfiles((prev) => [newProfile, ...prev]);
    setSuccessMsg(`Đã thêm thành công chính sách "${newProfile.label}".`);
  };

  return (
    <div className="flex flex-col min-h-full w-full overflow-x-hidden">
      {/* 1. Global Header */}
      <header className="bg-surface border-b border-border shadow-2xs shrink-0">
        <div className="w-full max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-3.5 sm:py-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-4">
            <div>
              <h1 className="text-lg sm:text-xl font-bold font-sans tracking-tight text-text flex items-center gap-2">
                <ShieldCheck className="w-5 h-5 text-primary" />
                <span>Hồ sơ chính sách bảo mật</span>
              </h1>
              <p className="text-xs text-text-muted mt-0.5 font-sans">
                Quản lý các danh mục quy tắc cô lập mạng, ứng dụng và khóa ngoại vi máy trạm.
              </p>
            </div>

            <div className="flex items-center gap-2.5 shrink-0">
              <Button
                variant="outline"
                size="sm"
                onClick={() => fetchProfiles(false)}
                isLoading={isRefreshing}
                leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
              >
                Làm mới
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={() => setIsCreateModalOpen(true)}
                leftIcon={<Plus className="w-4 h-4" />}
                className="font-bold bg-primary hover:bg-primary-dark text-surface shadow-2xs"
              >
                Tạo chính sách mới
              </Button>
            </div>
          </div>
        </div>
      </header>

      {/* 2. Main Content */}
      <div className="w-full max-w-[1600px] mx-auto p-4 sm:p-6 lg:p-8 space-y-6 flex-1 min-w-0">
        {errorMsg && (
          <div className="p-4 bg-error-soft border border-error/30 rounded text-sm text-error-dark flex items-center gap-3">
            <AlertCircle className="w-5 h-5 shrink-0" />
            <div className="flex-1 font-medium">{errorMsg}</div>
          </div>
        )}

        {successMsg && (
          <div className="p-4 bg-success-soft border border-success/30 rounded text-sm text-success-dark flex items-center gap-3">
            <Check className="w-5 h-5 shrink-0" />
            <div className="flex-1 font-medium">{successMsg}</div>
          </div>
        )}

        {isLoading ? (
          <div className="py-24 flex flex-col items-center justify-center">
            <Spinner size="lg" label="Đang tải danh mục chính sách..." />
          </div>
        ) : profiles.length === 0 ? (
          <div className="p-12 text-center bg-surface border border-dashed border-border rounded">
            <ShieldCheck className="w-12 h-12 text-text-subtle mx-auto mb-3" />
            <h3 className="font-bold text-text text-base">Chưa có chính sách nào</h3>
            <p className="text-xs text-text-muted mt-1 max-w-md mx-auto">
              Hãy tạo chính sách bảo mật đầu tiên để áp dụng cho các ca thi trên máy trạm.
            </p>
            <Button
              variant="primary"
              size="sm"
              onClick={() => setIsCreateModalOpen(true)}
              className="mt-4 font-bold"
            >
              Tạo chính sách ngay
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {profiles.map((profile) => {
              const allowedApps = profile.rules?.applications?.allow || [];
              const deniedApps = profile.rules?.applications?.deny || [];
              const blockedNet = profile.rules?.network?.block || [];
              const isUsbDenied = profile.rules?.devices?.usb === 'deny';
              const isYamlOpen = expandedYamlId === profile.id;

              return (
                <div
                  key={profile.id}
                  className="bg-surface border border-border rounded p-5 shadow-2xs space-y-4 flex flex-col justify-between"
                >
                  <div className="space-y-3">
                    {/* Header */}
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="flex items-center gap-2 flex-wrap">
                          <h3 className="font-bold text-base text-text font-sans">{profile.label}</h3>
                          {profile.is_builtin ? (
                            <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-surface-subtle text-text-muted border border-border">
                              HỆ THỐNG (BUILT-IN)
                            </span>
                          ) : (
                            <span className="px-2 py-0.5 rounded text-[10px] font-bold bg-primary-soft text-primary border border-primary/20">
                              TÙY BIẾN (CUSTOM)
                            </span>
                          )}
                        </div>
                        <span className="font-mono text-xs text-text-muted block mt-0.5">{profile.id}</span>
                      </div>

                      {!profile.is_builtin && (
                        <div className="flex items-center gap-1">
                          <button
                            type="button"
                            onClick={() => setEditingProfile(profile)}
                            className="p-1.5 text-text-muted hover:text-primary hover:bg-primary-soft rounded cursor-pointer transition-colors"
                            title="Chỉnh sửa chính sách"
                          >
                            <Pencil className="w-4 h-4" />
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDelete(profile.id)}
                            disabled={deletingId === profile.id}
                            className="p-1.5 text-text-muted hover:text-error hover:bg-error-soft rounded cursor-pointer transition-colors"
                            title="Xóa chính sách"
                          >
                            <Trash2 className="w-4 h-4" />
                          </button>
                        </div>
                      )}
                    </div>

                    <p className="text-xs text-text-muted leading-relaxed">
                      {profile.description || 'Không có mô tả chi tiết.'}
                    </p>

                    {/* Rule summary badges */}
                    <div className="flex flex-wrap items-center gap-1.5 text-xs font-medium pt-1">
                      {allowedApps.length > 0 && (
                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-success-soft text-success-dark border border-success/20">
                          <Check className="w-3.5 h-3.5" />
                          <span>{allowedApps.length} ứng dụng cho phép</span>
                        </span>
                      )}

                      {deniedApps.length > 0 && (
                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-error-soft text-error-dark border border-error/20">
                          <Lock className="w-3.5 h-3.5" />
                          <span>{deniedApps.length} ứng dụng cấm</span>
                        </span>
                      )}

                      {blockedNet.length > 0 && (
                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-warning-soft text-warning-dark border border-warning/20">
                          <Lock className="w-3.5 h-3.5" />
                          <span>Chặn AI/Mạng</span>
                        </span>
                      )}

                      {isUsbDenied && (
                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded bg-error-soft text-error-dark border border-error/20">
                          <Lock className="w-3.5 h-3.5" />
                          <span>Khóa USB</span>
                        </span>
                      )}
                    </div>

                    {/* Apps list display */}
                    {allowedApps.length > 0 && (
                      <div className="pt-2">
                        <span className="text-[11px] font-bold text-text-muted block mb-1">
                          Ứng dụng hợp lệ:
                        </span>
                        <div className="flex flex-wrap gap-1">
                          {allowedApps.map((app) => (
                            <span
                              key={app}
                              className="px-2 py-0.5 bg-surface-subtle border border-border text-[11px] font-mono text-text rounded"
                            >
                              {app}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>

                  {/* YAML Preview toggle */}
                  {profile.yaml && (
                    <div className="pt-3 border-t border-border-subtle">
                      <button
                        type="button"
                        onClick={() => setExpandedYamlId(isYamlOpen ? null : profile.id)}
                        className="text-xs text-primary hover:text-primary-dark font-medium flex items-center gap-1.5 cursor-pointer transition-colors"
                      >
                        <FileCode className="w-3.5 h-3.5" />
                        <span>{isYamlOpen ? 'Ẩn bản tin cấu hình YAML' : 'Xem bản tin cấu hình YAML'}</span>
                        {isYamlOpen ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
                      </button>

                      {isYamlOpen && (
                        <div className="mt-2 p-3 bg-surface-subtle border border-border rounded font-mono text-[11px] text-text leading-tight overflow-x-auto whitespace-pre select-text">
                          {profile.yaml}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Modal tạo chính sách mới */}
      <CreatePolicyProfileModal
        isOpen={isCreateModalOpen}
        onClose={() => setIsCreateModalOpen(false)}
        onSuccess={handleProfileCreated}
      />

      {/* Modal chỉnh sửa chính sách */}
      <EditPolicyProfileModal
        isOpen={Boolean(editingProfile)}
        profile={editingProfile}
        onClose={() => setEditingProfile(null)}
        onSuccess={(updated) => {
          setProfiles((prev) => prev.map((p) => (p.id === updated.id ? updated : p)));
          setSuccessMsg(`Đã cập nhật thành công chính sách "${updated.label}".`);
        }}
      />
    </div>
  );
};
