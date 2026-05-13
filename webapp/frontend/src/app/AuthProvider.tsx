import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { getCurrentUser, login as loginRequest, logout as logoutRequest, register as registerRequest } from '../api';
import type { User } from '../types';

type AuthState = 'loading' | 'authenticated' | 'anonymous';

interface AuthContextValue {
  status: AuthState;
  user: User | null;
  refresh: () => Promise<void>;
  login: (username: string, password: string) => Promise<void>;
  register: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthState>('loading');
  const [user, setUser] = useState<User | null>(null);

  const refresh = async () => {
    try {
      const payload = await getCurrentUser();
      setUser(payload.user);
      setStatus('authenticated');
    } catch {
      setUser(null);
      setStatus('anonymous');
    }
  };

  useEffect(() => {
    void refresh();
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({
      status,
      user,
      refresh,
      login: async (username, password) => {
        const payload = await loginRequest(username, password);
        setUser(payload.user);
        setStatus('authenticated');
      },
      register: async (username, password) => {
        const payload = await registerRequest(username, password);
        setUser(payload.user);
        setStatus('authenticated');
      },
      logout: async () => {
        await logoutRequest();
        setUser(null);
        setStatus('anonymous');
      },
    }),
    [status, user],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used inside AuthProvider');
  }
  return context;
}
