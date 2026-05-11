'use client';

import { useState, useTransition } from 'react';
import {
  ResponsiveDialog,
  ResponsiveDialogContent,
  ResponsiveDialogTitle,
  ResponsiveDialogDescription,
  ResponsiveDialogFooter,
} from '@/components/ui/responsive-dialog';
import { Button } from '@/components/ui/button';
import { useProjectStore } from '@/lib/store/projects';
import { useRouter } from 'next/navigation';
import { toast } from '@/lib/toast';

interface CreateProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  navigateOnCreate?: boolean;
}

export function CreateProjectDialog({
  open,
  onOpenChange,
  navigateOnCreate = true,
}: CreateProjectDialogProps) {
  const router = useRouter();
  const createProject = useProjectStore((s) => s.createProject);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [isPending, startTransition] = useTransition();

  const handleClose = (isOpen: boolean) => {
    if (!isOpen) {
      setName('');
      setDescription('');
    }
    onOpenChange(isOpen);
  };

  const handleCreate = () => {
    const trimmedName = name.trim();
    if (!trimmedName) return;

    const descValue = description.trim() || undefined;
    handleClose(false);
    startTransition(async () => {
      try {
        const project = await createProject(trimmedName, descValue);
        if (navigateOnCreate) {
          router.push(`/projects/${project.id}`);
        }
      } catch {
        toast.error('Failed to create project. Please try again.');
      }
    });
  };

  return (
    <ResponsiveDialog open={open} onOpenChange={handleClose}>
      <ResponsiveDialogContent className="sm:max-w-lg p-6 gap-0">
        <ResponsiveDialogTitle className="text-xl font-semibold text-foreground mb-5">Create a project</ResponsiveDialogTitle>
        <ResponsiveDialogDescription className="sr-only">Name your project and describe what you are working on</ResponsiveDialogDescription>

        <div className="space-y-4">
          <div>
            <label htmlFor="create-project-name" className="text-sm font-medium text-foreground">
              What are you working on?
            </label>
            <input
              id="create-project-name"
              value={name}
              onChange={(e) => setName(e.target.value.slice(0, 255))}
              placeholder="Name your project"
              maxLength={255}
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              autoFocus
              className="mt-2 w-full rounded-xl border border-border bg-muted/50 px-4 py-3 text-base md:text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </div>

          <div>
            <label htmlFor="create-project-description" className="text-sm font-medium text-foreground">
              What are you trying to achieve?
            </label>
            <textarea
              id="create-project-description"
              value={description}
              onChange={(e) => setDescription(e.target.value.slice(0, 2000))}
              placeholder="Describe your project, goals, subject, etc..."
              maxLength={2000}
              rows={3}
              style={{ maxHeight: '40vh' }}
              className="mt-2 w-full rounded-xl border border-border bg-muted/50 px-4 py-3 text-base md:text-sm resize-none placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </div>
        </div>

        <ResponsiveDialogFooter className="mt-5">
          <Button variant="ghost" onClick={() => handleClose(false)}>
            Cancel
          </Button>
          <Button onClick={handleCreate} disabled={isPending || !name.trim()}>
            {isPending ? 'Creating...' : 'Create project'}
          </Button>
        </ResponsiveDialogFooter>
      </ResponsiveDialogContent>
    </ResponsiveDialog>
  );
}
