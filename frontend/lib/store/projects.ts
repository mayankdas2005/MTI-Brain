import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import * as api from '../api';
import type { ProjectOut, ProjectDetail } from '../types/api';

let searchTimer: ReturnType<typeof setTimeout> | null = null;

interface ProjectStore {
  // List
  projects: ProjectOut[];
  loading: boolean;
  fetched: boolean;
  lastFetched: number;
  searchQuery: string;

  // Detail
  currentProject: ProjectDetail | null;
  currentProjectLoading: boolean;

  // Actions — list
  fetchProjects: (search?: string) => Promise<void>;
  setSearchQuery: (query: string) => void;

  // Actions — CRUD
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
}

export const useProjectStore = create<ProjectStore>()(persist((set, get) => ({
  projects: [],
  loading: false,
  fetched: false,
  lastFetched: 0,
  searchQuery: '',
  currentProject: null,
  currentProjectLoading: false,

  fetchProjects: async (search) => {
    // Only show loading skeleton on first fetch, not on background revalidation
    if (!get().fetched) set({ loading: true });
    try {
      const projects = await api.listProjects(search);
      set({ projects, loading: false, fetched: true, lastFetched: Date.now() });
    } catch {
      set({ loading: false });
    }
  },

  setSearchQuery: (query) => {
    set({ searchQuery: query });
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      get().fetchProjects(query || undefined);
    }, 300);
  },

  createProject: async (name, description) => {
    const project = await api.createProject(name, description);
    set((state) => ({ projects: [project, ...state.projects] }));
    return project;
  },

  fetchProject: async (id) => {
    // SWR: only show skeleton when there's nothing to display.
    // If we already have THIS project cached (e.g. from a recent visit),
    // render it immediately and refresh in the background.
    const currentId = get().currentProject?.id;
    const hasCached = currentId === id;
    if (!hasCached) set({ currentProjectLoading: true });
    try {
      const project = await api.getProject(id);
      set({ currentProject: project, currentProjectLoading: false });
    } catch {
      set({ currentProjectLoading: false });
      if (!hasCached) throw new Error('Project not found');
    }
  },

  updateProject: async (id, name, description) => {
    const prev = get().projects;
    // Optimistic update in list
    set({
      projects: prev.map((p) =>
        p.id === id
          ? { ...p, ...(name !== undefined && { name }), ...(description !== undefined && { description }) }
          : p,
      ),
    });
    // Optimistic update in detail
    const current = get().currentProject;
    if (current?.id === id) {
      set({
        currentProject: {
          ...current,
          ...(name !== undefined && { name }),
          ...(description !== undefined && { description }),
        },
      });
    }

    try {
      const body: { name?: string; description?: string } = {};
      if (name !== undefined) body.name = name;
      if (description !== undefined) body.description = description;
      await api.updateProject(id, body);
    } catch {
      set({ projects: prev }); // rollback
    }
  },

  deleteProject: async (id) => {
    const prev = get().projects;
    set({ projects: prev.filter((p) => p.id !== id) });
    if (get().currentProject?.id === id) {
      set({ currentProject: null });
    }

    try {
      await api.deleteProject(id);
    } catch {
      set({ projects: prev }); // rollback
    }
  },

  starProject: async (id) => {
    const prev = get().projects;
    set({
      projects: prev.map((p) =>
        p.id === id ? { ...p, starred: !p.starred } : p,
      ),
    });
    const current = get().currentProject;
    if (current?.id === id) {
      set({ currentProject: { ...current, starred: !current.starred } });
    }

    try {
      await api.starProject(id);
    } catch {
      set({ projects: prev }); // rollback
    }
  },

  refreshCurrentProjectIfMatches: (projectId) => {
    if (!projectId) return;
    const current = get().currentProject;
    if (current?.id === projectId) {
      // Fire-and-forget; fetchProject skips the loading skeleton when the
      // same project is already on screen, so the refresh is invisible.
      get().fetchProject(projectId).catch(() => {});
    }
  },
}), {
  name: 'quest-projects-cache',
  storage: createJSONStorage(() => localStorage),
  // Persist only the list snapshot; loading flags + currentProject are session-scoped.
  partialize: (state) => ({
    projects: state.projects,
    fetched: state.fetched,
    lastFetched: state.lastFetched,
  }),
}));
