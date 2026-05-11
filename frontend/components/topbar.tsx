'use client';

import { useEffect, useState, useRef } from 'react';
import { usePathname } from 'next/navigation';
import { Star, Search, FileDown, Menu, Link2 } from 'lucide-react';
import { exportThread } from '@/lib/utils/export';
import { exportChartAsCanvas } from '@/components/message-visualization';
import { Button } from '@/components/ui/button';
import { toast } from '@/lib/toast';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';

import { useThreadStore } from '@/lib/store/threads';
import { useSearchStore } from '@/lib/store/search';
import { useActivityStore } from '@/lib/store/activity';
import { useUIStore } from '@/lib/store/ui';
import { useIsMobile, useIsTablet } from '@/hooks/use-mobile';

export function Topbar() {
  const openSearch = useSearchStore((s) => s.openModal);
  const isMobile = useIsMobile();
  const isTablet = useIsTablet();
  const setMobileSidebarOpen = useUIStore((s) => s.setMobileSidebarOpen);
  const setTabletOverlayOpen = useUIStore((s) => s.setTabletSidebarOverlayOpen);
  const handleNavToggle = () => {
    if (isMobile) setMobileSidebarOpen(true);
    else if (isTablet) setTabletOverlayOpen(true);
  };
  // The thread chrome (title + star + export) only belongs in the topbar
  // while the user is actually viewing a chat. Without this gate the
  // last-viewed thread's title leaks onto /chats, /starred, /settings, etc.
  // because `currentThreadId` is sticky in the store across navigations.
  const pathname = usePathname();
  const onThreadRoute = pathname?.startsWith('/chat/') ?? false;

  const currentThreadId = useThreadStore((s) => s.currentThreadId);
  const currentThreadTitle = useThreadStore((s) => s.currentThreadTitle);
  const currentThreadStarred = useThreadStore((s) => s.currentThreadStarred);
  const starThread = useThreadStore((s) => s.starThread);
  const currentMessages = useThreadStore((s) => s.currentMessages);
  const threads = useThreadStore((s) => s.threads);

  const showThreadChrome = onThreadRoute && !!currentThreadId;

  // Seed activity history from already-loaded threads' updated_at the first
  // time they're available. Lets a returning user see their honest streak
  // even if the device was wiped or this is a new browser.
  useEffect(() => {
    if (threads.length === 0) return;
    const stamps = threads.map((t) => t.updated_at).filter(Boolean);
    useActivityStore.getState().seedFromUpdatedAts(stamps);
  }, [threads.length]);

  const handleExport = async () => {
    if (!currentThreadId || !currentThreadTitle || !currentMessages.length) return;
    // Capture every on-screen chart as a PNG data URL keyed by the assistant
    // message's conversation_id, so renderExchange can embed the rendered
    // visual in the PDF (vector → PNG via canvas, no extra dep).
    const chartImages = new Map<string, string>();
    const chartEls = document.querySelectorAll<HTMLElement>('[data-chart-conv-id]');
    for (const el of Array.from(chartEls)) {
      const convId = el.dataset.chartConvId;
      if (!convId) continue;
      try {
        const titleEl = el.querySelector('p.text-sm') as HTMLElement | null;
        const canvas = await exportChartAsCanvas(el, titleEl?.textContent ?? undefined);
        chartImages.set(convId, canvas.toDataURL('image/png'));
      } catch {
        // Skip charts that fail to capture; export.ts will fall back to the data table.
      }
    }
    exportThread(currentThreadId, currentThreadTitle, currentMessages, chartImages);
  };

  // Detect platform for shortcut label
  const [isMac, setIsMac] = useState(false);
  useEffect(() => {
    setIsMac(navigator.platform.toUpperCase().indexOf('MAC') >= 0);
  }, []);

  const handleShare = () => {
    void navigator.clipboard.writeText(window.location.href).then(() => {
      toast.success('Link copied to clipboard');
    }).catch(() => {
      toast.error('Failed to copy link');
    });
  };

  // Listen for /export slash command
  useEffect(() => {
    const handler = () => { void handleExport(); };
    window.addEventListener('mti-brain:export-pdf', handler);
    return () => window.removeEventListener('mti-brain:export-pdf', handler);
  });

  // Star reward burst animation
  const [starBurst, setStarBurst] = useState(false);
  const starBurstTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleStar = () => {
    if (!currentThreadId) return;
    if (!currentThreadStarred) {
      setStarBurst(true);
      if (starBurstTimer.current) clearTimeout(starBurstTimer.current);
      starBurstTimer.current = setTimeout(() => setStarBurst(false), 400);
    }
    starThread(currentThreadId);
  };

  return (
    <div
      className="flex items-center justify-between h-12 px-3 text-[var(--header-foreground)]"
      style={{
        backgroundColor: 'var(--header)',
        borderBottom: '1px solid var(--header-control-border)',
        boxShadow: '0 1px 0 0 var(--header-control-border), 0 2px 12px -2px rgba(0,0,0,0.18)',
        paddingTop: 'env(safe-area-inset-top)',
      }}
    >
      {/* Left: hamburger on phone/tablet, spacer on desktop */}
      <div className="flex items-center gap-1">
        {(isMobile || isTablet) && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={handleNavToggle}
                aria-label="Open navigation"
                className="tap-44 flex items-center justify-center h-10 w-10 rounded-lg text-[var(--header-foreground)] hover:bg-[var(--header-control-bg-hover)] active:scale-[0.92] transition-spring"
              >
                <Menu className="w-5 h-5" />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">Open navigation</TooltipContent>
          </Tooltip>
        )}
      </div>

      {/* Center: Thread title */}
      {showThreadChrome && currentThreadTitle ? (
        <div className="flex items-center gap-2 min-w-0 max-w-[160px] sm:max-w-xs md:max-w-md">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label={currentThreadStarred ? 'Unstar this conversation' : 'Star this conversation'}
                aria-pressed={currentThreadStarred}
                data-onboarding="star"
                className="shrink-0 h-8 w-8 border border-transparent hover:border-[var(--header-control-border)] hover:bg-[var(--header-control-bg)] transition-spring active:scale-[0.82]"
                onClick={handleStar}
              >
                <Star
                  className={`w-4 h-4 transition-colors duration-200 ${starBurst ? 'star-burst' : ''} ${
                    currentThreadStarred
                      ? 'fill-[var(--color-star)] text-[var(--color-star)]'
                      : 'text-[var(--header-foreground)]/60'
                  }`}
                />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="bottom">{currentThreadStarred ? 'Unstar' : 'Star'}</TooltipContent>
          </Tooltip>
          <span className="text-sm font-medium truncate text-[var(--header-foreground)]">
            {currentThreadTitle}
          </span>
        </div>
      ) : showThreadChrome ? (
        <Skeleton className="h-4 w-40" />
      ) : (
        <div />
      )}

      {/* Right: Export + Search */}
      <div className="flex items-center gap-1 sm:gap-2">
        {showThreadChrome && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={handleShare}
                aria-label="Copy link to this conversation"
                className="flex items-center justify-center h-8 w-8 rounded-lg border border-[var(--header-control-border)] bg-[var(--header-control-bg)] hover:bg-[var(--header-control-bg-hover)] transition-colors text-[var(--header-foreground)] transition-spring active:scale-[0.88]"
              >
                <Link2 className="w-3.5 h-3.5" aria-hidden />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">Copy link</TooltipContent>
          </Tooltip>
        )}
        {showThreadChrome && currentMessages.length > 0 && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                onClick={handleExport}
                aria-label="Export conversation as PDF"
                data-onboarding="export-pdf"
                className="flex items-center justify-center h-8 w-8 rounded-lg border border-[var(--header-control-border)] bg-[var(--header-control-bg)] hover:bg-[var(--header-control-bg-hover)] transition-colors text-[var(--header-foreground)] transition-spring active:scale-[0.88]"
              >
                <FileDown className="w-3.5 h-3.5" aria-hidden />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">Export as PDF</TooltipContent>
          </Tooltip>
        )}
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              onClick={openSearch}
              aria-label="Search"
              data-onboarding="cmd-k"
              className="tap-44 flex items-center gap-2 min-h-[40px] px-3 py-1.5 rounded-lg border border-[var(--header-control-border)] bg-[var(--header-control-bg)] hover:bg-[var(--header-control-bg-hover)] transition-colors text-[var(--header-foreground)] text-sm backdrop-blur-sm"
            >
              <Search className="w-3.5 h-3.5" aria-hidden />
              <span className="hidden sm:inline">Search</span>
              <kbd className="hidden sm:inline rounded border border-[var(--header-control-border)] bg-[var(--header-control-bg)] px-1.5 py-0.5 text-[10px] font-mono text-[var(--header-foreground)]/60">
                {isMac ? '⌘K' : 'Ctrl+K'}
              </kbd>
            </button>
          </TooltipTrigger>
          <TooltipContent side="bottom">{`Search (${isMac ? '⌘' : 'Ctrl+'}K)`}</TooltipContent>
        </Tooltip>
      </div>
    </div>
  );
}
