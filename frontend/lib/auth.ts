import { apiFetch } from '@/lib/api/client';

// ─── Types ───

export interface User {
  user_id: string;
  email: string;
  name: string;
  groups: string[];
}

interface JWTPayload {
  sub: string;
  user_id: string;
  email: string;
  name: string;
  groups: string[];
  exp: number;
  iat: number;
}

// ─── Storage keys ───

const TOKEN_KEY = 'mti_brain_token';
const USER_KEY = 'mti_brain_user';

// ─── Token helpers ───

export function getStoredToken(): string | null {
  if (typeof window === 'undefined') return null;
  try {
    return localStorage.getItem(TOKEN_KEY);
  } catch {
    return null;
  }
}

export function setStoredToken(token: string): void {
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem(TOKEN_KEY, token);
  } catch {
    // Safari private mode / quota exceeded - token only lives in-memory for
    // this tab. Subsequent reads will return null and the user will be sent
    // back to /login. Nothing more we can do safely here.
  }
}

export function clearStoredToken(): void {
  if (typeof window === 'undefined') return;
  try {
    localStorage.removeItem(TOKEN_KEY);
  } catch {
    // ignore
  }
}

// ─── User helpers ───

export function getStoredUser(): User | null {
  if (typeof window === 'undefined') return null;
  try {
    const stored = localStorage.getItem(USER_KEY);
    return stored ? JSON.parse(stored) : null;
  } catch {
    return null;
  }
}

export function setStoredUser(user: User): void {
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  } catch {
    // ignore quota / private-mode write failures
  }
}

function clearStoredUser(): void {
  if (typeof window === 'undefined') return;
  try {
    localStorage.removeItem(USER_KEY);
  } catch {
    // ignore
  }
}

// ─── JWT decode (lightweight, no dependency) ───

function decodeJWT(token: string): JWTPayload | null {
  try {
    const base64 = token.split('.')[1];
    const json = atob(base64.replace(/-/g, '+').replace(/_/g, '/'));
    return JSON.parse(json) as JWTPayload;
  } catch {
    return null;
  }
}

export function userFromToken(token: string): User | null {
  const payload = decodeJWT(token);
  if (!payload) return null;
  return {
    user_id: payload.user_id,
    email: payload.email,
    name: payload.name,
    groups: payload.groups ?? [],
  };
}

export function isTokenExpired(token: string): boolean {
  const payload = decodeJWT(token);
  if (!payload?.exp) return true;
  // 60-second buffer for clock skew
  return Date.now() >= (payload.exp - 60) * 1000;
}

// ─── Auth state check ───

export function isAuthenticated(): boolean {
  const token = getStoredToken();
  if (!token) return false;
  return !isTokenExpired(token);
}

// ─── Auth headers (for API calls) ───

export function getAuthHeaders(): Record<string, string> {
  const token = getStoredToken();
  if (!token) return {};
  return { Authorization: `Bearer ${token}` };
}

// ─── Login gate (optimistic navigation) ───
// Set before navigating to /new so the authenticated layout can await the
// in-flight token fetch instead of immediately redirecting to /.

let _loginGate: Promise<void> | null = null;
let _loginError: string | null = null;

export function setLoginGate(p: Promise<void> | null) { _loginGate = p; }
export function getLoginGate() { return _loginGate; }

export function setLoginError(msg: string) { _loginError = msg; }
export function consumeLoginError(): string | null {
  const err = _loginError;
  _loginError = null;
  return err;
}

// ─── Login ───

export async function login(username: string, password: string, role: 'admin' | 'user'): Promise<void> {
  const data = await apiFetch<{ token: string; user: User }>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password, role }),
  });
  setStoredToken(data.token);
  setStoredUser(data.user);
}

// ─── Logout ───

export async function logout(): Promise<void> {
  // Abort any active stream before tearing down auth so we don't leak
  // partial assistant content into a half-cleared store, and so the
  // backend can finalize the conversation row before we redirect.
  try {
    const { useThreadStore } = await import('./store/threads');
    const state = useThreadStore.getState();
    if (state.isStreaming && state.streamingThreadId) {
      await state.stopGeneration(state.streamingThreadId);
    }
  } catch {
    // Store import shouldn't fail, but if it does, proceed with logout.
  }

  clearStoredToken();
  clearStoredUser();
  // Clear the persisted threads cache so the next user on this device does
  // not briefly see previous user's thread list before the first fetch.
  try {
    const { useThreadStore } = await import('./store/threads');
    useThreadStore.persist.clearStorage();
  } catch {
    // best-effort
  }
  try {
    const { resetAnalytics } = await import('./analytics');
    resetAnalytics();
  } catch {
    // Analytics reset is best-effort.
  }
  if (typeof window !== 'undefined') {
    window.location.href = '/';
  }
}
