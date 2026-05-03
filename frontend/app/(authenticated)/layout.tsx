'use client';

import { Sidebar } from '@/components/sidebar';
import { CollapsedSidebar } from '@/components/collapsed-sidebar';
import { Topbar } from '@/components/topbar';
import { SearchModal } from '@/components/search-modal';
import { ShortcutsDialog } from '@/components/shortcuts-dialog';
import { useEffect, useState, startTransition } from 'react';
import { useRouter } from 'next/navigation';
import { isAuthenticated, getStoredUser } from '@/lib/auth';
import { useUIStore } from '@/lib/store/ui';
import { usePreferencesStore } from '@/lib/store/preferences';
import { useSearchStore } from '@/lib/store/search';
import { useThreadStore } from '@/lib/store/threads';
import { useProjectStore } from '@/lib/store/projects';
import { copyText } from '@/lib/utils';
import { useKeyboardShortcuts } from '@/hooks/use-keyboard-shortcuts';
import { useStreamCompletionNotice } from '@/lib/hooks/use-stream-completion-notice';
import { CreditsOverlay } from '@/components/credits-overlay';
import { OnboardingTour } from '@/components/onboarding-tour';
import { InstallPrompt } from '@/components/install-prompt';
import { LiveAnnouncer } from '@/components/live-announcer';

function OnboardingTourGate() {
  const replay = useUIStore((s) => s.tourReplay);
  const stopReplay = useUIStore((s) => s.stopTourReplay);
  return <OnboardingTour forceOpen={replay} onClose={stopReplay} />;
}
import { toast } from '@/lib/toast';

export default function AuthenticatedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const sidebarOpen = useUIStore((state) => state.sidebarOpen);
  const openSearch = useSearchStore((s) => s.openModal);
  const shortcutsOpen = useUIStore((s) => s.shortcutsOpen);
  const setShortcutsOpen = useUIStore((s) => s.setShortcutsOpen);
  const toggleShortcuts = useUIStore((s) => s.toggleShortcuts);
  const [creditsOpen, setCreditsOpen] = useState(false);

  // Cross-context stream-completion notifications. Routes each completion
  // to the right channel based on tab visibility and current route:
  // in-app toast for same-tab nav, OS notification for hidden tabs.
  useStreamCompletionNotice();

  // Listen for 401s dispatched by the API client and redirect via the router
  // (avoids the full-page reload that window.location.href would trigger).
  useEffect(() => {
    const onUnauth = () => router.replace('/');
    window.addEventListener('quest:unauthenticated', onUnauth);
    return () => window.removeEventListener('quest:unauthenticated', onUnauth);
  }, [router]);

  // Prefetch the routes that keyboard shortcuts can jump to so the first
  // Cmd+Shift+O / Cmd+Shift+P / Cmd+Shift+H feels instant. Only prefetches
  // once per mount; Next.js dedupes anyway.
  useEffect(() => {
    router.prefetch('/new');
    router.prefetch('/projects');
    router.prefetch('/chats');
    router.prefetch('/starred');
  }, [router]);

  // Redirect unauthenticated users - runs client-side only
  useEffect(() => {
    if (!isAuthenticated()) {
      router.replace('/');
      return;
    }
    // Load user-scoped preferences
    const user = getStoredUser();
    if (user?.user_id) {
      usePreferencesStore.getState().rehydrateForUser(user.user_id);
    }
    // TTL-gated priming: this useEffect re-runs on every navigation between
    // authenticated pages, so we MUST skip the network calls when the cache
    // is still fresh. Without this, every page transition fired two HTTP
    // requests even when the data was 1 second old.
    const FRESH_MS = 30_000;
    const now = Date.now();
    const tStore = useThreadStore.getState();
    if (now - (tStore.threadsLastFetched || 0) > FRESH_MS) {
      tStore.fetchRecents();
    }
    const pStore = useProjectStore.getState();
    if (now - (pStore.lastFetched || 0) > FRESH_MS) {
      pStore.fetchProjects();
    }
  }, [router]);

  // Global keyboard shortcuts. Priority: Claude.ai-aligned bindings first
  // (Ctrl+K, Ctrl+Shift+O, Ctrl+., Ctrl+/, Esc), then custom shortcuts not
  // present in Claude (Ctrl+S, Ctrl+Shift+C, Ctrl+Shift+P, Ctrl+Shift+H).
  useKeyboardShortcuts({
    'cmd-k': openSearch,
    'cmd-shift-o': () => startTransition(() => router.push('/new')),
    'cmd-shift-p': () => startTransition(() => router.push('/projects')),
    'cmd-shift-h': () => startTransition(() => router.push('/chats')),
    'cmd-/': () => toggleShortcuts(),
    'cmd-period': () => useUIStore.getState().toggleSidebar(),
    'cmd-s': () => {
      const { currentThreadId, starThread } = useThreadStore.getState();
      if (currentThreadId) starThread(currentThreadId);
    },
    'cmd-shift-c': () => {
      const msgs = useThreadStore.getState().currentMessages;
      const last = [...msgs].reverse().find((m) => m.role === 'assistant' && m.content);
      if (last) {
        void copyText(last.content).then((ok) => {
          if (ok) toast.success('Last response copied');
          else toast.error('Copy failed');
        });
      }
    },
    // Plain `?` opens the cheat sheet (GitHub/Linear convention). Plain
    // `/` opens the search palette (also a Linear/GitHub convention).
    // Both opt out of firing from form fields via the hook.
    'question-mark': () => useUIStore.getState().setShortcutsOpen(true),
    'slash': openSearch,
    'escape': () => {
      // Esc stops the active stream (matches Claude.ai). The dialog system
      // still receives Esc for closing modals because we don't preventDefault.
      const { isStreaming, streamingThreadId, stopGeneration } = useThreadStore.getState();
      if (isStreaming && streamingThreadId) stopGeneration(streamingThreadId);
    },
  });

  // Easter eggs: Konami code + Cmd+Shift+B Milestone facts
  useEffect(() => {
    // Konami: ↑↑↓↓←→←→BA
    const KONAMI = ['ArrowUp','ArrowUp','ArrowDown','ArrowDown','ArrowLeft','ArrowRight','ArrowLeft','ArrowRight','b','a'];
    let konamiPos = 0;
    let konamiTimer: ReturnType<typeof setTimeout> | null = null;

    // Milestone facts for Cmd+Shift+B
    const MTI_FACTS = [
      '🚀 Milestone Technologies has been powering smarter IT since 1997',
      '🌍 Milestone operates in 36 countries across 6 continents',
      '🏢 Headquartered in Fremont, California - the heart of Silicon Valley',
      '👥 3500+ employees delivering IT services and digital solutions at scale',
      '🏆 Great Place to Work certified in the USA, India, Ireland, the Philippines, the UK and Mexico',
      '🤖 Milestone specializes in AI/Automation, Cloud Infrastructure, and Application Services',
      '🤝 Trusted by 200+ of the world\'s leading companies to deliver technology at scale',
      '💡 Sameer Kishore was appointed CEO in 2020, driving Milestone\'s next era of growth',
    ];

    const handler = (e: KeyboardEvent) => {
      const isCmdMod = navigator.platform.toUpperCase().indexOf('MAC') >= 0 ? e.metaKey : e.ctrlKey;

      // Cmd+/ → keyboard shortcuts dialog. Checked BEFORE the input/textarea
      // guard because the user almost always has the chat composer focused
      // when they hit this. react-hotkeys-hook's `mod+/` binding gets flaky
      // on Edge (browser intercepts it for built-in commands when a PWA is
      // installed for the origin), so this manual handler is the reliable
      // path. We accept both `/` and `?` because some layouts emit `?` for
      // shift+/ and we want either to fire the dialog.
      if (isCmdMod && (e.key === '/' || e.key === '?')) {
        e.preventDefault();
        useUIStore.getState().toggleShortcuts();
        return;
      }

      // Skip if user is typing in an input (only matters for the easter
      // eggs / nav shortcuts below — Cmd+/ is handled above unconditionally).
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === 'INPUT' || tag === 'TEXTAREA') return;

      // Konami code detection
      const key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
      if (key === KONAMI[konamiPos]) {
        konamiPos++;
        if (konamiTimer) clearTimeout(konamiTimer);
        konamiTimer = setTimeout(() => { konamiPos = 0; }, 3000);
        if (konamiPos === KONAMI.length) {
          konamiPos = 0;
          setCreditsOpen(true);
        }
      } else if (key === KONAMI[0]) {
        konamiPos = 1; // restart from first match
      } else {
        konamiPos = 0;
      }

      const isCmd = navigator.platform.toUpperCase().indexOf('MAC') >= 0 ? e.metaKey : e.ctrlKey;

      // Cmd+Shift+B → Milestone fact
      if (isCmd && e.shiftKey && (e.key === 'B' || e.key === 'b')) {
        e.preventDefault();
        toast.info(MTI_FACTS[Math.floor(Math.random() * MTI_FACTS.length)]);
      }

      // Cmd+Shift+P → /projects
      if (isCmd && e.shiftKey && (e.key === 'P' || e.key === 'p')) {
        e.preventDefault();
        startTransition(() => router.push('/projects'));
      }

      // Cmd+Shift+H → /chats (H for history)
      if (isCmd && e.shiftKey && (e.key === 'H' || e.key === 'h')) {
        e.preventDefault();
        startTransition(() => router.push('/chats'));
      }

      // Cmd+1..9 → jump to Nth recent (non-starred) thread.
      // Power-user signal that pays off for daily-drivers; mirrors Linear's
      // workspace-switcher and Slack's channel-jump.
      if (isCmd && !e.shiftKey && /^[1-9]$/.test(e.key)) {
        const n = parseInt(e.key, 10);
        const recents = useThreadStore
          .getState()
          .threads.filter((t) => !t.starred)
          .slice(0, 9);
        const target = recents[n - 1];
        if (target) {
          e.preventDefault();
          startTransition(() => router.push(`/chat/${target.id}`));
        }
      }
    };

    window.addEventListener('keydown', handler);
    return () => {
      window.removeEventListener('keydown', handler);
      if (konamiTimer) clearTimeout(konamiTimer);
    };
  }, []);

  return (
    <div className="flex h-screen bg-background overflow-hidden" suppressHydrationWarning>
      {/* Skip link — visually hidden until keyboard-focused. Lets SR /
          keyboard users jump straight to the page content without
          tabbing through every sidebar item first. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[100] focus:rounded-md focus:bg-primary focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-primary-foreground focus:shadow-lg focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
      >
        Skip to main content
      </a>

      {sidebarOpen ? (
        <div className="w-[280px] shrink-0 transition-all duration-200 ease-in-out overflow-hidden">
          <Sidebar />
        </div>
      ) : (
        <CollapsedSidebar />
      )}

      <div className="flex-1 flex flex-col min-w-0">
        <Topbar />
        <main id="main-content" tabIndex={-1} className="flex-1 overflow-hidden">
          {children}
        </main>
      </div>

      <SearchModal />
      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
      <CreditsOverlay open={creditsOpen} onClose={() => setCreditsOpen(false)} />
      <OnboardingTourGate />
      <InstallPrompt />
      <LiveAnnouncer />
    </div>
  );
}
