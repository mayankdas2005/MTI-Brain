import { describe, it, expect, beforeEach } from 'vitest';
import { act } from '@testing-library/react';
import { useUIStore } from '@/lib/store/ui';

describe('useUIStore', () => {
  beforeEach(() => {
    act(() => {
      useUIStore.setState({
        sidebarOpen: true,
        mobileSidebarOpen: false,
        tabletSidebarOverlayOpen: false,
        shortcutsOpen: false,
        createProjectOpen: false,
        tourReplay: false,
        thinkingPanelOpen: false,
        thinkingPanelMessageId: null,
        thinkingPanelWidth: 440,
      });
    });
  });

  describe('sidebar', () => {
    it('starts with sidebar open', () => {
      expect(useUIStore.getState().sidebarOpen).toBe(true);
    });

    it('toggles sidebar closed', () => {
      act(() => {
        useUIStore.getState().toggleSidebar();
      });
      expect(useUIStore.getState().sidebarOpen).toBe(false);
    });

    it('toggles sidebar back open', () => {
      act(() => {
        useUIStore.getState().toggleSidebar();
      });
      act(() => {
        useUIStore.getState().toggleSidebar();
      });
      expect(useUIStore.getState().sidebarOpen).toBe(true);
    });

    it('sets sidebar open explicitly', () => {
      act(() => {
        useUIStore.getState().setSidebarOpen(false);
      });
      expect(useUIStore.getState().sidebarOpen).toBe(false);

      act(() => {
        useUIStore.getState().setSidebarOpen(true);
      });
      expect(useUIStore.getState().sidebarOpen).toBe(true);
    });
  });

  describe('mobile sidebar', () => {
    it('starts closed', () => {
      expect(useUIStore.getState().mobileSidebarOpen).toBe(false);
    });

    it('opens mobile sidebar', () => {
      act(() => {
        useUIStore.getState().setMobileSidebarOpen(true);
      });
      expect(useUIStore.getState().mobileSidebarOpen).toBe(true);
    });

    it('toggles mobile sidebar', () => {
      act(() => {
        useUIStore.getState().toggleMobileSidebar();
      });
      expect(useUIStore.getState().mobileSidebarOpen).toBe(true);

      act(() => {
        useUIStore.getState().toggleMobileSidebar();
      });
      expect(useUIStore.getState().mobileSidebarOpen).toBe(false);
    });

    it('is independent from desktop sidebar', () => {
      act(() => {
        useUIStore.getState().setMobileSidebarOpen(true);
        useUIStore.getState().setSidebarOpen(false);
      });

      expect(useUIStore.getState().mobileSidebarOpen).toBe(true);
      expect(useUIStore.getState().sidebarOpen).toBe(false);
    });
  });

  describe('tablet sidebar overlay', () => {
    it('starts closed', () => {
      expect(useUIStore.getState().tabletSidebarOverlayOpen).toBe(false);
    });

    it('opens and closes', () => {
      act(() => {
        useUIStore.getState().setTabletSidebarOverlayOpen(true);
      });
      expect(useUIStore.getState().tabletSidebarOverlayOpen).toBe(true);

      act(() => {
        useUIStore.getState().setTabletSidebarOverlayOpen(false);
      });
      expect(useUIStore.getState().tabletSidebarOverlayOpen).toBe(false);
    });
  });

  describe('shortcuts dialog', () => {
    it('starts closed', () => {
      expect(useUIStore.getState().shortcutsOpen).toBe(false);
    });

    it('opens shortcuts', () => {
      act(() => {
        useUIStore.getState().setShortcutsOpen(true);
      });
      expect(useUIStore.getState().shortcutsOpen).toBe(true);
    });

    it('toggles shortcuts', () => {
      act(() => {
        useUIStore.getState().toggleShortcuts();
      });
      expect(useUIStore.getState().shortcutsOpen).toBe(true);

      act(() => {
        useUIStore.getState().toggleShortcuts();
      });
      expect(useUIStore.getState().shortcutsOpen).toBe(false);
    });
  });

  describe('create project dialog', () => {
    it('starts closed', () => {
      expect(useUIStore.getState().createProjectOpen).toBe(false);
    });

    it('opens and closes', () => {
      act(() => {
        useUIStore.getState().setCreateProjectOpen(true);
      });
      expect(useUIStore.getState().createProjectOpen).toBe(true);

      act(() => {
        useUIStore.getState().setCreateProjectOpen(false);
      });
      expect(useUIStore.getState().createProjectOpen).toBe(false);
    });
  });

  describe('tour replay', () => {
    it('starts with tourReplay false', () => {
      expect(useUIStore.getState().tourReplay).toBe(false);
    });

    it('starts tour replay', () => {
      act(() => {
        useUIStore.getState().startTourReplay();
      });
      expect(useUIStore.getState().tourReplay).toBe(true);
    });

    it('stops tour replay', () => {
      act(() => {
        useUIStore.getState().startTourReplay();
      });
      act(() => {
        useUIStore.getState().stopTourReplay();
      });
      expect(useUIStore.getState().tourReplay).toBe(false);
    });
  });

  describe('thinking panel', () => {
    it('starts closed with default width', () => {
      const state = useUIStore.getState();
      expect(state.thinkingPanelOpen).toBe(false);
      expect(state.thinkingPanelMessageId).toBeNull();
      expect(state.thinkingPanelWidth).toBe(440);
    });

    it('opens with a message id', () => {
      act(() => {
        useUIStore.getState().openThinkingPanel('msg-123');
      });

      const state = useUIStore.getState();
      expect(state.thinkingPanelOpen).toBe(true);
      expect(state.thinkingPanelMessageId).toBe('msg-123');
    });

    it('closes the panel', () => {
      act(() => {
        useUIStore.getState().openThinkingPanel('msg-123');
      });
      act(() => {
        useUIStore.getState().closeThinkingPanel();
      });

      expect(useUIStore.getState().thinkingPanelOpen).toBe(false);
    });

    it('sets custom width', () => {
      act(() => {
        useUIStore.getState().setThinkingPanelWidth(600);
      });
      expect(useUIStore.getState().thinkingPanelWidth).toBe(600);
    });

    it('switches to different message when already open', () => {
      act(() => {
        useUIStore.getState().openThinkingPanel('msg-1');
      });
      act(() => {
        useUIStore.getState().openThinkingPanel('msg-2');
      });

      const state = useUIStore.getState();
      expect(state.thinkingPanelOpen).toBe(true);
      expect(state.thinkingPanelMessageId).toBe('msg-2');
    });
  });
});
