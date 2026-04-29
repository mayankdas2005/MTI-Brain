'use client';

import { useRouter } from 'next/navigation';
import { useSearchStore } from '@/lib/store/search';
import {
  MessageSquare,
  FileText,
  FolderOpen,
  Plus,
} from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Command,
  CommandInput,
  CommandList,
  CommandEmpty,
  CommandGroup,
  CommandItem,
  CommandSeparator,
} from '@/components/ui/command';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { useThreadStore } from '@/lib/store/threads';

export function SearchModal() {
  const router = useRouter();
  const open = useSearchStore((s) => s.open);
  const query = useSearchStore((s) => s.query);
  const chatResults = useSearchStore((s) => s.chatResults);
  const projectResults = useSearchStore((s) => s.projectResults);
  const recentChats = useSearchStore((s) => s.recentChats);
  const loading = useSearchStore((s) => s.loading);
  const search = useSearchStore((s) => s.search);
  const closeModal = useSearchStore((s) => s.closeModal);

  const createThread = useThreadStore((s) => s.createThread);
  const setPendingQuestion = useThreadStore((s) => s.setPendingQuestion);

  const hasQuery = query.trim().length > 0;
  const hasResults = chatResults.length > 0 || projectResults.length > 0;

  const navigate = (path: string) => {
    closeModal();
    router.push(path);
  };

  const handleNewChat = async () => {
    closeModal();
    router.push('/new');
  };

  return (
    <Dialog open={open} onOpenChange={(v) => !v && closeModal()}>
      <DialogTitle className="sr-only">Search</DialogTitle>
      <DialogDescription className="sr-only">Search chats and projects</DialogDescription>
      <DialogContent className="overflow-hidden p-0 sm:max-w-xl rounded-2xl border-border/80" showCloseButton={false}>
        <Command shouldFilter={false} className="[&_[cmdk-group-heading]]:text-muted-foreground [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[13px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:tracking-tight">
          <CommandInput
            placeholder="Search chats and projects..."
            value={query}
            onValueChange={search}
          />
          <CommandList className="max-h-[400px]">
            {/* Empty state — no results for query */}
            {!loading && hasQuery && !hasResults && (
              <CommandEmpty>No results found for &quot;{query}&quot;</CommandEmpty>
            )}

            {/* New Chat action — always available */}
            <CommandGroup>
              <CommandItem onSelect={handleNewChat} className="gap-2">
                <Plus className="w-4 h-4" />
                <span>New Chat</span>
                <span className="ml-auto text-xs text-muted-foreground">
                  Start a new conversation
                </span>
              </CommandItem>
            </CommandGroup>

            <CommandSeparator />

            {/* Loading skeletons — in the same position where recents appear */}
            {loading && !hasQuery && recentChats.length === 0 && (
              <CommandGroup heading="Recent Chats">
                <div className="px-2 py-1 space-y-2">
                  <div className="flex items-center gap-2 px-2 py-1.5"><Skeleton className="h-4 w-4 rounded shrink-0" /><Skeleton className="h-4 w-3/5" /></div>
                  <div className="flex items-center gap-2 px-2 py-1.5"><Skeleton className="h-4 w-4 rounded shrink-0" /><Skeleton className="h-4 w-3/4" /></div>
                  <div className="flex items-center gap-2 px-2 py-1.5"><Skeleton className="h-4 w-4 rounded shrink-0" /><Skeleton className="h-4 w-2/3" /></div>
                  <div className="flex items-center gap-2 px-2 py-1.5"><Skeleton className="h-4 w-4 rounded shrink-0" /><Skeleton className="h-4 w-4/5" /></div>
                </div>
              </CommandGroup>
            )}

            {/* When no query: show recent chats */}
            {!hasQuery && recentChats.length > 0 && (
              <CommandGroup heading="Recent Chats">
                {recentChats.map((chat) => (
                  <CommandItem
                    key={chat.id}
                    value={chat.id}
                    onSelect={() => navigate(`/chat/${chat.id}`)}
                    className="gap-2"
                  >
                    <MessageSquare className="w-4 h-4 shrink-0" />
                    <span className="truncate">{chat.title || 'Untitled'}</span>
                    <span className="ml-auto text-xs text-muted-foreground/60 shrink-0">
                      {formatRelativeTime(chat.updated_at)}
                    </span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}

            {/* When searching: show chat results */}
            {hasQuery && chatResults.length > 0 && (
              <CommandGroup heading="Conversations">
                {chatResults.map((result) => {
                  const hasContentMatch = !!result.headline;
                  const Icon = hasContentMatch ? FileText : MessageSquare;
                  return (
                    <CommandItem
                      key={result.thread_id}
                      value={result.thread_id}
                      onSelect={() => navigate(`/chat/${result.thread_id}`)}
                      className="gap-2"
                    >
                      <Icon className="w-4 h-4 shrink-0 mt-0.5 self-start" />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm truncate">{result.title || 'Untitled'}</p>
                        {hasContentMatch && (
                          <p
                            className="text-xs text-muted-foreground line-clamp-2 mt-0.5 leading-relaxed [&_b]:text-foreground [&_b]:font-semibold"
                            dangerouslySetInnerHTML={{ __html: result.headline ?? '' }}
                          />
                        )}
                      </div>
                      <span className="text-xs text-muted-foreground/60 shrink-0 self-start">
                        {formatRelativeTime(result.updated_at)}
                      </span>
                    </CommandItem>
                  );
                })}
              </CommandGroup>
            )}

            {/* When searching: show project results */}
            {hasQuery && projectResults.length > 0 && (
              <>
                <CommandSeparator />
                <CommandGroup heading="Projects">
                  {projectResults.map((project) => (
                    <CommandItem
                      key={project.id}
                      value={project.id}
                      onSelect={() => navigate(`/projects/${project.id}`)}
                      className="gap-2"
                    >
                      <FolderOpen className="w-4 h-4 shrink-0" />
                      <div className="flex-1 min-w-0">
                        <p className="text-sm truncate">{project.name}</p>
                        {project.description && (
                          <p className="text-xs text-muted-foreground truncate mt-0.5">
                            {project.description}
                          </p>
                        )}
                      </div>
                      <span className="text-xs text-muted-foreground/60 shrink-0">
                        {project.thread_count} chats
                      </span>
                    </CommandItem>
                  ))}
                </CommandGroup>
              </>
            )}
          </CommandList>

          {/* Footer */}
          <div className="flex items-center justify-between border-t border-border px-3 py-2 text-xs text-muted-foreground/60">
            <div className="flex items-center gap-3">
              <span className="flex items-center gap-1">
                <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] font-mono">↑↓</kbd>
                navigate
              </span>
              <span className="flex items-center gap-1">
                <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] font-mono">↵</kbd>
                open
              </span>
              <span className="flex items-center gap-1">
                <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] font-mono">esc</kbd>
                close
              </span>
            </div>
          </div>
        </Command>
      </DialogContent>
    </Dialog>
  );
}

function formatRelativeTime(dateStr: string): string {
  const date = new Date(dateStr);
  const now = new Date();
  const diffMs = now.getTime() - date.getTime();
  const diffMins = Math.floor(diffMs / 60000);
  const diffHours = Math.floor(diffMs / 3600000);
  const diffDays = Math.floor(diffMs / 86400000);

  if (diffMins < 1) return 'just now';
  if (diffMins < 60) return `${diffMins}m`;
  if (diffHours < 24) return `${diffHours}h`;
  if (diffDays < 7) return `${diffDays}d`;
  return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
