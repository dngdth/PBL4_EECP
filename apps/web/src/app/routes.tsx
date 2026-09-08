import React from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';
import { AppLayout } from './layout';
import { OverviewPage } from '@/src/pages/OverviewPage';
import { SessionListPage } from '@/src/pages/SessionListPage';
import { SessionCreatePage } from '@/src/pages/SessionCreatePage';
import { SessionDetailPage } from '@/src/pages/SessionDetailPage';

import { PolicyProfilesPage } from '@/src/pages/PolicyProfilesPage';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppLayout />,
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
        element: <SessionCreatePage />,
      },
      {
        path: 'sessions/:sessionId',
        element: <SessionDetailPage />,
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
