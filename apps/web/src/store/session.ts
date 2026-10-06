/**
 * Session state.
 *
 * The session token never reaches JavaScript; it lives in an httpOnly cookie and
 * this store holds only the resulting user. That constraint is the reason the
 * store persists nothing: putting the cookie value in local storage to "avoid a
 * round trip on reload" would hand any XSS payload the credential, which is the
 * one thing the httpOnly flag exists to prevent.
 */
import { create } from 'zustand';

import { errorMessage, fetchCurrentUser, login, logout, signup } from '@/lib/api';
import type { User } from '@/types/api';

export interface SessionState {
  readonly user: User | null;
  /** True while probing `/auth/me` on mount. Distinguishes "signed out" from "unknown yet". */
  readonly loading: boolean;
  readonly error: string | null;
  /** Resolve the current session. Safe to call on public pages; a 401 is not an error. */
  readonly bootstrap: () => Promise<void>;
  readonly signIn: (email: string, password: string) => Promise<boolean>;
  readonly signUp: (email: string, password: string, displayName: string) => Promise<boolean>;
  readonly signOut: () => Promise<void>;
  readonly clearError: () => void;
}

const SIGN_IN_FAILED = 'That email and password combination was not recognised.';

export const useSessionStore = create<SessionState>()((set) => ({
  user: null,
  loading: false,
  error: null,

  bootstrap: async () => {
    set({ loading: true, error: null });
    try {
      const response = await fetchCurrentUser();
      set({ user: response.user, loading: false });
    } catch {
      // A 401 here is the expected answer for a signed-out visitor, so it maps
      // to "no user" rather than to an error banner on every public page.
      set({ user: null, loading: false, error: null });
    }
  },

  signIn: async (email, password) => {
    set({ loading: true, error: null });
    try {
      const response = await login({ email, password });
      set({ user: response.user, loading: false, error: null });
      return true;
    } catch (cause) {
      set({ user: null, loading: false, error: errorMessage(cause, SIGN_IN_FAILED) });
      return false;
    }
  },

  signUp: async (email, password, displayName) => {
    set({ loading: true, error: null });
    try {
      const response = await signup({ email, password, display_name: displayName });
      set({ user: response.user, loading: false, error: null });
      return true;
    } catch (cause) {
      set({ user: null, loading: false, error: errorMessage(cause, 'Sign-up failed.') });
      return false;
    }
  },

  signOut: async () => {
    try {
      await logout();
    } finally {
      // Clear locally even if the network call failed: leaving a stale user on
      // screen after an explicit sign-out is worse than an orphaned cookie that
      // the server will reject on the next request.
      set({ user: null, error: null, loading: false });
    }
  },

  clearError: () => set({ error: null }),
}));
