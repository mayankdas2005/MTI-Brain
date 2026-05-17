'use client';

import { useState, useMemo, useEffect, useRef, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { useNow } from '@/lib/hooks/use-now';
import { formatRelativeTime } from '@/lib/utils/relative-time';
import { useThreadStore } from '@/lib/store/threads';
import { useProjectStore } from '@/lib/store/projects';
import { Input } from '@/components/ui/input';
import { Button } from '@/components/ui/button';
import { Loader2 } from 'lucide-react';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import { Skeleton } from '@/components/ui/skeleton';
import { highlightQueryInText, renderSearchSnippet } from '@/lib/utils/highlight';
import { Star, Search, X } from 'lucide-react';
import * as api from '@/lib/api/threads';
import type { ThreadSummary, SearchResult } from '@/lib/types/api';

const PAGE_SIZE = 20;

type SortMode = 'recent' | 'oldest' | 'alpha';

const SORT_OPTIONS: { value: SortMode; label: string }[] = [
  { value: 'recent', label: 'Recently updated' },
  { value: 'oldest', label: 'Oldest first' },
  { value: 'alpha', label: 'Alphabetical' },
];

/** Item shape used across both the unfiltered list and the search list.
 *  Backend search returns `SearchResult` (with matched_terms), the
 *  unfiltered listing returns `ThreadSummary` - we render the same row
 *  shape for both, and pass matched_terms through when present. */
type StarredRow = {
  id: string;
  title: string | null;
  project_id: string | null;
  starred: boolean;
  updated_at: string;
  created_at: string;
  /** Server-rendered headline snippet (with <b>-tagged FTS hits).
   *  Only present on search results. */
  headline?: string | null;
  matched_terms?: string[];
};

function fromSummary(t: ThreadSummary): StarredRow {
  return {
    id: t.id,
    title: t.title,
    project_id: t.project_id,
    starred: t.starred ?? true,
    updated_at: t.updated_at,
    created_at: t.created_at,
  };
}

function fromSearchResult(r: SearchResult): StarredRow {
  return {
    id: r.thread_id,
    title: r.title,
    project_id: r.project_id,
    starred: r.starred ?? true,
    updated_at: r.updated_at,
    created_at: r.created_at,
    headline: r.headline,
    matched_terms: r.matched_terms,
  };
}

/**
 * Starred - full-page surface for the user's saved/important threads.
 *
 * Search uses the backend `/chat/recents?starred=true&search=...` so it
 * matches the typo-tolerant FTS + Levenshtein + trigram pipeline that
 * `/chats` uses. The non-search list reads from the thread store
 * (already primed by the layout) for instant render with no skeleton.
 */
export default function StarredPage() {
  const router = useRouter();
  const now = useNow();

  // Fetch starred threads directly from the backend — the Zustand store only
  // holds the most-recent ~20 threads so older starred items would be missing.
  const [starred, setStarred] = useState<ThreadSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const [focusedIndex, setFocusedIndex] = useState(-1);
  const [focusedSearchIndex, setFocusedSearchIndex] = useState(-1);

  const starThread = useThreadStore((s) => s.starThread);
  const projects = useProjectStore((s) => s.projects);

  const projectNameMap = useMemo(
    () => new Map(projects.map((p) => [p.id, p.name])),
    [projects],
  );

  const fetchStarred = useCallback(async (append = false) => {
    setLoading(true);
    try {
      const newOffset = append ? offset : 0;
      const results = await api.getRecents({ starred: true, limit: PAGE_SIZE, offset: newOffset });
      const items = results as ThreadSummary[];
      if (append) {
        setStarred((prev) => {
          const ids = new Set(prev.map((t) => t.id));
          return [...prev, ...items.filter((t) => !ids.has(t.id))];
        });
      } else {
        setStarred(items);
      }
      setOffset(newOffset + items.length);
      setHasMore(items.length === PAGE_SIZE);
    } catch {
      // non-critical — page still shows whatever loaded
    }
    setLoading(false);
  }, [offset]);

  const didInitRef = useRef(false);
  useEffect(() => {
    if (didInitRef.current) return;
    didInitRef.current = true;
    void fetchStarred(false);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Scroll-based pagination — listens on the actual scroll container.
  // Same fix as /chats: IntersectionObserver with root:null breaks inside
  // overflow-hidden ancestors; a scroll event on the container is reliable.
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container || !hasMore || loading) return;
    const onScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = container;
      if (scrollHeight - scrollTop - clientHeight < 300) {
        void fetchStarred(true);
      }
    };
    container.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
    return () => container.removeEventListener('scroll', onScroll);
  }, [hasMore, loading, fetchStarred]);

  const [search, setSearch] = useState('');
  const [sort, setSort] = useState<SortMode>('recent');
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [searchLoading, setSearchLoading] = useState(false);
  const searchAbortRef = useRef<AbortController | null>(null);
  const searchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const isSearching = search.trim().length >= 2;

  useEffect(() => {
    if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    if (searchAbortRef.current) searchAbortRef.current.abort();

    if (!isSearching) {
      setSearchResults([]);
      setSearchLoading(false);
      return;
    }

    setSearchLoading(true);
    searchTimerRef.current = setTimeout(async () => {
      const controller = new AbortController();
      searchAbortRef.current = controller;
      try {
        const results = await api.getRecents(
          { search: search.trim(), starred: true, limit: 50 },
          controller.signal,
        );
        if (controller.signal.aborted) return;
        setSearchResults(results as SearchResult[]);
      } catch {
        if (!controller.signal.aborted) setSearchResults([]);
      } finally {
        if (!controller.signal.aborted) setSearchLoading(false);
      }
    }, 200);

    return () => {
      if (searchTimerRef.current) clearTimeout(searchTimerRef.current);
    };
  }, [search, isSearching]);

  // Mirror unstar actions back into the local list without a refetch.
  const handleUnstar = (threadId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    void starThread(threadId).then(() =>
      setStarred((prev) => prev.filter((t) => t.id !== threadId)),
    );
  };

  const rows: StarredRow[] = useMemo(() => {
    if (isSearching) return searchResults.map(fromSearchResult);

    const list = starred.map(fromSummary);
    return list.sort((a, b) => {
      if (sort === 'alpha') {
        return (a.title || '').localeCompare(b.title || '', 'en-US', { sensitivity: 'base' });
      }
      const aTime = new Date(sort === 'oldest' ? a.created_at : a.updated_at).getTime();
      const bTime = new Date(sort === 'oldest' ? b.created_at : b.updated_at).getTime();
      return sort === 'oldest' ? aTime - bTime : bTime - aTime;
    });
  }, [isSearching, searchResults, starred, sort]);

  const showEmptyState = !isSearching && !loading && starred.length === 0;
  const showNoMatches = isSearching && !searchLoading && rows.length === 0;

  // Reset focused index when the row set changes.
  useEffect(() => { setFocusedIndex(-1); }, [rows.length]);
  useEffect(() => { setFocusedSearchIndex(-1); }, [searchResults.length]);

  // Keyboard navigation — mirrors the pattern in /chats.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Block arrow keys from inputs (cursor movement), but always let Enter through.
      if (e.target instanceof HTMLInputElement && e.key !== 'Enter') return;
      const activeIdx = isSearching ? focusedSearchIndex : focusedIndex;
      const setActive = isSearching ? setFocusedSearchIndex : setFocusedIndex;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setActive((i) => Math.min(i + 1, rows.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActive((i) => Math.max(i - 1, 0));
      } else if (e.key === 'Enter' && activeIdx >= 0) {
        e.preventDefault();
        router.push(`/chat/${rows[activeIdx].id}`);
      } else if (e.key === 'Escape') {
        if (isSearching) setSearch('');
        else setFocusedIndex(-1);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [focusedIndex, focusedSearchIndex, rows, isSearching, router]);

  return (
    <div ref={scrollContainerRef} className="h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-6 sm:py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <h1 className="text-2xl font-semibold tracking-tight">Starred</h1>
            {!isSearching && starred.length > 0 && (
              <span className="text-sm text-muted-foreground tabular-nums">
                {starred.length}{hasMore ? '+' : ''} {starred.length === 1 ? 'thread' : 'threads'}
              </span>
            )}
          </div>
        </div>

        {/* Filter + sort - only when there's something to filter */}
        {(starred.length > 0 || isSearching) && (
          <div className="flex flex-col sm:flex-row gap-3 mb-4">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground pointer-events-none" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="Search starred threads..."
                className="pl-9 pr-9"
              />
              {search && (
                <button
                  onClick={() => setSearch('')}
                  aria-label="Clear filter"
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
            {!isSearching && (
              <Select value={sort} onValueChange={(v) => setSort(v as SortMode)}>
                <SelectTrigger className="w-full sm:w-48">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {SORT_OPTIONS.map((opt) => (
                    <SelectItem key={opt.value} value={opt.value}>
                      {opt.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}
          </div>
        )}

        {/* List / empty states */}
        {showEmptyState ? (
          <div className="text-center py-16 border border-dashed border-border rounded-xl">
            <Star className="w-10 h-10 text-muted-foreground/30 mx-auto mb-3" />
            <h3 className="text-sm font-medium text-foreground mb-1">
              No starred threads yet
            </h3>
            <p className="text-xs text-muted-foreground max-w-sm mx-auto">
              Star a thread from the sidebar or its header to keep it here for quick access.
            </p>
          </div>
        ) : loading && starred.length === 0 ? (
          <StarredSearchSkeleton />
        ) : isSearching && searchLoading ? (
          <StarredSearchSkeleton />
        ) : showNoMatches ? (
          <div className="text-center py-16">
            <p className="text-sm text-muted-foreground">
              No matches for &quot;{search}&quot;
            </p>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSearch('')}
              className="mt-2"
            >
              Clear filter
            </Button>
          </div>
        ) : (
          <div>
            {rows.map((row, index) => {
              const projectName = row.project_id
                ? projectNameMap.get(row.project_id)
                : null;
              return (
                <div key={row.id}>
                  {index > 0 && <div className="border-t border-border mx-4" />}
                  <div
                    onClick={() => router.push(`/chat/${row.id}`)}
                    onMouseEnter={() => {
                      router.prefetch(`/chat/${row.id}`);
                      if (isSearching) setFocusedSearchIndex(index);
                      else setFocusedIndex(index);
                    }}
                    className={`group flex items-center gap-3 px-4 py-[var(--density-pad-y-loose)] rounded-lg cursor-pointer transition-colors ${
                      (isSearching ? focusedSearchIndex : focusedIndex) === index
                        ? 'bg-muted/40 ring-1 ring-border'
                        : 'hover:bg-muted/50'
                    }`}
                  >
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          onClick={(e) => handleUnstar(row.id, e)}
                          aria-label="Unstar thread"
                          className="shrink-0 rounded hover:scale-110 transition-transform"
                        >
                          <Star className="w-4 h-4 fill-[var(--color-star)] text-[var(--color-star)]" />
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="right">Unstar</TooltipContent>
                    </Tooltip>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-foreground line-clamp-2 leading-snug">
                        {highlightQueryInText(row.title || 'Untitled', search, {
                          matchedTerms: row.matched_terms,
                        })}
                      </p>
                      {/* During search, show the matched body excerpt so the
                          user sees WHY a starred thread was returned (matters
                          most for stopword queries like "how" where the title
                          can't reveal the match). */}
                      {row.headline && (
                        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-3 leading-relaxed">
                          {renderSearchSnippet(row.headline, search, {
                            matchedTerms: row.matched_terms,
                          })}
                        </p>
                      )}
                      <p className="text-xs text-muted-foreground mt-0.5 flex flex-wrap items-center gap-x-1 gap-y-0.5">
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="cursor-default">
                              Last message {formatRelativeTime(row.updated_at, now)}
                            </span>
                          </TooltipTrigger>
                          <TooltipContent side="right">
                            {new Date(row.updated_at).toLocaleString([], {
                              dateStyle: 'long',
                              timeStyle: 'short',
                            })}
                          </TooltipContent>
                        </Tooltip>
                        {projectName && (
                          <>
                            <span className="text-muted-foreground/60">in</span>
                            <span>{projectName}</span>
                          </>
                        )}
                      </p>
                    </div>
                  </div>
                </div>
              );
            })}
            {!isSearching && loading && starred.length > 0 && (
              <div className="flex justify-center py-4">
                <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/** Loading skeleton shown while a backend search is in flight. Matches
 *  the row shape rendered above (star + title + headline + meta) so the
 *  layout doesn't jump when results land. */
function StarredSearchSkeleton() {
  return (
    <div>
      {[0, 1, 2, 3].map((i) => (
        <div key={i}>
          {i > 0 && <div className="border-t border-border mx-4" />}
          <div className="flex items-center gap-3 px-4 py-[var(--density-pad-y-loose)]">
            <Skeleton className="h-4 w-4 rounded-sm shrink-0" />
            <div className="flex-1 space-y-1.5">
              <Skeleton className="h-3.5 w-3/5" />
              <Skeleton className="h-3 w-4/5" />
              <Skeleton className="h-3 w-2/5" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
