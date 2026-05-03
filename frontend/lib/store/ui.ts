import { create } from 'zustand';

interface UIStore {
  sidebarOpen: boolean;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  // Shortcuts dialog visibility — lifted into the store so the cmd+K
  // palette and keyboard shortcuts can both open it without prop-drilling.
  shortcutsOpen: boolean;
  setShortcutsOpen: (open: boolean) => void;
  toggleShortcuts: () => void;
  // Create-project dialog visibility.
  createProjectOpen: boolean;
  setCreateProjectOpen: (open: boolean) => void;
  // Onboarding tour replay flag — set true from the user menu to re-open
  // the guided tour after the user has dismissed it once.
  tourReplay: boolean;
  startTourReplay: () => void;
  stopTourReplay: () => void;
}

export const useUIStore = create<UIStore>((set) => ({
  sidebarOpen: true,
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  shortcutsOpen: false,
  setShortcutsOpen: (open) => set({ shortcutsOpen: open }),
  toggleShortcuts: () => set((state) => ({ shortcutsOpen: !state.shortcutsOpen })),
  createProjectOpen: false,
  setCreateProjectOpen: (open) => set({ createProjectOpen: open }),
  tourReplay: false,
  startTourReplay: () => set({ tourReplay: true }),
  stopTourReplay: () => set({ tourReplay: false }),
}));
