'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
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

  const isSearching = search.trim().length > 0;
  const hasSelection = selectedIds.size > 0;

  const fetchThreads = useCallback(async (append = false) => {
    // Stale-while-revalidate: only show the loading skeleton when we have
    // nothing to display. If a cached list is already on screen, refresh
    // silently in the background.
    const hasCached = threads.length > 0;
    if (!hasCached || append) setLoading(true);
    try {
      const newOffset = append ? offset : 0;
      const results = await api.getRecents({ limit: PAGE_SIZE, offset: newOffset });
      const items = results as ThreadSummary[];
      if (append) {
        setThreads((prev) => [...prev, ...items]);
      } else {
        setThreads(items);
        // Also seed the global store so other pages can reuse the cache.
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
    setLoading(true);
    try {
      const results = await api.getRecents({ search: query, limit: 50 });
      setSearchResults(results as SearchResult[]);
    } catch {
      toast.error('Search failed');
    }
    setLoading(false);
  }, []);

  // Initial load — seed from Zustand cache so the page renders instantly,
  // then fetch fresh data in the background.
  useEffect(() => {
    const cached = useThreadStore.getState().threads;
    if (cached.length > 0) {
      setThreads(cached);
      setLoading(false);
    }
    // Run in parallel — these calls are independent.
    Promise.all([fetchThreads(false), fetchProjects()]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Mirror deletions from the global thread store into the local page state.
  // Sidebar / context-menu / chat-view deletes happen through the global
  // store; without this, /chats keeps showing threads that have already
  // been removed elsewhere. We compare previous-vs-current IDs so that
  // paginated items beyond the global store's window aren't dropped.
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

  // Debounced search
  useEffect(() => {
    if (!search.trim()) {
      setSearchResults([]);
      return;
    }
    const timer = setTimeout(() => fetchSearch(search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search, fetchSearch]);

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
      // Sync sidebar Zustand store so recents + starred update immediately
      useThreadStore.setState((state) => ({
        threads: state.threads.filter((t) => !ids.includes(t.id)),
      }));
      // Refresh project thread counts
      fetchProjects();
      toast.success(`Deleted ${ids.length} conversation${ids.length > 1 ? 's' : ''}`);
    } catch {
      toast.error('Delete failed');
    }
    setBulkDeleteOpen(false);
  };

  const formatTime = (dateStr: string) => {
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `Last message ${diffMins}m ago`;
    if (diffHours < 24) return `Last message ${diffHours}h ago`;
    if (diffDays < 30) return `Last message ${diffDays}d ago`;
    return `Last message ${date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' })}`;
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
          <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search your chats..."
            className="pl-10 h-11"
          />
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
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-8 w-8 p-0"
                        title="Move to project"
                        onClick={() => setMoveOpen(true)}
                      >
                        <FolderInput className="w-4 h-4" />
                      </Button>
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-8 w-8 p-0 text-destructive hover:text-destructive"
                        title="Delete selected"
                        onClick={() => setBulkDeleteOpen(true)}
                      >
                        <Trash2 className="w-4 h-4" />
                      </Button>
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

        {/* Thread List */}
        <div className="space-y-1">
          {threads.length === 0 && searchResults.length === 0 && loading ? (
            <ThreadListSkeleton />
          ) : isSearching ? (
            searchResults.length === 0 && !loading ? (
              <div className="text-center py-16">
                <p className="text-sm text-muted-foreground">No results found</p>
              </div>
            ) : (
              searchResults.map((result) => (
                <button
                  key={result.thread_id}
                  onClick={() => router.push(`/chat/${result.thread_id}`)}
                  onMouseEnter={() => router.prefetch(`/chat/${result.thread_id}`)}
                  className="w-full text-left rounded-lg px-4 py-3 hover:bg-muted/50 transition-colors group"
                >
                  <p className="text-sm font-medium text-foreground truncate">
                    {result.title || 'Untitled'}
                  </p>
                  {result.headline && (
                    <p
                      className="text-xs text-muted-foreground mt-0.5 line-clamp-1"
                      dangerouslySetInnerHTML={{ __html: result.headline }}
                    />
                  )}
                  <p className="text-xs text-muted-foreground/60 mt-0.5">
                    {formatTime(result.updated_at)}
                  </p>
                </button>
              ))
            )
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
                    onMouseEnter={() => !selectMode && router.prefetch(`/chat/${thread.id}`)}
                    className={`flex items-center px-4 py-3.5 cursor-pointer transition-colors ${
                      selectedIds.has(thread.id)
                        ? 'bg-primary/8'
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
                        {formatTime(thread.updated_at)}
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

              {/* Show more */}
              {hasMore && !loading && (
                <div className="pt-2">
                  <button
                    onClick={() => fetchThreads(true)}
                    className="w-full text-sm text-muted-foreground hover:text-foreground py-3 rounded-lg hover:bg-muted/50 transition-colors"
                  >
                    Show more
                  </button>
                </div>
              )}
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
