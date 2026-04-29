'use client';

import { Sidebar } from '@/components/sidebar';
import { CollapsedSidebar } from '@/components/collapsed-sidebar';
import { Topbar } from '@/components/topbar';
import { SearchModal } from '@/components/search-modal';
import { ShortcutsDialog } from '@/components/shortcuts-dialog';
import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { isAuthenticated, getStoredUser } from '@/lib/auth';
import { useUIStore } from '@/lib/store/ui';
import { usePreferencesStore } from '@/lib/store/preferences';
import { useSearchStore } from '@/lib/store/search';
import { useThreadStore } from '@/lib/store/threads';
import { useProjectStore } from '@/lib/store/projects';
import { copyText } from '@/lib/utils';
import { useKeyboardShortcuts } from '@/hooks/use-keyboard-shortcuts';
import { CreditsOverlay } from '@/components/credits-overlay';
import { toast } from '@/lib/toast';

export default function AuthenticatedLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const router = useRouter();
  const sidebarOpen = useUIStore((state) => state.sidebarOpen);
  const openSearch = useSearchStore((s) => s.openModal);
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [creditsOpen, setCreditsOpen] = useState(false);

  // Listen for 401s dispatched by the API client and redirect via the router
  // (avoids the full-page reload that window.location.href would trigger).
  useEffect(() => {
    const onUnauth = () => router.replace('/');
    window.addEventListener('quest:unauthenticated', onUnauth);
    return () => window.removeEventListener('quest:unauthenticated', onUnauth);
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

  // Global keyboard shortcuts
  useKeyboardShortcuts({
    'cmd-k': openSearch,
    'cmd-l': () => router.push('/new'),
    'cmd-/': () => setShortcutsOpen((v) => !v),
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
      // Skip if user is typing in an input
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
        router.push('/projects');
      }

      // Cmd+Shift+H → /chats (H for history)
      if (isCmd && e.shiftKey && (e.key === 'H' || e.key === 'h')) {
        e.preventDefault();
        router.push('/chats');
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
      {sidebarOpen ? (
        <div className="w-[280px] shrink-0 transition-all duration-200 ease-in-out overflow-hidden">
          <Sidebar />
        </div>
      ) : (
        <CollapsedSidebar />
      )}

      <div className="flex-1 flex flex-col min-w-0">
        <Topbar />
        <main className="flex-1 overflow-hidden">
          {children}
        </main>
      </div>

      <SearchModal />
      <ShortcutsDialog open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
      <CreditsOverlay open={creditsOpen} onClose={() => setCreditsOpen(false)} />
    </div>
  );
}
