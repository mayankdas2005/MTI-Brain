import { describe, it, expect, beforeEach, vi } from 'vitest';

// Mock apiFetch before importing auth module
vi.mock('@/lib/api/client', () => ({
  apiFetch: vi.fn(),
  apiBase: 'http://localhost:8000/api/v1',
}));

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: vi.fn((key: string) => store[key] ?? null),
    setItem: vi.fn((key: string, value: string) => { store[key] = value; }),
    removeItem: vi.fn((key: string) => { delete store[key]; }),
    clear: () => { store = {}; },
  };
})();

vi.stubGlobal('localStorage', localStorageMock);

import {
  getStoredToken,
  setStoredToken,
  clearStoredToken,
  getStoredUser,
  setStoredUser,
  userFromToken,
  isTokenExpired,
  isAuthenticated,
  getAuthHeaders,
} from '../auth';

// Helper: create a base64url-encoded JWT payload
function makeJWT(payload: Record<string, unknown>): string {
  const header = btoa(JSON.stringify({ alg: 'HS256', typ: 'JWT' }));
  const body = btoa(JSON.stringify(payload));
  const sig = 'fake-signature';
  return `${header}.${body}.${sig}`;
}

describe('auth lib', () => {
  beforeEach(() => {
    localStorageMock.clear();
    vi.clearAllMocks();
  });

  describe('getStoredToken / setStoredToken / clearStoredToken', () => {
    it('returns null when no token stored', () => {
      expect(getStoredToken()).toBeNull();
    });

    it('stores and retrieves a token', () => {
      setStoredToken('my-jwt-token');
      expect(localStorageMock.setItem).toHaveBeenCalledWith('mti_brain_token', 'my-jwt-token');
      expect(getStoredToken()).toBe('my-jwt-token');
    });

    it('clears stored token', () => {
      setStoredToken('token-to-clear');
      clearStoredToken();
      expect(localStorageMock.removeItem).toHaveBeenCalledWith('mti_brain_token');
    });
  });

  describe('getStoredUser / setStoredUser', () => {
    it('returns null when no user stored', () => {
      expect(getStoredUser()).toBeNull();
    });

    it('stores and retrieves user', () => {
      const user = { user_id: '1', email: 'test@test.com', name: 'Test', groups: ['admin'] };
      setStoredUser(user);
      expect(getStoredUser()).toEqual(user);
    });
  });

  describe('userFromToken', () => {
    it('extracts user from valid JWT', () => {
      const token = makeJWT({
        sub: 'user-1',
        user_id: 'user-1',
        email: 'jwt@test.com',
        name: 'JWT User',
        groups: ['engineering'],
        exp: Math.floor(Date.now() / 1000) + 3600,
        iat: Math.floor(Date.now() / 1000),
      });

      const user = userFromToken(token);
      expect(user).toEqual({
        user_id: 'user-1',
        email: 'jwt@test.com',
        name: 'JWT User',
        groups: ['engineering'],
      });
    });

    it('returns null for invalid token', () => {
      expect(userFromToken('not.a.jwt')).toBeNull();
      expect(userFromToken('')).toBeNull();
    });

    it('defaults groups to empty array when missing', () => {
      const token = makeJWT({
        sub: 'user-1',
        user_id: 'user-1',
        email: 'no-groups@test.com',
        name: 'No Groups',
        exp: 9999999999,
        iat: 0,
      });

      const user = userFromToken(token);
      expect(user?.groups).toEqual([]);
    });
  });

  describe('isTokenExpired', () => {
    it('returns true for expired token', () => {
      const token = makeJWT({
        exp: Math.floor(Date.now() / 1000) - 3600, // 1 hour ago
        iat: 0,
      });
      expect(isTokenExpired(token)).toBe(true);
    });

    it('returns false for valid (non-expired) token', () => {
      const token = makeJWT({
        exp: Math.floor(Date.now() / 1000) + 3600, // 1 hour from now
        iat: 0,
      });
      expect(isTokenExpired(token)).toBe(false);
    });

    it('returns true when token has no exp', () => {
      const token = makeJWT({ iat: 0 });
      expect(isTokenExpired(token)).toBe(true);
    });

    it('accounts for 60-second clock skew buffer', () => {
      // Token expires in 30 seconds - within the 60s buffer, so should be "expired"
      const token = makeJWT({
        exp: Math.floor(Date.now() / 1000) + 30,
        iat: 0,
      });
      expect(isTokenExpired(token)).toBe(true);
    });

    it('returns true for invalid token string', () => {
      expect(isTokenExpired('garbage')).toBe(true);
    });
  });

  describe('isAuthenticated', () => {
    it('returns false when no token', () => {
      expect(isAuthenticated()).toBe(false);
    });

    it('returns true with valid token', () => {
      const token = makeJWT({
        exp: Math.floor(Date.now() / 1000) + 3600,
        iat: 0,
      });
      setStoredToken(token);
      expect(isAuthenticated()).toBe(true);
    });

    it('returns false with expired token', () => {
      const token = makeJWT({
        exp: Math.floor(Date.now() / 1000) - 3600,
        iat: 0,
      });
      setStoredToken(token);
      expect(isAuthenticated()).toBe(false);
    });
  });

  describe('getAuthHeaders', () => {
    it('returns empty object when no token', () => {
      expect(getAuthHeaders()).toEqual({});
    });

    it('returns Authorization header with Bearer token', () => {
      setStoredToken('my-token');
      expect(getAuthHeaders()).toEqual({ Authorization: 'Bearer my-token' });
    });
  });
});
