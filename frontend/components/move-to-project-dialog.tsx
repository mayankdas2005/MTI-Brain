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
import { ScrollArea } from '@/components/ui/scroll-area';
import { useProjectStore } from '@/lib/store/projects';
import { useThreadStore } from '@/lib/store/threads';
import { FolderOpen, Plus, Check } from 'lucide-react';

interface MoveToProjectDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  threadIds: string[];
  isBulk?: boolean;
}

export function MoveToProjectDialog({
  open,
  onOpenChange,
  threadIds,
  isBulk = false,
}: MoveToProjectDialogProps) {
  const projects = useProjectStore((s) => s.projects);
  const fetchProjects = useProjectStore((s) => s.fetchProjects);
  const createProject = useProjectStore((s) => s.createProject);
  const moveThread = useThreadStore((s) => s.moveThread);
  const bulkMoveThreads = useThreadStore((s) => s.bulkMoveThreads);

  const [search, setSearch] = useState('');
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const hasSelection = selectedProjectId !== null;
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState('');
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (open) {
      fetchProjects();
      setSearch('');
      setSelectedProjectId(null);
      setCreating(false);
      setNewName('');
    }
  }, [open, fetchProjects]);

  const filtered = projects.filter((p) =>
    p.name.toLowerCase().includes(search.toLowerCase()),
  );

  const handleMove = async () => {
    setSaving(true);
    try {
      if (isBulk) {
        await bulkMoveThreads(selectedProjectId);
      } else if (threadIds.length === 1) {
        await moveThread(threadIds[0], selectedProjectId);
      }
      onOpenChange(false);
    } catch {
      // Error handled by store
    } finally {
      setSaving(false);
    }
  };

  const handleCreateProject = async () => {
    if (!newName.trim()) return;
    setSaving(true);
    try {
      const project = await createProject(newName.trim());
      setSelectedProjectId(project.id);
      setCreating(false);
      setNewName('');
    } catch {
      // ignore
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md" aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>
            Move {isBulk ? `${threadIds.length} conversations` : 'conversation'} to project
          </DialogTitle>
        </DialogHeader>

        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search projects..."
          className="mb-2"
        />

        <ScrollArea className="max-h-60">
          <div className="space-y-1">
            {filtered.map((p) => (
              <button
                key={p.id}
                onClick={() => setSelectedProjectId(p.id)}
                className={`w-full flex items-center gap-2 px-3 py-2 rounded-md text-sm text-left transition-colors ${
                  selectedProjectId === p.id
                    ? 'bg-primary/10 text-primary'
                    : 'hover:bg-accent text-foreground'
                }`}
              >
                <FolderOpen className="w-4 h-4 shrink-0" />
                <span className="truncate">{p.name}</span>
                {selectedProjectId === p.id && <Check className="w-4 h-4 ml-auto" />}
              </button>
            ))}
          </div>
        </ScrollArea>

        {creating ? (
          <div className="flex gap-2">
            <Input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="Project name..."
              maxLength={255}
              onKeyDown={(e) => e.key === 'Enter' && handleCreateProject()}
              autoFocus
            />
            <Button size="sm" onClick={handleCreateProject} disabled={saving || !newName.trim()}>
              Create
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setCreating(false)}>
              Cancel
            </Button>
          </div>
        ) : (
          <Button variant="outline" size="sm" onClick={() => setCreating(true)} className="gap-1">
            <Plus className="w-3.5 h-3.5" />
            New project
          </Button>
        )}

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleMove} disabled={saving || !hasSelection}>
            {saving ? 'Moving...' : 'Move'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
