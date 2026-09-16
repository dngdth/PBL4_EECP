import React from 'react';
import { SidebarHeader } from './SidebarHeader';
import { SidebarNav } from './SidebarNav';
import { Server } from 'lucide-react';
import { cn } from '@/src/shared/lib/cn';

export interface SidebarProps {
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

export const Sidebar: React.FC<SidebarProps> = ({ collapsed = false, onToggleCollapse }) => {
  return (
    <aside
      className={cn(
        'border-r border-border bg-surface flex flex-col h-screen shrink-0 hidden md:flex transition-all duration-200 ease-in-out select-none',
        collapsed ? 'w-16' : 'w-64'
      )}
    >
      <SidebarHeader collapsed={collapsed} onToggleCollapse={onToggleCollapse} />

      <div className="flex-1 overflow-y-auto overflow-x-hidden">
        <SidebarNav collapsed={collapsed} />
      </div>

      {/* Gateway Daemon Info footer */}
      <div
        className={cn(
          'border-t border-border-subtle bg-surface-subtle text-xs font-sans text-text-muted transition-all',
          collapsed ? 'p-2.5 flex flex-col items-center justify-center' : 'p-3.5'
        )}
      >
        {collapsed ? (
          <div
            className="relative cursor-pointer group flex items-center justify-center"
            title="Gateway Daemon: v2.4.1 (Trực tuyến)"
          >
            <Server className="w-5 h-5 text-text-muted group-hover:text-primary transition-colors" />
            <span className="absolute -top-0.5 -right-0.5 w-2 h-2 rounded-full bg-success ring-2 ring-surface animate-pulse" />
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2 mb-1 text-text font-semibold">
              <Server className="w-3.5 h-3.5 text-primary" />
              <span>Gateway Daemon</span>
            </div>
            <div className="flex items-center justify-between text-[11px]">
              <span>Phiên bản Core:</span>
              <span className="font-mono font-medium text-text">v2.4.1</span>
            </div>
            <div className="flex items-center justify-between text-[11px] mt-0.5">
              <span>Trạng thái kết nối:</span>
              <span className="text-success-dark font-semibold flex items-center gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse" />
                Trực tuyến
              </span>
            </div>
          </>
        )}
      </div>
    </aside>
  );
};

