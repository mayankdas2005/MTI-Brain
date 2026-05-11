'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { formatRelativeTime as sharedFormatRelativeTime } from '@/lib/utils/relative-time';
import { useNow } from '@/lib/hooks/use-now';
import { useSearchStore } from '@/lib/store/search';
import { usePreferencesStore, type ResponseTone } from '@/lib/store/preferences';
import { useThreadStore } from '@/lib/store/threads';
import {
  MessageSquare,
  FileText,
  FolderOpen,
  Loader2,
  Plus,
  FileDown,
  Settings,
  Star,
  BarChart3,
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
  ResponsiveDialog,
  ResponsiveDialogContent,
  ResponsiveDialogTitle,
  ResponsiveDialogDescription,
} from '@/components/ui/responsive-dialog';
import { highlightQueryInText, renderSearchSnippet } from '@/lib/utils/highlight';
import { toast } from '@/lib/toast';

type ModalMode = 'search' | 'actions';

interface QuickAction {
  id: string;
  label: string;
  description?: string;
  icon: React.ComponentType<{ className?: string }>;
  run: () => void;
  keywords: string;
  shortcut?: string;
}

function ActionRow({ action }: { action: QuickAction }) {
  const Icon = action.icon;
  return (
    <CommandItem value={action.id} onSelect={action.run} className="gap-3 py-2.5">
      <Icon className="w-4 h-4 shrink-0 text-muted-foreground" />
      <span className="flex-1 text-sm font-medium">{action.label}</span>
      {action.shortcut && (
        <kbd className="shrink-0 rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] font-mono text-muted-foreground/70">
          {action.shortcut}
        </kbd>
      )}
    </CommandItem>
  );
}

export function SearchModal() {
  const router = useRouter();
  const now = useNow();
  const open = useSearchStore((s) => s.open);
  const query = useSearchStore((s) => s.query);
  const chatResults = useSearchStore((s) => s.chatResults);
  const projectResults = useSearchStore((s) => s.projectResults);
  const recentChats = useSearchStore((s) => s.recentChats);
  const loading = useSearchStore((s) => s.loading);
  const search = useSearchStore((s) => s.search);
  const closeModal = useSearchStore((s) => s.closeModal);
  const setResponseTone = usePreferencesStore((s) => s.setResponseTone);
  const currentThreadId = useThreadStore((s) => s.currentThreadId);

  const [mode, setMode] = useState<ModalMode>('search');
  const [actionQuery, setActionQuery] = useState('');

  const hasQuery = query.trim().length > 0;
  const hasResults = chatResults.length > 0 || projectResults.length > 0;

  const navigate = (path: string) => {
    closeModal();
    router.push(path);
  };

  const TONE_ACTIONS: QuickAction[] = (
    ['analyst', 'manager', 'director', 'executive'] as ResponseTone[]
  ).map((tone) => ({
    id: `tone-${tone}`,
    label: `Switch to ${tone.charAt(0).toUpperCase() + tone.slice(1)} tone`,
    description: {
      analyst: 'Data-driven, detailed breakdowns',
      manager: 'Actionable insights with context',
      director: 'Strategic summaries with key metrics',
      executive: 'High-level, decision-ready answers',
    }[tone],
    icon: BarChart3,
    keywords: `tone ${tone} response style`,
    run: () => {
      setResponseTone(tone);
      toast.success(`Tone set to ${tone.charAt(0).toUpperCase() + tone.slice(1)}`);
      closeModal();
    },
  }));

  const ACTIONS: QuickAction[] = [
    {
      id: 'new-chat',
      label: 'New Chat',
      icon: Plus,
      keywords: 'new chat conversation ask question',
      shortcut: 'Ctrl+Shift+O',
      run: () => navigate('/new'),
    },
    {
      id: 'export-pdf',
      label: 'Export as PDF',
      icon: FileDown,
      keywords: 'export pdf download conversation',
      shortcut: 'Ctrl+Shift+E',
      run: () => {
        if (!currentThreadId) { toast.warning('Open a conversation first.'); return; }
        window.dispatchEvent(new CustomEvent('mti-brain:export-pdf'));
        closeModal();
      },
    },
    {
      id: 'open-starred',
      label: 'Starred chats',
      icon: Star,
      keywords: 'starred bookmarks favourites saved',
      run: () => navigate('/starred'),
    },
    {
      id: 'open-settings',
      label: 'Settings',
      icon: Settings,
      keywords: 'settings preferences theme voice',
      run: () => navigate('/settings'),
    },
  ];

  const ALL_ACTIONS = [...ACTIONS, ...TONE_ACTIONS];
  const filteredActions = actionQuery.trim()
    ? ALL_ACTIONS.filter((a) =>
        `${a.label} ${a.description ?? ''} ${a.keywords}`
          .toLowerCase()
          .includes(actionQuery.toLowerCase()),
      )
    : null; // null = show grouped (not filtered)

  const handleOpenChange = (v: boolean) => {
    if (!v) {
      closeModal();
      setMode('search');
      setActionQuery('');
    }
  };

  return (
    <ResponsiveDialog open={open} onOpenChange={handleOpenChange}>
      <ResponsiveDialogContent className="overflow-hidden p-0 w-full sm:max-w-lg rounded-2xl border-border/80" showCloseButton={false}>
        <ResponsiveDialogTitle className="sr-only">Search</ResponsiveDialogTitle>
        <ResponsiveDialogDescription className="sr-only">Search chats and projects, or run quick actions</ResponsiveDialogDescription>

        {/* Mode toggle tabs */}
        <div className="flex border-b border-border">
          {(['search', 'actions'] as ModalMode[]).map((m) => (
            <button
              key={m}
              onClick={() => setMode(m)}
              className={`flex-1 py-2 text-xs font-medium capitalize transition-colors ${
                mode === m
                  ? 'text-foreground border-b-2 border-primary'
                  : 'text-muted-foreground hover:text-foreground'
              }`}
            >
              {m === 'search' ? 'Search' : 'Actions'}
            </button>
          ))}
        </div>

        {/* ── Search mode ── */}
        {mode === 'search' && (
          <Command shouldFilter={false} className="[&_[cmdk-group-heading]]:text-muted-foreground [&_[cmdk-group-heading]]:px-2 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[13px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:tracking-tight">
            <div className="relative">
              <CommandInput
                placeholder="Search chats and projects..."
                value={query}
                onValueChange={search}
              />
              {loading && hasQuery && (
                <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none">
                  <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
                </div>
              )}
            </div>
            <CommandList className="h-72 overflow-hidden">
              {!loading && hasQuery && !hasResults && (
                <CommandEmpty>No results found for &quot;{query}&quot;</CommandEmpty>
              )}
              {loading && !hasQuery && recentChats.length === 0 && (
                <CommandGroup heading="Recent Chats">
                  <div className="px-2 py-1 space-y-2">
                    {[60, 75, 55, 80].map((w, i) => (
                      <div key={i} className="flex items-center gap-2 px-2 py-1.5">
                        <Skeleton className="h-4 w-4 rounded shrink-0" />
                        <Skeleton className="h-4" style={{ width: `${w}%` }} />
                      </div>
                    ))}
                  </div>
                </CommandGroup>
              )}
              {loading && hasQuery && (
                <CommandGroup heading="Conversations">
                  <div className="px-2 py-1 space-y-1">
                    {[0, 1, 2, 3].map((i) => (
                      <div key={i} className="flex items-center gap-2 px-2 py-1.5">
                        <Skeleton className="h-4 w-4 rounded shrink-0" />
                        <div className="flex-1 space-y-1.5">
                          <Skeleton className="h-3.5 w-3/5" />
                          <Skeleton className="h-3 w-4/5" />
                        </div>
                        <Skeleton className="h-3 w-10 shrink-0" />
                      </div>
                    ))}
                  </div>
                </CommandGroup>
              )}
              {!hasQuery && recentChats.length > 0 && (
                <CommandGroup heading="Recent Chats">
                  {recentChats.map((chat) => (
                    <CommandItem key={chat.id} value={chat.id} onSelect={() => navigate(`/chat/${chat.id}`)} className="gap-2">
                      <MessageSquare className="w-4 h-4 shrink-0" />
                      <span className="truncate">{chat.title || 'Untitled'}</span>
                      <span className="ml-auto text-xs text-muted-foreground/60 shrink-0">
                        {sharedFormatRelativeTime(chat.updated_at, now)}
                      </span>
                    </CommandItem>
                  ))}
                </CommandGroup>
              )}
              {hasQuery && chatResults.length > 0 && (
                <CommandGroup heading="Conversations">
                  {chatResults.map((result) => {
                    const hasContentMatch = !!result.headline;
                    const Icon = hasContentMatch ? FileText : MessageSquare;
                    return (
                      <CommandItem key={result.thread_id} value={result.thread_id} onSelect={() => navigate(`/chat/${result.thread_id}`)} className="gap-2">
                        <Icon className="w-4 h-4 shrink-0 mt-0.5 self-start" />
                        <div className="flex-1 min-w-0">
                          <p className="text-sm truncate">
                            {highlightQueryInText(result.title || 'Untitled', query, { matchedTerms: result.matched_terms })}
                          </p>
                          {hasContentMatch && (
                            <p className="text-xs text-muted-foreground line-clamp-4 mt-0.5 leading-relaxed">
                              {renderSearchSnippet(result.headline, query, { matchedTerms: result.matched_terms })}
                            </p>
                          )}
                        </div>
                        <span className="text-xs text-muted-foreground/60 shrink-0 self-start">
                          {sharedFormatRelativeTime(result.updated_at, now)}
                        </span>
                      </CommandItem>
                    );
                  })}
                </CommandGroup>
              )}
              {hasQuery && projectResults.length > 0 && (
                <>
                  <CommandSeparator />
                  <CommandGroup heading="Projects">
                    {projectResults.map((project) => (
                      <CommandItem key={project.id} value={project.id} onSelect={() => navigate(`/projects/${project.id}`)} className="gap-2">
                        <FolderOpen className="w-4 h-4 shrink-0" />
                        <div className="flex-1 min-w-0">
                          <p className="text-sm truncate">{highlightQueryInText(project.name, query)}</p>
                          {project.description && (
                            <p className="text-xs text-muted-foreground truncate mt-0.5">
                              {highlightQueryInText(project.description, query)}
                            </p>
                          )}
                        </div>
                        <span className="text-xs text-muted-foreground/60 shrink-0">{project.thread_count} chats</span>
                      </CommandItem>
                    ))}
                  </CommandGroup>
                </>
              )}
            </CommandList>
          </Command>
        )}

        {/* ── Actions mode ── */}
        {mode === 'actions' && (
          <Command shouldFilter={false} className="w-full">
            <CommandInput
              placeholder="Search actions..."
              value={actionQuery}
              onValueChange={setActionQuery}
            />
            <CommandList className="h-72 overflow-y-auto">
              {filteredActions !== null && filteredActions.length === 0 && (
                <CommandEmpty>No matching actions</CommandEmpty>
              )}

              {/* Filtered results (when searching) */}
              {filteredActions !== null && filteredActions.length > 0 && (
                <CommandGroup heading="Results">
                  {filteredActions.map((action) => <ActionRow key={action.id} action={action} />)}
                </CommandGroup>
              )}

              {/* Default: two separate groups */}
              {filteredActions === null && (
                <>
                  <CommandGroup heading="Navigation">
                    {ACTIONS.map((action) => <ActionRow key={action.id} action={action} />)}
                  </CommandGroup>
                  <CommandSeparator />
                  <CommandGroup heading="Response style">
                    {TONE_ACTIONS.map((action) => <ActionRow key={action.id} action={action} />)}
                  </CommandGroup>
                </>
              )}
            </CommandList>
          </Command>
        )}

        {/* Footer */}
        <div className="hidden sm:flex items-center justify-between border-t border-border px-3 py-2 text-xs text-muted-foreground/60">
          <div className="flex flex-wrap items-center gap-3">
            <span className="flex items-center gap-1">
              <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] font-mono">↑↓</kbd>
              navigate
            </span>
            <span className="flex items-center gap-1">
              <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] font-mono">↵</kbd>
              {mode === 'search' ? 'open' : 'run'}
            </span>
            <span className="flex items-center gap-1">
              <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 text-[10px] font-mono">esc</kbd>
              close
            </span>
          </div>
          <span className="text-[10px]">
            {mode === 'search' ? 'Tab → Actions' : 'Tab → Search'}
          </span>
        </div>
      </ResponsiveDialogContent>
    </ResponsiveDialog>
  );
}
