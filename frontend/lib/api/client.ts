/**
 * Base API client with typed fetch wrapper, auth headers, and error handling.
 */

import { getAuthHeaders, clearStoredToken } from '@/lib/auth';

const API_BASE =
  (typeof window !== 'undefined'
    ? process.env.NEXT_PUBLIC_API_URL
    : process.env.NEXT_PUBLIC_API_URL) || '';

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

  const res = await fetch(url, { ...options, headers });

  if (res.status === 401) {
    // Token expired or invalid - clear and notify the layout via a custom event.
    // Using window.location.href triggers a full page reload (slow). The
    // authenticated layout listens for 'quest:unauthenticated' and uses
    // Next.js routing to redirect, which keeps the bundle warm.
    clearStoredToken();
    if (typeof window !== 'undefined') {
      window.dispatchEvent(new CustomEvent('quest:unauthenticated'));
    }
    throw new ApiError(401, { detail: 'Session expired. Please log in again.' });
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

  return res.json() as Promise<T>;
}
