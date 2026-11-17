import React, { Suspense, lazy } from 'react';
import { createBrowserRouter, Navigate } from 'react-router-dom';
import { AppLayout } from './layout';
import { useAuth } from '@/src/features/auth';
import { Spinner } from '@/src/shared/ui/spinner';

const OverviewPage = lazy(() =>
  import('@/src/pages/OverviewPage').then((m) => ({ default: m.OverviewPage }))
);
const SessionListPage = lazy(() =>
  import('@/src/pages/SessionListPage').then((m) => ({ default: m.SessionListPage }))
);
const SessionCreatePage = lazy(() =>
  import('@/src/pages/SessionCreatePage').then((m) => ({ default: m.SessionCreatePage }))
);
const SessionDetailPage = lazy(() =>
  import('@/src/pages/SessionDetailPage').then((m) => ({ default: m.SessionDetailPage }))
);
const PolicyProfilesPage = lazy(() =>
  import('@/src/pages/PolicyProfilesPage').then((m) => ({ default: m.PolicyProfilesPage }))
);
const WorkstationsPage = lazy(() =>
  import('@/src/pages/WorkstationsPage').then((m) => ({ default: m.WorkstationsPage }))
);
const LoginPage = lazy(() =>
  import('@/src/pages/LoginPage').then((m) => ({ default: m.LoginPage }))
);
const GatewaysPage = lazy(() =>
  import('@/src/pages/GatewaysPage').then((m) => ({ default: m.GatewaysPage }))
);
const UsersPage = lazy(() =>
  import('@/src/pages/UsersPage').then((m) => ({ default: m.UsersPage }))
);

const PageFallback: React.FC = () => (
  <main className="min-h-[60vh] grid place-items-center bg-background">
    <Spinner size="lg" />
  </main>
);

const withSuspense = (node: React.ReactNode) => (
  <Suspense fallback={<PageFallback />}>{node}</Suspense>
);

const RequireAuthentication: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated, isLoading } = useAuth();
  if (isLoading && !isAuthenticated) {
    return (
      <main className="min-h-screen grid place-items-center bg-background">
        <Spinner size="lg" />
      </main>
    );
  }
  return isAuthenticated ? <>{children}</> : <Navigate to="/login" replace />;
};

const RequireAdmin: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAdmin, isLoading } = useAuth();
  if (isLoading) {
    return (
      <main className="min-h-screen grid place-items-center bg-background">
        <Spinner size="lg" />
      </main>
    );
  }
  return isAdmin ? <>{children}</> : <Navigate to="/" replace />;
};

export const router = createBrowserRouter([
  {
    path: '/login',
    element: withSuspense(<LoginPage />),
  },
  {
    path: '/',
    element: (
      <RequireAuthentication>
        <AppLayout />
      </RequireAuthentication>
    ),
    children: [
      {
        index: true,
        element: withSuspense(<OverviewPage />),
      },
      {
        path: 'sessions',
        element: withSuspense(<SessionListPage />),
      },
      {
        path: 'sessions/create',
        element: (
          <RequireAdmin>
            {withSuspense(<SessionCreatePage />)}
          </RequireAdmin>
        ),
      },
      {
        path: 'sessions/new',
        element: <Navigate to="/sessions/create" replace />,
      },
      {
        path: 'sessions/:sessionId',
        element: withSuspense(<SessionDetailPage />),
      },
      {
        path: 'gateways',
        element: withSuspense(<GatewaysPage />),
      },
      {
        path: 'workstations',
        element: withSuspense(<WorkstationsPage />),
      },
      {
        path: 'policies',
        element: withSuspense(<PolicyProfilesPage />),
      },
      {
        path: 'users',
        element: (
          <RequireAdmin>
            {withSuspense(<UsersPage />)}
          </RequireAdmin>
        ),
      },
      {
        path: '*',
        element: <Navigate to="/" replace />,
      },
    ],
  },
]);
