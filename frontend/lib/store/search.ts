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
let requestId = 0;

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
    set({
      open: false,
      query: '',
      chatResults: [],
      projectResults: [],
    });
    if (debounceTimer) clearTimeout(debounceTimer);
  },

  search: (query: string) => {
    set({ query });

    if (debounceTimer) clearTimeout(debounceTimer);

    if (query.trim().length < 2) {
      set({ chatResults: [], projectResults: [], loading: false });
      return;
    }

    set({ loading: true });
    const currentRequest = ++requestId;

    debounceTimer = setTimeout(async () => {
      try {
        const [chats, projects] = await Promise.all([
          getRecents({ search: query.trim(), limit: 15 }),
          listProjects(query.trim()),
        ]);

        // Only apply if this is still the latest request
        if (currentRequest === requestId) {
          set({
            chatResults: chats as SearchResult[],
            projectResults: projects,
            loading: false,
          });
        }
      } catch {
        if (currentRequest === requestId) {
          set({ loading: false });
        }
      }
    }, 150);
  },
}));
