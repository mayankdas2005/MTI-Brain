import { apiFetch } from './client';

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
