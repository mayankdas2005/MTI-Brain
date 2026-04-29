'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from '@/components/ui/context-menu';
import { useThreadStore } from '@/lib/store/threads';
import { Star, Pencil, FolderInput, Trash2, Copy } from 'lucide-react';
import { RenameDialog } from './rename-dialog';
import { MoveToProjectDialog } from './move-to-project-dialog';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';

interface ThreadContextMenuProps {
  threadId: string;
  threadTitle: string;
  starred: boolean;
  children: React.ReactNode;
}

export function ThreadContextMenu({
  threadId,
  threadTitle,
  starred,
  children,
}: ThreadContextMenuProps) {
  const router = useRouter();
  const starThread = useThreadStore((s) => s.starThread);
  const deleteThread = useThreadStore((s) => s.deleteThread);

  const [renameOpen, setRenameOpen] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  return (
    <>
      <ContextMenu>
        <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
        <ContextMenuContent className="w-48">
          <ContextMenuItem onClick={() => starThread(threadId)} className="gap-2">
            <Star className={`w-4 h-4 ${starred ? 'fill-yellow-400 text-yellow-400' : ''}`} />
            {starred ? 'Unstar' : 'Star'}
          </ContextMenuItem>
          <ContextMenuItem onClick={() => setRenameOpen(true)} className="gap-2">
            <Pencil className="w-4 h-4" />
            Rename
          </ContextMenuItem>
          <ContextMenuItem onClick={() => setMoveOpen(true)} className="gap-2">
            <FolderInput className="w-4 h-4" />
            Move to project
          </ContextMenuItem>
          <ContextMenuSeparator />
          <ContextMenuItem
            onClick={() => setDeleteOpen(true)}
            className="gap-2 text-destructive focus:text-destructive"
          >
            <Trash2 className="w-4 h-4" />
            Delete
          </ContextMenuItem>
        </ContextMenuContent>
      </ContextMenu>

      <RenameDialog
        open={renameOpen}
        onOpenChange={setRenameOpen}
        threadId={threadId}
        currentTitle={threadTitle}
      />

      <MoveToProjectDialog
        open={moveOpen}
        onOpenChange={setMoveOpen}
        threadIds={[threadId]}
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
                const wasCurrent = await deleteThread(threadId);
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
