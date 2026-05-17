import { apiFetch, apiBase } from './client';
import { getAuthHeaders } from '@/lib/auth';

export interface DashboardStatusOut {
  status: string;
  message: string;
  url?: string | null;
}

export async function generateDashboard(conversationId: string): Promise<DashboardStatusOut> {
  return apiFetch<DashboardStatusOut>(`/dashboard/generate/${conversationId}`, {
    method: 'POST',
  });
}

export async function getDashboard(conversationId: string): Promise<DashboardStatusOut> {
  return apiFetch<DashboardStatusOut>(`/dashboard/${conversationId}`);
}

export async function deleteDashboard(conversationId: string): Promise<DashboardStatusOut> {
  return apiFetch<DashboardStatusOut>(`/dashboard/${conversationId}`, {
    method: 'DELETE',
  });
}

export async function downloadDashboard(conversationId: string): Promise<{ blob: Blob; filename: string }> {
  const res = await fetch(`${apiBase}/dashboard/${conversationId}/download`, {
    headers: getAuthHeaders() as Record<string, string>,
  });
  if (!res.ok) throw new Error(`Download failed: ${res.status}`);
  const filename = res.headers.get('X-Filename') ?? `dashboard-${conversationId.slice(0, 8)}.html`;
  const blob = await res.blob();
  return { blob, filename };
}
