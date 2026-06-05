'use client';

import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { useNow } from '@/lib/hooks/use-now';
import { formatRelativeTime } from '@/lib/utils/relative-time';
import { useRouter } from 'next/navigation';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Plus,
  Search,
  Loader2,
  Trash2,
  FolderInput,
  MessageSquare,
  FileText,
  X,
} from 'lucide-react';
import { useLabelsStore, LABEL_COLORS } from '@/lib/store/labels';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { MoveToProjectDialog } from '@/components/move-to-project-dialog';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { useProjectStore } from '@/lib/store/projects';
import { useThreadStore } from '@/lib/store/threads';
import { toast } from '@/lib/toast';
import * as api from '@/lib/api/threads';
import type { ThreadSummary, SearchResult } from '@/lib/types/api';
import { highlightQueryInText, renderSearchSnippet } from '@/lib/utils/highlight';

const PAGE_SIZE = 20;

export default function ChatsPage() {
  const router = useRouter();

  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [search, setSearch] = useState('');
  const [loading, setLoading] = useState(true);
  const [searchLoading, setSearchLoading] = useState(false);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  // Projects (for resolving project names)
  const projects = useProjectStore((s) => s.projects);
  const fetchProjects = useProjectStore((s) => s.fetchProjects);
  const projectNameMap = new Map(projects.map((p) => [p.id, p.name]));

  // Dialogs
  const [selectMode, setSelectMode] = useState(false);
  const [bulkDeleteOpen, setBulkDeleteOpen] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);

  const [focusedIndex, setFocusedIndex] = useState(-1);
  const [focusedSearchIndex, setFocusedSearchIndex] = useState(-1);
  const scrollContainerRef = useRef<HTMLDivElement>(null);
  const now = useNow();

  const isSearching = search.trim().length > 0;
  const hasSelection = selectedIds.size > 0;

  const labelsByThread = useLabelsStore((s) => s.byThread);
  const fetchAllLabels = useLabelsStore((s) => s.fetchAllLabels);
  useEffect(() => { fetchAllLabels(); }, [fetchAllLabels]);
  const [activeLabel, setActiveLabel] = useState<string | null>(null);
  const [labelThreads, setLabelThreads] = useState<ThreadSummary[]>([]);
  const [labelLoading, setLabelLoading] = useState(false);

  const availableLabels = useMemo(() => {
    const seen = new Map<string, { label: string; color: string }>();
    Object.values(labelsByThread).flat().forEach((l) => {
      if (!seen.has(l.label)) seen.set(l.label, { label: l.label, color: l.color });
    });
    return Array.from(seen.values());
  }, [labelsByThread]);

  // When a label is selected, fetch all matching threads from the backend.
  // Client-side filtering only covers the currently-loaded page slice, so
  // threads on later pages would be silently excluded.
  useEffect(() => {
    if (!activeLabel) { setLabelThreads([]); return; }
    let cancelled = false;
    setLabelLoading(true);
    api.getRecents({ label: activeLabel, limit: 200 })
      .then((results) => {
        if (!cancelled) setLabelThreads(results as ThreadSummary[]);
      })
      .catch(() => { if (!cancelled) toast.error('Failed to filter by label'); })
      .finally(() => { if (!cancelled) setLabelLoading(false); });
    return () => { cancelled = true; };
  }, [activeLabel]);

  const displayedThreads = useMemo(() => {
    if (isSearching) return [];
    return activeLabel ? labelThreads : threads;
  }, [isSearching, threads, activeLabel, labelThreads]);

  const fetchThreads = useCallback(async (append = false) => {
    const hasCached = threads.length > 0;
    if (!hasCached || append) setLoading(true);
    try {
      const newOffset = append ? offset : 0;
      const results = await api.getRecents({ limit: PAGE_SIZE, offset: newOffset });
      const items = results as ThreadSummary[];
      if (append) {
        setThreads((prev) => {
          const existingIds = new Set(prev.map((t) => t.id));
          const deduped = items.filter((t) => !existingIds.has(t.id));
          return [...prev, ...deduped];
        });
      } else {
        setThreads(items);
        useThreadStore.setState({ threads: items, threadsLastFetched: Date.now() });
      }
      setOffset(newOffset + items.length);
      setHasMore(items.length === PAGE_SIZE);
    } catch {
      if (!hasCached) toast.error('Failed to load chats');
    }
    setLoading(false);
  }, [offset, threads.length]);

  const fetchSearch = useCallback(async (query: string, signal?: AbortSignal) => {
    setSearchLoading(true);
    try {
      const results = await api.getRecents({ search: query, limit: 50 }, signal);
      if (signal?.aborted) return;
      setSearchResults(results as SearchResult[]);
    } catch (err) {
      // Aborted requests are expected during fast typing; don't toast.
      if ((err as { name?: string })?.name === 'AbortError') return;
      toast.error('Search failed');
    } finally {
      if (!signal?.aborted) setSearchLoading(false);
    }
  }, []);

  // Initial load - seed from Zustand cache so the page renders instantly.
  // The didInit guard prevents StrictMode's dev double-mount from firing two
  // network requests; the deps array is already [] so this is the only path
  // that could re-trigger this effect.
  const didInitRef = useRef(false);
  useEffect(() => {
    if (didInitRef.current) return;
    didInitRef.current = true;
    const cached = useThreadStore.getState().threads;
    if (cached.length > 0) {
      setThreads(cached);
      setLoading(false);
    }
    Promise.all([fetchThreads(false), fetchProjects()]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Mirror deletions from the global thread store into local page state.
  const globalThreads = useThreadStore((s) => s.threads);
  const prevGlobalIdsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    const globalIds = new Set(globalThreads.map((t) => t.id));
    const deletedIds: string[] = [];
    prevGlobalIdsRef.current.forEach((id) => {
      if (!globalIds.has(id)) deletedIds.push(id);
    });
    if (deletedIds.length > 0) {
      const deletedSet = new Set(deletedIds);
      setThreads((local) => local.filter((t) => !deletedSet.has(t.id)));
      setSearchResults((local) => local.filter((r) => !deletedSet.has(r.thread_id)));
    }
    prevGlobalIdsRef.current = globalIds;
  }, [globalThreads]);

  // Debounced search - min 2 chars to avoid noisy single-char results.
  useEffect(() => {
    const trimmed = search.trim();
    if (!trimmed) {
      setSearchResults([]);
      setSearchLoading(false);
      setFocusedSearchIndex(-1);
      return;
    }
    if (trimmed.length < 2) {
      setSearchResults([]);
      setSearchLoading(false);
      return;
    }
    setSearchLoading(true);
    const controller = new AbortController();
    const timer = setTimeout(() => fetchSearch(trimmed, controller.signal), 300);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [search, fetchSearch]);

  // Reset focused search index when results change.
  useEffect(() => {
    setFocusedSearchIndex(-1);
  }, [searchResults]);

  // Clear selection when switching between search and browse mode.
  useEffect(() => {
    setSelectedIds(new Set());
    setSelectMode(false);
  }, [isSearching]);

  // Infinite scroll — listen on the actual scroll container, not the viewport.
  // IntersectionObserver with root:null breaks when the scroll container is
  // nested inside overflow-hidden (the authenticated layout's main element):
  // the sentinel always appears "in viewport", so the observer fires once and
  // never again. A scroll event on the real container is reliable.
  useEffect(() => {
    const container = scrollContainerRef.current;
    if (!container || !hasMore || loading || activeLabel) return;

    const onScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = container;
      if (scrollHeight - scrollTop - clientHeight < 300) {
        fetchThreads(true);
      }
    };

    container.addEventListener('scroll', onScroll, { passive: true });
    // Fire immediately in case the first page doesn't fill the container.
    onScroll();
    return () => container.removeEventListener('scroll', onScroll);
  }, [hasMore, loading, fetchThreads, activeLabel]);

  // Keyboard navigation.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      // Block arrow keys from inputs (cursor movement), but always let Enter through.
      if (e.target instanceof HTMLInputElement && e.key !== 'Enter') return;

      if (isSearching) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setFocusedSearchIndex((i) => Math.min(i + 1, searchResults.length - 1));
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          setFocusedSearchIndex((i) => Math.max(i - 1, 0));
        } else if (e.key === 'Enter' && focusedSearchIndex >= 0) {
          e.preventDefault();
          router.push(`/chat/${searchResults[focusedSearchIndex].thread_id}`);
        } else if (e.key === 'Escape') {
          setSearch('');
        }
        return;
      }

      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setFocusedIndex((i) => Math.min(i + 1, displayedThreads.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setFocusedIndex((i) => Math.max(i - 1, 0));
      } else if (e.key === 'Enter' && focusedIndex >= 0) {
        e.preventDefault();
        router.push(`/chat/${displayedThreads[focusedIndex].id}`);
      } else if (e.key === 'Escape') {
        setFocusedIndex(-1);
        setSelectMode(false);
        setSelectedIds(new Set());
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [focusedIndex, focusedSearchIndex, searchResults, displayedThreads, isSearching, router]);

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const selectAll = () => {
    if (isSearching) {
      const allIds = searchResults.map((r) => r.thread_id);
      setSelectedIds(selectedIds.size === allIds.length ? new Set() : new Set(allIds));
      return;
    }
    setSelectedIds(selectedIds.size === threads.length ? new Set() : new Set(threads.map((t) => t.id)));
  };

  const handleBulkDelete = async () => {
    const ids = [...selectedIds];
    try {
      await api.bulkDeleteThreads(ids);
      setThreads((prev) => prev.filter((t) => !selectedIds.has(t.id)));
      setSearchResults((prev) => prev.filter((r) => !selectedIds.has(r.thread_id)));
      setSelectedIds(new Set());
      useThreadStore.setState((state) => ({
        threads: state.threads.filter((t) => !ids.includes(t.id)),
      }));
      fetchProjects();
      toast.success(`Deleted ${ids.length} conversation${ids.length > 1 ? 's' : ''}`);
    } catch {
      toast.error('Delete failed');
    }
    setBulkDeleteOpen(false);
  };

  const formatTime = (dateStr: string) => {
    const rel = formatRelativeTime(dateStr, now);
    return rel === 'just now' ? 'Just now' : `Last message ${rel}`;
  };

  const exitSelectMode = () => {
    setSelectMode(false);
    setSelectedIds(new Set());
  };

  // If the user enters select mode and then deletes everything, the
  // remaining "Select all" UI strands them with no Cancel button to exit.
  // Reset whenever threads goes empty so a fresh re-entry is clean.
  useEffect(() => {
    if (threads.length === 0 && (selectMode || selectedIds.size > 0)) {
      setSelectMode(false);
      setSelectedIds(new Set());
    }
  }, [threads.length, selectMode, selectedIds.size]);

  return (
    <div ref={scrollContainerRef} className="h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-6 sm:py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">Chats</h1>
          </div>
          <Button
            onClick={() => router.push('/new')}
            className="gap-2"
          >
            <Plus className="w-4 h-4" />
            New chat
          </Button>
        </div>

        {/* Search */}
        <div className="relative mb-6">
          <div className="absolute left-3 top-3 h-4 w-4 text-muted-foreground pointer-events-none">
            {searchLoading
              ? <Loader2 className="h-4 w-4 animate-spin" />
              : <Search className="h-4 w-4" />
            }
          </div>
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search your chats..."
            className={`pl-10 h-11 transition-all ${search ? 'pr-9' : ''}`}
          />
          {search && (
            <button
              onClick={() => setSearch('')}
              className="absolute right-3 top-3 h-4 w-4 text-muted-foreground hover:text-foreground transition-colors"
              aria-label="Clear search"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        {/* Label filter chips */}
        {availableLabels.length > 0 && !isSearching && (
          <div className="flex flex-wrap gap-1.5 mb-4 max-h-20 overflow-y-auto">
            {availableLabels.map(({ label, color }) => {
              const c = LABEL_COLORS.find((x) => x.name === color) ?? LABEL_COLORS[0];
              const isActive = activeLabel === label;
              return (
                <button
                  key={label}
                  onClick={() => setActiveLabel(isActive ? null : label)}
                  className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-medium transition-colors ${
                    isActive ? `${c.bg} ${c.text} ring-1 ring-current` : 'bg-muted text-muted-foreground hover:bg-accent hover:text-foreground'
                  }`}
                >
                  <span className={`w-2 h-2 rounded-full ${c.dot}`} />
                  {label}
                </button>
              );
            })}
            {activeLabel && (
              <button onClick={() => setActiveLabel(null)} className="text-xs text-muted-foreground hover:text-foreground px-2">
                Clear
              </button>
            )}
          </div>
        )}

        {/* Selection bar */}
        {((isSearching && !searchLoading && searchResults.length > 0) || (!isSearching && threads.length > 0)) && (
          <div className="flex items-center justify-between mb-3 min-h-8">
            <div className="flex items-center gap-3">
              {selectMode ? (
                <>
                  <Checkbox
                    checked={
                      isSearching
                        ? selectedIds.size === searchResults.length && searchResults.length > 0
                        : selectedIds.size === threads.length && threads.length > 0
                    }
                    onCheckedChange={selectAll}
                    className="h-5 w-5"
                  />
                  <span className="text-sm text-foreground font-medium">
                    {hasSelection ? `${selectedIds.size} selected` : 'Select all'}
                  </span>
                  {hasSelection && (
                    <div className="flex items-center gap-1 ml-1">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-8 w-8 p-0"
                            onClick={() => setMoveOpen(true)}
                          >
                            <FolderInput className="w-4 h-4" />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent side="top">Move to project</TooltipContent>
                      </Tooltip>
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-8 w-8 p-0 text-destructive hover:text-destructive"
                            onClick={() => setBulkDeleteOpen(true)}
                          >
                            <Trash2 className="w-4 h-4" />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent side="top">Delete selected</TooltipContent>
                      </Tooltip>
                    </div>
                  )}
                </>
              ) : (
                <span className="text-sm text-muted-foreground">
                  Your chats
                </span>
              )}
            </div>

            <button
              onClick={() => selectMode ? exitSelectMode() : setSelectMode(true)}
              className={`flex items-center gap-1 text-sm transition-colors ${
                selectMode
                  ? 'text-muted-foreground hover:text-foreground'
                  : 'text-primary hover:underline'
              }`}
            >
              {selectMode ? <><X className="w-4 h-4" /> Cancel</> : 'Select'}
            </button>
          </div>
        )}

        {/* Thread / Search List */}
        <div className="space-y-[var(--density-list-gap)]">
          {isSearching ? (
            <div className="min-h-[16rem]">
              {/* Search result count */}
              {!searchLoading && searchResults.length > 0 && (
                <p className="text-xs text-muted-foreground mb-2 px-1">
                  {searchResults.length} result{searchResults.length !== 1 ? 's' : ''}
                </p>
              )}

              {/* Search loading skeleton */}
              {searchLoading && <SearchResultSkeleton />}

              {/* Empty state */}
              {!searchLoading && searchResults.length === 0 && search.trim().length >= 2 && (
                <div className="text-center py-16">
                  <Search className="w-8 h-8 text-muted-foreground/30 mx-auto mb-3" />
                  <p className="text-sm font-medium text-foreground mb-1">No results found</p>
                  <p className="text-xs text-muted-foreground">
                    Try different keywords or check your spelling
                  </p>
                </div>
              )}

              {/* Results */}
              {!searchLoading && searchResults.map((result, index) => (
                <div key={result.thread_id}>
                  {index > 0 && !selectedIds.has(result.thread_id) && !selectedIds.has(searchResults[index - 1]?.thread_id) && (
                    <div className="border-t border-border mx-4" />
                  )}
                  <div
                    onClick={() => {
                      if (selectMode || selectedIds.has(result.thread_id)) {
                        toggleSelect(result.thread_id);
                      } else {
                        router.push(`/chat/${result.thread_id}`);
                      }
                    }}
                    onMouseEnter={() => {
                      router.prefetch(`/chat/${result.thread_id}`);
                      setFocusedSearchIndex(index);
                    }}
                    className={`group flex items-center px-4 py-[var(--density-pad-y-loose)] cursor-pointer transition-all duration-100 rounded-lg animate-in fade-in slide-in-from-bottom-1 fill-mode-both ${
                      selectedIds.has(result.thread_id)
                        ? 'bg-primary/15 ring-1 ring-primary/40'
                        : focusedSearchIndex === index
                        ? 'bg-muted/70 ring-1 ring-border'
                        : 'hover:bg-muted/50'
                    }`}
                    style={{ animationDelay: `${Math.min(index, 6) * 35}ms` }}
                  >
                    <div
                      className={`mr-3 shrink-0 transition-opacity duration-100 ${
                        selectMode || selectedIds.has(result.thread_id)
                          ? 'opacity-100'
                          : 'opacity-0 group-hover:opacity-100'
                      }`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <Checkbox
                        checked={selectedIds.has(result.thread_id)}
                        onCheckedChange={() => {
                          if (!selectMode) setSelectMode(true);
                          toggleSelect(result.thread_id);
                        }}
                        className="h-5 w-5"
                        aria-label={`Select chat: ${result.title || 'Untitled'}`}
                      />
                    </div>
                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-foreground line-clamp-2 leading-snug">
                        {highlightQueryInText(result.title || 'Untitled', search, {
                          matchedTerms: result.matched_terms,
                        })}
                      </p>
                      {result.headline && (
                        <p className="text-xs text-muted-foreground mt-0.5 line-clamp-4 leading-relaxed">
                          {renderSearchSnippet(result.headline, search, {
                            matchedTerms: result.matched_terms,
                          })}
                        </p>
                      )}
                      <div className="flex items-center gap-1.5 mt-0.5">
                        {result.match_type === 'message' && (
                          <FileText className="w-3 h-3 text-muted-foreground/70 shrink-0" />
                        )}
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="text-xs text-muted-foreground/80 cursor-default">
                              {formatTime(result.updated_at)}
                            </span>
                          </TooltipTrigger>
                          <TooltipContent side="right">
                            {new Date(result.updated_at).toLocaleString([], { dateStyle: 'long', timeStyle: 'short' })}
                          </TooltipContent>
                        </Tooltip>
                        {result.project_id && projectNameMap.get(result.project_id) && (
                          <>
                            <span className="text-muted-foreground/60 text-xs">in</span>
                            <span className="text-xs text-muted-foreground/80">
                              {projectNameMap.get(result.project_id)}
                            </span>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          ) : threads.length === 0 && loading ? (
            null
          ) : threads.length === 0 ? (
            <div className="text-center py-16">
              <MessageSquare className="w-10 h-10 text-muted-foreground/30 mx-auto mb-3" />
              <h3 className="text-sm font-medium text-foreground mb-1">No conversations yet</h3>
              <p className="text-xs text-muted-foreground mb-4">
                Start a new conversation to get going.
              </p>
              <Button onClick={() => router.push('/new')} variant="outline" size="sm" className="gap-2">
                <Plus className="w-3.5 h-3.5" />
                New chat
              </Button>
            </div>
          ) : labelLoading ? (
            <ThreadListSkeleton />
          ) : displayedThreads.length === 0 && activeLabel ? (
            <div className="text-center py-10">
              <p className="text-sm text-muted-foreground">No chats with label &ldquo;{activeLabel}&rdquo;</p>
              <button onClick={() => setActiveLabel(null)} className="mt-2 text-xs text-primary hover:underline">Clear filter</button>
            </div>
          ) : (
            <>
              {displayedThreads.map((thread, index) => (
                <div key={thread.id}>
                  {index > 0 && !selectedIds.has(thread.id) && !selectedIds.has(displayedThreads[index - 1]?.id) && (
                    <div className="border-t border-border mx-4" />
                  )}
                  <div
                    onClick={() => selectMode ? toggleSelect(thread.id) : router.push(`/chat/${thread.id}`)}
                    onMouseEnter={() => { if (!selectMode) { router.prefetch(`/chat/${thread.id}`); setFocusedIndex(index); } }}
                    className={`group flex items-center px-4 py-[var(--density-pad-y-loose)] cursor-pointer transition-all duration-100 rounded-lg ${
                      selectedIds.has(thread.id)
                        ? 'bg-primary/15 ring-1 ring-primary/40'
                        : focusedIndex === index
                        ? 'bg-muted/40 ring-1 ring-border'
                        : 'hover:bg-muted/50'
                    }`}
                  >
                    <div
                      className={`mr-3 shrink-0 transition-opacity duration-100 ${
                        selectMode || selectedIds.has(thread.id)
                          ? 'opacity-100'
                          : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100'
                      }`}
                      onClick={(e) => e.stopPropagation()}
                    >
                      <Checkbox
                        checked={selectedIds.has(thread.id)}
                        onCheckedChange={() => {
                          if (!selectMode) setSelectMode(true);
                          toggleSelect(thread.id);
                        }}
                        className="h-5 w-5"
                        aria-label={`Select chat: ${thread.title || 'Untitled'}`}
                      />
                    </div>

                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-foreground line-clamp-2 leading-snug">
                        {thread.title || 'Untitled'}
                      </p>
                      <p className="text-xs text-muted-foreground mt-0.5 flex items-center gap-1" suppressHydrationWarning>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <span className="cursor-default">{formatTime(thread.updated_at)}</span>
                          </TooltipTrigger>
                          <TooltipContent side="right">
                            {new Date(thread.updated_at).toLocaleString([], { dateStyle: 'long', timeStyle: 'short' })}
                          </TooltipContent>
                        </Tooltip>
                        {thread.project_id && projectNameMap.get(thread.project_id) && (
                          <>
                            <span className="text-muted-foreground/60">in</span>
                            <span className="text-muted-foreground">{projectNameMap.get(thread.project_id)}</span>
                          </>
                        )}
                      </p>
                    </div>
                  </div>
                </div>
              ))}

              {!activeLabel && loading && threads.length > 0 && (
                <div className="flex justify-center py-4">
                  <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {/* Move to Project Dialog */}
      <MoveToProjectDialog
        open={moveOpen}
        onOpenChange={(open) => {
          setMoveOpen(open);
          if (!open) {
            fetchThreads(false);
            exitSelectMode();
          }
        }}
        threadIds={[...selectedIds]}
        isBulk={selectedIds.size > 1}
      />

      {/* Bulk Delete Confirmation */}
      <AlertDialog open={bulkDeleteOpen} onOpenChange={setBulkDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {selectedIds.size} conversations?</AlertDialogTitle>
            <AlertDialogDescription>
              This action cannot be undone. All selected conversations will be permanently deleted.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="flex gap-3 justify-end">
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleBulkDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete {selectedIds.size} conversations
            </AlertDialogAction>
          </div>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}

const THREAD_WIDTHS = ['w-3/5', 'w-4/5', 'w-3/4', 'w-full', 'w-3/5', 'w-4/5', 'w-3/4', 'w-11/12'] as const;

function ThreadListSkeleton() {
  return (
    <div className="space-y-[var(--density-list-gap)]">
      {THREAD_WIDTHS.map((w, i) => (
        <div key={i} className="rounded-lg px-4 py-[var(--density-pad-y-loose)] min-h-[3.5rem]">
          <Skeleton className={`h-4 mb-2 ${w}`} />
          <Skeleton className="h-3 w-1/4" />
        </div>
      ))}
    </div>
  );
}

function SearchResultSkeleton() {
  return (
    <div className="space-y-[var(--density-list-gap)]">
      {[0.6, 0.8, 0.7, 0.5].map((opacity, i) => (
        <div key={i} className="rounded-lg px-4 py-[var(--density-pad-y-loose)] min-h-[5rem]" style={{ opacity }}>
          <Skeleton className="h-4 w-3/5 mb-1.5" />
          <Skeleton className="h-3 w-full mb-1" />
          <Skeleton className="h-3 w-4/5 mb-1.5" />
          <Skeleton className="h-3 w-1/4" />
        </div>
      ))}
    </div>
  );
}
