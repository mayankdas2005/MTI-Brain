'use client';

import { useState } from 'react';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { useProjectStore } from '@/lib/store/projects';
import { useRouter } from 'next/navigation';

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
  const [saving, setSaving] = useState(false);

  const handleClose = (isOpen: boolean) => {
    if (!isOpen) {
      setName('');
      setDescription('');
    }
    onOpenChange(isOpen);
  };

  const handleCreate = async () => {
    const trimmedName = name.trim();
    if (!trimmedName) return;

    setSaving(true);
    try {
      const project = await createProject(trimmedName, description.trim() || undefined);
      handleClose(false);
      if (navigateOnCreate) {
        router.push(`/projects/${project.id}`);
      }
    } catch {
      // Error handled by store
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleClose}>
      <DialogContent className="sm:max-w-lg p-6 gap-0" aria-describedby={undefined}>
        <DialogTitle className="text-xl font-semibold text-foreground mb-5">Create a project</DialogTitle>

        <div className="space-y-4">
          <div>
            <label className="text-sm font-medium text-foreground">
              What are you working on?
            </label>
            <input
              value={name}
              onChange={(e) => setName(e.target.value.slice(0, 255))}
              placeholder="Name your project"
              maxLength={255}
              onKeyDown={(e) => e.key === 'Enter' && handleCreate()}
              autoFocus
              className="mt-2 w-full rounded-xl border border-border bg-muted/50 px-4 py-3 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </div>

          <div>
            <label className="text-sm font-medium text-foreground">
              What are you trying to achieve?
            </label>
            <textarea
              value={description}
              onChange={(e) => setDescription(e.target.value.slice(0, 2000))}
              placeholder="Describe your project, goals, subject, etc..."
              maxLength={2000}
              rows={3}
              className="mt-2 w-full rounded-xl border border-border bg-muted/50 px-4 py-3 text-sm resize-none placeholder:text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
            />
          </div>
        </div>

        <DialogFooter className="mt-5">
          <Button variant="ghost" onClick={() => handleClose(false)}>
            Cancel
          </Button>
          <Button onClick={handleCreate} disabled={saving || !name.trim()}>
            {saving ? 'Creating...' : 'Create project'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
