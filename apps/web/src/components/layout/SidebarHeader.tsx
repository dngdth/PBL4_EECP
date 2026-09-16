import React from 'react';
import { Shield, ChevronLeft, ChevronRight, PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import { Link } from 'react-router-dom';
import { cn } from '@/src/shared/lib/cn';

export interface SidebarHeaderProps {
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

export const SidebarHeader: React.FC<SidebarHeaderProps> = ({ collapsed, onToggleCollapse }) => {
  return (
    <div
      className={cn(
        'h-14 flex items-center border-b border-border bg-surface shrink-0 transition-all duration-200',
        collapsed ? 'justify-center px-2' : 'justify-between px-3.5'
      )}
    >
      <Link
        to="/"
        className={cn(
          'flex items-center gap-2.5 min-w-0 group',
          collapsed ? 'justify-center' : ''
        )}
        title="PBL4 Ops Center"
      >
        <div className="w-8 h-8 rounded-sm bg-primary text-surface flex items-center justify-center font-bold shrink-0 shadow-2xs group-hover:bg-primary-dark transition-colors">
          <Shield className="w-5 h-5" />
        </div>
        {!collapsed && (
          <div className="flex flex-col min-w-0">
            <span className="font-bold font-sans text-sm tracking-tight text-text truncate group-hover:text-primary transition-colors">
              PBL4 Ops Center
            </span>
            <span className="text-[10px] uppercase font-sans font-semibold tracking-wider text-text-muted">
              Hệ thống giám sát thi
            </span>
          </div>
        )}
      </Link>

      {onToggleCollapse && (
        <button
          type="button"
          onClick={onToggleCollapse}
          className={cn(
            'p-1.5 rounded text-text-muted hover:text-text hover:bg-surface-subtle transition-colors cursor-pointer',
            collapsed ? 'mt-1' : ''
          )}
          title={collapsed ? 'Mở rộng thanh điều hướng' : 'Thu gọn thanh điều hướng'}
          aria-label={collapsed ? 'Mở rộng thanh điều hướng' : 'Thu gọn thanh điều hướng'}
        >
          {collapsed ? (
            <ChevronRight className="w-4 h-4" />
          ) : (
            <ChevronLeft className="w-4 h-4" />
          )}
        </button>
      )}
    </div>
  );
};

