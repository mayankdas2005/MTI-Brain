import { create } from 'zustand';

interface UIStore {
  sidebarOpen: boolean;
  toggleSidebar: () => void;
  setSidebarOpen: (open: boolean) => void;
  // Mobile off-canvas sheet (independent of desktop `sidebarOpen` so the
  // two viewports don't clobber each other's state).
  mobileSidebarOpen: boolean;
  setMobileSidebarOpen: (open: boolean) => void;
  toggleMobileSidebar: () => void;
  // Tablet overlay sheet - when the user expands the icon rail at md→lg,
  // the full sidebar slides over the content instead of squeezing it.
  tabletSidebarOverlayOpen: boolean;
  setTabletSidebarOverlayOpen: (open: boolean) => void;
  // Shortcuts dialog visibility - lifted into the store so the cmd+K
  // palette and keyboard shortcuts can both open it without prop-drilling.
  shortcutsOpen: boolean;
  setShortcutsOpen: (open: boolean) => void;
  toggleShortcuts: () => void;
  // Create-project dialog visibility.
  createProjectOpen: boolean;
  setCreateProjectOpen: (open: boolean) => void;
  // Onboarding tour replay flag - set true from the user menu to re-open
  // the guided tour after the user has dismissed it once.
  tourReplay: boolean;
  startTourReplay: () => void;
  stopTourReplay: () => void;
  // Thinking side panel state
  thinkingPanelOpen: boolean;
  thinkingPanelMessageId: string | null;
  thinkingPanelWidth: number;
  openThinkingPanel: (messageId: string) => void;
  closeThinkingPanel: () => void;
  setThinkingPanelWidth: (width: number) => void;
}

export const useUIStore = create<UIStore>((set) => ({
  sidebarOpen: true,
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  mobileSidebarOpen: false,
  setMobileSidebarOpen: (open) => set({ mobileSidebarOpen: open }),
  toggleMobileSidebar: () => set((state) => ({ mobileSidebarOpen: !state.mobileSidebarOpen })),
  tabletSidebarOverlayOpen: false,
  setTabletSidebarOverlayOpen: (open) => set({ tabletSidebarOverlayOpen: open }),
  shortcutsOpen: false,
  setShortcutsOpen: (open) => set({ shortcutsOpen: open }),
  toggleShortcuts: () => set((state) => ({ shortcutsOpen: !state.shortcutsOpen })),
  createProjectOpen: false,
  setCreateProjectOpen: (open) => set({ createProjectOpen: open }),
  tourReplay: false,
  startTourReplay: () => set({ tourReplay: true }),
  stopTourReplay: () => set({ tourReplay: false }),
  thinkingPanelOpen: false,
  thinkingPanelMessageId: null,
  thinkingPanelWidth: 380,
  openThinkingPanel: (messageId) => set({ thinkingPanelOpen: true, thinkingPanelMessageId: messageId }),
  closeThinkingPanel: () => set({ thinkingPanelOpen: false }),
  setThinkingPanelWidth: (width) => set({ thinkingPanelWidth: width }),
}));
