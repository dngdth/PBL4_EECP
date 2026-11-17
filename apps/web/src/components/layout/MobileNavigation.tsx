import React from 'react';
import { useNavigate } from 'react-router-dom';
import { SidebarHeader } from './SidebarHeader';
import { SidebarNav } from './SidebarNav';
import { LogOut, X } from 'lucide-react';
import { useAuth } from '@/src/features/auth';

export interface MobileNavigationProps {
  isOpen: boolean;
  onClose: () => void;
}

export const MobileNavigation: React.FC<MobileNavigationProps> = ({ isOpen, onClose }) => {
  const navigate = useNavigate();
  const { user, authEnforced, logout } = useAuth();

  if (!isOpen) return null;

  const handleLogout = async () => {
    await logout();
    onClose();
    if (authEnforced) {
      navigate('/login', { replace: true });
    }
  };

  return (
    <div className="fixed inset-0 z-50 md:hidden flex">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-text/40 backdrop-blur-xs transition-opacity"
        onClick={onClose}
      />

      {/* Drawer */}
      <div className="relative w-72 max-w-[80%] bg-surface border-r border-border flex flex-col h-full z-10 shadow-lg">
        <div className="flex items-center justify-between pr-3">
          <SidebarHeader />
          <button
            type="button"
            onClick={onClose}
            className="p-2 text-text-muted hover:text-text rounded"
            aria-label="Đóng menu"
          >
            <X className="w-5 h-5" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto">
          <SidebarNav onItemClick={onClose} />
        </div>
        {user && (
          <div className="p-4 border-t border-border-subtle flex items-center justify-between gap-2 text-xs">
            <div className="min-w-0">
              <div className="font-semibold text-text truncate">{user.full_name}</div>
              <div className="text-text-muted font-mono text-[11px]">
                @{user.username} · {user.role}
              </div>
            </div>
            {authEnforced && (
              <button
                type="button"
                onClick={handleLogout}
                className="flex items-center gap-1 px-2.5 py-1.5 rounded text-error hover:bg-error-soft font-semibold"
              >
                <LogOut className="w-3.5 h-3.5" />
                <span>Đăng xuất</span>
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
};
