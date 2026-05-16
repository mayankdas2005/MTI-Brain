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

const PM_CACHE_KEY = 'mti_pinned_count';
const _pmGetCache = (): number => {
  try { return Math.max(0, parseInt(localStorage.getItem(PM_CACHE_KEY) ?? '0', 10) || 0); }
  catch { return 0; }
};
const _pmSetCache = (n: number) => {
  try { localStorage.setItem(PM_CACHE_KEY, String(n)); } catch {}
};

// In-flight dedup: welcome-state calls fetchMetrics on mount.
// React StrictMode double-invokes effects before the first call resolves,
// causing two real network requests without this guard.
let fetchMetricsFlight: Promise<void> | null = null;

interface PinnedMetricsStore {
  metrics: PinnedMetric[];
  loading: boolean;
  fetched: boolean;
  lastKnownCount: number;

  fetchMetrics: () => Promise<void>;
  pinMetric: (label: string, sourceQuery: string) => Promise<PinnedMetric>;
  updateMetric: (id: string, patch: { label?: string; position?: number }) => Promise<void>;
  unpinMetric: (id: string) => Promise<void>;
}

export const usePinnedMetricsStore = create<PinnedMetricsStore>()((set, get) => ({
  metrics: [],
  loading: false,
  fetched: false,
  lastKnownCount: _pmGetCache(),

  fetchMetrics: () => {
    if (get().fetched) return Promise.resolve();
    if (fetchMetricsFlight) return fetchMetricsFlight;

    set({ loading: true });
    const run = async () => {
      try {
        const data = await apiFetch<PinnedMetric[]>('/pinned-metrics');
        _pmSetCache(data.length);
        set({ metrics: data, fetched: true, loading: false, lastKnownCount: data.length });
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
    set((s) => {
      const next = [...s.metrics, created];
      _pmSetCache(next.length);
      return { metrics: next, lastKnownCount: next.length };
    });
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
    set((s) => {
      const next = s.metrics.filter((m) => m.id !== id);
      _pmSetCache(next.length);
      return { metrics: next, lastKnownCount: next.length };
    });
    try {
      await apiFetch(`/pinned-metrics/${id}`, { method: 'DELETE' });
    } catch (err) {
      set({ metrics: prev });
      throw err;
    }
  },
}));
