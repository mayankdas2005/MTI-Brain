import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export type GraphContextStatus = 'idle' | 'pending' | 'ready' | 'failed';

export const GRAPH_CONTEXT_TIMEOUT_MS = 5 * 60 * 1000;
const POLL_WINDOW_MS = GRAPH_CONTEXT_TIMEOUT_MS;

interface GraphContextEntry {
  status: GraphContextStatus;
  url: string | null;
  queuedAt: number;
}

interface GraphContextStore {
  entries: Record<string, GraphContextEntry>;
  set: (conversationId: string, entry: GraphContextEntry) => void;
  get: (conversationId: string) => GraphContextEntry | undefined;
  remove: (conversationId: string) => void;
  getPending: () => string[];
  expireStale: () => void;
}

export const useGraphContextStore = create<GraphContextStore>()(
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
    { name: 'mti-brain-graph-contexts', version: 1 },
  ),
);
