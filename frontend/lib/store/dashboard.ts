import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type DashboardStatus = 'idle' | 'pending' | 'ready' | 'failed';

export const DASHBOARD_TIMEOUT_MS = 5 * 60 * 1000; // 5 min — generation must complete by this
const POLL_WINDOW_MS = DASHBOARD_TIMEOUT_MS;         // stop polling exactly when UI shows "Retry"

interface DashboardEntry {
  status: DashboardStatus;
  url: string | null;
  queuedAt: number;
}

interface DashboardStore {
  entries: Record<string, DashboardEntry>;
  set: (conversationId: string, entry: DashboardEntry) => void;
  get: (conversationId: string) => DashboardEntry | undefined;
  remove: (conversationId: string) => void;
  getPending: () => string[];
  /** Mark any pending entry older than POLL_WINDOW_MS as failed. */
  expireStale: () => void;
}

export const useDashboardStore = create<DashboardStore>()(
  persist(
    (setState, get) => ({
      entries: {},

      set: (conversationId, entry) =>
        setState((s) => ({ entries: { ...s.entries, [conversationId]: entry } })),

      get: (conversationId) => get().entries[conversationId],

      remove: (conversationId) =>
        setState((s) => {
          const next = { ...s.entries };
          delete next[conversationId];
          return { entries: next };
        }),

      getPending: () => {
        const now = Date.now();
        return Object.entries(get().entries)
          .filter(([, e]) => e.status === 'pending' && now - e.queuedAt < POLL_WINDOW_MS)
          .map(([id]) => id);
      },

      expireStale: () => {
        const now = Date.now();
        const stale = Object.entries(get().entries).filter(
          ([, e]) => e.status === 'pending' && now - e.queuedAt >= POLL_WINDOW_MS,
        );
        if (stale.length === 0) return;
        setState((s) => {
          const next = { ...s.entries };
          for (const [id, e] of stale) {
            next[id] = { ...e, status: 'failed' };
          }
          return { entries: next };
        });
      },
    }),
    { name: 'mti-brain-dashboards', version: 1 },
  ),
);
