/**
 * Base API client with typed fetch wrapper, auth headers, and error handling.
 */

import { getAuthHeaders, clearStoredToken, setStoredToken, setStoredUser } from '@/lib/auth';
import type { User } from '@/lib/auth';

const API_BASE = process.env.NEXT_PUBLIC_API_URL || '';

export const apiBase = `${API_BASE}/api/v1`;

export class ApiError extends Error {
  status: number;
  body: unknown;

  constructor(status: number, body: unknown) {
    const msg = typeof body === 'object' && body && 'detail' in body
      ? String((body as { detail: string }).detail)
      : `API error ${status}`;
    super(msg);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

// ─── Token refresh ───

let _refreshPromise: Promise<boolean> | null = null;

async function _tryRefresh(): Promise<boolean> {
  try {
    const res = await fetch(`${apiBase}/auth/refresh`, {
      method: 'POST',
      credentials: 'include',
    });
    if (!res.ok) return false;
    const data = await res.json() as { token: string; user: User };
    setStoredToken(data.token);
    setStoredUser(data.user);
    return true;
  } catch {
    return false;
  }
}

/**
 * Attempt a single token refresh. Deduplicates concurrent calls so only one
 * refresh request is in flight at a time.
 */
function tryRefreshOnce(): Promise<boolean> {
  if (!_refreshPromise) {
    _refreshPromise = _tryRefresh().finally(() => { _refreshPromise = null; });
  }
  return _refreshPromise;
}

export async function apiFetch<T>(
  path: string,
  options: RequestInit & { signal?: AbortSignal } = {},
): Promise<T> {
  const url = `${apiBase}${path}`;

  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...getAuthHeaders(),
    ...(options.headers as Record<string, string> | undefined),
  };

  let res = await fetch(url, { ...options, headers });

  if (res.status === 401 && !path.startsWith('/auth/')) {
    // Access token expired — attempt silent refresh
    const refreshed = await tryRefreshOnce();
    if (refreshed) {
      // Retry the original request with the new token
      const retryHeaders: Record<string, string> = {
        ...headers,
        ...getAuthHeaders(),
      };
      res = await fetch(url, { ...options, headers: retryHeaders });
    }
  }

  if (res.status === 401) {
    clearStoredToken();
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('mti-brain:unauthenticated'));
    }
    throw new ApiError(401, { detail: 'Session expired. Please log in again.' });
  }

  if (res.status === 429) {
    const retryAfter = res.headers.get('Retry-After');
    const detail = retryAfter
      ? `Too many requests. Please wait ${retryAfter} second${retryAfter === '1' ? '' : 's'} before trying again.`
      : 'Too many requests. Please slow down and try again.';
    throw new ApiError(429, { detail });
  }

  if (res.status === 503) {
    throw new ApiError(503, { detail: 'Service temporarily unavailable. Please try again shortly.' });
  }

  if (!res.ok) {
    let body: unknown;
    try {
      body = await res.json();
    } catch {
      body = await res.text();
    }
    throw new ApiError(res.status, body);
  }

  if (res.status === 204 || res.headers.get('content-length') === '0') {
    return undefined as T;
  }
  return res.json() as Promise<T>;
}
