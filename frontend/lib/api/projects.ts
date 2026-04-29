/**
 * Project API functions — typed wrappers for /api/v1/projects endpoints.
 */

import { apiFetch } from './client';
import type {
  ProjectOut,
  ProjectDetail,
  DeleteProjectResponse,
} from '../types/api';

export async function listProjects(search?: string): Promise<ProjectOut[]> {
  const qs = search ? `?search=${encodeURIComponent(search)}` : '';
  return apiFetch<ProjectOut[]>(`/projects${qs}`);
}

export async function createProject(
  name: string,
  description?: string,
): Promise<ProjectOut> {
  return apiFetch<ProjectOut>('/projects/create', {
    method: 'POST',
    body: JSON.stringify({ name, description }),
  });
}

export async function getProject(projectId: string): Promise<ProjectDetail> {
  return apiFetch<ProjectDetail>(`/projects/${projectId}`);
}

export async function updateProject(
  projectId: string,
  body: { name?: string; description?: string },
): Promise<ProjectOut> {
  return apiFetch<ProjectOut>(`/projects/${projectId}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

export async function deleteProject(
  projectId: string,
): Promise<DeleteProjectResponse> {
  return apiFetch<DeleteProjectResponse>(`/projects/${projectId}`, {
    method: 'DELETE',
  });
}

export async function starProject(
  projectId: string,
): Promise<{ project_id: string; starred: boolean }> {
  return apiFetch<{ project_id: string; starred: boolean }>(
    `/projects/${projectId}/star`,
    { method: 'PATCH' },
  );
}
