'use client';

import { useEffect, useState, use, useTransition } from 'react';
import { useRouter } from 'next/navigation';
import { useProjectStore } from '@/lib/store/projects';
import { toast } from '@/lib/toast';
import { useThreadStore } from '@/lib/store/threads';
import { Button } from '@/components/ui/button';
import {
  Star,
  Pencil,
  Trash2,
  Plus,
  MessageSquare,
  ArrowLeft,
  MoreHorizontal,
  FolderInput,
  FolderMinus,
} from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { EditProjectDialog } from '@/components/edit-project-dialog';
import { RenameDialog } from '@/components/rename-dialog';
import { MoveToProjectDialog } from '@/components/move-to-project-dialog';
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

interface ProjectDetailPageProps {
  params: Promise<{ projectId: string }>;
}

export default function ProjectDetailPage({ params }: ProjectDetailPageProps) {
  const { projectId } = use(params);
  const router = useRouter();

  const currentProject = useProjectStore((s) => s.currentProject);
  const currentProjectLoading = useProjectStore((s) => s.currentProjectLoading);
  const fetchProject = useProjectStore((s) => s.fetchProject);
  const starProject = useProjectStore((s) => s.starProject);
  const deleteProject = useProjectStore((s) => s.deleteProject);

  const moveThread = useThreadStore((s) => s.moveThread);
  const starThread = useThreadStore((s) => s.starThread);
  const deleteThread = useThreadStore((s) => s.deleteThread);

  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [renameOpen, setRenameOpen] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [activeThreadTitle, setActiveThreadTitle] = useState('');

  // Detail seeding (cache hit, list-only seed, or cold) is fully owned by
  // fetchProject in the store - no inline pre-seed needed here.
  useEffect(() => {
    fetchProject(projectId).catch(() => {
      toast.error('Project not found.');
      router.replace('/projects');
    });

    return () => {
      // Clear stale currentProject when leaving this page so other project
      // pages don't briefly flash this project's data. The detail map keeps
      // the data warm for an instant re-render on return.
      useProjectStore.setState({ currentProject: null });
    };
  }, [projectId, fetchProject, router]);

  const [starPending, startStarTransition] = useTransition();

  const handleStar = () => {
    startStarTransition(async () => {
      try {
        await starProject(projectId);
      } catch {
        toast.error('Failed to update star. Please try again.');
      }
    });
  };

  const handleDelete = async () => {
    try {
      await deleteProject(projectId);
      setDeleteOpen(false);
      router.push('/projects');
    } catch {
      setDeleteOpen(false);
      toast.error('Failed to delete project. Please try again.');
    }
  };

  const handleNewThread = () => {
    router.push(`/new?project=${projectId}`);
  };

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  };

  // Cold load only: no cache, no list seed - currentProject is still null.
  if (!currentProject) {
    return (
      <div className="h-full overflow-y-auto">
        <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-6 sm:py-8">
          <Skeleton className="h-4 w-24 mb-4" />
          <div className="flex items-center gap-3 mb-6">
            <Skeleton className="h-8 w-48" />
            <Skeleton className="h-6 w-6 rounded" />
          </div>
          <Skeleton className="h-4 w-64 mb-8" />
          <div className="space-y-[var(--density-list-gap)]">
            <div className="rounded-lg px-4 py-[var(--density-pad-y-loose)]"><Skeleton className="h-4 mb-2 w-1/2" /><Skeleton className="h-3 w-1/4" /></div>
            <div className="rounded-lg px-4 py-[var(--density-pad-y-loose)]"><Skeleton className="h-4 mb-2 w-3/4" /><Skeleton className="h-3 w-1/4" /></div>
            <div className="rounded-lg px-4 py-[var(--density-pad-y-loose)]"><Skeleton className="h-4 mb-2 w-2/3" /><Skeleton className="h-3 w-1/4" /></div>
            <div className="rounded-lg px-4 py-[var(--density-pad-y-loose)]"><Skeleton className="h-4 mb-2 w-3/5" /><Skeleton className="h-3 w-1/4" /></div>
          </div>
        </div>
      </div>
    );
  }

  // Show the threads-only skeleton when we have header info but the detail
  // request hasn't landed yet (list-only seed path).
  const showThreadSkeleton = currentProjectLoading && currentProject.threads.length === 0;

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-6 sm:py-8">
        {/* Back link */}
        <button
          onClick={() => router.push('/projects')}
          className="flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground transition-colors mb-4"
        >
          <ArrowLeft className="w-4 h-4" />
          All projects
        </button>

        {/* Project Header */}
        <div className="flex items-start justify-between mb-2">
          <div className="flex items-center gap-3 min-w-0">
            <h1 className="text-2xl font-semibold text-foreground truncate">
              {currentProject.name}
            </h1>
            <Tooltip>
              <TooltipTrigger asChild>
                <button
                  onClick={handleStar}
                  disabled={starPending}
                  className="shrink-0 p-1 rounded hover:bg-muted transition-colors disabled:opacity-50"
                >
                  <Star
                    className={`w-5 h-5 ${
                      currentProject.starred
                        ? 'fill-[var(--color-star)] text-[var(--color-star)]'
                        : 'text-muted-foreground'
                    }`}
                  />
                </button>
              </TooltipTrigger>
              <TooltipContent side="top">{currentProject.starred ? 'Unstar' : 'Star'}</TooltipContent>
            </Tooltip>
          </div>

          <div className="flex items-center gap-1 shrink-0">
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setEditOpen(true)}
                >
                  <Pencil className="w-4 h-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">Edit project</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => setDeleteOpen(true)}
                  className="text-destructive hover:text-destructive"
                >
                  <Trash2 className="w-4 h-4" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">Delete project</TooltipContent>
            </Tooltip>
          </div>
        </div>

        {currentProject.description && (
          <p className="text-sm text-muted-foreground mb-6">
            {currentProject.description}
          </p>
        )}

        {/* New Chat in Project + Count */}
        <div className="flex items-center justify-between mb-4">
          <p className="text-sm text-muted-foreground">
            {currentProject.threads.length}{' '}
            {currentProject.threads.length === 1 ? 'conversation' : 'conversations'}
          </p>
          <Button
            onClick={handleNewThread}
            variant="outline"
            size="sm"
            className="gap-2"
          >
            <Plus className="w-3.5 h-3.5" />
            New Chat
          </Button>
        </div>

        {/* Thread List */}
        {showThreadSkeleton ? (
          <div className="space-y-[var(--density-list-gap)]">
            {[1, 2, 3].map((i) => (
              <div key={i} className="rounded-xl border border-border bg-background p-[var(--density-card-pad)]">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <Skeleton className="h-4 w-4 rounded" />
                    <Skeleton className="h-4 w-48" />
                  </div>
                  <Skeleton className="h-3 w-32" />
                </div>
              </div>
            ))}
          </div>
        ) : currentProject.threads.length === 0 ? (
          <div className="text-center py-16 border border-dashed border-border rounded-xl">
            <MessageSquare className="w-10 h-10 text-muted-foreground/30 mx-auto mb-3" />
            <h3 className="text-sm font-medium text-foreground mb-1">No conversations yet</h3>
            <p className="text-xs text-muted-foreground mb-4">
              Start a new conversation in this project.
            </p>
            <Button
              onClick={handleNewThread}
              variant="outline"
              size="sm"
              className="gap-2"
            >
              <Plus className="w-3.5 h-3.5" />
              Start a conversation
            </Button>
          </div>
        ) : (
          <div className="space-y-[var(--density-list-gap)]">
            {currentProject.threads.map((thread) => (
              <div
                key={thread.id}
                className="group flex items-center rounded-xl border border-border bg-background hover:bg-muted/50 hover:border-border/80 transition-colors"
              >
                <button
                  onClick={() => router.push(`/chat/${thread.id}`)}
                  className="flex-1 text-left p-[var(--density-card-pad)] min-w-0"
                >
                  <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-1">
                    <div className="flex items-center gap-2 min-w-0">
                      <MessageSquare className="w-4 h-4 text-muted-foreground shrink-0" />
                      <span className="text-sm font-medium text-foreground truncate">
                        {thread.title || 'Untitled'}
                      </span>
                    </div>
                    <span className="text-xs text-muted-foreground/60 shrink-0 sm:ml-4">
                      {formatDate(thread.created_at)}
                    </span>
                  </div>
                </button>

                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <button
                      className="shrink-0 p-2 mr-2 rounded-md opacity-0 group-hover:opacity-100 transition-opacity text-muted-foreground hover:text-foreground hover:bg-accent"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <MoreHorizontal className="w-4 h-4" />
                    </button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent side="bottom" align="end" className="w-48">
                    <DropdownMenuItem
                      onClick={() => starThread(thread.id)}
                      className="gap-2"
                    >
                      <Star className={`w-3.5 h-3.5 ${thread.starred ? 'fill-[var(--color-star)] text-[var(--color-star)]' : ''}`} />
                      {thread.starred ? 'Unstar' : 'Star'}
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => {
                        setActiveThreadId(thread.id);
                        setActiveThreadTitle(thread.title || 'Untitled');
                        setRenameOpen(true);
                      }}
                      className="gap-2"
                    >
                      <Pencil className="w-3.5 h-3.5" />
                      Rename
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      onClick={() => {
                        setActiveThreadId(thread.id);
                        setMoveOpen(true);
                      }}
                      className="gap-2"
                    >
                      <FolderInput className="w-3.5 h-3.5" />
                      Change project
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={async () => {
                        await moveThread(thread.id, null);
                        fetchProject(projectId);
                      }}
                      className="gap-2"
                    >
                      <FolderMinus className="w-3.5 h-3.5" />
                      Remove from project
                    </DropdownMenuItem>
                    <DropdownMenuSeparator />
                    <DropdownMenuItem
                      onClick={async () => {
                        await deleteThread(thread.id);
                        fetchProject(projectId);
                      }}
                      className="gap-2 text-destructive focus:text-destructive"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                      Delete
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Thread Rename Dialog */}
      {activeThreadId && (
        <RenameDialog
          open={renameOpen}
          onOpenChange={(open) => {
            setRenameOpen(open);
            if (!open) setActiveThreadId(null);
          }}
          threadId={activeThreadId}
          currentTitle={activeThreadTitle}
        />
      )}

      {/* Thread Move Dialog */}
      {activeThreadId && (
        <MoveToProjectDialog
          open={moveOpen}
          onOpenChange={(open) => {
            setMoveOpen(open);
            if (!open) {
              setActiveThreadId(null);
              fetchProject(projectId);
            }
          }}
          threadIds={[activeThreadId]}
        />
      )}

      {/* Edit Dialog */}
      <EditProjectDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        projectId={projectId}
        currentName={currentProject.name}
        currentDescription={currentProject.description || ''}
      />

      {/* Delete Confirmation */}
      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              Delete project &quot;{currentProject.name}&quot;?
            </AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete the project and all {currentProject.threads.length}{' '}
              conversation{currentProject.threads.length !== 1 ? 's' : ''} inside it.
              This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="flex gap-3 justify-end">
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete project
            </AlertDialogAction>
          </div>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
