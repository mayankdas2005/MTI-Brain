import { apiFetch } from './client';

export interface UserInstruction {
  id: string;
  title: string;
  content: string;
  enabled: boolean;
  scope: 'all' | 'written_answers' | 'sql_only';
  created_at: string;
  updated_at: string;
}

export type InstructionScope = UserInstruction['scope'];

export interface CreateInstructionPayload {
  title: string;
  content: string;
  enabled?: boolean;
  scope?: InstructionScope;
}

export interface UpdateInstructionPayload {
  title?: string;
  content?: string;
  enabled?: boolean;
  scope?: InstructionScope;
}

export async function listInstructions(): Promise<UserInstruction[]> {
  return apiFetch<UserInstruction[]>('/settings/instructions');
}

export async function createInstruction(
  payload: CreateInstructionPayload,
): Promise<UserInstruction> {
  return apiFetch<UserInstruction>('/settings/instructions', {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

export async function updateInstruction(
  id: string,
  payload: UpdateInstructionPayload,
): Promise<UserInstruction> {
  return apiFetch<UserInstruction>(`/settings/instructions/${id}`, {
    method: 'PATCH',
    body: JSON.stringify(payload),
  });
}

export async function deleteInstruction(id: string): Promise<void> {
  await apiFetch<void>(`/settings/instructions/${id}`, { method: 'DELETE' });
}
