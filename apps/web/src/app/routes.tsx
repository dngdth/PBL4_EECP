import React from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';
import { AppLayout } from './layout';
import { OverviewPage } from '@/src/pages/OverviewPage';
import { SessionListPage } from '@/src/pages/SessionListPage';
import { SessionCreatePage } from '@/src/pages/SessionCreatePage';
import { SessionDetailPage } from '@/src/pages/SessionDetailPage';

import { PolicyProfilesPage } from '@/src/pages/PolicyProfilesPage';
import { WorkstationsPage } from '@/src/pages/WorkstationsPage';
import { LoginPage } from '@/src/pages/LoginPage';
import { GatewaysPage } from '@/src/pages/GatewaysPage';

const RequireAuthentication: React.FC<{ children: React.ReactNode }> = ({ children }) =>
  sessionStorage.getItem('eecp_access_token') ? children : <Navigate to="/login" replace />;

export const router = createBrowserRouter([
  {
    path: '/login',
    element: <LoginPage />,
  },
  {
    path: '/',
    element: <RequireAuthentication><AppLayout /></RequireAuthentication>,
    children: [
      {
        index: true,
        element: <OverviewPage />,
      },
      {
        path: 'sessions',
        element: <SessionListPage />,
      },
      {
        path: 'sessions/create',
        element: <SessionCreatePage />,
      },
      {
        path: 'sessions/new',
        element: <Navigate to="/sessions/create" replace />,
      },
      {
        path: 'sessions/:sessionId',
        element: <SessionDetailPage />,
      },
      {
        path: 'gateways',
        element: <GatewaysPage />,
      },
      {
        path: 'workstations',
        element: <WorkstationsPage />,
      },
      {
        path: 'policies',
        element: <PolicyProfilesPage />,
      },
      {
        path: '*',
        element: <Navigate to="/" replace />,
      },
    ],
  },
]);
