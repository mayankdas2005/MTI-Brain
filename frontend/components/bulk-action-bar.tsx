'use client';

import { useState } from 'react';
import { Button } from '@/components/ui/button';
import { useThreadStore } from '@/lib/store/threads';
import { Trash2, FolderInput, X } from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { MoveToProjectDialog } from './move-to-project-dialog';

export function BulkActionBar() {
  const selectedThreadIds = useThreadStore((s) => s.selectedThreadIds);
  const bulkDeleteThreads = useThreadStore((s) => s.bulkDeleteThreads);
  const clearSelection = useThreadStore((s) => s.clearSelection);

  const [deleteConfirm, setDeleteConfirm] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);

  const count = selectedThreadIds.size;
  if (count === 0) return null;

  return (
    <>
      <div className="px-3 py-2 border-t border-sidebar-border bg-sidebar flex items-center gap-2">
        <span className="text-xs text-sidebar-foreground/70 flex-1">
          {count} selected
        </span>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => setMoveOpen(true)}
            >
              <FolderInput className="w-3.5 h-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="top">Move to project</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs text-destructive hover:text-destructive hover:bg-destructive/10"
              onClick={() => setDeleteConfirm(true)}
            >
              <Trash2 className="w-3.5 h-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="top">Delete selected</TooltipContent>
        </Tooltip>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={clearSelection}
            >
              <X className="w-3.5 h-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="top">Cancel</TooltipContent>
        </Tooltip>
      </div>

      <AlertDialog open={deleteConfirm} onOpenChange={setDeleteConfirm}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete {count} conversations?</AlertDialogTitle>
            <AlertDialogDescription>
              This action cannot be undone. All selected conversations will be permanently deleted.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="flex gap-3 justify-end">
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                bulkDeleteThreads();
                setDeleteConfirm(false);
              }}
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
            >
              Delete all
            </AlertDialogAction>
          </div>
        </AlertDialogContent>
      </AlertDialog>

      <MoveToProjectDialog
        open={moveOpen}
        onOpenChange={setMoveOpen}
        threadIds={[...selectedThreadIds]}
        isBulk
      />
    </>
  );
}
