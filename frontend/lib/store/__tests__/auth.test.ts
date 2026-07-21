import { describe, it, expect, beforeEach, vi } from 'vitest';
import { act } from '@testing-library/react';

// Mock the auth module before importing the store
vi.mock('@/lib/auth', () => ({
  getStoredToken: vi.fn(() => null),
  getStoredUser: vi.fn(() => null),
  isTokenExpired: vi.fn(() => false),
  userFromToken: vi.fn(() => null),
  setStoredUser: vi.fn(),
}));

import { useAuthStore } from '../auth';
import * as authLib from '@/lib/auth';

describe('useAuthStore', () => {
  beforeEach(() => {
    // Reset store state between tests
    act(() => {
      useAuthStore.setState({
        user: null,
        token: null,
        isLoading: true,
      });
    });
    vi.clearAllMocks();
  });

  describe('initial state', () => {
    it('starts with null user and token', () => {
      const state = useAuthStore.getState();
      expect(state.user).toBeNull();
      expect(state.token).toBeNull();
    });

    it('starts in loading state', () => {
      const state = useAuthStore.getState();
      expect(state.isLoading).toBe(true);
    });
  });

  describe('loginWithToken', () => {
    it('sets token and user after login', () => {
      const user = { user_id: '1', email: 'test@example.com', name: 'Test User', groups: ['admin'] };

      act(() => {
        useAuthStore.getState().loginWithToken('jwt-token-123', user);
      });

      const state = useAuthStore.getState();
      expect(state.token).toBe('jwt-token-123');
      expect(state.user).toEqual(user);
      expect(state.isLoading).toBe(false);
    });

    it('clears loading state on login', () => {
      act(() => {
        useAuthStore.getState().loginWithToken('token', {
          user_id: '1',
          email: 'a@b.com',
          name: 'A',
          groups: [],
        });
      });

      expect(useAuthStore.getState().isLoading).toBe(false);
    });
  });

  describe('clearAuth', () => {
    it('removes user and token', () => {
      // Set up logged-in state first
      act(() => {
        useAuthStore.getState().loginWithToken('token', {
          user_id: '1',
          email: 'test@test.com',
          name: 'Test',
          groups: [],
        });
      });

      act(() => {
        useAuthStore.getState().clearAuth();
      });

      const state = useAuthStore.getState();
      expect(state.user).toBeNull();
      expect(state.token).toBeNull();
      expect(state.isLoading).toBe(false);
    });
  });

  describe('initialize', () => {
    it('sets user from stored token when token is valid', () => {
      const mockUser = { user_id: '1', email: 'stored@test.com', name: 'Stored', groups: ['user'] };
      vi.mocked(authLib.getStoredToken).mockReturnValue('valid-token');
      vi.mocked(authLib.isTokenExpired).mockReturnValue(false);
      vi.mocked(authLib.getStoredUser).mockReturnValue(mockUser);

      act(() => {
        useAuthStore.getState().initialize();
      });

      const state = useAuthStore.getState();
      expect(state.token).toBe('valid-token');
      expect(state.user).toEqual(mockUser);
      expect(state.isLoading).toBe(false);
    });

    it('clears state when token is expired', () => {
      vi.mocked(authLib.getStoredToken).mockReturnValue('expired-token');
      vi.mocked(authLib.isTokenExpired).mockReturnValue(true);

      act(() => {
        useAuthStore.getState().initialize();
      });

      const state = useAuthStore.getState();
      expect(state.token).toBeNull();
      expect(state.user).toBeNull();
      expect(state.isLoading).toBe(false);
    });

    it('clears state when no token stored', () => {
      vi.mocked(authLib.getStoredToken).mockReturnValue(null);

      act(() => {
        useAuthStore.getState().initialize();
      });

      const state = useAuthStore.getState();
      expect(state.token).toBeNull();
      expect(state.user).toBeNull();
      expect(state.isLoading).toBe(false);
    });

    it('falls back to userFromToken when no stored user', () => {
      const tokenUser = { user_id: '2', email: 'token@test.com', name: 'Token User', groups: [] };
      vi.mocked(authLib.getStoredToken).mockReturnValue('valid-token');
      vi.mocked(authLib.isTokenExpired).mockReturnValue(false);
      vi.mocked(authLib.getStoredUser).mockReturnValue(null);
      vi.mocked(authLib.userFromToken).mockReturnValue(tokenUser);

      act(() => {
        useAuthStore.getState().initialize();
      });

      const state = useAuthStore.getState();
      expect(state.user).toEqual(tokenUser);
      expect(authLib.setStoredUser).toHaveBeenCalledWith(tokenUser);
    });
  });

  describe('isAuthenticated', () => {
    it('returns true when token exists and is not expired', () => {
      vi.mocked(authLib.isTokenExpired).mockReturnValue(false);

      act(() => {
        useAuthStore.setState({ token: 'valid-token', user: null, isLoading: false });
      });

      expect(useAuthStore.getState().isAuthenticated()).toBe(true);
    });

    it('returns false when token is null', () => {
      act(() => {
        useAuthStore.setState({ token: null, user: null, isLoading: false });
      });

      expect(useAuthStore.getState().isAuthenticated()).toBe(false);
    });

    it('returns false when token is expired', () => {
      vi.mocked(authLib.isTokenExpired).mockReturnValue(true);

      act(() => {
        useAuthStore.setState({ token: 'expired-token', user: null, isLoading: false });
      });

      expect(useAuthStore.getState().isAuthenticated()).toBe(false);
    });
  });
});
