import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import {
  AuthUser,
  fetchMe,
  login as loginApi,
  logoutApi,
  refreshToken as refreshApi,
} from './authApi';
import { getTokenRemainingMs, isTokenExpired, parseTokenPayload } from './token';
import { disconnectRealtime, reconnectRealtime } from '@/src/shared/api/realtime';

const TOKEN_KEY = 'eecp_access_token';

interface AuthContextValue {
  user: AuthUser | null;
  isAdmin: boolean;
  isAuthenticated: boolean;
  isLoading: boolean;
  authEnforced: boolean;
  loginWithCredentials: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  reloadUser: () => Promise<void>;
}

const DEV_FALLBACK_USER: AuthUser = {
  id: null,
  username: 'dev-admin',
  full_name: 'Chế độ phát triển (Dev)',
  role: 'ADMIN',
  session_scope: [],
};

const AuthContext = createContext<AuthContextValue | null>(null);

function userFromToken(token: string): AuthUser | null {
  const payload = parseTokenPayload(token);
  if (!payload || !payload.sub || !payload.role) return null;
  return {
    id: payload.uid ?? null,
    username: payload.sub,
    full_name: payload.name || payload.sub,
    role: payload.role,
    session_scope: Array.isArray(payload.sessions) ? payload.sessions : [],
  };
}

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<AuthUser | null>(() => {
    const stored = sessionStorage.getItem(TOKEN_KEY);
    if (!stored || isTokenExpired(stored)) return null;
    return userFromToken(stored);
  });
  const [authEnforced, setAuthEnforced] = useState<boolean>(true);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const refreshTimerRef = useRef<number | undefined>(undefined);

  const clearRefreshTimer = useCallback(() => {
    if (refreshTimerRef.current !== undefined) {
      window.clearTimeout(refreshTimerRef.current);
      refreshTimerRef.current = undefined;
    }
  }, []);

  const clearSessionState = useCallback(() => {
    clearRefreshTimer();
    sessionStorage.removeItem(TOKEN_KEY);
    setUser(null);
    disconnectRealtime();
  }, [clearRefreshTimer]);

  const scheduleRefresh = useCallback(
    (token: string) => {
      clearRefreshTimer();
      const remainingMs = getTokenRemainingMs(token);
      if (remainingMs <= 0) return;
      // Refresh 5 minutes before expiry (or at 80% lifetime for short-lived tokens).
      const leadMs = Math.min(5 * 60 * 1000, Math.floor(remainingMs * 0.2));
      const delayMs = Math.max(5000, remainingMs - leadMs);
      refreshTimerRef.current = window.setTimeout(async () => {
        try {
          const refreshed = await refreshApi();
          sessionStorage.setItem(TOKEN_KEY, refreshed.access_token);
          setUser(refreshed.user);
          scheduleRefresh(refreshed.access_token);
        } catch {
          clearSessionState();
        }
      }, delayMs);
    },
    [clearRefreshTimer, clearSessionState],
  );

  const reloadUser = useCallback(async () => {
    const stored = sessionStorage.getItem(TOKEN_KEY);
    if (stored && isTokenExpired(stored)) {
      sessionStorage.removeItem(TOKEN_KEY);
    }
    try {
      const me = await fetchMe();
      setAuthEnforced(me.auth_enforced);
      if (me.authenticated && me.user) {
        setUser(me.user);
        const currentToken = sessionStorage.getItem(TOKEN_KEY);
        if (currentToken && !isTokenExpired(currentToken)) {
          scheduleRefresh(currentToken);
        }
      } else if (!me.auth_enforced) {
        setUser(DEV_FALLBACK_USER);
      } else {
        clearSessionState();
      }
    } catch {
      const currentToken = sessionStorage.getItem(TOKEN_KEY);
      if (!currentToken || isTokenExpired(currentToken)) {
        clearSessionState();
      }
    } finally {
      setIsLoading(false);
    }
  }, [clearSessionState, scheduleRefresh]);

  useEffect(() => {
    reloadUser();
    return () => clearRefreshTimer();
  }, [reloadUser, clearRefreshTimer]);

  useEffect(() => {
    const handleUnauthorized = () => {
      clearSessionState();
      setAuthEnforced(true);
      setIsLoading(false);
    };
    window.addEventListener('eecp:unauthorized', handleUnauthorized);
    return () => window.removeEventListener('eecp:unauthorized', handleUnauthorized);
  }, [clearSessionState]);

  const loginWithCredentials = useCallback(
    async (username: string, password: string) => {
      const result = await loginApi(username, password);
      sessionStorage.setItem(TOKEN_KEY, result.access_token);
      setUser(result.user);
      setIsLoading(false);
      scheduleRefresh(result.access_token);
      reconnectRealtime();
    },
    [scheduleRefresh],
  );

  const logout = useCallback(async () => {
    try {
      if (sessionStorage.getItem(TOKEN_KEY)) {
        await logoutApi();
      }
    } catch {
      // Clear local session even if server already expired the token.
    } finally {
      clearSessionState();
      if (!authEnforced) {
        setUser(DEV_FALLBACK_USER);
      }
    }
  }, [authEnforced, clearSessionState]);

  const value = useMemo<AuthContextValue>(() => {
    const isAuthenticated = Boolean(user);
    return {
      user,
      isAdmin: user?.role === 'ADMIN',
      isAuthenticated,
      isLoading,
      authEnforced,
      loginWithCredentials,
      logout,
      reloadUser,
    };
  }, [user, isLoading, authEnforced, loginWithCredentials, logout, reloadUser]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
};

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error('useAuth must be used inside AuthProvider');
  }
  return ctx;
}

