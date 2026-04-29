'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useProjectStore } from '@/lib/store/projects';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Plus,
  Search,
  Star,
  FolderOpen,
  MessageSquare,
} from 'lucide-react';
import { Skeleton } from '@/components/ui/skeleton';
import { CreateProjectDialog } from '@/components/create-project-dialog';
import { ProjectContextMenu } from '@/components/project-context-menu';

export default function ProjectsPage() {
  const router = useRouter();
  const projects = useProjectStore((s) => s.projects);
  const loading = useProjectStore((s) => s.loading);
  const fetched = useProjectStore((s) => s.fetched);
  const searchQuery = useProjectStore((s) => s.searchQuery);
  const setSearchQuery = useProjectStore((s) => s.setSearchQuery);
  const fetchProjects = useProjectStore((s) => s.fetchProjects);

  const [createOpen, setCreateOpen] = useState(false);

  useEffect(() => {
    fetchProjects();
  }, [fetchProjects]);

  const formatDate = (dateStr: string) => {
    return new Date(dateStr).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
  };

  return (
    <div className="h-full overflow-y-auto">
      <div className="max-w-4xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-semibold text-foreground">Projects</h1>
            <p className="text-sm text-muted-foreground mt-1">
              Organize your conversations into projects
            </p>
          </div>
          <Button onClick={() => setCreateOpen(true)} className="gap-2">
            <Plus className="w-4 h-4" />
            New Project
          </Button>
        </div>

        {/* Search */}
        <div className="relative mb-6">
          <Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
          <Input
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search projects..."
            className="pl-10"
          />
        </div>

        {/* Project Grid */}
        {projects.length === 0 && (loading || !fetched) ? (
          <ProjectGridSkeleton />
        ) : projects.length === 0 ? (
          <div className="text-center py-20">
            <FolderOpen className="w-12 h-12 text-muted-foreground/30 mx-auto mb-4" />
            <h3 className="text-lg font-medium text-foreground mb-1">No projects yet</h3>
            <p className="text-sm text-muted-foreground mb-4">
              Create a project to start organizing your conversations.
            </p>
            <Button onClick={() => setCreateOpen(true)} variant="outline" className="gap-2">
              <Plus className="w-4 h-4" />
              Create your first project
            </Button>
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {projects.map((project) => (
              <ProjectContextMenu
                key={project.id}
                projectId={project.id}
                projectName={project.name}
                projectDescription={project.description || ''}
                starred={project.starred}
              >
                <button
                  onClick={() => router.push(`/projects/${project.id}`)}
                  onMouseEnter={() => router.prefetch(`/projects/${project.id}`)}
                  className="text-left rounded-xl border border-border bg-background p-4 shadow-sm hover:shadow-md hover:bg-muted/30 hover:border-primary/20 transition-all duration-200 group"
                >
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex items-center gap-2 min-w-0">
                      <FolderOpen className="w-4 h-4 text-primary shrink-0" />
                      <h3 className="text-sm font-medium text-foreground truncate">
                        {project.name}
                      </h3>
                    </div>
                    {project.starred && (
                      <Star className="w-3.5 h-3.5 fill-[var(--color-star)] text-[var(--color-star)] shrink-0" />
                    )}
                  </div>

                  {project.description && (
                    <p className="text-xs text-muted-foreground line-clamp-2 mb-3">
                      {project.description}
                    </p>
                  )}

                  <div className="flex items-center justify-between text-xs text-muted-foreground/70">
                    <span className="flex items-center gap-1">
                      <MessageSquare className="w-3 h-3" />
                      {project.thread_count} {project.thread_count === 1 ? 'thread' : 'threads'}
                    </span>
                    <span>{formatDate(project.updated_at)}</span>
                  </div>
                </button>
              </ProjectContextMenu>
            ))}
          </div>
        )}
      </div>

      <CreateProjectDialog
        open={createOpen}
        onOpenChange={setCreateOpen}
      />
    </div>
  );
}

function ProjectGridSkeleton() {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
      {Array.from({ length: 6 }, (_, i) => (
        <div key={i} className="rounded-xl border border-border p-5">
          <Skeleton className="h-5 w-3/5 mb-3" />
          <Skeleton className="h-3 w-4/5 mb-4" />
          <div className="flex items-center gap-2">
            <Skeleton className="h-3 w-3 rounded" />
            <Skeleton className="h-3 w-16" />
          </div>
        </div>
      ))}
    </div>
  );
}
