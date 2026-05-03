'use client';

import { useState, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useProjectStore } from '@/lib/store/projects';

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
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      setName(currentName);
      setDescription(currentDescription);
    }
  }, [open, currentName, currentDescription]);

  const handleSave = async () => {
    const trimmedName = name.trim();
    if (!trimmedName) return;

    const nameChanged = trimmedName !== currentName;
    const descChanged = description !== currentDescription;
    if (!nameChanged && !descChanged) {
      onOpenChange(false);
      return;
    }

    setSaving(true);
    try {
      await updateProject(
        projectId,
        nameChanged ? trimmedName : undefined,
        descChanged ? description : undefined,
      );
      onOpenChange(false);
    } catch {
      // Error handled by store
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>Edit project</DialogTitle>
        </DialogHeader>
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
              className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm resize-none focus:outline-none focus:ring-1 focus:ring-ring"
            />
            <p className="text-xs text-muted-foreground mt-1">{description.length}/2000</p>
          </div>
        </div>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={saving || !name.trim()}>
            {saving ? 'Saving...' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
