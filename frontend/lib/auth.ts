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

const TOKEN_KEY = 'quest_token';
const USER_KEY = 'quest_user';

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
  localStorage.setItem(TOKEN_KEY, token);
}

export function clearStoredToken(): void {
  if (typeof window === 'undefined') return;
  localStorage.removeItem(TOKEN_KEY);
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
  localStorage.setItem(USER_KEY, JSON.stringify(user));
}

function clearStoredUser(): void {
  if (typeof window === 'undefined') return;
  localStorage.removeItem(USER_KEY);
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

// ─── Login ───

export async function login(username: string, password: string): Promise<void> {
  const data = await apiFetch<{ token: string; user: User }>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  });
  setStoredToken(data.token);
  setStoredUser(data.user);
}

// ─── Logout ───

export function logout(): void {
  clearStoredToken();
  clearStoredUser();
  if (typeof window !== 'undefined') {
    window.location.href = '/';
  }
}
