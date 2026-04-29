'use client';

import { useState } from 'react';
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from '@/components/ui/context-menu';
import { useProjectStore } from '@/lib/store/projects';
import { Star, Pencil, Trash2 } from 'lucide-react';
import { EditProjectDialog } from './edit-project-dialog';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog';
import { useRouter } from 'next/navigation';

interface ProjectContextMenuProps {
  projectId: string;
  projectName: string;
  projectDescription: string;
  starred: boolean;
  children: React.ReactNode;
}

export function ProjectContextMenu({
  projectId,
  projectName,
  projectDescription,
  starred,
  children,
}: ProjectContextMenuProps) {
  const router = useRouter();
  const starProject = useProjectStore((s) => s.starProject);
  const deleteProject = useProjectStore((s) => s.deleteProject);

  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const handleDelete = async () => {
    await deleteProject(projectId);
    setDeleteOpen(false);
    // If we're on the project detail page, go back to list
    router.push('/projects');
  };

  return (
    <>
      <ContextMenu>
        <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
        <ContextMenuContent className="w-48">
          <ContextMenuItem onClick={() => starProject(projectId)} className="gap-2">
            <Star className={`w-4 h-4 ${starred ? 'fill-yellow-400 text-yellow-400' : ''}`} />
            {starred ? 'Unstar' : 'Star'}
          </ContextMenuItem>
          <ContextMenuItem onClick={() => setEditOpen(true)} className="gap-2">
            <Pencil className="w-4 h-4" />
            Edit
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

      <EditProjectDialog
        open={editOpen}
        onOpenChange={setEditOpen}
        projectId={projectId}
        currentName={projectName}
        currentDescription={projectDescription}
      />

      <AlertDialog open={deleteOpen} onOpenChange={setDeleteOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete project &quot;{projectName}&quot;?</AlertDialogTitle>
            <AlertDialogDescription>
              This will permanently delete the project and all its conversations. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <div className="flex gap-3 justify-end">
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={handleDelete}
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
