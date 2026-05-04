import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import * as api from '../api';
import type { ProjectOut, ProjectDetail } from '../types/api';

let searchTimer: ReturnType<typeof setTimeout> | null = null;
let searchController: AbortController | null = null;

// In-flight dedup. Layout fires fetchProjects on every navigation; the projects
// page also fires it on mount; React StrictMode double-mounts both in dev.
// One flight for the default no-search call, one Map keyed by id for details.
let fetchProjectsFlight: Promise<void> | null = null;
const fetchProjectFlights = new Map<string, Promise<void>>();

interface ProjectStore {
  // List
  projects: ProjectOut[];
  // Search results live here, separate from `projects`, so the sidebar /
  // starred list (which reads `projects`) keeps showing the full set when
  // the user is searching from the Projects page.
  searchResults: ProjectOut[];
  searchLoading: boolean;
  loading: boolean;
  fetched: boolean;
  lastFetched: number;
  searchQuery: string;

  // Detail
  currentProject: ProjectDetail | null;
  currentProjectLoading: boolean;

  // Per-project detail cache. Mirrors threadMessageMap in the threads store:
  // populated on first fetch, mutated/evicted by writes, in-memory only so
  // cold loads still re-fetch authoritative data.
  projectDetailMap: Record<string, ProjectDetail>;

  // Actions - list
  fetchProjects: (search?: string) => Promise<void>;
  setSearchQuery: (query: string) => void;

  // Actions - CRUD
  createProject: (name: string, description?: string) => Promise<ProjectOut>;
  fetchProject: (id: string) => Promise<void>;
  updateProject: (id: string, name?: string, description?: string) => Promise<void>;
  deleteProject: (id: string) => Promise<void>;
  starProject: (id: string) => Promise<void>;

  // Background refresh: if the project detail is currently open for this id,
  // re-pull it silently. Called by thread mutations in the thread store so
  // the detail page stays in sync with moves/deletes/new-chat events that
  // happened elsewhere in the app.
  refreshCurrentProjectIfMatches: (projectId: string | null | undefined) => void;

  // Cross-store helpers used by the threads store to keep the detail cache
  // (and the open detail page, when relevant) consistent without forcing a
  // round-trip for every small mutation.
  mutateProjectDetail: (
    projectId: string,
    updater: (p: ProjectDetail) => ProjectDetail,
  ) => void;
  invalidateProjectDetail: (projectId: string) => void;
}

export const useProjectStore = create<ProjectStore>()(persist((set, get) => ({
  projects: [],
  searchResults: [],
  searchLoading: false,
  loading: false,
  fetched: false,
  lastFetched: 0,
  searchQuery: '',
  currentProject: null,
  currentProjectLoading: false,
  projectDetailMap: {},

  fetchProjects: async (search) => {
    const isDefault = search === undefined;
    if (isDefault && fetchProjectsFlight) return fetchProjectsFlight;

    const run = async () => {
      if (isDefault) {
        // Master cache fetch: only show loading skeleton on first fetch,
        // not on background revalidation.
        if (!get().fetched) set({ loading: true });
        try {
          const projects = await api.listProjects(undefined);
          set({ projects, loading: false, fetched: true, lastFetched: Date.now() });
        } catch {
          set({ loading: false });
        }
      } else {
        // Search call: writes to `searchResults` only, leaves `projects` alone
        // so the sidebar (which reads `projects`) stays populated.
        if (searchController) searchController.abort();
        const controller = new AbortController();
        searchController = controller;
        set({ searchLoading: true });
        try {
          const results = await api.listProjects(search, controller.signal);
          if (controller.signal.aborted) return;
          set({ searchResults: results, searchLoading: false });
        } catch (err) {
          if (controller.signal.aborted) return;
          if ((err as { name?: string })?.name === 'AbortError') return;
          set({ searchLoading: false });
        } finally {
          if (searchController === controller) searchController = null;
        }
      }
    };

    const p = run();
    if (isDefault) {
      fetchProjectsFlight = p.finally(() => { fetchProjectsFlight = null; });
    }
    return p;
  },

  setSearchQuery: (query) => {
    set({ searchQuery: query });
    if (searchTimer) clearTimeout(searchTimer);
    // Clear any in-flight search immediately on keystroke.
    if (searchController) {
      searchController.abort();
      searchController = null;
    }
    if (!query) {
      // Clearing the input: drop search results, sidebar/page fall back
      // to the `projects` cache.
      set({ searchResults: [], searchLoading: false });
      return;
    }
    searchTimer = setTimeout(() => {
      get().fetchProjects(query);
    }, 300);
  },

  createProject: async (name, description) => {
    const project = await api.createProject(name, description);
    set((state) => ({ projects: [project, ...state.projects] }));
    return project;
  },

  fetchProject: async (id) => {
    const inflight = fetchProjectFlights.get(id);
    if (inflight) return inflight;

    const run = async () => {
      // Three-tier seeding to match the chat detail page's instant feel:
      //  1. Full cache hit  → render header + threads immediately, refresh quietly.
      //  2. List-only seed  → render header now, show a thread-list skeleton
      //                       while the detail request lands.
      //  3. Cold            → full-page skeleton (rare; only on first ever visit).
      const cached = get().projectDetailMap[id];
      const currentId = get().currentProject?.id;

      if (cached) {
        if (currentId !== id) set({ currentProject: cached });
        set({ currentProjectLoading: false });
      } else {
        const listEntry = get().projects.find((p) => p.id === id);
        if (listEntry && currentId !== id) {
          set({
            currentProject: {
              id: listEntry.id,
              name: listEntry.name,
              description: listEntry.description,
              starred: listEntry.starred,
              threads: [],
              created_at: listEntry.created_at,
              updated_at: listEntry.updated_at,
            },
          });
        }
        // Threads aren't available yet either way - flag for the page.
        set({ currentProjectLoading: true });
      }

      try {
        const project = await api.getProject(id);
        // Always update the per-id cache, even if the user navigated away
        // mid-flight - the next visitor will get fresh data.
        set((state) => ({
          projectDetailMap: { ...state.projectDetailMap, [id]: project },
          // Reconcile the list entry with the authoritative detail response.
          // Without this, sidebar (driven by `projects`) and the detail page
          // (driven by `currentProject`) can disagree on starred / name /
          // description after a cross-tab edit or stale-cache hydration.
          projects: state.projects.map((p) =>
            p.id === id
              ? {
                  ...p,
                  name: project.name,
                  description: project.description,
                  starred: project.starred,
                  thread_count: project.threads.length,
                  updated_at: project.updated_at,
                }
              : p,
          ),
        }));
        // Race guard: only overwrite currentProject if we're still viewing
        // this id (or holding a header-only seed for it).
        const latest = get().currentProject;
        if (!latest || latest.id === id) {
          set({ currentProject: project, currentProjectLoading: false });
        } else {
          set({ currentProjectLoading: false });
        }
      } catch {
        set({ currentProjectLoading: false });
        // Only surface a hard error when we have nothing to render. With a
        // cache hit the user keeps seeing the stale-but-valid view and the
        // next mutation/refresh will reconcile.
        if (!cached) throw new Error('Project not found');
      }
    };

    const p = run();
    fetchProjectFlights.set(id, p.finally(() => fetchProjectFlights.delete(id)));
    return p;
  },

  updateProject: async (id, name, description) => {
    const prev = get().projects;
    const prevCurrent = get().currentProject;
    const prevMap = get().projectDetailMap;
    const patch = (p: { name: string; description: string | null }) => ({
      ...p,
      ...(name !== undefined && { name }),
      ...(description !== undefined && { description }),
    });
    // Optimistic update: list, currentProject, and detail cache.
    set({
      projects: prev.map((p) => (p.id === id ? { ...p, ...patch(p) } : p)),
    });
    if (prevCurrent?.id === id) {
      set({ currentProject: { ...prevCurrent, ...patch(prevCurrent) } });
    }
    if (prevMap[id]) {
      set((state) => ({
        projectDetailMap: { ...state.projectDetailMap, [id]: { ...prevMap[id], ...patch(prevMap[id]) } },
      }));
    }

    try {
      const body: { name?: string; description?: string } = {};
      if (name !== undefined) body.name = name;
      if (description !== undefined) body.description = description;
      await api.updateProject(id, body);
    } catch {
      // Rollback all three.
      set({ projects: prev, currentProject: prevCurrent, projectDetailMap: prevMap });
    }
  },

  deleteProject: async (id) => {
    const prev = get().projects;
    const prevCurrent = get().currentProject;
    const prevMap = get().projectDetailMap;
    set({ projects: prev.filter((p) => p.id !== id) });
    if (get().currentProject?.id === id) {
      set({ currentProject: null });
    }
    // Drop the detail cache entry so a re-create with the same id (or a
    // navigation back) doesn't render stale data.
    if (prevMap[id]) {
      const { [id]: _evict, ...rest } = prevMap;
      set({ projectDetailMap: rest });
    }

    try {
      await api.deleteProject(id);
    } catch {
      set({ projects: prev, currentProject: prevCurrent, projectDetailMap: prevMap });
    }
  },

  starProject: async (id) => {
    const prev = get().projects;
    const prevCurrent = get().currentProject;
    const prevMap = get().projectDetailMap;
    // Optimistic toggle across list, currentProject, and detail cache so the
    // UI reacts instantly. Source of truth is the API response below.
    const target = prev.find((p) => p.id === id);
    if (!target) return;
    const optimistic = !target.starred;
    set({
      projects: prev.map((p) =>
        p.id === id ? { ...p, starred: optimistic } : p,
      ),
    });
    if (prevCurrent?.id === id) {
      set({ currentProject: { ...prevCurrent, starred: optimistic } });
    }
    if (prevMap[id]) {
      set((state) => ({
        projectDetailMap: {
          ...state.projectDetailMap,
          [id]: { ...prevMap[id], starred: optimistic },
        },
      }));
    }

    try {
      // The endpoint returns the post-toggle authoritative value - apply it
      // everywhere so list, currentProject, and cache can't drift even if
      // the local optimistic guess disagreed (stale state, cross-tab edit).
      const { starred } = await api.starProject(id);
      set((state) => ({
        projects: state.projects.map((p) =>
          p.id === id ? { ...p, starred } : p,
        ),
        currentProject:
          state.currentProject?.id === id
            ? { ...state.currentProject, starred }
            : state.currentProject,
        projectDetailMap: state.projectDetailMap[id]
          ? { ...state.projectDetailMap, [id]: { ...state.projectDetailMap[id], starred } }
          : state.projectDetailMap,
      }));
    } catch {
      set({ projects: prev, currentProject: prevCurrent, projectDetailMap: prevMap });
    }
  },

  refreshCurrentProjectIfMatches: (projectId) => {
    if (!projectId) return;
    const current = get().currentProject;
    if (current?.id === projectId) {
      // Fire-and-forget; fetchProject has its own SWR semantics so the
      // refresh is invisible (cache hit keeps the screen warm).
      get().fetchProject(projectId).catch(() => {});
    }
  },

  mutateProjectDetail: (projectId, updater) => {
    const cur = get().currentProject;
    const cached = get().projectDetailMap[projectId];
    if (cur?.id === projectId) {
      set({ currentProject: updater(cur) });
    }
    if (cached) {
      set((state) => ({
        projectDetailMap: { ...state.projectDetailMap, [projectId]: updater(cached) },
      }));
    }
  },

  invalidateProjectDetail: (projectId) => {
    const prevMap = get().projectDetailMap;
    if (!prevMap[projectId]) return;
    const { [projectId]: _evict, ...rest } = prevMap;
    set({ projectDetailMap: rest });
  },
}), {
  name: 'mti-brain-projects-cache',
  storage: createJSONStorage(() => localStorage),
  // Persist only the list snapshot; loading flags, currentProject, and the
  // per-id detail cache are session-scoped (parity with threadMessageMap).
  partialize: (state) => ({
    projects: state.projects,
    fetched: state.fetched,
    lastFetched: state.lastFetched,
  }),
}));
