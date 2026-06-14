'use client';

import Image from 'next/image';
import { Sidebar } from '@/components/sidebar';
import { Topbar } from '@/components/topbar';
import { SearchModal } from '@/components/search-modal';
import { ShortcutsDialog } from '@/components/shortcuts-dialog';
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet';
import { useEffect, useState, startTransition } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import { isAuthenticated, getStoredUser, getLoginGate, setLoginGate } from '@/lib/auth';
import { useUIStore } from '@/lib/store/ui';
import { useIsMobile, useIsTablet } from '@/hooks/use-mobile';
import { usePreferencesStore } from '@/lib/store/preferences';
import { useSearchStore } from '@/lib/store/search';
import { useThreadStore } from '@/lib/store/threads';
import { useProjectStore } from '@/lib/store/projects';
import { usePinnedMetricsStore } from '@/lib/store/pinned-metrics';
import { copyText } from '@/lib/utils';
import { useKeyboardShortcuts } from '@/hooks/use-keyboard-shortcuts';
import { useTheme } from 'next-themes';
import { useStreamCompletionNotice } from '@/lib/hooks/use-stream-completion-notice';
import { useDashboardNotice } from '@/lib/hooks/use-dashboard-notice';
import { useGraphContextNotice } from '@/lib/hooks/use-graph-context-notice';
import { CreditsOverlay } from '@/components/credits-overlay';
import { OnboardingTour } from '@/components/onboarding-tour';
import { InstallPrompt } from '@/components/install-prompt';
import { LiveAnnouncer } from '@/components/live-announcer';
import { NavigationProgress } from '@/components/navigation-progress';

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
  const pathname = usePathname();
  const openSearch = useSearchStore((s) => s.openModal);
  const { theme, setTheme } = useTheme();
  const shortcutsOpen = useUIStore((s) => s.shortcutsOpen);
  const setShortcutsOpen = useUIStore((s) => s.setShortcutsOpen);
  const toggleShortcuts = useUIStore((s) => s.toggleShortcuts);
  const [creditsOpen, setCreditsOpen] = useState(false);

  const isMobile = useIsMobile();
  const isTablet = useIsTablet();
  const mobileSidebarOpen = useUIStore((s) => s.mobileSidebarOpen);
  const setMobileSidebarOpen = useUIStore((s) => s.setMobileSidebarOpen);
  const tabletOverlayOpen = useUIStore((s) => s.tabletSidebarOverlayOpen);
  const setTabletOverlayOpen = useUIStore((s) => s.setTabletSidebarOverlayOpen);

  // Auth guard: nothing renders (no child effects fire) until isAuthenticated()
  // confirms a valid non-expired token. This prevents pages like /chat/[id]
  // from firing API calls against an expired session before the redirect fires.
  const [authChecked, setAuthChecked] = useState(false);

  // First-paint guard: useIsMobile/useIsTablet return false during SSR and on
  // the very first client render, which would briefly flash the desktop
  // sidebar on phones. Render a neutral placeholder for one frame instead.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  // Close any open mobile/tablet sheets if the user resizes back into desktop
  // range - otherwise the sheet stays open invisibly and reappears next time
  // the viewport drops below md.
  useEffect(() => {
    if (!isMobile && !isTablet) {
      if (mobileSidebarOpen) setMobileSidebarOpen(false);
      if (tabletOverlayOpen) setTabletOverlayOpen(false);
    }
  }, [isMobile, isTablet, mobileSidebarOpen, tabletOverlayOpen, setMobileSidebarOpen, setTabletOverlayOpen]);

  // Cross-context stream-completion notifications. Routes each completion
  // to the right channel based on tab visibility and current route:
  // in-app toast for same-tab nav, OS notification for hidden tabs.
  useStreamCompletionNotice();
  useDashboardNotice();
  useGraphContextNotice();

  // Listen for 401s dispatched by the API client and redirect via the router
  // (avoids the full-page reload that window.location.href would trigger).
  useEffect(() => {
    const onUnauth = () => router.replace('/');
    window.addEventListener('mti-brain:unauthenticated', onUnauth);
    return () => window.removeEventListener('mti-brain:unauthenticated', onUnauth);
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
    // React StrictMode double-invokes effects. The `cancelled` flag ensures
    // only the final (active) invocation acts - the first run's callback is
    // a no-op once cleanup fires, so the gate and prime() fire exactly once.
    let cancelled = false;

    const prime = () => {
      if (cancelled) return;
      setAuthChecked(true);
      const user = getStoredUser();
      if (user?.user_id) {
        usePreferencesStore.getState().rehydrateForUser(user.user_id);
      }
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
      // Fire in parallel with threads + projects so pinned-metrics is ready
      // by the time the user lands on /new, instead of waiting for WelcomeState
      // to mount and trigger it sequentially.
      const pmStore = usePinnedMetricsStore.getState();
      if (!pmStore.fetched) {
        pmStore.fetchMetrics();
      }
    };

    // If a login is in-flight (optimistic navigation from the login page),
    // wait for it to complete before checking auth. Clear the gate only
    // inside the callback so the second StrictMode run still finds it.
    const gate = getLoginGate();
    if (gate) {
      gate.then(() => {
        if (cancelled) return;
        setLoginGate(null);
        if (!isAuthenticated()) { router.replace('/'); return; }
        prime();
      });
      return () => { cancelled = true; };
    }

    if (!isAuthenticated()) { router.replace('/'); return; }
    prime();
    return () => { cancelled = true; };
  }, [router]);

  // Global keyboard shortcuts. Priority: Claude.ai-aligned bindings first
  // (Ctrl+K, Ctrl+Shift+O, Ctrl+., Ctrl+/, Esc), then custom shortcuts not
  // present in Claude (Ctrl+S, Ctrl+Shift+C, Ctrl+Shift+P, Ctrl+Shift+H).
  useKeyboardShortcuts({
    'cmd-k': openSearch,
    'cmd-comma': () => startTransition(() => {
      if (pathname === '/settings') router.back();
      else router.push('/settings');
    }),
    'cmd-shift-o': () => startTransition(() => router.push('/new')),
    'cmd-shift-p': () => startTransition(() => router.push('/projects')),
    'cmd-shift-h': () => startTransition(() => router.push('/chats')),
    'cmd-/': () => toggleShortcuts(),
    'cmd-period': () => useUIStore.getState().toggleSidebar(),
    'cmd-s': () => {
      const { currentThreadId, starThread } = useThreadStore.getState();
      if (currentThreadId) starThread(currentThreadId);
    },
    // 'cmd-shift-v': () => {
    //   window.dispatchEvent(new CustomEvent('mti-brain:toggle-voice'));
    // },
    // 'cmd-shift-e': () => {
    //   window.dispatchEvent(new CustomEvent('mti-brain:export-pdf'));
    // },
    'cmd-shift-l': () => setTheme(theme === 'dark' ? 'light' : 'dark'),
    'cmd-shift-m': () => {
      const { currentThreadId } = useThreadStore.getState();
      if (currentThreadId) window.dispatchEvent(new CustomEvent('mti-brain:add-to-project'));
    },
    'cmd-r': () => {
      const { currentThreadId, currentMessages, retryResponse } = useThreadStore.getState();
      if (!currentThreadId) return;
      const lastAssistant = [...currentMessages].reverse().find((m) => m.role === 'assistant' && m.content);
      if (lastAssistant?.conversation_id) {
        void retryResponse(currentThreadId, lastAssistant.conversation_id);
      }
    },
    'cmd-q': () => useUIStore.getState().startTourReplay(),
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
      // eggs / nav shortcuts below - Cmd+/ is handled above unconditionally).
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

  if (!authChecked) {
    return (
      <div className="relative flex h-screen flex-col items-center justify-center gap-10 bg-background select-none overflow-hidden">
        {/* Soft ambient glow behind the logo */}
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 flex items-center justify-center"
        >
          <div className="h-80 w-80 rounded-full bg-primary/8 blur-[80px]" />
        </div>

        {/* Brand logo - gentle breathe */}
        <div className="relative z-10 animate-brand-pulse">
          <Image
            src="/milestone-logo-black.png"
            alt="Milestone"
            width={180}
            height={101}
            priority
            style={{ height: 'auto' }}
            className="dark:hidden drop-shadow-sm select-none"
            draggable={false}
          />
          <Image
            src="/milestone-logo-white.png"
            alt="Milestone"
            width={180}
            height={101}
            priority
            style={{ height: 'auto' }}
            className="hidden dark:block drop-shadow-sm select-none"
            draggable={false}
          />
        </div>

        {/* Gradient shimmer track */}
        <div className="relative z-10 w-44 h-[2px] rounded-full bg-border/60 overflow-hidden">
          <div className="absolute inset-y-0 animate-shimmer-bar rounded-full" style={{
            width: '50%',
            background: 'linear-gradient(90deg, transparent 0%, var(--primary) 50%, transparent 100%)',
          }} />
        </div>
      </div>
    );
  }

  return (
    <div className="flex h-screen bg-background overflow-hidden" suppressHydrationWarning>
      {/* Skip link - visually hidden until keyboard-focused. Lets SR /
          keyboard users jump straight to the page content without
          tabbing through every sidebar item first. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-[100] focus:rounded-md focus:bg-primary focus:px-3 focus:py-2 focus:text-sm focus:font-medium focus:text-primary-foreground focus:shadow-lg focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
      >
        Skip to main content
      </a>

      {!mounted ? (
        <div aria-hidden className="hidden md:block w-12 lg:w-[280px] shrink-0" />
      ) : isMobile ? (
        <Sheet open={mobileSidebarOpen} onOpenChange={setMobileSidebarOpen}>
          <SheetContent side="left" showClose={false} aria-describedby={undefined} className="p-0 w-[88%] sm:max-w-sm border-r-0 bg-sidebar">
            <SheetTitle className="sr-only">Navigation</SheetTitle>
            <Sidebar forceExpanded />
          </SheetContent>
        </Sheet>
      ) : isTablet ? (
        <>
          <Sidebar forceCollapsed />
          <Sheet open={tabletOverlayOpen} onOpenChange={setTabletOverlayOpen}>
            <SheetContent side="left" showClose={false} aria-describedby={undefined} className="p-0 w-[320px] border-r-0 bg-sidebar">
              <SheetTitle className="sr-only">Navigation</SheetTitle>
              <Sidebar forceExpanded />
            </SheetContent>
          </Sheet>
        </>
      ) : (
        <Sidebar />
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
      <NavigationProgress />
    </div>
  );
}
