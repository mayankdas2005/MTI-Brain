/**
 * TypeScript types mirroring backend Pydantic schemas.
 * Source of truth: backend/app/schemas/chat.py, backend/app/schemas/project.py
 */

// ─── Message Metadata ───

export interface MessageMetadata {
  sql?: string;
  intent?: string;
  resolved_filters?: string;
  columns?: string[];
  rows?: unknown[][];
  row_count?: number;
  chart_spec?: Record<string, unknown>;
  follow_ups?: string[];
  run_id?: string;
  stopped?: boolean;
  needs_clarification?: boolean;
  duration_ms?: number;
  source_conversation_id?: string;
  interrupted?: boolean;
}

// ─── Response Types ───

export interface ThreadSummary {
  id: string;
  project_id: string | null;
  title: string | null;
  starred: boolean;
  last_message: string | null;
  message_count: number;
  created_at: string;
  updated_at: string;
}

export interface SearchResult {
  thread_id: string;
  project_id: string | null;
  title: string | null;
  match_type: string;
  preview: string | null;
  headline: string | null;
  rank: number;
  created_at: string;
  updated_at: string;
}

export interface MessageOut {
  id: string;
  thread_id: string;
  conversation_id: string;
  parent_conversation_id: string | null;
  role: 'user' | 'assistant';
  content: string;
  reasoning: string | null;
  metadata_: MessageMetadata | null;
  feedback: { liked: boolean; comment?: string | null } | null;
  created_at: string;
}

export interface ThreadDetail {
  id: string;
  project_id: string | null;
  title: string | null;
  starred: boolean;
  messages: MessageOut[];
  created_at: string;
  updated_at: string;
}

export interface NewChatResponse {
  thread_id: string;
  title: string | null;
}

export interface DeleteResponse {
  deleted: boolean;
  thread_id: string;
}

export interface BulkDeleteResponse {
  deleted_count: number;
}

export interface BulkMoveResponse {
  moved_count: number;
  project_id: string | null;
}

export interface FeedbackOut {
  id: string;
  conversation_id: string;
  liked: boolean;
  comment: string | null;
  created_at: string;
}

export interface ProjectOut {
  id: string;
  name: string;
  description: string | null;
  starred: boolean;
  thread_count: number;
  created_at: string;
  updated_at: string;
}

export interface ThreadBrief {
  id: string;
  title: string | null;
  starred: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProjectDetail {
  id: string;
  name: string;
  description: string | null;
  starred: boolean;
  threads: ThreadBrief[];
  created_at: string;
  updated_at: string;
}

export interface DeleteProjectResponse {
  deleted: boolean;
  project_id: string;
}

// ─── Request Types ───

export interface CreateProjectRequest {
  name: string;
  description?: string;
}

export interface UpdateProjectRequest {
  name?: string;
  description?: string;
}

export interface NewChatRequest {
  thread_id?: string;
  project_id?: string;
  title?: string;
}

export interface AskRequest {
  question: string;
  conversation_id?: string;
}

export interface RetryRequest {
  conversation_id: string;
}

export interface EditRequest {
  conversation_id: string;
  question: string;
}

export interface FeedbackRequest {
  liked: boolean;
  comment?: string;
}

export interface RenameRequest {
  title: string;
}

export interface MoveRequest {
  project_id: string | null;
}

export interface BulkDeleteRequest {
  thread_ids: string[];
}

export interface BulkMoveRequest {
  thread_ids: string[];
  project_id: string | null;
}

// ─── SSE Event Types ───

export interface SSETitleGenerated {
  event: 'title.generated';
  data: { thread_id: string; title: string };
}

export interface SSENodeStart {
  event: 'node.start';
  data: { node: string; message: string };
}

export interface SSEReasoningDelta {
  event: 'reasoning.delta';
  data: { node: string; text: string };
}

export interface SSEAnswerDelta {
  event: 'answer.delta';
  data: { node: string; text: string };
}

export interface SSEValidation {
  event: 'validation';
  data: { status: string; message: string };
}

export interface SSEExecuteDone {
  event: 'execute.done';
  data: {
    status: 'success' | 'error';
    sql: string;
    columns: string[];
    rows: unknown[][];
    row_count: number;
  };
}

export interface SSEChart {
  event: 'chart';
  data: { spec: Record<string, unknown> };
}

export interface SSEFollowUps {
  event: 'follow_ups';
  data: { questions: string[] };
}

export interface SSEStopped {
  event: 'stopped';
  data: { message: string };
}

export interface SSEDone {
  event: 'done';
  data: {
    run_id: string;
    conversation_id: string;
    question: string;
    question_type: string;
    stopped: boolean;
    needs_clarification: boolean;
    intent: string | null;
    resolved_filters: string | null;
    sql: string | null;
    columns: string[] | null;
    rows: unknown[][] | null;
    row_count: number | null;
    chart_spec: Record<string, unknown> | null;
    answer: string;
    follow_ups: string[];
    reasoning: string[];
  };
}

export interface SSEError {
  event: 'error';
  data: { message: string; conversation_id?: string };
}

export type SSEEvent =
  | SSETitleGenerated
  | SSENodeStart
  | SSEReasoningDelta
  | SSEAnswerDelta
  | SSEValidation
  | SSEExecuteDone
  | SSEChart
  | SSEFollowUps
  | SSEStopped
  | SSEDone
  | SSEError;
