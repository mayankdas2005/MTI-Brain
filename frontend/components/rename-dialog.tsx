'use client';

import { useState, useEffect, useTransition } from 'react';
import {
  ResponsiveDialog,
  ResponsiveDialogContent,
  ResponsiveDialogTitle,
  ResponsiveDialogDescription,
  ResponsiveDialogFooter,
} from '@/components/ui/responsive-dialog';
import { Button } from '@/components/ui/button';
import { useThreadStore } from '@/lib/store/threads';
import { toast } from '@/lib/toast';

interface RenameDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  threadId: string;
  currentTitle: string;
}

export function RenameDialog({ open, onOpenChange, threadId, currentTitle }: RenameDialogProps) {
  const renameThread = useThreadStore((s) => s.renameThread);
  const [title, setTitle] = useState(currentTitle);
  const [isPending, startTransition] = useTransition();

  useEffect(() => {
    if (open) setTitle(currentTitle);
  }, [open, currentTitle]);

  const handleSave = () => {
    const trimmed = title.trim();
    if (!trimmed || trimmed === currentTitle) {
      onOpenChange(false);
      return;
    }
    onOpenChange(false);
    startTransition(async () => {
      try {
        await renameThread(threadId, trimmed);
      } catch {
        toast.error('Failed to rename chat. Please try again.');
      }
    });
  };

  return (
    <ResponsiveDialog open={open} onOpenChange={onOpenChange}>
      <ResponsiveDialogContent className="sm:max-w-md p-6 gap-0">
        <ResponsiveDialogTitle className="text-lg font-semibold text-foreground mb-4">Rename chat</ResponsiveDialogTitle>
        <ResponsiveDialogDescription className="sr-only">Enter a new title for this conversation</ResponsiveDialogDescription>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value.slice(0, 500))}
          placeholder="Enter a title..."
          aria-label="Chat title"
          maxLength={500}
          onKeyDown={(e) => e.key === 'Enter' && handleSave()}
          autoFocus
          className="w-full rounded-xl border border-border bg-muted/50 px-4 py-3 text-base md:text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
        />
        <ResponsiveDialogFooter className="mt-4">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={isPending || !title.trim()}>
            {isPending ? 'Saving...' : 'Save'}
          </Button>
        </ResponsiveDialogFooter>
      </ResponsiveDialogContent>
    </ResponsiveDialog>
  );
}
