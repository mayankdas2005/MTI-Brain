'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
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
  const sentinelRef = useRef<HTMLDivElement>(null);
  const now = useNow();

  const isSearching = search.trim().length > 0;
  const hasSelection = selectedIds.size > 0;
  const displayedThreads = isSearching ? [] : threads;

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

  const fetchSearch = useCallback(async (query: string) => {
    setSearchLoading(true);
    try {
      const results = await api.getRecents({ search: query, limit: 50 });
      setSearchResults(results as SearchResult[]);
    } catch {
      toast.error('Search failed');
    }
    setSearchLoading(false);
  }, []);

  // Initial load - seed from Zustand cache so the page renders instantly.
  useEffect(() => {
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
    const timer = setTimeout(() => fetchSearch(trimmed), 150);
    return () => clearTimeout(timer);
  }, [search, fetchSearch]);

  // Reset focused search index when results change.
  useEffect(() => {
    setFocusedSearchIndex(-1);
  }, [searchResults]);

  // Infinite scroll sentinel.
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el) return;
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && hasMore && !loading) {
          fetchThreads(true);
        }
      },
      { threshold: 0.1 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, [hasMore, loading, fetchThreads]);

  // Keyboard navigation.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement) return;

      if (isSearching) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setFocusedSearchIndex((i) => Math.min(i + 1, searchResults.length - 1));
        } else if (e.key === 'ArrowUp') {
          e.preventDefault();
          setFocusedSearchIndex((i) => Math.max(i - 1, 0));
        } else if (e.key === 'Enter' && focusedSearchIndex >= 0) {
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
    if (selectedIds.size === threads.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(threads.map((t) => t.id)));
    }
  };

  const handleBulkDelete = async () => {
    const ids = [...selectedIds];
    try {
      await api.bulkDeleteThreads(ids);
      setThreads((prev) => prev.filter((t) => !selectedIds.has(t.id)));
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

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-8">
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

        {/* Selection bar */}
        {!isSearching && (
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-3">
              {selectMode ? (
                <>
                  <Checkbox
                    checked={selectedIds.size === threads.length && threads.length > 0}
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

            {threads.length > 0 && (
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
            )}
          </div>
        )}

        {/* Thread / Search List */}
        <div className="space-y-1">
          {isSearching ? (
            <>
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
                <button
                  key={result.thread_id}
                  onClick={() => router.push(`/chat/${result.thread_id}`)}
                  onMouseEnter={() => {
                    router.prefetch(`/chat/${result.thread_id}`);
                    setFocusedSearchIndex(index);
                  }}
                  className={`w-full text-left rounded-lg px-4 py-3 transition-colors group animate-in fade-in slide-in-from-bottom-1 duration-150 fill-mode-both ${
                    focusedSearchIndex === index
                      ? 'bg-muted/70 ring-1 ring-border'
                      : 'hover:bg-muted/50'
                  }`}
                  style={{ animationDelay: `${Math.min(index, 6) * 35}ms` }}
                >
                  <p className="text-sm font-medium text-foreground truncate">
                    {result.title || 'Untitled'}
                  </p>
                  {result.headline && (
                    <p
                      className="text-xs text-muted-foreground mt-0.5 line-clamp-2 [&_b]:text-foreground [&_b]:font-semibold"
                      dangerouslySetInnerHTML={{ __html: result.headline }}
                    />
                  )}
                  <div className="flex items-center gap-1.5 mt-0.5">
                    {result.match_type === 'message' && (
                      <FileText className="w-3 h-3 text-muted-foreground/50 shrink-0" />
                    )}
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="text-xs text-muted-foreground/60 cursor-default">
                          {formatTime(result.updated_at)}
                        </span>
                      </TooltipTrigger>
                      <TooltipContent side="right">
                        {new Date(result.updated_at).toLocaleString([], { dateStyle: 'long', timeStyle: 'short' })}
                      </TooltipContent>
                    </Tooltip>
                    {result.project_id && projectNameMap.get(result.project_id) && (
                      <>
                        <span className="text-muted-foreground/40 text-xs">in</span>
                        <span className="text-xs text-muted-foreground/60">
                          {projectNameMap.get(result.project_id)}
                        </span>
                      </>
                    )}
                  </div>
                </button>
              ))}
            </>
          ) : threads.length === 0 && loading ? (
            <ThreadListSkeleton />
          ) : threads.length === 0 && !loading ? (
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
          ) : (
            <>
              {threads.map((thread, index) => (
                <div key={thread.id}>
                  {index > 0 && !selectedIds.has(thread.id) && !selectedIds.has(threads[index - 1]?.id) && (
                    <div className="border-t border-border mx-4" />
                  )}
                  <div
                    onClick={() => selectMode ? toggleSelect(thread.id) : router.push(`/chat/${thread.id}`)}
                    onMouseEnter={() => { if (!selectMode) { router.prefetch(`/chat/${thread.id}`); setFocusedIndex(index); } }}
                    className={`flex items-center px-4 py-3.5 cursor-pointer transition-colors rounded-lg ${
                      selectedIds.has(thread.id)
                        ? 'bg-primary/8'
                        : focusedIndex === index
                        ? 'bg-muted/70 ring-1 ring-border'
                        : 'hover:bg-muted/50'
                    }`}
                  >
                    {selectMode && (
                      <div className="mr-3 shrink-0" onClick={(e) => e.stopPropagation()}>
                        <Checkbox
                          checked={selectedIds.has(thread.id)}
                          onCheckedChange={() => toggleSelect(thread.id)}
                          className="h-5 w-5"
                        />
                      </div>
                    )}

                    <div className="flex-1 min-w-0">
                      <p className="text-sm font-medium text-foreground truncate">
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
                            <span className="text-muted-foreground/40">in</span>
                            <span className="text-muted-foreground">{projectNameMap.get(thread.project_id)}</span>
                          </>
                        )}
                      </p>
                    </div>
                  </div>
                </div>
              ))}

              {/* Infinite scroll sentinel */}
              <div ref={sentinelRef} className="h-4" />
              {loading && threads.length > 0 && (
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
    <div className="space-y-1">
      {THREAD_WIDTHS.map((w, i) => (
        <div key={i} className="rounded-lg px-4 py-3">
          <Skeleton className={`h-4 mb-2 ${w}`} />
          <Skeleton className="h-3 w-1/4" />
        </div>
      ))}
    </div>
  );
}

function SearchResultSkeleton() {
  return (
    <div className="space-y-1">
      {[0.6, 0.8, 0.7, 0.5].map((opacity, i) => (
        <div key={i} className="rounded-lg px-4 py-3" style={{ opacity }}>
          <Skeleton className="h-4 w-3/5 mb-1.5" />
          <Skeleton className="h-3 w-full mb-1" />
          <Skeleton className="h-3 w-4/5 mb-1.5" />
          <Skeleton className="h-3 w-1/4" />
        </div>
      ))}
    </div>
  );
}
