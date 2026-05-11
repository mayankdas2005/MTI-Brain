import { create } from 'zustand';
import { apiFetch } from '@/lib/api/client';

export interface ThreadLabel {
  id: string;
  thread_id: string;
  label: string;
  color: string;
  created_at: string;
}

export const LABEL_COLORS = [
  { name: 'blue',   bg: 'bg-blue-500/15',   text: 'text-blue-700 dark:text-blue-400',   dot: 'bg-blue-500' },
  { name: 'green',  bg: 'bg-emerald-500/15', text: 'text-emerald-700 dark:text-emerald-400', dot: 'bg-emerald-500' },
  { name: 'red',    bg: 'bg-red-500/15',     text: 'text-red-700 dark:text-red-400',     dot: 'bg-red-500' },
  { name: 'orange', bg: 'bg-orange-500/15',  text: 'text-orange-700 dark:text-orange-400', dot: 'bg-orange-500' },
  { name: 'purple', bg: 'bg-purple-500/15',  text: 'text-purple-700 dark:text-purple-400', dot: 'bg-purple-500' },
  { name: 'gray',   bg: 'bg-muted',          text: 'text-muted-foreground',              dot: 'bg-muted-foreground/60' },
] as const;

// In-flight dedup: sidebar + chats page both call fetchAllLabels on mount.
// Without this, concurrent callers both pass `fetched` check before either
// sets it to true, resulting in two real network requests.
let fetchLabelsFlight: Promise<void> | null = null;

interface LabelsStore {
  byThread: Record<string, ThreadLabel[]>;
  fetched: boolean;

  fetchAllLabels: () => Promise<void>;
  addLabel: (threadId: string, label: string, color: string) => Promise<void>;
  removeLabel: (labelId: string, threadId: string) => Promise<void>;
}

export const useLabelsStore = create<LabelsStore>()((set, get) => ({
  byThread: {},
  fetched: false,

  fetchAllLabels: () => {
    if (get().fetched) return Promise.resolve();
    if (fetchLabelsFlight) return fetchLabelsFlight;

    const run = async () => {
      try {
        const data = await apiFetch<ThreadLabel[]>('/labels');
        const byThread: Record<string, ThreadLabel[]> = {};
        data.forEach((l) => {
          if (!byThread[l.thread_id]) byThread[l.thread_id] = [];
          byThread[l.thread_id].push(l);
        });
        set({ byThread, fetched: true });
      } catch { /* silent */ }
    };

    fetchLabelsFlight = run().finally(() => { fetchLabelsFlight = null; });
    return fetchLabelsFlight;
  },

  addLabel: async (threadId, label, color) => {
    const created = await apiFetch<ThreadLabel>(`/labels/thread/${threadId}`, {
      method: 'POST',
      body: JSON.stringify({ label, color }),
    });
    set((s) => ({
      byThread: {
        ...s.byThread,
        [threadId]: [...(s.byThread[threadId] ?? []), created],
      },
    }));
  },

  removeLabel: async (labelId, threadId) => {
    const prev = get().byThread;
    set((s) => ({
      byThread: {
        ...s.byThread,
        [threadId]: (s.byThread[threadId] ?? []).filter((l) => l.id !== labelId),
      },
    }));
    try {
      await apiFetch(`/labels/${labelId}`, { method: 'DELETE' });
    } catch {
      set({ byThread: prev });
    }
  },
}));
