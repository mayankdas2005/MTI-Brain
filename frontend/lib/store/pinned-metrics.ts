import { create } from 'zustand';
import { apiFetch } from '@/lib/api/client';

export interface PinnedMetric {
  id: string;
  label: string;
  source_query: string;
  position: number;
  created_at: string;
  updated_at: string;
}

// In-flight dedup: welcome-state calls fetchMetrics on mount.
// React StrictMode double-invokes effects before the first call resolves,
// causing two real network requests without this guard.
let fetchMetricsFlight: Promise<void> | null = null;

interface PinnedMetricsStore {
  metrics: PinnedMetric[];
  loading: boolean;
  fetched: boolean;

  fetchMetrics: () => Promise<void>;
  pinMetric: (label: string, sourceQuery: string) => Promise<PinnedMetric>;
  updateMetric: (id: string, patch: { label?: string; position?: number }) => Promise<void>;
  unpinMetric: (id: string) => Promise<void>;
}

export const usePinnedMetricsStore = create<PinnedMetricsStore>()((set, get) => ({
  metrics: [],
  loading: false,
  fetched: false,

  fetchMetrics: () => {
    if (get().fetched) return Promise.resolve();
    if (fetchMetricsFlight) return fetchMetricsFlight;

    set({ loading: true });
    const run = async () => {
      try {
        const data = await apiFetch<PinnedMetric[]>('/pinned-metrics');
        set({ metrics: data, fetched: true, loading: false });
      } catch {
        set({ loading: false });
      }
    };

    fetchMetricsFlight = run().finally(() => { fetchMetricsFlight = null; });
    return fetchMetricsFlight;
  },

  pinMetric: async (label, sourceQuery) => {
    const position = get().metrics.length;
    const created = await apiFetch<PinnedMetric>('/pinned-metrics', {
      method: 'POST',
      body: JSON.stringify({ label, source_query: sourceQuery, position }),
    });
    set((s) => ({ metrics: [...s.metrics, created] }));
    return created;
  },

  updateMetric: async (id, patch) => {
    const prev = get().metrics;
    set((s) => ({
      metrics: s.metrics.map((m) => (m.id === id ? { ...m, ...patch } : m)),
    }));
    try {
      await apiFetch(`/pinned-metrics/${id}`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
      });
    } catch (err) {
      set({ metrics: prev });
      throw err;
    }
  },

  unpinMetric: async (id) => {
    const prev = get().metrics;
    set((s) => ({ metrics: s.metrics.filter((m) => m.id !== id) }));
    try {
      await apiFetch(`/pinned-metrics/${id}`, { method: 'DELETE' });
    } catch (err) {
      set({ metrics: prev });
      throw err;
    }
  },
}));
