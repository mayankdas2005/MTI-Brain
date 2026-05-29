/**
 * POST-based SSE stream parser for backend streaming endpoints.
 * Uses fetch + ReadableStream (EventSource only supports GET).
 */

import { apiBase, ApiError } from './client';
import { getAuthHeaders } from '@/lib/auth';

export interface SSEHandlers {
  onTimingSync?: (data: { elapsed_ms: number }) => void;
  onTitleGenerated?: (data: { thread_id: string; title: string }) => void;
  onNodeStart?: (data: { node: string; message: string; started_at_ms?: number }) => void;
  onReasoningPending?: (data: { node: string }) => void;
  onReasoningDelta?: (data: { node: string; text: string }) => void;
  onAnswerDelta?: (data: { node: string; text: string }) => void;
  onSparqlGenerated?: (data: { sql: string }) => void;
  onValidation?: (data: { status: string; message: string }) => void;
  onExecuteDone?: (data: {
    status: 'success' | 'error';
    sql: string;
    columns: string[];
    rows: unknown[][];
    row_count: number;
    will_visualize?: boolean;
    /** Trust-strip fields. Backend populates them when available
     *  (sqlglot for source_tables, Snowflake/catalog later). */
    source_tables?: string[];
    data_freshness_at?: string;
    metric_name?: string | null;
    metric_owner?: string | null;
    metric_defined_at?: string | null;
  }) => void;
  onNodeDone?: (data: { node: string; duration_ms: number }) => void;
  onChart?: (data: { spec: Record<string, unknown>; chart_type?: string; alternative_chart_specs?: string[] }) => void;
  onVizSkip?: () => void;
  onFollowUps?: (data: { questions: string[] }) => void;
  onStopped?: (data: { message: string; conversation_id?: string; pipeline_steps?: unknown; duration_ms?: number }) => void;
  onDone?: (data: Record<string, unknown>) => void;
  onError?: (data: { message: string; conversation_id?: string }) => void;
}

/**
 * Stream SSE events from a POST endpoint.
 * Parses the standard SSE protocol (event: / data: lines, blank line dispatch).
 */
export async function streamSSE(
  path: string,
  body: object,
  handlers: SSEHandlers,
  signal?: AbortSignal,
): Promise<void> {
  const url = `${apiBase}${path}`;

  const res = await fetch(url, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Accept: 'text/event-stream',
      ...getAuthHeaders(),
    },
    body: JSON.stringify(body),
    signal,
  });

  if (!res.ok) {
    let errBody: unknown;
    try {
      errBody = await res.json();
    } catch {
      errBody = await res.text();
    }
    throw new ApiError(res.status, errBody);
  }

  const reader = res.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let currentEvent = '';
  let currentData = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    // Keep the last incomplete line in the buffer
    buffer = lines.pop() || '';

    for (const rawLine of lines) {
      // Strip trailing \r for backends that send \r\n line endings
      const line = rawLine.replace(/\r$/, '');

      if (line.startsWith('event:')) {
        currentEvent = line.slice(6).trim();
      } else if (line.startsWith('data:')) {
        // SSE spec: multi-line data fields are joined with newlines
        if (currentData) currentData += '\n';
        currentData += line.slice(5).trim();
      } else if (line === '' && currentEvent) {
        // Blank line = dispatch event
        dispatchEvent(currentEvent, currentData, handlers);
        currentEvent = '';
        currentData = '';
      }
    }
  }

  // Flush any remaining event
  if (currentEvent && currentData) {
    dispatchEvent(currentEvent, currentData, handlers);
  }
}

function dispatchEvent(event: string, rawData: string, handlers: SSEHandlers) {
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(rawData);
  } catch {
    return; // skip malformed data
  }

  switch (event) {
    case 'timing.sync':
      handlers.onTimingSync?.(data as { elapsed_ms: number });
      break;
    case 'title.generated':
      handlers.onTitleGenerated?.(data as { thread_id: string; title: string });
      break;
    case 'node.start':
      handlers.onNodeStart?.(data as { node: string; message: string; started_at_ms?: number });
      break;
    case 'reasoning.pending':
      handlers.onReasoningPending?.(data as { node: string });
      break;
    case 'reasoning.delta':
      handlers.onReasoningDelta?.(data as { node: string; text: string });
      break;
    case 'answer.delta':
      handlers.onAnswerDelta?.(data as { node: string; text: string });
      break;
    case 'generate_sql':
      handlers.onSparqlGenerated?.(data as { sql: string });
      break;
    case 'validation':
      handlers.onValidation?.(data as { status: string; message: string });
      break;
    case 'execute.done':
      handlers.onExecuteDone?.(data as {
        status: 'success' | 'error';
        sql: string;
        columns: string[];
        rows: unknown[][];
        row_count: number;
        source_tables?: string[];
        data_freshness_at?: string;
        metric_name?: string | null;
        metric_owner?: string | null;
        metric_defined_at?: string | null;
      });
      break;
    case 'node.done':
      handlers.onNodeDone?.(data as { node: string; duration_ms: number });
      break;
    case 'chart':
      handlers.onChart?.(data as { spec: Record<string, unknown>; chart_type?: string; alternative_chart_specs?: { chart_type: string; spec: Record<string, unknown> }[] });
      break;
    case 'viz.skip':
      handlers.onVizSkip?.();
      break;
    case 'follow_ups':
      handlers.onFollowUps?.(data as { questions: string[] });
      break;
    case 'stopped':
      handlers.onStopped?.(data as { message: string; conversation_id?: string; pipeline_steps?: unknown; duration_ms?: number });
      break;
    case 'done':
      handlers.onDone?.(data);
      break;
    case 'error':
      handlers.onError?.(data as { message: string; conversation_id?: string });
      break;
  }
}
