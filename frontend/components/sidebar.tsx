'use client';

import { useState, useEffect, useRef } from 'react';
import { useNow } from '@/lib/hooks/use-now';
import { formatRelativeTime } from '@/lib/utils/relative-time';
import Image from 'next/image';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { ScrollArea } from '@/components/ui/scroll-area';
import { Checkbox } from '@/components/ui/checkbox';
import { useThreadStore } from '@/lib/store/threads';
import { useProjectStore } from '@/lib/store/projects';
import { useUIStore } from '@/lib/store/ui';
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
} from 'lucide-react';
import { logout, getStoredUser, getStoredToken, userFromToken, setStoredUser } from '@/lib/auth';
import { useTheme } from 'next-themes';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { useRouter } from 'next/navigation';
import { ProjectContextMenu } from './project-context-menu';
import { BulkActionBar } from './bulk-action-bar';
import { CreateProjectDialog } from './create-project-dialog';
import { RenameDialog } from './rename-dialog';
import { MoveToProjectDialog } from './move-to-project-dialog';
import { SettingsModal } from './settings-modal';
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

function SidebarThreadsSkeleton() {
  return (
    <div className="space-y-1">
      {SIDEBAR_WIDTHS.map((w, i) => (
        <div key={i} className="rounded-lg px-2.5 py-1.5">
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
  const starThread = useThreadStore((s) => s.starThread);
  const deleteThread = useThreadStore((s) => s.deleteThread);
  const now = useNow();

  return (
    <>
      <div
        className={`group relative rounded-lg px-2.5 py-1.5 cursor-pointer transition-colors ${
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

          <button
            onClick={() => router.push(`/chat/${thread.id}`)}
            onMouseEnter={() => router.prefetch(`/chat/${thread.id}`)}
            className="flex-1 text-left min-w-0"
          >
            <p className="text-sm font-medium truncate">{title}</p>
            <p className="text-xs opacity-55 mt-0.5" suppressHydrationWarning>
              {formatRelativeTime(thread.updated_at, now)}
            </p>
          </button>

          {/* Three-dot menu */}
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                className="shrink-0 mt-0.5 p-1 rounded-md opacity-0 group-hover:opacity-100 transition-opacity text-sidebar-foreground/50 hover:text-sidebar-foreground hover:bg-sidebar-accent"
                onClick={(e) => e.stopPropagation()}
              >
                <MoreHorizontal className="w-3.5 h-3.5" />
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

export function Sidebar() {
  const router = useRouter();
  const { theme, setTheme } = useTheme();
  const toggleSidebar = useUIStore((s) => s.toggleSidebar);
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
  const [createProjectOpen, setCreateProjectOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [showAllStarred, setShowAllStarred] = useState(false);

  // Note: initial fetch of threads & projects is handled once in the
  // authenticated layout so toggling the sidebar open/closed does not
  // re-trigger API calls on every mount.

  const setCurrentThread = useThreadStore((s) => s.setCurrentThread);

  const handleNewChat = () => {
    setCurrentThread(null);
    router.push('/new');
  };

  const handleLogout = () => {
    logout();
  };

  // Easter egg: click Q logo 7 times
  const logoClickCount = useRef(0);
  const logoClickTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const handleLogoClick = () => {
    logoClickCount.current++;
    if (logoClickTimer.current) clearTimeout(logoClickTimer.current);
    logoClickTimer.current = setTimeout(() => { logoClickCount.current = 0; }, 2000);
    if (logoClickCount.current >= 7) {
      logoClickCount.current = 0;
      if (!localStorage.getItem('quest-logo-achievement')) {
        localStorage.setItem('quest-logo-achievement', '1');
        toast.success('Achievement unlocked: Logo Clicker 🏆');
      } else {
        toast.info('You already unlocked this one 😉');
      }
    }
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
        className="rounded-lg px-2.5 py-2 cursor-pointer transition-colors hover:bg-sidebar-accent text-sidebar-foreground"
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
            <p
              className="text-xs opacity-60 mt-0.5 line-clamp-2"
              dangerouslySetInnerHTML={{ __html: result.headline }}
            />
          )}
          <p className="text-xs opacity-40 mt-0.5" suppressHydrationWarning>{formatTime(result.updated_at)}</p>
        </button>
      </div>
    );
  };

  const renderThread = (thread: ThreadSummary) => {
    const isSelected = selectedThreadIds.has(thread.id);
    const isCurrent = currentThreadId === thread.id;
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
    <div className="flex flex-col h-full bg-sidebar border-sidebar-border w-[280px]">
      {/* Header */}
      <div
        className="px-3 h-12 flex items-center justify-between border-b border-sidebar-border"
        style={{ backgroundColor: 'var(--header)' }}
      >
        <Image
          src="/Milestone%20Logo%2016x9%20Transparent%20MAIN%20LOGO%20(white%20text).png"
          alt="Milestone"
          width={0}
          height={0}
          sizes="168px"
          style={{ width: '168px', height: '55px', objectFit: 'contain', objectPosition: 'left' }}
          loading="eager"
          priority
          className="cursor-pointer select-none"
          onClick={handleLogoClick}
        />
        <button
          type="button"
          className="flex h-8 w-8 items-center justify-center text-[var(--header-foreground)]"
          onClick={toggleSidebar}
          aria-label="Close sidebar"
        >
          <PanelLeft className="w-[18px] h-[18px]" />
        </button>
      </div>

      {/* New Chat */}
      <div className="px-3 pt-3 pb-2">
        <Button
          onClick={handleNewChat}
          className="w-full h-10 rounded-xl justify-center gap-2 font-semibold active:scale-[0.98] transition-transform"
          variant="outline"
        >
          <Plus className="w-[18px] h-[18px]" />
          New Chat
        </Button>
      </div>

      {/* Search - opens global search modal */}
      <div className="px-3 pb-2">
        <button
          onClick={() => {
            const { useSearchStore } = require('@/lib/store/search');
            useSearchStore.getState().openModal();
          }}
          className="w-full flex items-center gap-2 px-2.5 h-9 rounded-xl border border-sidebar-border bg-sidebar text-sm text-sidebar-foreground/55 hover:text-sidebar-foreground hover:bg-sidebar-accent transition-colors"
        >
          <Search className="w-4 h-4 shrink-0" />
          <span>Search...</span>
        </button>
      </div>

      <ScrollArea className="flex-1 min-h-0">
        {/* Projects navigation */}
        <div className="px-3 pt-1 pb-0.5">
          <div className="flex items-center">
            <button
              onClick={() => router.push('/projects')}
              onMouseEnter={() => router.prefetch('/projects')}
              className="flex-1 flex items-center gap-2 px-2 py-1.5 rounded-lg text-left transition-colors hover:bg-sidebar-accent text-sidebar-foreground"
            >
              <FolderOpen className="w-[18px] h-[18px] text-sidebar-foreground/50 shrink-0" />
              <span className="text-sm font-medium">Projects</span>
            </button>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 w-6 p-0 text-sidebar-foreground/60 hover:text-sidebar-foreground shrink-0"
                  onClick={() => setCreateProjectOpen(true)}
                >
                  <Plus className="w-3.5 h-3.5" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="right">New project</TooltipContent>
            </Tooltip>
          </div>
        </div>

        {/* Chats navigation */}
        <div className="px-3 pb-1">
          <button
            onClick={() => router.push('/chats')}
            onMouseEnter={() => router.prefetch('/chats')}
            className="w-full flex items-center gap-2 px-2 py-1.5 rounded-lg text-left transition-colors hover:bg-sidebar-accent text-sidebar-foreground"
          >
            <MessageSquare className="w-[18px] h-[18px] text-sidebar-foreground/50 shrink-0" />
            <span className="text-sm font-medium">Chats</span>
          </button>
        </div>

        {/* Starred Section - starred projects + starred threads, capped at 5 */}
        {(() => {
          const STARRED_LIMIT = 5;
          const starredProjects = projects.filter((p) => p.starred);
          const starredThreads = threads.filter((t) => t.starred);
          if (starredProjects.length === 0 && starredThreads.length === 0) return null;
          const totalStarred = starredProjects.length + starredThreads.length;
          const starredProjectsVisible = showAllStarred
            ? starredProjects
            : starredProjects.slice(0, STARRED_LIMIT);
          const starredThreadsVisible = showAllStarred
            ? starredThreads
            : starredThreads.slice(0, Math.max(0, STARRED_LIMIT - starredProjects.length));
          const starredOverflow = totalStarred > STARRED_LIMIT;
          return (
            <>
              <div className="px-3 pb-1">
                <p className="text-[13px] font-semibold tracking-tight text-sidebar-foreground/55 py-1">
                  Starred
                </p>
              </div>
              <div className="px-2 pb-1 space-y-0.5">
                {starredProjectsVisible.map((project) => (
                  <ProjectContextMenu
                    key={project.id}
                    projectId={project.id}
                    projectName={project.name}
                    projectDescription={project.description || ''}
                    starred={project.starred}
                  >
                    <button
                      onClick={() => router.push(`/projects/${project.id}`)}
                      onMouseEnter={() => router.prefetch(`/projects/${project.id}`)}
                      className="w-full flex items-center gap-2 px-2.5 py-1.5 rounded-lg text-left transition-colors hover:bg-sidebar-accent"
                    >
                      <FolderOpen className="w-3.5 h-3.5 text-sidebar-foreground/50 shrink-0" />
                      <span className="text-sm text-sidebar-foreground truncate flex-1">
                        {project.name}
                      </span>
                    </button>
                  </ProjectContextMenu>
                ))}
                {starredThreadsVisible.map(renderThread)}
                {starredOverflow && (
                  <button
                    onClick={() => setShowAllStarred((v) => !v)}
                    className="w-full text-left text-xs text-sidebar-foreground/50 hover:text-sidebar-foreground px-2 py-1 rounded-lg hover:bg-sidebar-accent transition-colors"
                  >
                    {showAllStarred ? 'Show less' : `Show ${totalStarred - STARRED_LIMIT} more`}
                  </button>
                )}
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
        <div className="px-2 pb-3 pt-0 space-y-0.5">
          {threads.length === 0 && threadsLoading ? (
            <SidebarThreadsSkeleton />
          ) : threads.length === 0 ? (
            <div className="text-center py-8">
              <p className="text-sm text-sidebar-foreground/60" suppressHydrationWarning>
                {['No conversations yet', 'Your treasury data awaits', 'Ask your first question'][Math.floor(Date.now() / 60000) % 3]}
              </p>
            </div>
          ) : (
            <>
              {threads.filter((t) => !t.starred).slice(0, 8).map(renderThread)}
              {(threads.filter((t) => !t.starred).length > 8 || hasMore) && (
                <button
                  onClick={() => router.push('/chats')}
                  onMouseEnter={() => router.prefetch('/chats')}
                  className="w-full text-left text-xs text-sidebar-foreground/50 hover:text-sidebar-foreground px-2 py-1.5 rounded-lg hover:bg-sidebar-accent transition-colors flex items-center gap-1"
                >
                  <span>See all chats</span>
                  <ChevronRight className="w-3 h-3" />
                </button>
              )}
            </>
          )}
        </div>
      </ScrollArea>

      {/* Bulk action bar */}
      <BulkActionBar />

      {/* Footer - User menu */}
      <div className="px-3 py-2 border-t border-sidebar-border">
        {!user ? (
          <div className="flex items-center gap-2.5 px-2 py-1.5">
            <Skeleton className="h-8 w-8 rounded-full shrink-0" />
            <div className="flex-1 min-w-0">
              <Skeleton className="h-4 w-24 mb-1.5" />
              <Skeleton className="h-3 w-36" />
            </div>
          </div>
        ) : (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className="w-full flex items-center gap-2.5 rounded-lg px-2 py-1.5 hover:bg-sidebar-accent transition-colors">
              <Avatar className="h-8 w-8">
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
          <DropdownMenuContent side="top" align="start" className="w-56 mb-1">
            {/* Theme options */}
            <div className="px-2 py-1.5">
              <p className="text-xs font-medium text-muted-foreground mb-1.5">Theme</p>
              <div className="flex gap-1">
                <button
                  onClick={() => setTheme('light')}
                  className={`flex-1 flex items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs transition-colors ${
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
                  className={`flex-1 flex items-center justify-center gap-1.5 rounded-md px-2 py-1.5 text-xs transition-colors ${
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
            <DropdownMenuItem onClick={() => setSettingsOpen(true)} className="gap-2">
              <Settings className="w-4 h-4" />
              Settings
            </DropdownMenuItem>
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

      {/* Create Project Dialog */}
      <CreateProjectDialog
        open={createProjectOpen}
        onOpenChange={setCreateProjectOpen}
      />

      {/* Settings Modal */}
      <SettingsModal open={settingsOpen} onOpenChange={setSettingsOpen} />
    </div>
  );
}
