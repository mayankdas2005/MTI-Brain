import { create } from 'zustand';
import {
  getStoredToken,
  getStoredUser,
  isTokenExpired,
  userFromToken,
  setStoredUser,
  type User,
} from '@/lib/auth';

interface AuthStore {
  user: User | null;
  token: string | null;
  isLoading: boolean;

  /** Read token + user from localStorage and validate. */
  initialize: () => void;

  /** Set token and user after successful login. */
  loginWithToken: (token: string, user: User) => void;

  /** Clear auth state (localStorage is cleared separately by auth.logout). */
  clearAuth: () => void;

  /** Check if user is authenticated with a valid (non-expired) token. */
  isAuthenticated: () => boolean;
}

export const useAuthStore = create<AuthStore>((set, get) => ({
  user: null,
  token: null,
  isLoading: true,

  initialize: () => {
    const token = getStoredToken();
    if (token && !isTokenExpired(token)) {
      const user = getStoredUser() ?? userFromToken(token);
      if (user) setStoredUser(user);
      set({ token, user, isLoading: false });
    } else {
      set({ token: null, user: null, isLoading: false });
    }
  },

  loginWithToken: (token, user) => {
    set({ token, user, isLoading: false });
  },

  clearAuth: () => {
    set({ token: null, user: null, isLoading: false });
  },

  isAuthenticated: () => {
    const { token } = get();
    return !!token && !isTokenExpired(token);
  },
}));
