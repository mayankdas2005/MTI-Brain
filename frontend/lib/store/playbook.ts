import { create } from 'zustand';
import { apiFetch } from '@/lib/api/client';

// In-flight dedup - prevents concurrent mount effects from firing two requests.
let fetchPlaybookFlight: Promise<void> | null = null;

export interface SavedQuery {
  id: string;
  name: string;
  query_text: string;
  created_at: string;
  updated_at: string;
}

interface PlaybookStore {
  queries: SavedQuery[];
  loading: boolean;
  fetched: boolean;

  fetchQueries: () => Promise<void>;
  createQuery: (name: string, queryText: string) => Promise<SavedQuery>;
  updateQuery: (id: string, patch: { name?: string; query_text?: string }) => Promise<void>;
  deleteQuery: (id: string) => Promise<void>;
}

export const usePlaybookStore = create<PlaybookStore>()((set, get) => ({
  queries: [],
  loading: false,
  fetched: false,

  fetchQueries: () => {
    if (get().fetched) return Promise.resolve();
    if (fetchPlaybookFlight) return fetchPlaybookFlight;

    set({ loading: true });
    const run = async () => {
      try {
        const data = await apiFetch<SavedQuery[]>('/playbook');
        set({ queries: data, fetched: true, loading: false });
      } catch {
        set({ loading: false });
      }
    };

    fetchPlaybookFlight = run().finally(() => { fetchPlaybookFlight = null; });
    return fetchPlaybookFlight;
  },

  createQuery: async (name, queryText) => {
    const optimistic: SavedQuery = {
      id: `temp_${Date.now()}`,
      name,
      query_text: queryText,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    set((s) => ({ queries: [...s.queries, optimistic] }));
    try {
      const created = await apiFetch<SavedQuery>('/playbook', {
        method: 'POST',
        body: JSON.stringify({ name, query_text: queryText }),
      });
      set((s) => ({
        queries: s.queries.map((q) => (q.id === optimistic.id ? created : q)),
      }));
      return created;
    } catch (err) {
      set((s) => ({ queries: s.queries.filter((q) => q.id !== optimistic.id) }));
      throw err;
    }
  },

  updateQuery: async (id, patch) => {
    const prev = get().queries;
    set((s) => ({
      queries: s.queries.map((q) => (q.id === id ? { ...q, ...patch } : q)),
    }));
    try {
      await apiFetch(`/playbook/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
      });
    } catch (err) {
      set({ queries: prev });
      throw err;
    }
  },

  deleteQuery: async (id) => {
    const prev = get().queries;
    set((s) => ({ queries: s.queries.filter((q) => q.id !== id) }));
    try {
      await apiFetch(`/playbook/${id}`, { method: 'DELETE' });
    } catch (err) {
      set({ queries: prev });
      throw err;
    }
  },
}));
