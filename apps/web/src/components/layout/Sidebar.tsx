import React from 'react';
import { useNavigate } from 'react-router-dom';
import { SidebarHeader } from './SidebarHeader';
import { SidebarNav } from './SidebarNav';
import { LogOut, Server, UserCheck, Shield } from 'lucide-react';
import { cn } from '@/src/shared/lib/cn';
import { useAuth } from '@/src/features/auth';

export interface SidebarProps {
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ collapsed = false, onToggleCollapse }) => {
  const navigate = useNavigate();
  const { user, authEnforced, logout } = useAuth();

  const handleLogout = async () => {
    await logout();
    if (authEnforced) {
      navigate('/login', { replace: true });
    }
  };

  return (
    <aside
      className={cn(
        'border-r border-border bg-surface flex flex-col h-screen shrink-0 hidden md:flex transition-all duration-200 ease-in-out select-none',
        collapsed ? 'w-16' : 'w-64',
      )}
    >
      <SidebarHeader collapsed={collapsed} onToggleCollapse={onToggleCollapse} />

      <div className="flex-1 overflow-y-auto overflow-x-hidden">
        <SidebarNav collapsed={collapsed} />
      </div>

      {/* User profile & Logout */}
      {user && (
        <div
          className={cn(
            'border-t border-border-subtle bg-surface text-xs font-sans transition-all',
            collapsed ? 'p-2 flex flex-col items-center gap-2' : 'p-3.5 space-y-2.5',
          )}
        >
          {collapsed ? (
            <>
              <div
                className="w-8 h-8 rounded bg-primary-soft text-primary flex items-center justify-center"
                title={`${user.full_name} (${user.role === 'ADMIN' ? 'Quản trị viên' : 'Giám thị'})`}
              >
                {user.role === 'ADMIN' ? (
                  <Shield className="w-4 h-4" />
                ) : (
                  <UserCheck className="w-4 h-4" />
                )}
              </div>
              {authEnforced && (
                <button
                  type="button"
                  onClick={handleLogout}
                  title="Đăng xuất"
                  className="p-2 text-text-muted hover:text-error hover:bg-error-soft rounded transition-colors cursor-pointer"
                >
                  <LogOut className="w-4 h-4" />
                </button>
              )}
            </>
          ) : (
            <div className="flex items-center justify-between gap-2">
              <div className="min-w-0">
                <div className="font-semibold text-text truncate">{user.full_name}</div>
                <div className="flex items-center gap-1.5 mt-0.5">
                  <span className="font-mono text-[10px] text-text-muted truncate">
                    @{user.username}
                  </span>
                  <span
                    className={cn(
                      'px-1.5 py-0.2 rounded text-[10px] font-bold border whitespace-nowrap',
                      user.role === 'ADMIN'
                        ? 'bg-primary-soft text-primary border-primary/20'
                        : 'bg-surface-subtle text-text-muted border-border',
                    )}
                  >
                    {user.role === 'ADMIN' ? 'Quản trị viên' : 'Giám thị'}
                  </span>
                </div>
              </div>
              {authEnforced && (
                <button
                  type="button"
                  onClick={handleLogout}
                  title="Đăng xuất"
                  className="p-1.5 text-text-muted hover:text-error hover:bg-error-soft rounded transition-colors shrink-0 cursor-pointer"
                >
                  <LogOut className="w-4 h-4" />
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {/* System Info footer */}
      <div
        className={cn(
          'border-t border-border-subtle bg-surface-subtle text-xs font-sans text-text-muted transition-all',
          collapsed ? 'p-2.5 flex flex-col items-center justify-center' : 'p-3.5',
        )}
      >
        {collapsed ? (
          <div
            className="flex items-center justify-center"
            title="EECP Exam Control"
          >
            <Server className="w-4 h-4 text-text-muted" />
          </div>
        ) : (
          <div className="flex items-center justify-between text-[11px]">
            <span className="inline-flex items-center gap-1.5 font-semibold text-text">
              <Server className="w-3.5 h-3.5 text-primary" />
              EECP Exam Control
            </span>
            <span className="text-text-muted">
              {authEnforced ? 'Bảo mật: Bật' : 'Chế độ thử nghiệm'}
            </span>
          </div>
        )}
      </div>
    </aside>
  );
};
