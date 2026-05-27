import { apiFetch, apiBase } from './client';
import { getAuthHeaders } from '@/lib/auth';

export interface GraphContextStatusOut {
  status: string;
  message: string;
  url?: string | null;
}

export async function generateGraphContext(conversationId: string): Promise<GraphContextStatusOut> {
  return apiFetch<GraphContextStatusOut>(`/graph-context/generate/${conversationId}`, {
    method: 'POST',
  });
}

export async function getGraphContext(conversationId: string): Promise<GraphContextStatusOut> {
  return apiFetch<GraphContextStatusOut>(`/graph-context/${conversationId}`);
}

export async function deleteGraphContext(conversationId: string): Promise<GraphContextStatusOut> {
  return apiFetch<GraphContextStatusOut>(`/graph-context/${conversationId}`, {
    method: 'DELETE',
  });
}

export async function downloadGraphContext(conversationId: string): Promise<{ blob: Blob; filename: string }> {
  const res = await fetch(`${apiBase}/graph-context/${conversationId}/download`, {
    headers: getAuthHeaders() as Record<string, string>,
  });
  if (!res.ok) throw new Error(`Download failed: ${res.status}`);
  const filename = res.headers.get('X-Filename') ?? `graph-${conversationId.slice(0, 8)}.html`;
  const blob = await res.blob();
  return { blob, filename };
}
