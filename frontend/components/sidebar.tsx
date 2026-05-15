'use client';

import { useState, useEffect, useRef, startTransition } from 'react';
import { useNow } from '@/lib/hooks/use-now';
import { formatRelativeTime, groupByRecencyBucket } from '@/lib/utils/relative-time';
import Image from 'next/image';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Checkbox } from '@/components/ui/checkbox';
import { useThreadStore } from '@/lib/store/threads';
import { useProjectStore } from '@/lib/store/projects';
import { useUIStore } from '@/lib/store/ui';
import { useSearchStore } from '@/lib/store/search';
import {
  Plus,
  Star,
  PanelLeft,
  LogOut,
  Search,
  Loader2,
  FolderOpen,
  ChevronDown,
  ChevronRight,
  MoreHorizontal,
  Pencil,
  FolderInput,
  Trash2,
  Moon,
  Sun,
  Monitor,
  Settings,
  MessageSquare,
  Download,
  Sparkles,
  Bell,
  Newspaper,
  Tag,
} from 'lucide-react';
import { WhatsNewDialog, useChangelogUnread } from './whats-new-dialog';
import { useLabelsStore, LABEL_COLORS } from '@/lib/store/labels';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import { logout, getStoredUser, getStoredToken, userFromToken, setStoredUser } from '@/lib/auth';
import { renderHighlightedSnippet } from '@/lib/utils/highlight';
import { cn } from '@/lib/utils';
import { useIsTablet } from '@/hooks/use-mobile';
import { track, Events } from '@/lib/analytics';
import { useInstallStore } from '@/lib/store/install';
import {
  getPermission,
  requestPermission,
  notificationsSupported,
} from '@/lib/utils/notifications';
import { useTheme } from 'next-themes';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { useRouter, usePathname } from 'next/navigation';
import { ProjectContextMenu } from './project-context-menu';
import { BulkActionBar } from './bulk-action-bar';
import { CreateProjectDialog } from './create-project-dialog';
import { RenameDialog } from './rename-dialog';
import { MoveToProjectDialog } from './move-to-project-dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { Skeleton } from '@/components/ui/skeleton';
import { toast } from '@/lib/toast';
import type { ThreadSummary, SearchResult } from '@/lib/types/api';
import type { AppRouterInstance } from 'next/dist/shared/lib/app-router-context.shared-runtime';

const SIDEBAR_WIDTHS = ['w-3/4', 'w-1/2', 'w-4/5', 'w-2/3', 'w-3/5', 'w-3/4'] as const;

// Sidebar Recents empty-state phrases. The visible string is picked by
// `Math.floor(Date.now() / 60000) % length`, so it rotates roughly every
// minute on render. Keep the tone professional and action-inviting -
// these are the first words a fresh user reads on the home surface, so
// no jokes and no nags. Matching the "premium analyst tool" voice.
const RECENTS_EMPTY_PHRASES = [
  'No conversations yet',
  'Your treasury data awaits',
  'Ask your first question',
  'Insights start with a question',
  'Ready when you are',
  'A clean slate awaits',
  'Your analyst is on standby',
  'What can we look at today?',
] as const;

function SidebarThreadsSkeleton() {
  return (
    <div className="space-y-[var(--density-list-gap)]">
      {SIDEBAR_WIDTHS.map((w, i) => (
        <div key={i} className="rounded-lg px-2.5 py-[var(--density-pad-y-tight)]">
          <Skeleton className={`h-4 mb-1.5 ${w}`} />
          <Skeleton className="h-3 w-1/3" />
        </div>
      ))}
    </div>
  );
}

function ThreadItem({
  thread,
  title,
  isSelected,
  isCurrent,
  hasSelection,
  toggleThreadSelection,
  router,
}: {
  thread: ThreadSummary;
  title: string;
  isSelected: boolean;
  isCurrent: boolean;
  hasSelection: boolean;
  toggleThreadSelection: (id: string) => void;
  router: AppRouterInstance;
}) {
  const [renameOpen, setRenameOpen] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [inlineRenaming, setInlineRenaming] = useState(false);
  const [inlineDraft, setInlineDraft] = useState(title);
  const inlineInputRef = useRef<HTMLInputElement>(null);
  const [labelPickerOpen, setLabelPickerOpen] = useState(false);
  const [newLabelName, setNewLabelName] = useState('');
  const [newLabelColor, setNewLabelColor] = useState('blue');
  const threadLabels = useLabelsStore((s) => s.byThread[thread.id] ?? []);
  const addLabel = useLabelsStore((s) => s.addLabel);
  const removeLabel = useLabelsStore((s) => s.removeLabel);
  const labelSubmittingRef = useRef(false);

  const handleAddLabel = () => {
    if (!newLabelName.trim() || labelSubmittingRef.current) return;
    labelSubmittingRef.current = true;
    void addLabel(thread.id, newLabelName.trim(), newLabelColor)
      .then(() => setLabelPickerOpen(false))
      .finally(() => { labelSubmittingRef.current = false; });
  };
  const starThread = useThreadStore((s) => s.starThread);
  const deleteThread = useThreadStore((s) => s.deleteThread);
  const renameThread = useThreadStore((s) => s.renameThread);
  const closeMobileSidebar = useUIStore((s) => s.setMobileSidebarOpen);
  const closeTabletOverlay = useUIStore((s) => s.setTabletSidebarOverlayOpen);
  const closeOnNav = () => {
    closeMobileSidebar(false);
    closeTabletOverlay(false);
  };
  const now = useNow();

  useEffect(() => {
    if (inlineRenaming) {
      setInlineDraft(title);
      // Focus + select on next tick so the input is mounted.
      requestAnimationFrame(() => {
        inlineInputRef.current?.focus();
        inlineInputRef.current?.select();
      });
    }
  }, [inlineRenaming, title]);

  const commitInlineRename = () => {
    const next = inlineDraft.trim();
    setInlineRenaming(false);
    if (!next || next === title) return;
    track(Events.ThreadRenamed, { method: 'inline' });
    void renameThread(thread.id, next);
  };

  const cancelInlineRename = () => {
    setInlineRenaming(false);
    setInlineDraft(title);
  };

  return (
    <>
      <div
        className={`group relative rounded-lg px-2.5 py-[var(--density-pad-y-tight)] cursor-pointer transition-colors ${
          isCurrent
            ? 'bg-sidebar-accent text-sidebar-accent-foreground'
            : 'hover:bg-sidebar-accent text-sidebar-foreground'
        }`}
      >
        <div className="flex items-start gap-1.5">
          {hasSelection && (
            <div className="mt-0.5 shrink-0">
              <Checkbox
                checked={isSelected}
                onCheckedChange={() => toggleThreadSelection(thread.id)}
                onClick={(e) => e.stopPropagation()}
                className="h-4 w-4"
              />
            </div>
          )}

          {inlineRenaming ? (
            <div className="flex-1 min-w-0">
              <input
                ref={inlineInputRef}
                value={inlineDraft}
                onChange={(e) => setInlineDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    commitInlineRename();
                  } else if (e.key === 'Escape') {
                    e.preventDefault();
                    cancelInlineRename();
                  }
                }}
                onBlur={commitInlineRename}
                onClick={(e) => e.stopPropagation()}
                aria-label="Rename conversation"
                maxLength={200}
                className="w-full text-sm font-medium bg-background border border-ring rounded px-1 py-0.5 outline-none"
              />
              <p className="text-xs opacity-55 mt-0.5" suppressHydrationWarning>
                {formatRelativeTime(thread.updated_at, now)}
              </p>
            </div>
          ) : (
            <button
              data-thread-row
              data-thread-id={thread.id}
              onClick={() => {
                closeOnNav();
                router.push(`/chat/${thread.id}`);
              }}
              onDoubleClick={(e) => {
                e.preventDefault();
                e.stopPropagation();
                setInlineRenaming(true);
              }}
              onKeyDown={(e) => {
                // Roving keyboard nav across all thread rows (works across
                // grouped Today / Yesterday / older sections, since the
                // selector is global within the sidebar).
                if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                  const rows = Array.from(
                    document.querySelectorAll<HTMLButtonElement>('[data-thread-row]'),
                  );
                  const idx = rows.indexOf(e.currentTarget);
                  if (idx === -1) return;
                  e.preventDefault();
                  const next =
                    e.key === 'ArrowDown'
                      ? rows[Math.min(idx + 1, rows.length - 1)]
                      : rows[Math.max(idx - 1, 0)];
                  next?.focus();
                }
              }}
              onMouseEnter={() => router.prefetch(`/chat/${thread.id}`)}
              className="flex-1 text-left min-w-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
            >
              <div className="flex items-center gap-1.5 min-w-0">
                <p className="text-sm font-medium truncate">{title}</p>
                {threadLabels.slice(0, 3).map((lbl) => {
                  const color = LABEL_COLORS.find((c) => c.name === lbl.color) ?? LABEL_COLORS[0];
                  return (
                    <Tooltip key={lbl.id}>
                      <TooltipTrigger asChild>
                        <span className={`shrink-0 w-2 h-2 rounded-full ${color.dot}`} />
                      </TooltipTrigger>
                      <TooltipContent side="bottom">{lbl.label}</TooltipContent>
                    </Tooltip>
                  );
                })}
              </div>
              <p className="text-xs opacity-55 mt-0.5" suppressHydrationWarning>
                {formatRelativeTime(thread.updated_at, now)}
              </p>
            </button>
          )}

          {/* Hover-revealed quick rename */}
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                aria-label="Rename"
                className="shrink-0 mt-0.5 p-1 rounded-md opacity-0 group-hover:opacity-100 focus-visible:opacity-100 transition-opacity text-sidebar-foreground/50 hover:text-sidebar-foreground hover:bg-sidebar-accent"
                onClick={(e) => {
                  e.stopPropagation();
                  setInlineRenaming(true);
                }}
              >
                <Pencil className="w-3.5 h-3.5" aria-hidden />
              </button>
            </TooltipTrigger>
            <TooltipContent side="bottom">Rename</TooltipContent>
          </Tooltip>

          {/* Three-dot menu */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                aria-label="More actions"
                className="shrink-0 mt-0.5 p-1 rounded-md opacity-0 group-hover:opacity-100 focus-visible:opacity-100 transition-opacity text-sidebar-foreground/50 hover:text-sidebar-foreground hover:bg-sidebar-accent"
                onClick={(e) => e.stopPropagation()}
              >
                <MoreHorizontal className="w-3.5 h-3.5" aria-hidden />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent side="right" align="start" className="w-44">
              <DropdownMenuItem onClick={() => starThread(thread.id)} className="gap-2">
                <Star className={`w-3.5 h-3.5 ${thread.starred ? 'fill-[var(--color-star)] text-[var(--color-star)]' : ''}`} />
                {thread.starred ? 'Unstar' : 'Star'}
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setRenameOpen(true)} className="gap-2">
                <Pencil className="w-3.5 h-3.5" />
                Rename
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => setMoveOpen(true)} className="gap-2">
                <FolderInput className="w-3.5 h-3.5" />
                Add to project
              </DropdownMenuItem>
              <DropdownMenuItem onClick={() => { setNewLabelName(''); setNewLabelColor('blue'); setLabelPickerOpen(true); }} className="gap-2">
                <Tag className="w-3.5 h-3.5" />
                Add label
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onClick={() => setDeleteOpen(true)}
                className="gap-2 text-destructive focus:text-destructive"
              >
                <Trash2 className="w-3.5 h-3.5" />
                Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>

          <Dialog open={labelPickerOpen} onOpenChange={(o) => { if (!o) setLabelPickerOpen(false); }}>
            <DialogContent className="sm:max-w-sm p-6 gap-0">
              <DialogTitle className="text-base font-semibold mb-1">Add label</DialogTitle>
              <DialogDescription className="sr-only">Add a colored label to this conversation</DialogDescription>
              <div className="flex gap-2 flex-wrap mb-4">
                {LABEL_COLORS.map((c) => (
                  <button
                    key={c.name}
                    onClick={() => setNewLabelColor(c.name)}
                    className={`w-6 h-6 rounded-full ${c.dot} ring-2 transition-all ${newLabelColor === c.name ? 'ring-foreground ring-offset-2' : 'ring-transparent'}`}
                    aria-label={c.name}
                  />
                ))}
              </div>
              <input
                autoFocus
                value={newLabelName}
                onChange={(e) => setNewLabelName(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); handleAddLabel(); } }}
                placeholder="Label name..."
                className="w-full rounded-xl border border-border bg-muted/50 px-4 py-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
              />
              {threadLabels.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mt-3 max-h-24 overflow-y-auto">
                  {threadLabels.map((lbl) => {
                    const color = LABEL_COLORS.find((c) => c.name === lbl.color) ?? LABEL_COLORS[0];
                    return (
                      <span key={lbl.id} className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs ${color.bg} ${color.text}`}>
                        {lbl.label}
                        <button onClick={() => void removeLabel(lbl.id, thread.id)} className="hover:opacity-70 ml-0.5">×</button>
                      </span>
                    );
                  })}
                </div>
              )}
              <DialogFooter className="mt-4">
                <button onClick={() => setLabelPickerOpen(false)} className="text-sm text-muted-foreground hover:text-foreground px-3 py-2">Cancel</button>
                <button
                  onClick={handleAddLabel}
                  className="rounded-xl bg-primary text-primary-foreground text-sm px-4 py-2 hover:bg-primary/90"
                >
                  Add
                </button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </div>

      {/* Dialogs */}
      <RenameDialog
        open={renameOpen}
        onOpenChange={setRenameOpen}
        threadId={thread.id}
        currentTitle={title}
      />
      <MoveToProjectDialog
        open={moveOpen}
        onOpenChange={setMoveOpen}
        threadIds={[thread.id]}
      />
      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete conversation?</AlertDialogTitle>
            <AlertDialogDescription>
              This action cannot be undone. The conversation will be permanently deleted.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="flex gap-3 justify-end">
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={async () => {
                const wasCurrent = await deleteThread(thread.id);
                setDeleteOpen(false);
                if (wasCurrent) router.push('/new');
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete
            </AlertDialogAction>
          </div>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
}

export function Sidebar({ forceExpanded = false, forceCollapsed = false }: { forceExpanded?: boolean; forceCollapsed?: boolean } = {}) {
  const router = useRouter();
  const pathname = usePathname() ?? '';
  const onChats = pathname === '/chats';
  const onProjects = pathname.startsWith('/projects');
  const { theme, setTheme } = useTheme();
  const isTablet = useIsTablet();
  const sidebarOpen = useUIStore((s) => s.sidebarOpen);
  const toggleSidebar = useUIStore((s) => s.toggleSidebar);
  const closeMobileSidebar = useUIStore((s) => s.setMobileSidebarOpen);
  const setTabletOverlayOpen = useUIStore((s) => s.setTabletSidebarOverlayOpen);
  const closeTabletOverlay = setTabletOverlayOpen;

  // isOpen: forceExpanded wins over store, forceCollapsed wins over store
  const isOpen = forceExpanded ? true : forceCollapsed ? false : sidebarOpen;

  const closeOnNav = () => {
    closeMobileSidebar(false);
    closeTabletOverlay(false);
  };

  const handleToggle = () => {
    if (!isOpen) {
      if (isTablet) setTabletOverlayOpen(true);
      else toggleSidebar();
    } else {
      if (forceExpanded) {
        if (isTablet) setTabletOverlayOpen(false);
        else closeMobileSidebar(false);
      } else {
        toggleSidebar();
      }
    }
  };
  const [whatsNewOpen, setWhatsNewOpen] = useState(false);
  const changelogUnread = useChangelogUnread();
  const fetchAllLabels = useLabelsStore((s) => s.fetchAllLabels);
  useEffect(() => { fetchAllLabels(); }, [fetchAllLabels]);

  const [user, setUser] = useState<ReturnType<typeof getStoredUser>>(null);

  // Defer localStorage read to client-side only to avoid hydration mismatch.
  // If the stored user is missing but a valid token exists, recover from the JWT.
  useEffect(() => {
    let stored = getStoredUser();
    if (!stored) {
      const token = getStoredToken();
      if (token) {
        stored = userFromToken(token);
        if (stored) setStoredUser(stored);
      }
    }
    setUser(stored);
  }, []);

  // Thread store
  const threads = useThreadStore((s) => s.threads);
  const searchResults = useThreadStore((s) => s.searchResults);
  const isSearching = useThreadStore((s) => s.isSearching);
  const threadsLoading = useThreadStore((s) => s.threadsLoading);
  const searchQuery = useThreadStore((s) => s.searchQuery);
  const hasMore = useThreadStore((s) => s.hasMore);
  const currentThreadId = useThreadStore((s) => s.currentThreadId);
  const selectedThreadIds = useThreadStore((s) => s.selectedThreadIds);
  const fetchRecents = useThreadStore((s) => s.fetchRecents);
  const setSearchQuery = useThreadStore((s) => s.setSearchQuery);
  const toggleThreadSelection = useThreadStore((s) => s.toggleThreadSelection);
  // Project store
  const projects = useProjectStore((s) => s.projects);
  const projectsLoading = useProjectStore((s) => s.loading);

  const [projectsOpen, setProjectsOpen] = useState(true);
  // Settings + create-project visibility lives in the UI store so Cmd+K
  // commands (and any other surface) can open them without prop-drilling.
  const createProjectOpen = useUIStore((s) => s.createProjectOpen);
  const setCreateProjectOpen = useUIStore((s) => s.setCreateProjectOpen);
  const canInstall = useInstallStore((s) => s.canInstall);
  const installed = useInstallStore((s) => s.installed);
  const promptInstall = useInstallStore((s) => s.promptInstall);
  const startTourReplay = useUIStore((s) => s.startTourReplay);
  // Re-renders the user menu after the user grants/denies notification
  // permission so the "Enable notifications" entry hides itself.
  const [notifyPermission, setNotifyPermission] = useState<string>(
    notificationsSupported() ? 'default' : 'unsupported',
  );
  useEffect(() => {
    setNotifyPermission(getPermission());
  }, []);

  // Note: initial fetch of threads & projects is handled once in the
  // authenticated layout so toggling the sidebar open/closed does not
  // re-trigger API calls on every mount.

  const setCurrentThread = useThreadStore((s) => s.setCurrentThread);

  const handleNewChat = () => {
    setCurrentThread(null);
    closeOnNav();
    router.push('/new');
  };

  const handleLogout = () => {
    void logout();
  };


  const handleLoadMore = () => {
    fetchRecents({ append: true });
  };

  const sidebarNow = useNow();
  const formatTime = (dateStr: string) => formatRelativeTime(dateStr, sidebarNow);

  const hasSelection = selectedThreadIds.size > 0;

  const renderSearchResult = (result: SearchResult) => {
    const title = result.title || 'Untitled';
    return (
      <div
        key={result.thread_id}
        className="rounded-lg px-2.5 py-[var(--density-pad-y)] cursor-pointer transition-colors hover:bg-sidebar-accent text-sidebar-foreground"
      >
        <button
          onClick={() => router.push(`/chat/${result.thread_id}`)}
          onMouseEnter={() => router.prefetch(`/chat/${result.thread_id}`)}
          className="w-full text-left min-w-0"
        >
          <p className="text-sm font-medium truncate">
            {title}
          </p>
          {result.headline && (
            <p className="text-xs opacity-60 mt-0.5 line-clamp-2">
              {renderHighlightedSnippet(result.headline, 'bg-transparent text-current font-semibold')}
            </p>
          )}
          <p className="text-xs opacity-40 mt-0.5" suppressHydrationWarning>{formatTime(result.updated_at)}</p>
        </button>
      </div>
    );
  };

  const renderThread = (thread: ThreadSummary) => {
    const isSelected = selectedThreadIds.has(thread.id);
    // Drive the highlighted-row state from the URL, not the store. The
    // store's currentThreadId stays set to the last-loaded thread (useful
    // for caching) even after navigating to /chats or /settings - without
    // this gate the sidebar would keep showing a thread as "current"
    // long after the user left it.
    const isCurrent = pathname === `/chat/${thread.id}`;
    const title = thread.title || 'Untitled';

    return (
      <ThreadItem
        key={thread.id}
        thread={thread}
        title={title}
        isSelected={isSelected}
        isCurrent={isCurrent}
        hasSelection={hasSelection}
        toggleThreadSelection={toggleThreadSelection}
        router={router}
      />
    );
  };

  return (
    <div data-no-contrast className={cn("flex flex-col h-full bg-sidebar overflow-hidden shrink-0", !forceExpanded && "border-r border-sidebar-border transition-[width] duration-200", forceExpanded ? "w-full" : isOpen ? "w-[280px]" : "w-12")} data-onboarding="sidebar">
      {/* Header */}
      <div
        className="h-12 flex items-center shrink-0"
        style={{
          backgroundColor: 'var(--header)',
          borderBottom: '1px solid var(--header-control-border)',
          boxShadow: '0 1px 0 0 var(--header-control-border), 0 2px 12px -2px rgba(0,0,0,0.18)',
        }}
      >
        {isOpen ? (
          /* Expanded: logo left, close button right */
          <>
            <div className="flex-1 min-w-0 pl-3">
              <Image
                src="/milestone-logo-white.png"
                alt="Milestone"
                width={98}
                height={55}
                style={{ width: 'auto', height: '55px', objectFit: 'contain', objectPosition: 'left' }}
                loading="eager"
                priority
                className="select-none"
              />
            </div>
            <button
              type="button"
              className="tap-44 flex h-8 w-8 items-center justify-center text-[var(--header-foreground)] shrink-0 mr-1"
              onClick={handleToggle}
              aria-label="Close sidebar"
            >
              <PanelLeft className="w-[18px] h-[18px]" />
            </button>
          </>
        ) : (
          /* Collapsed: the entire header is one centered click target */
          <button
            type="button"
            className="tap-44 flex h-full w-full items-center justify-center text-[var(--header-foreground)]"
            onClick={handleToggle}
            aria-label="Open sidebar"
          >
            <Image
              src="/milestone-icon.png"
              alt="Milestone"
              width={0}
              height={0}
              sizes="26px"
              style={{ width: '26px', height: 'auto', objectFit: 'contain' }}
              loading="eager"
              priority
              className="select-none"
            />
          </button>
        )}
      </div>

      {/* New Chat */}
      <div className={cn("shrink-0 flex", isOpen ? "px-3 pt-3 pb-2" : "pt-3 pb-2 justify-center")}>
        {isOpen ? (
          <Button
            onClick={handleNewChat}
            data-onboarding="new-chat"
            className="w-full h-10 rounded-xl justify-center gap-2 font-semibold active:scale-[0.98] transition-transform"
            variant="outline"
          >
            <Plus className="w-[18px] h-[18px]" />
            New Chat
          </Button>
        ) : (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                onClick={handleNewChat}
                data-onboarding="new-chat"
                variant="ghost"
                size="icon"
                className="h-9 w-9 tap-44 text-sidebar-foreground/60 hover:text-sidebar-foreground hover:bg-sidebar-accent"
              >
                <Plus className="w-[18px] h-[18px]" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right" sideOffset={6}>New chat</TooltipContent>
          </Tooltip>
        )}
      </div>

      {/* Search - opens global search modal */}
      <div className={cn("shrink-0 flex", isOpen ? "px-3 pb-2" : "pb-2 justify-center")}>
        {isOpen ? (
          <button
            onClick={() => useSearchStore.getState().openModal()}
            aria-label="Open search"
            className="w-full flex items-center gap-2 px-2.5 h-9 rounded-xl border border-sidebar-border bg-sidebar text-sm text-sidebar-foreground/55 hover:text-sidebar-foreground hover:bg-sidebar-accent transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar"
          >
            <Search className="w-4 h-4 shrink-0" />
            <span>Search...</span>
          </button>
        ) : (
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                onClick={() => useSearchStore.getState().openModal()}
                variant="ghost"
                size="icon"
                className="h-9 w-9 tap-44 text-sidebar-foreground/55 hover:text-sidebar-foreground hover:bg-sidebar-accent"
                aria-label="Search"
              >
                <Search className="w-4 h-4" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right" sideOffset={6}>Search</TooltipContent>
          </Tooltip>
        )}
      </div>

      {/* Projects navigation — always visible */}
      {isOpen ? (
        <div className="px-3 pt-1 pb-0.5 shrink-0">
          <div className="flex items-center">
            <button
              onClick={() => { closeOnNav(); router.push('/projects'); }}
              onMouseEnter={() => router.prefetch('/projects')}
              aria-current={onProjects ? 'page' : undefined}
              className={cn("flex-1 flex items-center gap-2 px-2 py-[var(--density-pad-y-tight)] rounded-lg text-left transition-colors hover:bg-sidebar-accent text-sidebar-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar", onProjects && "bg-sidebar-accent")}
            >
              <span className="flex items-center h-5 shrink-0">
                <FolderOpen className="w-[18px] h-[18px] text-sidebar-foreground/50" />
              </span>
              <span className="text-sm font-medium">Projects</span>
            </button>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="sm" className="h-6 w-6 p-0 text-sidebar-foreground/60 hover:text-sidebar-foreground shrink-0" onClick={() => setCreateProjectOpen(true)}>
                  <Plus className="w-3.5 h-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="right">New project</TooltipContent>
            </Tooltip>
          </div>
        </div>
      ) : (
        <div className="pt-1 pb-0.5 shrink-0 flex justify-center">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                onClick={() => { closeOnNav(); router.push('/projects'); }}
                variant="ghost"
                size="icon"
                aria-current={onProjects ? 'page' : undefined}
                className={cn("h-9 w-9 tap-44 text-sidebar-foreground/60 hover:text-sidebar-foreground hover:bg-sidebar-accent", onProjects && "bg-sidebar-accent")}
              >
                <FolderOpen className="w-[18px] h-[18px]" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right" sideOffset={6}>Projects</TooltipContent>
          </Tooltip>
        </div>
      )}

      {/* Chats navigation — always visible */}
      {isOpen ? (
        <div className="px-3 pb-1 shrink-0">
          <button
            onClick={() => { closeOnNav(); router.push('/chats'); }}
            onMouseEnter={() => router.prefetch('/chats')}
            aria-current={onChats ? 'page' : undefined}
            className={cn("w-full flex items-center gap-2 px-2 py-[var(--density-pad-y-tight)] rounded-lg text-left transition-colors hover:bg-sidebar-accent text-sidebar-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-sidebar", onChats && "bg-sidebar-accent")}
          >
            <span className="flex items-center h-5 shrink-0">
              <MessageSquare className="w-[18px] h-[18px] text-sidebar-foreground/50" />
            </span>
            <span className="text-sm font-medium">Chats</span>
          </button>
        </div>
      ) : (
        <div className="pb-1 shrink-0 flex justify-center">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                onClick={() => { closeOnNav(); router.push('/chats'); }}
                variant="ghost"
                size="icon"
                aria-current={onChats ? 'page' : undefined}
                className={cn("h-9 w-9 tap-44 text-sidebar-foreground/60 hover:text-sidebar-foreground hover:bg-sidebar-accent", onChats && "bg-sidebar-accent")}
              >
                <MessageSquare className="w-[18px] h-[18px]" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="right" sideOffset={6}>Chats</TooltipContent>
          </Tooltip>
        </div>
      )}

      <ScrollArea className="flex-1 min-h-0">
        {isOpen && (<>
        {/* Starred Section - starred projects + starred threads, capped at 5 */}
        {(() => {
          const STARRED_LIMIT = 5;
          const starredProjects = projects.filter((p) => p.starred);
          const starredThreads = threads.filter((t) => t.starred);
          if (starredProjects.length === 0 && starredThreads.length === 0) return null;
          const totalStarred = starredProjects.length + starredThreads.length;
          // Sidebar always shows up to STARRED_LIMIT; overflow links out to
          // /starred (the full saved/important surface) instead of expanding
          // inline. Keeps the sidebar scannable and gives the user a real
          // browse view when they have many.
          const starredProjectsVisible = starredProjects.slice(0, STARRED_LIMIT);
          const starredThreadsVisible = starredThreads.slice(
            0,
            Math.max(0, STARRED_LIMIT - starredProjects.length),
          );
          const starredOverflow = totalStarred > STARRED_LIMIT;
          return (
            <>
              <div className="px-3 pb-1">
                <p className="text-[13px] font-semibold tracking-tight text-sidebar-foreground/55 py-1">
                  Starred
                </p>
              </div>
              <div className="px-2 pb-1 space-y-[var(--density-list-gap)]">
                {starredProjectsVisible.map((project) => (
                  <ProjectContextMenu
                    key={project.id}
                    projectId={project.id}
                    projectName={project.name}
                    projectDescription={project.description || ''}
                    starred={project.starred}
                  >
                    <button
                      onClick={() => { closeOnNav(); router.push(`/projects/${project.id}`); }}
                      onMouseEnter={() => router.prefetch(`/projects/${project.id}`)}
                      className="w-full flex items-center gap-2 px-2.5 py-[var(--density-pad-y-tight)] rounded-lg text-left transition-colors hover:bg-sidebar-accent"
                    >
                      <FolderOpen className="w-3.5 h-3.5 text-sidebar-foreground/50 shrink-0" />
                      <span className="text-sm text-sidebar-foreground truncate flex-1">
                        {project.name}
                      </span>
                    </button>
                  </ProjectContextMenu>
                ))}
                {starredThreadsVisible.map(renderThread)}
                {/* Always show "See all starred" so users have a clear path
                    to the full /starred page, not only when the sidebar
                    truncates. Matches the Recents footer pattern below. */}
                <button
                  onClick={() => { closeOnNav(); router.push('/starred'); }}
                  onMouseEnter={() => router.prefetch('/starred')}
                  className="w-full text-left text-xs text-sidebar-foreground/50 hover:text-sidebar-foreground px-2 py-[var(--density-pad-y-tight)] rounded-lg hover:bg-sidebar-accent transition-colors flex items-center gap-1"
                >
                  <span>{starredOverflow ? `See all ${totalStarred} starred` : 'See all starred'}</span>
                  <ChevronRight className="w-3 h-3" />
                </button>
              </div>
              <div className="px-3 py-1">
                <div className="border-t border-sidebar-border" />
              </div>
            </>
          );
        })()}

        {/* Recent Chats Section */}
        <div className="px-3 pb-1">
          <p className="text-[13px] font-semibold tracking-tight text-sidebar-foreground/55 py-1">
            Recents
          </p>
        </div>
        <div className="px-2 pb-3 pt-0 space-y-[var(--density-list-gap)]">
          {threads.length === 0 && threadsLoading ? (
            <SidebarThreadsSkeleton />
          ) : threads.length === 0 ? (
            <div className="text-center py-8">
              <p className="text-sm text-sidebar-foreground/60" suppressHydrationWarning>
                {RECENTS_EMPTY_PHRASES[Math.floor(Date.now() / 60000) % RECENTS_EMPTY_PHRASES.length]}
              </p>
            </div>
          ) : (
            <>
              {(() => {
                const recents = threads.filter((t) => !t.starred).slice(0, 8);
                const groups = groupByRecencyBucket(
                  recents,
                  (t) => t.updated_at,
                  sidebarNow,
                );
                return groups.map(({ bucket, label, items }) => (
                  <div key={bucket} className="space-y-[var(--density-list-gap)]">
                    <p className="text-[10px] uppercase tracking-widest font-medium text-sidebar-foreground/40 px-2 pt-2 pb-1">
                      {label}
                    </p>
                    {items.map(renderThread)}
                  </div>
                ));
              })()}
              {(threads.filter((t) => !t.starred).length > 8 || hasMore) && (
                <button
                  onClick={() => { closeOnNav(); router.push('/chats'); }}
                  onMouseEnter={() => router.prefetch('/chats')}
                  className="w-full text-left text-xs text-sidebar-foreground/50 hover:text-sidebar-foreground px-2 py-[var(--density-pad-y-tight)] rounded-lg hover:bg-sidebar-accent transition-colors flex items-center gap-1"
                >
                  <span>See all chats</span>
                  <ChevronRight className="w-3 h-3" />
                </button>
              )}
            </>
          )}
        </div>
        </>)}
      </ScrollArea>

      {/* Bulk action bar */}
      {isOpen && <BulkActionBar />}

      {/* Footer - User menu */}
      <div className={cn("border-t border-sidebar-border shrink-0", isOpen ? "px-3" : "flex justify-center")} style={{ paddingTop: 'var(--density-pad-y)', paddingBottom: 'var(--density-pad-y)' }}>
        {!user ? (
          isOpen ? (
            <div className="flex items-center gap-2.5 px-2 py-[var(--density-pad-y-tight)]">
              <Skeleton className="h-8 w-8 rounded-full shrink-0" />
              <div className="flex-1 min-w-0">
                <Skeleton className="h-4 w-24 mb-1.5" />
                <Skeleton className="h-3 w-36" />
              </div>
            </div>
          ) : (
            <div className="w-9 flex items-center justify-center py-[var(--density-pad-y-tight)]">
              <Skeleton className="h-8 w-8 rounded-full shrink-0" />
            </div>
          )
        ) : (
        <DropdownMenu>
          {isOpen ? (
            <DropdownMenuTrigger asChild>
              <button data-onboarding="user-menu" className="w-full flex items-center gap-2.5 rounded-lg px-2 py-[var(--density-pad-y-tight)] min-h-[48px] md:min-h-0 hover:bg-sidebar-accent transition-colors">
                <Avatar className="h-8 w-8 shrink-0">
                  <AvatarFallback className="bg-primary/15 text-primary text-sm font-semibold">
                    {(user.name || user.email)?.charAt(0).toUpperCase()}
                  </AvatarFallback>
                </Avatar>
                <div className="flex-1 text-left min-w-0">
                  <p className="text-sm font-medium text-sidebar-foreground truncate">
                    {user.name || user.email}
                  </p>
                  {user.email && (
                    <p className="text-[11px] text-sidebar-foreground/50 truncate">
                      {user.email}
                    </p>
                  )}
                </div>
              </button>
            </DropdownMenuTrigger>
          ) : (
            <Tooltip>
              <TooltipTrigger asChild>
                <DropdownMenuTrigger asChild>
                  <button data-onboarding="user-menu" className="w-9 flex items-center justify-center rounded-lg py-[var(--density-pad-y-tight)] hover:bg-sidebar-accent transition-colors">
                    <Avatar className="h-8 w-8 shrink-0">
                      <AvatarFallback className="bg-primary/15 text-primary text-xs font-semibold">
                        {(user.name || user.email)?.charAt(0).toUpperCase()}
                      </AvatarFallback>
                    </Avatar>
                  </button>
                </DropdownMenuTrigger>
              </TooltipTrigger>
              <TooltipContent side="right" sideOffset={6}>Account</TooltipContent>
            </Tooltip>
          )}
          <DropdownMenuContent side="top" align="start" className="w-56 mb-1">
            {/* Theme options */}
            <div className="px-2 py-[var(--density-pad-y-tight)]">
              <p className="text-xs font-medium text-muted-foreground mb-1.5">Theme</p>
              <div className="flex gap-1">
                <button
                  onClick={() => setTheme('light')}
                  aria-pressed={theme === 'light'}
                  aria-label="Light theme"
                  className={`flex-1 flex items-center justify-center gap-1.5 rounded-md px-2 py-[var(--density-pad-y-tight)] text-xs transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${
                    theme === 'light'
                      ? 'bg-accent text-accent-foreground font-medium'
                      : 'hover:bg-accent text-muted-foreground hover:text-foreground'
                  }`}
                >
                  <Sun className="w-3.5 h-3.5" />
                  Light
                </button>
                <button
                  onClick={() => setTheme('dark')}
                  aria-pressed={theme === 'dark'}
                  aria-label="Dark theme"
                  className={`flex-1 flex items-center justify-center gap-1.5 rounded-md px-2 py-[var(--density-pad-y-tight)] text-xs transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 ${
                    theme === 'dark'
                      ? 'bg-accent text-accent-foreground font-medium'
                      : 'hover:bg-accent text-muted-foreground hover:text-foreground'
                  }`}
                >
                  <Moon className="w-3.5 h-3.5" />
                  Dark
                </button>
              </div>
            </div>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onClick={() => { closeOnNav(); startTransition(() => router.push('/settings')); }}
              className="gap-2"
            >
              <Settings className="w-4 h-4" />
              Settings
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => { setWhatsNewOpen(true); }} className="gap-2">
              <span className="relative flex items-center gap-2">
                <Newspaper className="w-4 h-4" />
                What&apos;s New
                {changelogUnread && (
                  <span className="absolute -top-1 -right-2 w-2 h-2 rounded-full bg-primary" />
                )}
              </span>
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => startTourReplay()} className="gap-2">
              <Sparkles className="w-4 h-4" />
              Replay product tour
            </DropdownMenuItem>
            {notifyPermission === 'default' && (
              <DropdownMenuItem
                onClick={async () => {
                  const next = await requestPermission();
                  setNotifyPermission(next);
                  if (next === 'granted') {
                    toast.success('Notifications enabled', {
                      id: 'notifications-enabled',
                    });
                  } else if (next === 'denied') {
                    toast.info(
                      'Notifications blocked - you can re-enable from your browser\'s site settings.',
                      { id: 'notifications-denied' },
                    );
                  }
                }}
                className="gap-2"
              >
                <Bell className="w-4 h-4" />
                Enable notifications
              </DropdownMenuItem>
            )}
            {!installed && canInstall && (
              <DropdownMenuItem
                onClick={async () => {
                  await promptInstall();
                }}
                className="gap-2"
              >
                <Download className="w-4 h-4" />
                Install MTI Brain
              </DropdownMenuItem>
            )}
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onClick={handleLogout}
              className="gap-2"
            >
              <LogOut className="w-4 h-4" />
              Log out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
        )}
      </div>

      <WhatsNewDialog open={whatsNewOpen} onOpenChange={setWhatsNewOpen} />

      {/* Create Project Dialog */}
      <CreateProjectDialog
        open={createProjectOpen}
        onOpenChange={setCreateProjectOpen}
      />

    </div>
  );
}
