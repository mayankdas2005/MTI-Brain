'use client';

import { useEffect, useState } from 'react';
import { Star, Search } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Skeleton } from '@/components/ui/skeleton';

import { useThreadStore } from '@/lib/store/threads';
import { useSearchStore } from '@/lib/store/search';

export function Topbar() {
  const openSearch = useSearchStore((s) => s.openModal);

  const currentThreadId = useThreadStore((s) => s.currentThreadId);
  const currentThreadTitle = useThreadStore((s) => s.currentThreadTitle);
  const currentThreadStarred = useThreadStore((s) => s.currentThreadStarred);
  const starThread = useThreadStore((s) => s.starThread);

  // Detect platform for shortcut label
  const [isMac, setIsMac] = useState(false);
  useEffect(() => {
    setIsMac(navigator.platform.toUpperCase().indexOf('MAC') >= 0);
  }, []);

  return (
    <div
      className="flex items-center justify-between h-12 px-3 border-b border-[var(--header-control-border)] text-[var(--header-foreground)]"
      style={{ backgroundColor: 'var(--header)' }}
    >
      {/* Left spacer */}
      <div className="flex items-center gap-1" />

      {/* Center: Thread title */}
      {currentThreadId && currentThreadTitle ? (
        <div className="flex items-center gap-2 min-w-0 max-w-md">
          <Button
            variant="ghost"
            size="icon"
            className="shrink-0 h-8 w-8 border border-transparent hover:border-[var(--header-control-border)] hover:bg-[var(--header-control-bg)]"
            onClick={() => currentThreadId && starThread(currentThreadId)}
            title={currentThreadStarred ? 'Unstar' : 'Star'}
          >
            <Star
              className={`w-4 h-4 ${
                currentThreadStarred
                  ? 'fill-yellow-500 text-yellow-500'
                  : 'text-white/70'
              }`}
            />
          </Button>
          <span className="text-sm font-medium truncate text-white/90">
            {currentThreadTitle}
          </span>
        </div>
      ) : currentThreadId ? (
        <Skeleton className="h-4 w-40" />
      ) : (
        <div />
      )}

      {/* Right: Search trigger */}
      <button
        onClick={openSearch}
        className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-[var(--header-control-border)] bg-[var(--header-control-bg)] hover:bg-[var(--header-control-bg-hover)] transition-colors text-[var(--header-foreground)] text-sm backdrop-blur-sm"
        title={`Search (${isMac ? '⌘' : 'Ctrl+'}K)`}
      >
        <Search className="w-3.5 h-3.5" />
        <span className="hidden sm:inline">Search</span>
        <kbd className="hidden sm:inline rounded border border-[var(--header-control-border)] bg-[var(--header-control-bg)] px-1.5 py-0.5 text-[10px] font-mono text-white/70">
          {isMac ? '⌘K' : 'Ctrl+K'}
        </kbd>
      </button>
    </div>
  );
}
