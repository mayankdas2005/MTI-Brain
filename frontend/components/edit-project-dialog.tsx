'use client';

import { useState, useEffect, useTransition } from 'react';
import {
  ResponsiveDialog,
  ResponsiveDialogContent,
  ResponsiveDialogHeader,
  ResponsiveDialogTitle,
  ResponsiveDialogDescription,
  ResponsiveDialogFooter,
} from '@/components/ui/responsive-dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useProjectStore } from '@/lib/store/projects';
import { toast } from '@/lib/toast';

interface EditProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  projectId: string;
  currentName: string;
  currentDescription: string;
}

export function EditProjectDialog({
  open,
  onOpenChange,
  projectId,
  currentName,
  currentDescription,
}: EditProjectDialogProps) {
  const updateProject = useProjectStore((s) => s.updateProject);
  const [name, setName] = useState(currentName);
  const [description, setDescription] = useState(currentDescription);
  const [isPending, startTransition] = useTransition();

  useEffect(() => {
    if (open) {
      setName(currentName);
      setDescription(currentDescription);
    }
  }, [open, currentName, currentDescription]);

  const handleSave = () => {
    const trimmedName = name.trim();
    if (!trimmedName) return;

    const nameChanged = trimmedName !== currentName;
    const descChanged = description !== currentDescription;
    if (!nameChanged && !descChanged) {
      onOpenChange(false);
      return;
    }

    onOpenChange(false);
    startTransition(async () => {
      try {
        await updateProject(
          projectId,
          nameChanged ? trimmedName : undefined,
          descChanged ? description : undefined,
        );
      } catch {
        toast.error('Failed to save project. Please try again.');
      }
    });
  };

  return (
    <ResponsiveDialog open={open} onOpenChange={onOpenChange}>
      <ResponsiveDialogContent className="sm:max-w-md">
        <ResponsiveDialogHeader>
          <ResponsiveDialogTitle>Edit project</ResponsiveDialogTitle>
          <ResponsiveDialogDescription className="sr-only">Update the project name and description</ResponsiveDialogDescription>
        </ResponsiveDialogHeader>
        <div className="space-y-3">
          <div>
            <label htmlFor="edit-project-name" className="text-sm font-medium text-foreground">Name</label>
            <Input
              id="edit-project-name"
              value={name}
              onChange={(e) => setName(e.target.value.slice(0, 255))}
              placeholder="Project name..."
              maxLength={255}
              autoFocus
              className="mt-1"
            />
            <p className="text-xs text-muted-foreground mt-1">{name.length}/255</p>
          </div>
          <div>
            <label htmlFor="edit-project-description" className="text-sm font-medium text-foreground">
              Description <span className="text-muted-foreground font-normal">(optional)</span>
            </label>
            <textarea
              id="edit-project-description"
              value={description}
              onChange={(e) => setDescription(e.target.value.slice(0, 2000))}
              placeholder="Describe the project..."
              maxLength={2000}
              rows={3}
              style={{ maxHeight: '40vh' }}
              className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-base md:text-sm resize-none focus:outline-none focus:ring-1 focus:ring-ring"
            />
            <p className="text-xs text-muted-foreground mt-1">{description.length}/2000</p>
          </div>
        </div>
        <ResponsiveDialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={isPending || !name.trim()}>
            {isPending ? 'Saving...' : 'Save'}
          </Button>
        </ResponsiveDialogFooter>
      </ResponsiveDialogContent>
    </ResponsiveDialog>
  );
}
