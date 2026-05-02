import { create } from 'zustand';
import { getRecents } from '@/lib/api/threads';
import { listProjects } from '@/lib/api/projects';
import type { ThreadSummary, SearchResult, ProjectOut } from '@/lib/types/api';

interface SearchStore {
  open: boolean;
  query: string;
  chatResults: SearchResult[];
  projectResults: ProjectOut[];
  recentChats: ThreadSummary[];
  loading: boolean;

  openModal: () => void;
  closeModal: () => void;
  search: (query: string) => void;
}

let debounceTimer: ReturnType<typeof setTimeout> | null = null;
let inflightController: AbortController | null = null;

function cancelInflight() {
  if (debounceTimer) {
    clearTimeout(debounceTimer);
    debounceTimer = null;
  }
  if (inflightController) {
    inflightController.abort();
    inflightController = null;
  }
}

export const useSearchStore = create<SearchStore>((set, get) => ({
  open: false,
  query: '',
  chatResults: [],
  projectResults: [],
  recentChats: [],
  loading: false,

  openModal: () => {
    set({ open: true, query: '', chatResults: [], projectResults: [], loading: true });
    // Load recent chats for empty state
    getRecents({ limit: 8 })
      .then((results) => {
        set({ recentChats: results as ThreadSummary[], loading: false });
      })
      .catch(() => set({ loading: false }));
  },

  closeModal: () => {
    cancelInflight();
    set({
      open: false,
      query: '',
      chatResults: [],
      projectResults: [],
    });
  },

  search: (query: string) => {
    set({ query });

    cancelInflight();

    if (query.trim().length < 2) {
      set({ chatResults: [], projectResults: [], loading: false });
      return;
    }

    set({ loading: true });

    debounceTimer = setTimeout(async () => {
      const controller = new AbortController();
      inflightController = controller;
      try {
        const [chats, projects] = await Promise.all([
          getRecents({ search: query.trim(), limit: 15 }, controller.signal),
          listProjects(query.trim(), controller.signal),
        ]);

        if (controller.signal.aborted) return;
        set({
          chatResults: chats as SearchResult[],
          projectResults: projects,
          loading: false,
        });
      } catch (err) {
        if (controller.signal.aborted) return;
        if ((err as { name?: string })?.name === 'AbortError') return;
        set({ loading: false });
      } finally {
        if (inflightController === controller) inflightController = null;
      }
    }, 300);
  },
}));
