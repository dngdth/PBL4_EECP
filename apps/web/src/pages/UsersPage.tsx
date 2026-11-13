import React, { FormEvent, useEffect, useMemo, useState } from 'react';
import {
  Users,
  Plus,
  RefreshCw,
  Lock,
  Unlock,
  KeyRound,
  AlertCircle,
  Check,
  Shield,
  UserCheck,
  X,
  Eye,
  EyeOff,
  Wand2,
  Copy,
  Search,
  Pencil,
} from 'lucide-react';
import {
  UserAccount,
  UserRole,
  createUser,
  listUsers,
  updateUser,
  useAuth,
} from '@/src/features/auth';
import { Button } from '@/src/shared/ui/button';
import { Input } from '@/src/shared/ui/input';
import { UI_LABELS } from '@/src/shared/config/labels';
import { Spinner } from '@/src/shared/ui/spinner';
import { formatDateTime } from '@/src/shared/lib/formatters';
import { cn } from '@/src/shared/lib/cn';

function generateTemporaryPassword(length = 10): string {
  const upper = 'ABCDEFGHJKLMNPQRSTUVWXYZ';
  const lower = 'abcdefghijkmnpqrstuvwxyz';
  const digits = '23456789';
  const symbols = '@#!';
  const all = upper + lower + digits + symbols;
  const pick = (pool: string) => pool[Math.floor(Math.random() * pool.length)];
  const chars = [pick(upper), pick(lower), pick(digits), pick(symbols)];
  while (chars.length < length) {
    chars.push(pick(all));
  }
  return chars.sort(() => Math.random() - 0.5).join('');
}

interface PasswordFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
}

const PasswordFieldWithActions: React.FC<PasswordFieldProps> = ({
  label,
  value,
  onChange,
  placeholder = 'Tối thiểu 6 ký tự',
}) => {
  const [visible, setVisible] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    if (!value) return;
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // Clipboard API may be blocked in non-HTTPS contexts
    }
  };

  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        <label className="text-xs font-semibold text-text">
          {label} <span className="text-error">*</span>
        </label>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              onChange(generateTemporaryPassword());
              setVisible(true);
            }}
            className="inline-flex items-center gap-1 text-[11px] font-semibold text-primary hover:underline cursor-pointer"
          >
            <Wand2 className="w-3 h-3" />
            <span>Tạo ngẫu nhiên</span>
          </button>
          {value && (
            <button
              type="button"
              onClick={handleCopy}
              className="inline-flex items-center gap-1 text-[11px] font-semibold text-text-muted hover:text-text cursor-pointer"
            >
              <Copy className="w-3 h-3" />
              <span>{copied ? 'Đã chép' : 'Sao chép'}</span>
            </button>
          )}
        </div>
      </div>
      <div className="relative">
        <input
          type={visible ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={placeholder}
          minLength={6}
          required
          autoComplete="new-password"
          className="w-full h-9 pl-3 pr-9 text-sm bg-surface border border-border rounded text-text placeholder:text-text-subtle focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary font-mono"
        />
        <button
          type="button"
          onClick={() => setVisible((prev) => !prev)}
          title={visible ? 'Ẩn mật khẩu' : 'Hiện mật khẩu'}
          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-text-muted hover:text-text cursor-pointer"
        >
          {visible ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
        </button>
      </div>
    </div>
  );
};

export const UsersPage: React.FC = () => {
  const { user: currentUser } = useAuth();
  const [accounts, setAccounts] = useState<UserAccount[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Filters
  const [searchQuery, setSearchQuery] = useState('');
  const [roleFilter, setRoleFilter] = useState<'ALL' | UserRole>('ALL');
  const [statusFilter, setStatusFilter] = useState<'ALL' | 'ACTIVE' | 'LOCKED'>('ALL');

  // Create form modal
  const [isCreateOpen, setIsCreateOpen] = useState(false);
  const [newUsername, setNewUsername] = useState('');
  const [newFullName, setNewFullName] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [newRole, setNewRole] = useState<UserRole>('EXAMINER');
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Edit profile modal
  const [editTarget, setEditTarget] = useState<UserAccount | null>(null);
  const [editFullName, setEditFullName] = useState('');
  const [editRole, setEditRole] = useState<UserRole>('EXAMINER');
  const [isSavingEdit, setIsSavingEdit] = useState(false);

  // Reset password modal
  const [resetTarget, setResetTarget] = useState<UserAccount | null>(null);
  const [resetPasswordValue, setResetPasswordValue] = useState('');
  const [isResetting, setIsResetting] = useState(false);
  const [togglingId, setTogglingId] = useState<string | null>(null);

  const fetchUsers = async (silent = false) => {
    if (!silent) setIsRefreshing(true);
    setErrorMsg(null);
    try {
      const data = await listUsers();
      setAccounts(data);
    } catch (err: any) {
      setErrorMsg(err.message || 'Không thể tải danh sách tài khoản.');
    } finally {
      setIsLoading(false);
      setIsRefreshing(false);
    }
  };

  useEffect(() => {
    fetchUsers(false);
  }, []);

  const filteredAccounts = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    return accounts.filter((account) => {
      if (roleFilter !== 'ALL' && account.role !== roleFilter) return false;
      if (statusFilter === 'ACTIVE' && !account.is_active) return false;
      if (statusFilter === 'LOCKED' && account.is_active) return false;
      if (
        q &&
        !account.full_name.toLowerCase().includes(q) &&
        !account.username.toLowerCase().includes(q)
      ) {
        return false;
      }
      return true;
    });
  }, [accounts, searchQuery, roleFilter, statusFilter]);

  const openCreateModal = () => {
    setNewFullName('');
    setNewUsername('');
    setNewPassword('');
    setNewRole('EXAMINER');
    setIsCreateOpen(true);
  };

  const handleCreate = async (event: FormEvent) => {
    event.preventDefault();
    setIsSubmitting(true);
    setErrorMsg(null);
    setSuccessMsg(null);
    try {
      const created = await createUser({
        username: newUsername.trim(),
        full_name: newFullName.trim(),
        password: newPassword,
        role: newRole,
      });
      setAccounts((prev) => [created, ...prev]);
      setSuccessMsg(`Đã tạo tài khoản "${created.username}" (${created.role}).`);
      setIsCreateOpen(false);
      setNewUsername('');
      setNewFullName('');
      setNewPassword('');
      setNewRole('EXAMINER');
    } catch (err: any) {
      setErrorMsg(err.message || 'Không thể tạo tài khoản mới.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleSaveEdit = async (event: FormEvent) => {
    event.preventDefault();
    if (!editTarget) return;
    setIsSavingEdit(true);
    setErrorMsg(null);
    setSuccessMsg(null);
    try {
      const isSelf = currentUser?.id === editTarget.id;
      const updated = await updateUser(editTarget.id, {
        full_name: editFullName.trim(),
        ...(isSelf ? {} : { role: editRole }),
      });
      setAccounts((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      setSuccessMsg(`Đã cập nhật thông tin tài khoản "${updated.username}".`);
      setEditTarget(null);
    } catch (err: any) {
      setErrorMsg(err.message || 'Không thể cập nhật thông tin tài khoản.');
    } finally {
      setIsSavingEdit(false);
    }
  };

  const handleToggleActive = async (account: UserAccount) => {
    setTogglingId(account.id);
    setErrorMsg(null);
    setSuccessMsg(null);
    try {
      const updated = await updateUser(account.id, { is_active: !account.is_active });
      setAccounts((prev) => prev.map((item) => (item.id === updated.id ? updated : item)));
      setSuccessMsg(
        updated.is_active
          ? `Đã mở khóa tài khoản "${updated.username}".`
          : `Đã khóa tài khoản "${updated.username}".`,
      );
    } catch (err: any) {
      setErrorMsg(err.message || 'Không thể cập nhật trạng thái tài khoản.');
    } finally {
      setTogglingId(null);
    }
  };

  const handleResetPassword = async (event: FormEvent) => {
    event.preventDefault();
    if (!resetTarget) return;
    setIsResetting(true);
    setErrorMsg(null);
    setSuccessMsg(null);
    try {
      await updateUser(resetTarget.id, { password: resetPasswordValue });
      setSuccessMsg(`Đã đặt lại mật khẩu cho "${resetTarget.username}".`);
      setResetTarget(null);
      setResetPasswordValue('');
    } catch (err: any) {
      setErrorMsg(err.message || 'Không thể đặt lại mật khẩu.');
    } finally {
      setIsResetting(false);
    }
  };

  return (
    <div className="flex flex-col min-h-full w-full overflow-x-hidden">
      <header className="bg-surface border-b border-border shadow-2xs shrink-0">
        <div className="w-full max-w-[1600px] mx-auto px-4 sm:px-6 lg:px-8 py-3.5 sm:py-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-4">
            <div>
              <h1 className="text-lg sm:text-xl font-bold font-sans tracking-tight text-text flex items-center gap-2">
                <Users className="w-5 h-5 text-primary" />
                <span>{UI_LABELS.nav.users}</span>
              </h1>
              <p className="text-xs text-text-muted mt-0.5 font-sans">
                Tạo tài khoản giám thị, cập nhật thông tin, cấp lại mật khẩu và khóa quyền truy cập khi kết thúc phân công.
              </p>
            </div>
            <div className="flex items-center gap-2.5 shrink-0">
              <Button
                variant="outline"
                size="sm"
                onClick={() => fetchUsers(false)}
                isLoading={isRefreshing}
                leftIcon={<RefreshCw className="w-3.5 h-3.5" />}
              >
                Làm mới
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={openCreateModal}
                leftIcon={<Plus className="w-4 h-4" />}
                className="font-bold bg-primary hover:bg-primary-dark text-surface shadow-2xs"
              >
                Tạo tài khoản
              </Button>
            </div>
          </div>
        </div>
      </header>

      <div className="w-full max-w-[1600px] mx-auto p-4 sm:p-6 lg:p-8 space-y-5 flex-1 min-w-0">
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

        {/* Search and Filters */}
        <div className="bg-surface border border-border rounded p-3.5 shadow-2xs flex flex-col sm:flex-row sm:items-center gap-3">
          <div className="relative flex-1 min-w-0">
            <Search className="w-4 h-4 text-text-muted absolute left-3 top-1/2 -translate-y-1/2 pointer-events-none" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Tìm theo họ tên hoặc tên đăng nhập..."
              className="w-full h-9 pl-9 pr-3 text-sm bg-surface border border-border rounded text-text placeholder:text-text-subtle focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary"
            />
          </div>
          <div className="flex flex-wrap items-center gap-2.5 shrink-0">
            <select
              value={roleFilter}
              onChange={(e) => setRoleFilter(e.target.value as 'ALL' | UserRole)}
              className="h-9 px-3 text-xs font-medium bg-surface border border-border rounded text-text"
            >
              <option value="ALL">Tất cả vai trò</option>
              <option value="EXAMINER">Giám thị</option>
              <option value="ADMIN">Quản trị viên</option>
            </select>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value as 'ALL' | 'ACTIVE' | 'LOCKED')}
              className="h-9 px-3 text-xs font-medium bg-surface border border-border rounded text-text"
            >
              <option value="ALL">Tất cả trạng thái</option>
              <option value="ACTIVE">Đang hoạt động</option>
              <option value="LOCKED">Đã khóa</option>
            </select>
            <span className="text-xs text-text-muted font-medium px-1">
              {filteredAccounts.length}/{accounts.length} tài khoản
            </span>
          </div>
        </div>

        {isLoading ? (
          <div className="py-16 flex flex-col items-center justify-center">
            <Spinner size="lg" />
            <p className="text-sm text-text-muted mt-3">Đang tải danh sách tài khoản...</p>
          </div>
        ) : (
          <div className="bg-surface border border-border rounded shadow-2xs overflow-x-auto">
            <table className="w-full text-left border-collapse text-sm">
              <thead>
                <tr className="bg-surface-subtle border-b border-border text-xs uppercase tracking-wider text-text-muted">
                  <th className="py-3 px-4 font-bold">Họ và tên</th>
                  <th className="py-3 px-4 font-bold">Tên đăng nhập</th>
                  <th className="py-3 px-4 font-bold">Vai trò</th>
                  <th className="py-3 px-4 font-bold">Trạng thái</th>
                  <th className="py-3 px-4 font-bold">Ngày tạo</th>
                  <th className="py-3 px-4 font-bold text-right">Thao tác</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-border-subtle">
                {filteredAccounts.map((account) => {
                  const isSelf = currentUser?.id === account.id;
                  return (
                    <tr key={account.id} className="hover:bg-surface-subtle/60 transition-colors">
                      <td className="py-3 px-4 font-semibold text-text whitespace-nowrap">
                        <span>{account.full_name}</span>
                        {isSelf && (
                          <span className="ml-2 text-[11px] font-semibold px-1.5 py-0.5 rounded bg-primary-soft text-primary">
                            Bạn
                          </span>
                        )}
                      </td>
                      <td className="py-3 px-4 font-mono text-xs text-text-muted">{account.username}</td>
                      <td className="py-3 px-4 whitespace-nowrap">
                        <span
                          className={cn(
                            'inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-bold border whitespace-nowrap',
                            account.role === 'ADMIN'
                              ? 'bg-primary-soft text-primary border-primary/20'
                              : 'bg-surface-subtle text-text border-border',
                          )}
                        >
                          {account.role === 'ADMIN' ? (
                            <Shield className="w-3 h-3" />
                          ) : (
                            <UserCheck className="w-3 h-3" />
                          )}
                          {account.role === 'ADMIN' ? 'Quản trị viên' : 'Giám thị'}
                        </span>
                      </td>
                      <td className="py-3 px-4 whitespace-nowrap">
                        <span
                          className={cn(
                            'inline-flex items-center px-2 py-0.5 rounded text-[11px] font-semibold border whitespace-nowrap',
                            account.is_active
                              ? 'bg-success-soft text-success-dark border-success/30'
                              : 'bg-error-soft text-error-dark border-error/30',
                          )}
                        >
                          {account.is_active ? 'Đang hoạt động' : 'Đã khóa'}
                        </span>
                      </td>
                      <td className="py-3 px-4 text-xs text-text-muted">
                        {formatDateTime(account.created_at)}
                      </td>
                      <td className="py-3 px-4 text-right space-x-2 whitespace-nowrap">
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            setEditTarget(account);
                            setEditFullName(account.full_name);
                            setEditRole(account.role);
                          }}
                          leftIcon={<Pencil className="w-3.5 h-3.5" />}
                        >
                          Sửa
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            setResetTarget(account);
                            setResetPasswordValue('');
                          }}
                          leftIcon={<KeyRound className="w-3.5 h-3.5" />}
                        >
                          Đổi mật khẩu
                        </Button>
                        {!isSelf && (
                          <Button
                            variant="outline"
                            size="sm"
                            isLoading={togglingId === account.id}
                            onClick={() => handleToggleActive(account)}
                            leftIcon={
                              account.is_active ? (
                                <Lock className="w-3.5 h-3.5" />
                              ) : (
                                <Unlock className="w-3.5 h-3.5" />
                              )
                            }
                          >
                            {account.is_active ? 'Khóa' : 'Mở khóa'}
                          </Button>
                        )}
                      </td>
                    </tr>
                  );
                })}
                {filteredAccounts.length === 0 && (
                  <tr>
                    <td colSpan={6} className="py-10 text-center text-sm text-text-muted">
                      {accounts.length === 0
                        ? 'Chưa có tài khoản nào trong cơ sở dữ liệu.'
                        : 'Không tìm thấy tài khoản nào khớp bộ lọc.'}
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>

      {/* Create User Modal */}
      {isCreateOpen && (
        <div className="fixed inset-0 z-50 bg-text/40 backdrop-blur-xs grid place-items-center p-4">
          <form
            onSubmit={handleCreate}
            autoComplete="off"
            className="w-full max-w-md bg-surface border border-border rounded shadow-lg p-5 space-y-4"
          >
            <div className="flex items-center justify-between border-b border-border-subtle pb-3">
              <h2 className="text-base font-bold text-text">Tạo tài khoản mới</h2>
              <button
                type="button"
                onClick={() => setIsCreateOpen(false)}
                className="p-1 text-text-muted hover:text-text cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <Input
              label="Họ và tên"
              placeholder="VD: Nguyễn Văn A"
              value={newFullName}
              onChange={(e) => setNewFullName(e.target.value)}
              autoComplete="off"
              required
            />
            <Input
              label="Tên đăng nhập"
              placeholder="VD: giamthi01"
              value={newUsername}
              onChange={(e) => setNewUsername(e.target.value)}
              autoComplete="off"
              required
            />
            <PasswordFieldWithActions
              label="Mật khẩu"
              value={newPassword}
              onChange={setNewPassword}
            />
            <div>
              <label className="block text-xs font-semibold text-text mb-1.5">Vai trò</label>
              <select
                value={newRole}
                onChange={(e) => setNewRole(e.target.value as UserRole)}
                className="w-full h-9 px-3 text-sm bg-surface border border-border rounded text-text"
              >
                <option value="EXAMINER">Giám thị</option>
                <option value="ADMIN">Quản trị viên</option>
              </select>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="secondary" type="button" onClick={() => setIsCreateOpen(false)}>
                Hủy
              </Button>
              <Button
                type="submit"
                variant="primary"
                isLoading={isSubmitting}
                className="font-bold bg-primary hover:bg-primary-dark text-surface"
              >
                Tạo tài khoản
              </Button>
            </div>
          </form>
        </div>
      )}

      {/* Edit User Modal */}
      {editTarget && (
        <div className="fixed inset-0 z-50 bg-text/40 backdrop-blur-xs grid place-items-center p-4">
          <form
            onSubmit={handleSaveEdit}
            autoComplete="off"
            className="w-full max-w-md bg-surface border border-border rounded shadow-lg p-5 space-y-4"
          >
            <div className="flex items-center justify-between border-b border-border-subtle pb-3">
              <h2 className="text-base font-bold text-text">
                Sửa thông tin ({editTarget.username})
              </h2>
              <button
                type="button"
                onClick={() => setEditTarget(null)}
                className="p-1 text-text-muted hover:text-text cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <Input
              label="Họ và tên"
              value={editFullName}
              onChange={(e) => setEditFullName(e.target.value)}
              autoComplete="off"
              required
            />
            <div>
              <label className="block text-xs font-semibold text-text mb-1.5">Vai trò</label>
              <select
                value={editRole}
                onChange={(e) => setEditRole(e.target.value as UserRole)}
                disabled={currentUser?.id === editTarget.id}
                className="w-full h-9 px-3 text-sm bg-surface border border-border rounded text-text disabled:opacity-60"
              >
                <option value="EXAMINER">Giám thị</option>
                <option value="ADMIN">Quản trị viên</option>
              </select>
              {currentUser?.id === editTarget.id && (
                <p className="text-[11px] text-text-muted mt-1">
                  Không thể tự thay đổi vai trò của tài khoản đang đăng nhập.
                </p>
              )}
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="secondary" type="button" onClick={() => setEditTarget(null)}>
                Hủy
              </Button>
              <Button
                type="submit"
                variant="primary"
                isLoading={isSavingEdit}
                className="font-bold bg-primary hover:bg-primary-dark text-surface"
              >
                Lưu thay đổi
              </Button>
            </div>
          </form>
        </div>
      )}

      {/* Reset Password Modal */}
      {resetTarget && (
        <div className="fixed inset-0 z-50 bg-text/40 backdrop-blur-xs grid place-items-center p-4">
          <form
            onSubmit={handleResetPassword}
            autoComplete="off"
            className="w-full max-w-md bg-surface border border-border rounded shadow-lg p-5 space-y-4"
          >
            <div className="flex items-center justify-between border-b border-border-subtle pb-3">
              <h2 className="text-base font-bold text-text">
                Đặt lại mật khẩu ({resetTarget.username})
              </h2>
              <button
                type="button"
                onClick={() => setResetTarget(null)}
                className="p-1 text-text-muted hover:text-text cursor-pointer"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
            <PasswordFieldWithActions
              label="Mật khẩu mới"
              value={resetPasswordValue}
              onChange={setResetPasswordValue}
            />
            <div className="flex justify-end gap-2 pt-2">
              <Button variant="secondary" type="button" onClick={() => setResetTarget(null)}>
                Hủy
              </Button>
              <Button
                type="submit"
                variant="primary"
                isLoading={isResetting}
                className="font-bold bg-primary hover:bg-primary-dark text-surface"
              >
                Cập nhật mật khẩu
              </Button>
            </div>
          </form>
        </div>
      )}
    </div>
  );
};
