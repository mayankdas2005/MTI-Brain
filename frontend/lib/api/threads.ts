/**
 * Thread API functions — typed wrappers for /api/v1/chat endpoints.
 */

import { apiFetch } from './client';
import type {
  ThreadSummary,
  SearchResult,
  ThreadDetail,
  NewChatResponse,
  DeleteResponse,
  BulkDeleteResponse,
  BulkMoveResponse,
  FeedbackOut,
  NewChatRequest,
  FeedbackRequest,
} from '../types/api';

export async function createThread(body: NewChatRequest = {}): Promise<NewChatResponse> {
  return apiFetch<NewChatResponse>('/chat/new', {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

export async function getRecents(params?: {
  search?: string;
  project_id?: string;
  limit?: number;
  offset?: number;
}): Promise<ThreadSummary[] | SearchResult[]> {
  const query = new URLSearchParams();
  if (params?.search) query.set('search', params.search);
  if (params?.project_id) query.set('project_id', params.project_id);
  if (params?.limit != null) query.set('limit', String(params.limit));
  if (params?.offset != null) query.set('offset', String(params.offset));

  const qs = query.toString();
  return apiFetch<ThreadSummary[] | SearchResult[]>(
    `/chat/recents${qs ? `?${qs}` : ''}`,
  );
}

export async function getThread(threadId: string): Promise<ThreadDetail> {
  return apiFetch<ThreadDetail>(`/chat/${threadId}`);
}

export async function deleteThread(
  threadId: string,
): Promise<DeleteResponse> {
  return apiFetch<DeleteResponse>(`/chat/${threadId}`, { method: 'DELETE' });
}

export async function bulkDeleteThreads(
  threadIds: string[],
): Promise<BulkDeleteResponse> {
  return apiFetch<BulkDeleteResponse>('/chat/bulk/delete', {
    method: 'POST',
    body: JSON.stringify({ thread_ids: threadIds }),
  });
}

export async function bulkMoveThreads(
  threadIds: string[],
  projectId: string | null,
): Promise<BulkMoveResponse> {
  return apiFetch<BulkMoveResponse>('/chat/bulk/move', {
    method: 'POST',
    body: JSON.stringify({ thread_ids: threadIds, project_id: projectId }),
  });
}

export async function starThread(
  threadId: string,
): Promise<{ thread_id: string; starred: boolean }> {
  return apiFetch<{ thread_id: string; starred: boolean }>(
    `/chat/${threadId}/star`,
    { method: 'PATCH' },
  );
}

export async function renameThread(
  threadId: string,
  title: string,
): Promise<{ thread_id: string; title: string }> {
  return apiFetch<{ thread_id: string; title: string }>(
    `/chat/${threadId}/rename`,
    { method: 'PATCH', body: JSON.stringify({ title }) },
  );
}

export async function moveThread(
  threadId: string,
  projectId: string | null,
): Promise<{ thread_id: string; project_id: string | null }> {
  return apiFetch<{ thread_id: string; project_id: string | null }>(
    `/chat/${threadId}/move`,
    { method: 'PATCH', body: JSON.stringify({ project_id: projectId }) },
  );
}

export async function submitFeedback(
  threadId: string,
  conversationId: string,
  body: FeedbackRequest,
): Promise<FeedbackOut> {
  return apiFetch<FeedbackOut>(
    `/chat/${threadId}/conversations/${conversationId}/feedback`,
    { method: 'POST', body: JSON.stringify(body) },
  );
}

export async function stopGeneration(
  threadId: string,
): Promise<{ thread_id: string; stopped: boolean }> {
  return apiFetch<{ thread_id: string; stopped: boolean }>(
    `/chat/${threadId}/stop`,
    { method: 'POST' },
  );
}
