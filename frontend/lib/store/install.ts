'use client';

import { create } from 'zustand';

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>;
}

interface InstallState {
  canInstall: boolean;
  installed: boolean;
  /** Trigger the platform install prompt. Returns true if the user accepted. */
  promptInstall: () => Promise<boolean>;
}

let cachedEvent: BeforeInstallPromptEvent | null = null;

export const useInstallStore = create<InstallState>((set) => ({
  canInstall: false,
  installed: false,
  promptInstall: async () => {
    if (!cachedEvent) return false;
    const ev = cachedEvent;
    cachedEvent = null;
    set({ canInstall: false });
    await ev.prompt();
    const result = await ev.userChoice;
    if (result.outcome === 'accepted') {
      set({ installed: true });
      return true;
    }
    return false;
  },
}));

interface PwaWindow {
  __mtiBrainPwaPrompt?: BeforeInstallPromptEvent | null;
  __mtiBrainPwaInstalled?: boolean;
}

/** Wire up global listeners. Idempotent - safe to call from multiple places. */
let wired = false;
export function ensureInstallListeners() {
  if (typeof window === 'undefined') return;
  if (wired) return;
  wired = true;

  const w = window as unknown as PwaWindow;

  // Detect already-installed (display-mode standalone) on mount.
  const standalone = window.matchMedia?.('(display-mode: standalone)').matches;
  if (standalone || w.__mtiBrainPwaInstalled) {
    useInstallStore.setState({ installed: true });
  }

  // Pick up an event the inline <head> script captured before React loaded.
  if (w.__mtiBrainPwaPrompt) {
    cachedEvent = w.__mtiBrainPwaPrompt;
    useInstallStore.setState({ canInstall: true });
  }

  window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    cachedEvent = e as BeforeInstallPromptEvent;
    useInstallStore.setState({ canInstall: true });
  });

  window.addEventListener('appinstalled', () => {
    cachedEvent = null;
    useInstallStore.setState({ canInstall: false, installed: true });
  });
}

// Attach listeners as soon as this module is imported. The
// `beforeinstallprompt` event fires VERY early - sometimes before React even
// mounts. If we wait for a useEffect to run, the event has already been
// dispatched and we miss it forever (it doesn't replay). Module-load wiring
// guarantees we catch the first emission. Safe in SSR because of the
// typeof-window guard inside ensureInstallListeners.
ensureInstallListeners();
