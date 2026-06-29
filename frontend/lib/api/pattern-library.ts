import { apiFetch } from './client';

export type PatternRecord = Record<string, unknown>;

export interface PatternPage {
  items: PatternRecord[];
  total: number;
  enabled_total: number;
  disabled_total: number;
  skip: number;
  limit: number;
}

export async function listQueryPatterns(skip = 0, limit = 20, search = '', filter = 'all'): Promise<PatternPage> {
  const params = new URLSearchParams({ skip: String(skip), limit: String(limit), search, filter });
  return apiFetch<PatternPage>(`/settings/admin/query-patterns?${params}`);
}

export async function listAntiPatterns(skip = 0, limit = 20, search = '', filter = 'all'): Promise<PatternPage> {
  const params = new URLSearchParams({ skip: String(skip), limit: String(limit), search, filter });
  return apiFetch<PatternPage>(`/settings/admin/anti-patterns?${params}`);
}

export async function setQueryPatternEnabled(id: string, is_enabled: boolean): Promise<void> {
  await apiFetch(`/settings/admin/query-patterns/${id}/enabled`, {
    method: 'PATCH',
    body: JSON.stringify({ is_enabled }),
  });
}

export async function setAntiPatternEnabled(id: string, is_enabled: boolean): Promise<void> {
  await apiFetch(`/settings/admin/anti-patterns/${id}/enabled`, {
    method: 'PATCH',
    body: JSON.stringify({ is_enabled }),
  });
}

export async function listEnabledQueryPatterns(): Promise<{ items: PatternRecord[]; total: number }> {
  return apiFetch('/settings/query-patterns/enabled');
}

export async function listEnabledAntiPatterns(): Promise<{ items: PatternRecord[]; total: number }> {
  return apiFetch('/settings/anti-patterns/enabled');
}

export async function deleteQueryPattern(id: string): Promise<void> {
  await apiFetch(`/settings/admin/query-patterns/${id}`, { method: 'DELETE' });
}

export async function deleteAntiPattern(id: string): Promise<void> {
  await apiFetch(`/settings/admin/anti-patterns/${id}`, { method: 'DELETE' });
}
