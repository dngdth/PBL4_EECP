import React from 'react';
import { NavLink } from 'react-router-dom';
import { LayoutDashboard, Layers, PlusCircle, ShieldCheck } from 'lucide-react';
import { cn } from '@/src/shared/lib/cn';
import { UI_LABELS } from '@/src/shared/config/labels';

export interface SidebarNavProps {
  collapsed?: boolean;
  onItemClick?: () => void;
}

export const SidebarNav: React.FC<SidebarNavProps> = ({ collapsed, onItemClick }) => {
  const navItems = [
    {
      to: '/',
      label: UI_LABELS.nav.dashboard,
      icon: LayoutDashboard,
      end: true,
    },
    {
      to: '/sessions',
      label: UI_LABELS.nav.liveSessions,
      icon: Layers,
      end: true,
    },
    {
      to: '/sessions/new',
      label: UI_LABELS.nav.newSession,
      icon: PlusCircle,
      end: true,
    },
    {
      to: '/policies',
      label: UI_LABELS.nav.policies,
      icon: ShieldCheck,
      end: true,
    },
  ];

  return (
    <nav className={cn('space-y-1', collapsed ? 'p-2' : 'p-3')}>
      {navItems.map((item) => {
        const Icon = item.icon;
        return (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.end}
            onClick={onItemClick}
            className={({ isActive }) =>
              cn(
                'flex items-center rounded text-xs sm:text-sm font-sans font-medium transition-colors select-none group',
                collapsed
                  ? 'justify-center p-2.5'
                  : 'gap-3 px-3 py-2',
                isActive
                  ? 'bg-surface-subtle text-primary font-bold shadow-2xs border border-border-subtle'
                  : 'text-text-muted hover:text-text hover:bg-surface-subtle'
              )
            }
            title={collapsed ? item.label : undefined}
          >
            <Icon
              className={cn(
                'shrink-0 transition-transform group-hover:scale-105',
                collapsed ? 'w-5 h-5' : 'w-4 h-4'
              )}
            />
            {!collapsed && <span className="truncate">{item.label}</span>}
          </NavLink>
        );
      })}
    </nav>
  );
};

