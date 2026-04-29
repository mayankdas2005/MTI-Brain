import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import * as api from '../api';
import { streamSSE, SSEHandlers } from '../api/sse';
import type {
  ThreadSummary,
  SearchResult,
  MessageMetadata,
} from '../types/api';

// ─── Local Message type (superset of MessageOut for streaming state) ───

export interface StreamingStep {
  node: string;
  message: string;
  status: 'active' | 'done';
  timestamp: number;
}

export interface Message {
  id: string;
  conversation_id: string;
  parent_conversation_id?: string;
  source_conversation_id?: string;
  role: 'user' | 'assistant';
  content: string;
  reasoning?: string;
  reasoningPending?: boolean;
  metadata_?: MessageMetadata | null;
  created_at: string;
  isStreaming?: boolean;
  streamingSteps?: StreamingStep[];
  feedback?: { liked: boolean; comment?: string };
  /** Timing anchor from backend timing.sync event — lets LiveTimer
   *  track the server's elapsed clock instead of the client's wall clock. */
  _timingAnchor?: { serverElapsedMs: number; clientReceivedAt: number };
}

// `crypto.randomUUID` is only exposed on secure contexts (HTTPS or localhost).
// On plain-http deployments it's undefined, so fall back to a v4-shaped id built
// from `getRandomValues`, which is available everywhere. IDs here are only used
// as React keys / optimistic-message handles, not for anything cryptographic.
function randomId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/**
 * Parse reasoning from backend — may be a JSON array of step objects or a plain string.
 */
function parseReasoningArray(raw: unknown): { label?: string; text?: string }[] | null {
  if (!raw) return null;
  if (typeof raw === 'string') {
    try {
      const parsed = JSON.parse(raw);
      if (Array.isArray(parsed)) return parsed;
    } catch {
      // Not JSON
    }
    return null;
  }
  if (Array.isArray(raw)) return raw;
  return null;
}

function formatReasoning(steps: { label?: string; text?: string }[]): string {
  return steps
    .map((step) => {
      const heading = step.label ? `**${step.label}**\n\n` : '';
      return heading + (step.text || '');
    })
    .join('\n\n---\n\n');
}

function parseReasoning(raw: unknown): string | undefined {
  if (!raw) return undefined;
  const arr = parseReasoningArray(raw);
  if (arr) return formatReasoning(arr);
  return typeof raw === 'string' ? raw : String(raw);
}

/**
 * Reconstruct pipeline steps from reasoning data + message metadata.
 *
 * During live streaming all nodes emit `node.start` events, but those aren't
 * persisted. The reasoning array only contains nodes that produced reasoning
 * text — non-streaming nodes (classify, validate_sql, run_query, respond)
 * are missing. We fill in the gaps using metadata fields to determine which
 * nodes ran.
 */
function extractSteps(
  raw: unknown,
  content?: string,
  metadata?: MessageMetadata | null,
): StreamingStep[] | undefined {
  const arr = parseReasoningArray(raw);
  // If no reasoning data at all, check if there's content (general_chat path)
  if (!arr || arr.length === 0) {
    if (content) {
      return [{ node: 'responding', message: 'Responding', status: 'done', timestamp: 0 }];
    }
    return undefined;
  }

  const reasoningLabels = new Set(arr.filter((s) => s.label).map((s) => s.label!));

  const steps: StreamingStep[] = [];
  const addStep = (node: string, message: string) => {
    steps.push({ node, message, status: 'done' as const, timestamp: 0 });
  };

  // 1. Understanding your question (classify) — always runs first
  addStep('classify', 'Understanding your question');

  // 2. Resolving entities — from reasoning
  if (reasoningLabels.has('Resolving entities')) {
    addStep('resolve', 'Resolving entities');
  }

  // 3. Analyzing question and building query — from reasoning
  if (reasoningLabels.has('Analyzing question and building query')) {
    addStep('generate_sql', 'Analyzing question and building query');
  }

  // 4. Validating SQL syntax — ran if SQL exists
  if (metadata?.sql) {
    addStep('validate_sql', 'Validating SQL syntax');
  }

  // 5. Fetching results — ran if columns/rows exist
  if (metadata?.columns && metadata.columns.length > 0) {
    addStep('run_query', 'Fetching results');
  }

  // 5b. Validating results — from reasoning
  if (reasoningLabels.has('Validating results')) {
    addStep('validate_results', 'Validating results');
  }

  // 5c. Fix query + retry cycles — from reasoning
  for (const label of reasoningLabels) {
    if (label.startsWith('Fixing the query')) {
      addStep('fix_query', label);
    }
  }

  // 6. Preparing your answer — ran if message has content
  if (content) {
    addStep('respond', 'Preparing your answer');
  }

  return steps.length > 1 ? steps : undefined;
}

// ─── Store interface ───

interface ThreadStore {
  // Sidebar thread list
  threads: ThreadSummary[];
  searchResults: SearchResult[];
  isSearching: boolean;
  threadsLoading: boolean;
  searchQuery: string;
  threadsOffset: number;
  hasMore: boolean;
  threadsLastFetched: number;

  // Current thread detail
  currentThreadId: string | null;
  currentMessages: Message[];
  currentThreadTitle: string | null;
  currentThreadStarred: boolean;
  currentThreadProjectId: string | null;
  messagesLoading: boolean;

  // Streaming
  isStreaming: boolean;
  streamingMessageId: string | null;
  // Thread currently receiving the stream. Used by the chat page guard
  // so navigating to a different thread while one is generating doesn't
  // block the new thread's fetch — only skip when streaming INTO the
  // thread we're rendering.
  streamingThreadId: string | null;
  // Dedicated slot holding the user+assistant messages for the active
  // stream. The chat page renders from THIS slot whenever the viewed
  // thread matches streamingThreadId, so the stream stays visible even
  // if setCurrentThread clears currentMessages while navigating away.
  // Populated by askQuestion/retry/edit at stream start, updated by
  // every SSE handler, and cleared on onDone/onStopped/onError.
  streamingMessages: Message[];
  abortController: AbortController | null;

  // Pending question (from /new page)
  pendingQuestion: string | null;

  // Selection (bulk ops)
  selectedThreadIds: Set<string>;

  // ─── Actions ───

  // Fetch
  fetchRecents: (params?: { search?: string; project_id?: string; append?: boolean }) => Promise<void>;
  fetchThread: (threadId: string) => Promise<void>;

  // Thread CRUD
  createThread: (title?: string, projectId?: string) => Promise<string>;
  deleteThread: (threadId: string) => Promise<boolean>;
  starThread: (threadId: string) => Promise<void>;
  renameThread: (threadId: string, title: string) => Promise<void>;
  moveThread: (threadId: string, projectId: string | null) => Promise<void>;

  // Bulk
  bulkDeleteThreads: () => Promise<void>;
  bulkMoveThreads: (projectId: string | null) => Promise<void>;

  // Streaming
  askQuestion: (threadId: string, question: string, sourceConversationId?: string, priorSql?: string) => Promise<void>;
  retryResponse: (threadId: string, conversationId: string) => Promise<void>;
  editQuestion: (threadId: string, conversationId: string, question: string) => Promise<void>;
  stopGeneration: (threadId: string) => Promise<void>;

  // Feedback
  submitFeedback: (threadId: string, conversationId: string, liked: boolean, comment?: string) => Promise<void>;

  // Local UI
  setCurrentThread: (id: string | null) => void;
  setSearchQuery: (query: string) => void;
  setPendingQuestion: (question: string | null) => void;
  toggleThreadSelection: (id: string) => void;
  selectAllThreads: () => void;
  clearSelection: () => void;
}

// ─── Debounce helper ───

let searchTimer: ReturnType<typeof setTimeout> | null = null;

// ─── Store ───

export const useThreadStore = create<ThreadStore>()(persist((set, get) => ({
  // State
  threads: [],
  searchResults: [],
  isSearching: false,
  threadsLoading: false,
  searchQuery: '',
  threadsOffset: 0,
  hasMore: true,
  threadsLastFetched: 0,
  currentThreadId: null,
  currentMessages: [],
  currentThreadTitle: null,
  currentThreadStarred: false,
  currentThreadProjectId: null,
  messagesLoading: false,
  isStreaming: false,
  streamingMessageId: null,
  streamingThreadId: null,
  streamingMessages: [],
  abortController: null,
  pendingQuestion: null,
  selectedThreadIds: new Set(),

  // ─── Fetch ───

  fetchRecents: async (params) => {
    const { searchQuery, threadsOffset, threads } = get();
    const search = params?.search ?? searchQuery;
    const append = params?.append ?? false;
    const offset = append ? threadsOffset : 0;
    const limit = 20;

    set({ threadsLoading: true, isSearching: !!search });
    try {
      const result = await api.getRecents({
        search: search || undefined,
        project_id: params?.project_id,
        limit,
        offset,
      });

      if (search) {
        set({
          searchResults: result as SearchResult[],
          threadsLoading: false,
        });
      } else {
        const items = result as ThreadSummary[];
        set({
          threads: append ? [...threads, ...items] : items,
          searchResults: [],
          threadsOffset: offset + items.length,
          hasMore: items.length === limit,
          threadsLoading: false,
          isSearching: false,
          threadsLastFetched: Date.now(),
        });
      }
    } catch {
      set({ threadsLoading: false, isSearching: false });
    }
  },

  fetchThread: async (threadId) => {
    // Only show loading skeleton on first load, not on background revalidation
    const hasMessages = get().currentThreadId === threadId && get().currentMessages.length > 0;
    set({ messagesLoading: !hasMessages, currentThreadId: threadId });
    try {
      const detail = await api.getThread(threadId);
      const messages: Message[] = detail.messages.map((m) => ({
        id: m.id,
        conversation_id: m.conversation_id,
        parent_conversation_id: m.parent_conversation_id ?? undefined,
        source_conversation_id: m.role === 'user'
          ? (m.metadata_?.source_conversation_id as string | undefined)
          : undefined,
        role: m.role as 'user' | 'assistant',
        content: m.content,
        reasoning: parseReasoning(m.reasoning),
        streamingSteps: m.role === 'assistant' ? extractSteps(m.reasoning, m.content, m.metadata_) : undefined,
        metadata_: m.metadata_,
        feedback: m.feedback ? { liked: m.feedback.liked, comment: m.feedback.comment ?? undefined } : undefined,
        created_at: m.created_at,
      }));
      // Don't overwrite messages if askQuestion already populated them (race with pendingQuestion)
      const current = get().currentMessages;
      const shouldKeepMessages = current.length > 0 && messages.length === 0 && get().currentThreadId === threadId;
      set({
        currentMessages: shouldKeepMessages ? current : messages,
        currentThreadTitle: detail.title,
        currentThreadStarred: detail.starred,
        currentThreadProjectId: detail.project_id,
        messagesLoading: false,
      });
    } catch {
      set({ messagesLoading: false });
      throw new Error('Thread not found');
    }
  },

  // ─── Thread CRUD ───

  createThread: async (title, projectId) => {
    const res = await api.createThread({
      title,
      project_id: projectId,
    });
    // Refresh sidebar
    get().fetchRecents();
    // If the detail page for this project is currently open, refresh it so
    // the new thread appears immediately.
    if (projectId) {
      const { useProjectStore } = await import('./projects');
      useProjectStore.getState().refreshCurrentProjectIfMatches(projectId);
    }
    return res.thread_id;
  },

  deleteThread: async (threadId) => {
    const prev = get().threads;
    const prevSearchResults = get().searchResults;
    const thread = prev.find((t) => t.id === threadId);
    const affectedProjectId = thread?.project_id ?? null;
    const wasCurrent = get().currentThreadId === threadId;
    // Optimistic: remove from list + search results, clear if active
    set({
      threads: prev.filter((t) => t.id !== threadId),
      searchResults: prevSearchResults.filter((r) => r.thread_id !== threadId),
      selectedThreadIds: new Set([...get().selectedThreadIds].filter((id) => id !== threadId)),
      ...(wasCurrent ? { currentThreadId: null, currentMessages: [] } : {}),
    });
    try {
      await api.deleteThread(threadId);
      // Refresh project counts + detail if open
      const { useProjectStore } = await import('./projects');
      useProjectStore.getState().fetchProjects();
      useProjectStore.getState().refreshCurrentProjectIfMatches(affectedProjectId);
    } catch {
      set({ threads: prev, searchResults: prevSearchResults }); // rollback
    }
    return wasCurrent;
  },

  starThread: async (threadId) => {
    const prev = get().threads;
    // Optimistic toggle
    set({
      threads: prev.map((t) =>
        t.id === threadId ? { ...t, starred: !t.starred } : t,
      ),
    });
    if (get().currentThreadId === threadId) {
      set({ currentThreadStarred: !get().currentThreadStarred });
    }
    // Mirror the toggle into currentProject.threads if the open project
    // contains this thread.
    const affectedProjectId = prev.find((t) => t.id === threadId)?.project_id ?? null;
    if (affectedProjectId) {
      const { useProjectStore } = await import('./projects');
      const cur = useProjectStore.getState().currentProject;
      if (cur?.id === affectedProjectId) {
        useProjectStore.setState({
          currentProject: {
            ...cur,
            threads: cur.threads.map((t) =>
              t.id === threadId ? { ...t, starred: !t.starred } : t,
            ),
          },
        });
      }
    }
    try {
      await api.starThread(threadId);
    } catch {
      set({ threads: prev }); // rollback
    }
  },

  renameThread: async (threadId, title) => {
    const prev = get().threads;
    set({
      threads: prev.map((t) =>
        t.id === threadId ? { ...t, title } : t,
      ),
    });
    if (get().currentThreadId === threadId) {
      set({ currentThreadTitle: title });
    }
    // Mirror rename into currentProject.threads if it's the open project.
    const affectedProjectId = prev.find((t) => t.id === threadId)?.project_id ?? null;
    if (affectedProjectId) {
      const { useProjectStore } = await import('./projects');
      const cur = useProjectStore.getState().currentProject;
      if (cur?.id === affectedProjectId) {
        useProjectStore.setState({
          currentProject: {
            ...cur,
            threads: cur.threads.map((t) =>
              t.id === threadId ? { ...t, title } : t,
            ),
          },
        });
      }
    }
    try {
      await api.renameThread(threadId, title);
    } catch {
      set({ threads: prev }); // rollback
    }
  },

  moveThread: async (threadId, projectId) => {
    const prev = get().threads;
    const fromProjectId = prev.find((t) => t.id === threadId)?.project_id ?? null;
    await api.moveThread(threadId, projectId);
    get().fetchRecents();
    // Refresh project counts + whichever project detail is currently open
    // (could be the source or destination).
    const { useProjectStore } = await import('./projects');
    useProjectStore.getState().fetchProjects();
    useProjectStore.getState().refreshCurrentProjectIfMatches(fromProjectId);
    useProjectStore.getState().refreshCurrentProjectIfMatches(projectId);
  },

  // ─── Bulk ───

  bulkDeleteThreads: async () => {
    const ids = [...get().selectedThreadIds];
    if (ids.length === 0) return;

    const prev = get().threads;
    const prevSearchResults = get().searchResults;
    const affectedProjectIds = new Set(
      prev.filter((t) => ids.includes(t.id)).map((t) => t.project_id).filter(Boolean) as string[],
    );
    set({
      threads: prev.filter((t) => !ids.includes(t.id)),
      searchResults: prevSearchResults.filter((r) => !ids.includes(r.thread_id)),
      selectedThreadIds: new Set(),
    });
    try {
      await api.bulkDeleteThreads(ids);
      if (ids.includes(get().currentThreadId ?? '')) {
        set({ currentThreadId: null, currentMessages: [] });
      }
      // Refresh project counts + detail if open
      const { useProjectStore } = await import('./projects');
      useProjectStore.getState().fetchProjects();
      for (const pid of affectedProjectIds) {
        useProjectStore.getState().refreshCurrentProjectIfMatches(pid);
      }
    } catch {
      set({ threads: prev, searchResults: prevSearchResults });
    }
  },

  bulkMoveThreads: async (projectId) => {
    const ids = [...get().selectedThreadIds];
    if (ids.length === 0) return;
    const prev = get().threads;
    const affectedProjectIds = new Set(
      prev.filter((t) => ids.includes(t.id)).map((t) => t.project_id).filter(Boolean) as string[],
    );
    await api.bulkMoveThreads(ids, projectId);
    set({ selectedThreadIds: new Set() });
    get().fetchRecents();
    // Refresh project counts + detail if open (source or destination)
    const { useProjectStore } = await import('./projects');
    useProjectStore.getState().fetchProjects();
    for (const pid of affectedProjectIds) {
      useProjectStore.getState().refreshCurrentProjectIfMatches(pid);
    }
    useProjectStore.getState().refreshCurrentProjectIfMatches(projectId);
  },

  // ─── Streaming ───

  askQuestion: async (threadId, question, sourceConversationId?, priorSql?) => {
    const { currentMessages } = get();
    const userMsgId = randomId();
    const assistantMsgId = randomId();

    const userMsg: Message = {
      id: userMsgId,
      conversation_id: '', // will be assigned by backend
      role: 'user',
      content: question,
      created_at: new Date().toISOString(),
      source_conversation_id: sourceConversationId,
    };

    const assistantMsg: Message = {
      id: assistantMsgId,
      conversation_id: '',
      role: 'assistant',
      content: '',
      reasoning: '',
      created_at: new Date().toISOString(),
      isStreaming: true,
    };

    const controller = new AbortController();

    // streamingMessages holds the FULL thread (history + new turn) so the
    // chat page can render the whole conversation from this slot while the
    // stream is in flight. Only the new user/assistant messages change
    // inside — the historical ones pass through unchanged.
    const nextMessages = [...currentMessages, userMsg, assistantMsg];
    set({
      currentMessages: nextMessages,
      streamingMessages: nextMessages,
      isStreaming: true,
      streamingMessageId: assistantMsgId,
      streamingThreadId: threadId,
      abortController: controller,
      pendingQuestion: null,
    });

    // Dual-write helper: every SSE update lands in BOTH currentMessages
    // (happy path render) AND streamingMessages (survives any clear of
    // currentMessages that happens while navigating away, so returning
    // to the streaming thread shows it live).
    const mapMsgs = (mapper: (m: Message) => Message) => {
      set((state) => ({
        currentMessages: state.currentMessages.map(mapper),
        streamingMessages: state.streamingMessages.map(mapper),
      }));
    };

    const handlers: SSEHandlers = {
      onTimingSync: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, _timingAnchor: { serverElapsedMs: data.elapsed_ms, clientReceivedAt: Date.now() } }
            : m,
        );
      },
      onTitleGenerated: (data) => {
        set({
          currentThreadTitle: data.title,
          threads: get().threads.map((t) =>
            t.id === threadId ? { ...t, title: data.title } : t,
          ),
        });
      },
      onNodeStart: (data) => {
        mapMsgs((m) => {
          if (m.id !== assistantMsgId) return m;
          const prev = (m.streamingSteps || []).map((s) =>
            s.status === 'active' ? { ...s, status: 'done' as const } : s,
          );
          return {
            ...m,
            streamingSteps: [
              ...prev,
              { node: data.node, message: data.message, status: 'active' as const, timestamp: Date.now() },
            ],
          };
        });
      },
      onAnswerDelta: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId ? { ...m, content: m.content + data.text } : m,
        );
      },
      onReasoningPending: () => {
        mapMsgs((m) =>
          m.id === assistantMsgId ? { ...m, reasoningPending: true } : m,
        );
      },
      onReasoningDelta: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, reasoning: (m.reasoning || '') + data.text, reasoningPending: false }
            : m,
        );
      },
      onExecuteDone: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? {
                ...m,
                metadata_: {
                  ...m.metadata_,
                  sql: data.sql,
                  columns: data.columns,
                  rows: data.rows,
                  row_count: data.row_count,
                },
              }
            : m,
        );
      },
      onChart: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, metadata_: { ...m.metadata_, chart_spec: data.spec } }
            : m,
        );
      },
      onFollowUps: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, metadata_: { ...m.metadata_, follow_ups: data.questions } }
            : m,
        );
      },
      onDone: (data) => {
        const convId = data.conversation_id as string;
        const mapper = (m: Message): Message => {
          if (m.id === assistantMsgId) {
            return {
              ...m,
              conversation_id: convId,
              content: m.content || ((data.answer as string) ?? m.content),
              isStreaming: false,
              streamingSteps: (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })),
              metadata_: {
                ...m.metadata_,
                run_id: data.run_id as string,
                sql: (data.sql as string) ?? m.metadata_?.sql,
                columns: (data.columns as string[]) ?? m.metadata_?.columns,
                rows: (data.rows as unknown[][]) ?? m.metadata_?.rows,
                row_count: (data.row_count as number) ?? m.metadata_?.row_count,
                chart_spec: (data.chart_spec as Record<string, unknown>) ?? m.metadata_?.chart_spec,
                follow_ups: (data.follow_ups as string[]) ?? m.metadata_?.follow_ups,
                duration_ms: (data.duration_ms as number) ?? m.metadata_?.duration_ms,
              },
            };
          }
          if (m.id === userMsgId) return { ...m, conversation_id: convId };
          return m;
        };
        set((state) => ({
          currentMessages: state.currentMessages.map(mapper),
          streamingMessages: [],
          isStreaming: false,
          streamingMessageId: null,
          streamingThreadId: null,
          abortController: null,
        }));
        get().fetchRecents();
        // If this thread belongs to the project currently open in the
        // detail page, refresh it so the new conversation appears there.
        const pid = get().currentThreadProjectId;
        if (pid) {
          import('./projects').then(({ useProjectStore }) => {
            useProjectStore.getState().refreshCurrentProjectIfMatches(pid);
          });
        }
      },
      onStopped: () => {
        set((state) => ({
          currentMessages: state.currentMessages.map((m) =>
            m.id === assistantMsgId
              ? { ...m, isStreaming: false, metadata_: { ...m.metadata_, stopped: true } }
              : m,
          ),
          streamingMessages: [],
          isStreaming: false,
          streamingMessageId: null,
          streamingThreadId: null,
          abortController: null,
        }));
      },
      onError: (data) => {
        set((state) => ({
          currentMessages: state.currentMessages.map((m) =>
            m.id === assistantMsgId
              ? {
                  ...m,
                  content: data.message || 'Something went wrong. Please try again.',
                  isStreaming: false,
                  conversation_id: data.conversation_id || '',
                }
              : m,
          ),
          streamingMessages: [],
          isStreaming: false,
          streamingMessageId: null,
          streamingThreadId: null,
          abortController: null,
        }));
      },
    };

    try {
      // Include user preferences in the request
      const { usePreferencesStore } = await import('./preferences');
      const prefs = usePreferencesStore.getState();
      const askBody: Record<string, unknown> = {
        question,
        response_tone: prefs.responseTone,
        max_rows: prefs.maxResultRows,
      };
      if (sourceConversationId) askBody.source_conversation_id = sourceConversationId;
      if (priorSql) askBody.prior_sql = priorSql;
      await streamSSE(`/chat/${threadId}/ask`, askBody, handlers, controller.signal);
    } catch (err: unknown) {
      if ((err as Error).name === 'AbortError') {
        // User cancelled — already handled by onStopped if backend confirmed
      } else {
        // Stream broke mid-flight. If reasoning or answer already streamed,
        // preserve it and mark the message as interrupted instead of wiping
        // everything to a generic error.
        const msg = get().currentMessages.find((m) => m.id === assistantMsgId);
        const hasPartial =
          !!(msg?.content && msg.content.trim()) ||
          !!(msg?.reasoning && msg.reasoning.trim());
        if (hasPartial) {
          set((state) => ({
            currentMessages: state.currentMessages.map((m) =>
              m.id === assistantMsgId
                ? {
                    ...m,
                    isStreaming: false,
                    metadata_: { ...m.metadata_, interrupted: true },
                    streamingSteps: (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })),
                  }
                : m,
            ),
            streamingMessages: [],
            isStreaming: false,
            streamingMessageId: null,
            streamingThreadId: null,
            abortController: null,
          }));
        } else {
          handlers.onError?.({ message: 'Failed to get a response' });
        }
      }
    } finally {
      // Safety net: if stream ended but onDone/onError/onStopped never fired, clean up
      if (get().streamingMessageId === assistantMsgId) {
        set((state) => ({
          currentMessages: state.currentMessages.map((m) =>
            m.id === assistantMsgId
              ? { ...m, isStreaming: false, streamingSteps: (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })) }
              : m,
          ),
          streamingMessages: [],
          isStreaming: false,
          streamingMessageId: null,
          streamingThreadId: null,
          abortController: null,
        }));
      }
    }
  },

  retryResponse: async (threadId, conversationId) => {
    const { currentMessages } = get();

    // Resolve to root conversation_id for version branching
    const anyMsg = currentMessages.find((m) => m.conversation_id === conversationId);
    const rootConvId = anyMsg?.parent_conversation_id || conversationId;

    // Find the original user message
    const originalUserMsg = currentMessages.find(
      (m) => m.conversation_id === conversationId && m.role === 'user',
    ) || currentMessages.find(
      (m) => m.conversation_id === rootConvId && m.role === 'user',
    );

    const userMsgId = randomId();
    const assistantMsgId = randomId();

    const userMsg: Message = {
      id: userMsgId,
      conversation_id: '',
      parent_conversation_id: rootConvId,
      role: 'user',
      content: originalUserMsg?.content || '',
      created_at: new Date().toISOString(),
    };

    const assistantMsg: Message = {
      id: assistantMsgId,
      conversation_id: '',
      parent_conversation_id: rootConvId,
      role: 'assistant',
      content: '',
      reasoning: '',
      created_at: new Date().toISOString(),
      isStreaming: true,
    };

    const controller = new AbortController();

    // streamingMessages holds the FULL thread (history + new turn) so the
    // chat page can render the whole conversation from this slot while the
    // stream is in flight. Only the new user/assistant messages change
    // inside — the historical ones pass through unchanged.
    const nextMessages = [...currentMessages, userMsg, assistantMsg];
    set({
      currentMessages: nextMessages,
      streamingMessages: nextMessages,
      isStreaming: true,
      streamingMessageId: assistantMsgId,
      streamingThreadId: threadId,
      abortController: controller,
    });

    const mapMsgs = (mapper: (m: Message) => Message) => {
      set((state) => ({
        currentMessages: state.currentMessages.map(mapper),
        streamingMessages: state.streamingMessages.map(mapper),
      }));
    };

    const handlers: SSEHandlers = {
      onTimingSync: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, _timingAnchor: { serverElapsedMs: data.elapsed_ms, clientReceivedAt: Date.now() } }
            : m,
        );
      },
      onNodeStart: (data) => {
        mapMsgs((m) => {
          if (m.id !== assistantMsgId) return m;
          const prev = (m.streamingSteps || []).map((s) =>
            s.status === 'active' ? { ...s, status: 'done' as const } : s,
          );
          return {
            ...m,
            streamingSteps: [
              ...prev,
              { node: data.node, message: data.message, status: 'active' as const, timestamp: Date.now() },
            ],
          };
        });
      },
      onAnswerDelta: (data) => {
        mapMsgs((m) => (m.id === assistantMsgId ? { ...m, content: m.content + data.text } : m));
      },
      onReasoningPending: () => {
        mapMsgs((m) => (m.id === assistantMsgId ? { ...m, reasoningPending: true } : m));
      },
      onReasoningDelta: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, reasoning: (m.reasoning || '') + data.text, reasoningPending: false }
            : m,
        );
      },
      onExecuteDone: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, metadata_: { ...m.metadata_, sql: data.sql, columns: data.columns, rows: data.rows, row_count: data.row_count } }
            : m,
        );
      },
      onChart: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId ? { ...m, metadata_: { ...m.metadata_, chart_spec: data.spec } } : m,
        );
      },
      onFollowUps: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId ? { ...m, metadata_: { ...m.metadata_, follow_ups: data.questions } } : m,
        );
      },
      onDone: (data) => {
        const convId = data.conversation_id as string;
        const mapper = (m: Message): Message => {
          if (m.id === assistantMsgId) {
            return {
              ...m,
              conversation_id: convId,
              content: m.content || ((data.answer as string) ?? m.content),
              isStreaming: false,
              streamingSteps: (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })),
              metadata_: {
                ...m.metadata_,
                run_id: data.run_id as string,
                sql: (data.sql as string) ?? m.metadata_?.sql,
                columns: (data.columns as string[]) ?? m.metadata_?.columns,
                rows: (data.rows as unknown[][]) ?? m.metadata_?.rows,
                row_count: (data.row_count as number) ?? m.metadata_?.row_count,
                chart_spec: (data.chart_spec as Record<string, unknown>) ?? m.metadata_?.chart_spec,
                follow_ups: (data.follow_ups as string[]) ?? m.metadata_?.follow_ups,
                duration_ms: (data.duration_ms as number) ?? m.metadata_?.duration_ms,
              },
            };
          }
          if (m.id === userMsgId) return { ...m, conversation_id: convId };
          return m;
        };
        set((state) => ({
          currentMessages: state.currentMessages.map(mapper),
          streamingMessages: [],
          isStreaming: false,
          streamingMessageId: null,
          streamingThreadId: null,
          abortController: null,
        }));
        get().fetchRecents();
      },
      onStopped: () => {
        set((state) => ({
          currentMessages: state.currentMessages.map((m) =>
            m.id === assistantMsgId
              ? { ...m, isStreaming: false, streamingSteps: (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })) }
              : m,
          ),
          streamingMessages: [],
          isStreaming: false,
          streamingMessageId: null,
          streamingThreadId: null,
          abortController: null,
        }));
      },
      onError: (data) => {
        set((state) => ({
          currentMessages: state.currentMessages.map((m) =>
            m.id === assistantMsgId
              ? { ...m, content: data.message || 'Retry failed.', isStreaming: false, streamingSteps: (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })) }
              : m,
          ),
          streamingMessages: [],
          isStreaming: false,
          streamingMessageId: null,
          streamingThreadId: null,
          abortController: null,
        }));
      },
    };

    try {
      await streamSSE(`/chat/${threadId}/retry`, { conversation_id: conversationId }, handlers, controller.signal);
    } catch (err: unknown) {
      if ((err as Error).name !== 'AbortError') {
        handlers.onError?.({ message: 'Retry failed' });
      }
    } finally {
      if (get().streamingMessageId === assistantMsgId) {
        set((state) => ({
          currentMessages: state.currentMessages.map((m) =>
            m.id === assistantMsgId
              ? { ...m, isStreaming: false, streamingSteps: (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })) }
              : m,
          ),
          streamingMessages: [],
          isStreaming: false,
          streamingMessageId: null,
          streamingThreadId: null,
          abortController: null,
        }));
      }
    }
  },

  editQuestion: async (threadId, conversationId, question) => {
    const { currentMessages } = get();

    // Resolve to root conversation_id for version branching
    const anyMsg = currentMessages.find((m) => m.conversation_id === conversationId);
    const rootConvId = anyMsg?.parent_conversation_id || conversationId;

    const userMsgId = randomId();
    const assistantMsgId = randomId();
    const tempConvId = `temp:${userMsgId}`;

    const userMsg: Message = {
      id: userMsgId,
      conversation_id: tempConvId,
      parent_conversation_id: rootConvId,
      role: 'user',
      content: question,
      created_at: new Date().toISOString(),
    };

    const assistantMsg: Message = {
      id: assistantMsgId,
      conversation_id: tempConvId,
      parent_conversation_id: rootConvId,
      role: 'assistant',
      content: '',
      reasoning: '',
      created_at: new Date().toISOString(),
      isStreaming: true,
    };

    const controller = new AbortController();

    // streamingMessages holds the FULL thread (history + new turn) so the
    // chat page can render the whole conversation from this slot while the
    // stream is in flight. Only the new user/assistant messages change
    // inside — the historical ones pass through unchanged.
    const nextMessages = [...currentMessages, userMsg, assistantMsg];
    set({
      currentMessages: nextMessages,
      streamingMessages: nextMessages,
      isStreaming: true,
      streamingMessageId: assistantMsgId,
      streamingThreadId: threadId,
      abortController: controller,
    });

    const mapMsgs = (mapper: (m: Message) => Message) => {
      set((state) => ({
        currentMessages: state.currentMessages.map(mapper),
        streamingMessages: state.streamingMessages.map(mapper),
      }));
    };

    const handlers: SSEHandlers = {
      onTimingSync: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, _timingAnchor: { serverElapsedMs: data.elapsed_ms, clientReceivedAt: Date.now() } }
            : m,
        );
      },
      onTitleGenerated: (data) => {
        set({
          currentThreadTitle: data.title,
          threads: get().threads.map((t) =>
            t.id === threadId ? { ...t, title: data.title } : t,
          ),
        });
      },
      onNodeStart: (data) => {
        mapMsgs((m) => {
          if (m.id !== assistantMsgId) return m;
          const prev = (m.streamingSteps || []).map((s) =>
            s.status === 'active' ? { ...s, status: 'done' as const } : s,
          );
          return {
            ...m,
            streamingSteps: [
              ...prev,
              { node: data.node, message: data.message, status: 'active' as const, timestamp: Date.now() },
            ],
          };
        });
      },
      onAnswerDelta: (data) => {
        mapMsgs((m) => (m.id === assistantMsgId ? { ...m, content: m.content + data.text } : m));
      },
      onReasoningPending: () => {
        mapMsgs((m) => (m.id === assistantMsgId ? { ...m, reasoningPending: true } : m));
      },
      onReasoningDelta: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, reasoning: (m.reasoning || '') + data.text, reasoningPending: false }
            : m,
        );
      },
      onExecuteDone: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, metadata_: { ...m.metadata_, sql: data.sql, columns: data.columns, rows: data.rows, row_count: data.row_count } }
            : m,
        );
      },
      onChart: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId ? { ...m, metadata_: { ...m.metadata_, chart_spec: data.spec } } : m,
        );
      },
      onFollowUps: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId ? { ...m, metadata_: { ...m.metadata_, follow_ups: data.questions } } : m,
        );
      },
      onDone: (data) => {
        const convId = data.conversation_id as string;
        const mapper = (m: Message): Message => {
          if (m.id === assistantMsgId) return { ...m, conversation_id: convId, content: m.content || ((data.answer as string) ?? m.content), isStreaming: false, streamingSteps: (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })), metadata_: { ...m.metadata_, duration_ms: (data.duration_ms as number) ?? m.metadata_?.duration_ms } };
          if (m.id === userMsgId) return { ...m, conversation_id: convId };
          return m;
        };
        set((state) => ({
          currentMessages: state.currentMessages.map(mapper),
          streamingMessages: [],
          isStreaming: false,
          streamingMessageId: null,
          streamingThreadId: null,
          abortController: null,
        }));
        get().fetchRecents();
      },
      onStopped: () => {
        set((state) => ({
          currentMessages: state.currentMessages.map((m) =>
            m.id === assistantMsgId ? { ...m, isStreaming: false, streamingSteps: (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })) } : m,
          ),
          streamingMessages: [],
          isStreaming: false,
          streamingMessageId: null,
          streamingThreadId: null,
          abortController: null,
        }));
      },
      onError: (data) => {
        set((state) => ({
          currentMessages: state.currentMessages.map((m) =>
            m.id === assistantMsgId
              ? { ...m, content: data.message || 'Edit failed.', isStreaming: false, streamingSteps: (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })) }
              : m,
          ),
          streamingMessages: [],
          isStreaming: false,
          streamingMessageId: null,
          streamingThreadId: null,
          abortController: null,
        }));
      },
    };

    try {
      await streamSSE(
        `/chat/${threadId}/edit`,
        { conversation_id: conversationId, question },
        handlers,
        controller.signal,
      );
    } catch (err: unknown) {
      if ((err as Error).name !== 'AbortError') {
        handlers.onError?.({ message: 'Edit failed' });
      }
    } finally {
      if (get().streamingMessageId === assistantMsgId) {
        set((state) => ({
          currentMessages: state.currentMessages.map((m) =>
            m.id === assistantMsgId
              ? { ...m, isStreaming: false, streamingSteps: (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })) }
              : m,
          ),
          streamingMessages: [],
          isStreaming: false,
          streamingMessageId: null,
          streamingThreadId: null,
          abortController: null,
        }));
      }
    }
  },

  stopGeneration: async (threadId) => {
    const { abortController } = get();
    abortController?.abort();
    try {
      await api.stopGeneration(threadId);
    } catch {
      // Ignore errors on stop
    }
    set({
      isStreaming: false,
      streamingThreadId: null,
      streamingMessages: [],
      abortController: null,
    });
  },

  // ─── Feedback ───

  submitFeedback: async (threadId, conversationId, liked, comment) => {
    const result = await api.submitFeedback(threadId, conversationId, { liked, comment });
    // Update local message feedback state
    const msgs = get().currentMessages;
    set({
      currentMessages: msgs.map((m) =>
        m.conversation_id === conversationId && m.role === 'assistant'
          ? { ...m, feedback: { liked: result.liked, comment: result.comment ?? undefined } }
          : m,
      ),
    });
  },

  // ─── Local UI ───

  setCurrentThread: (id) => {
    const state = get();
    const { currentThreadId, isStreaming, streamingThreadId, currentMessages, streamingMessages } = state;

    const log = (decision: string) => {
      if (process.env.NODE_ENV !== 'production') {
        // eslint-disable-next-line no-console
        console.debug('[threads] setCurrentThread', {
          from: currentThreadId,
          to: id,
          decision,
          isStreaming,
          streamingThreadId,
          currentMessages: currentMessages.length,
          streamingMessages: streamingMessages.length,
        });
      }
    };

    // No-op when already on this thread.
    if (id === currentThreadId) {
      log('no-op-same-thread');
      return;
    }

    // IMPORTANT: Do NOT abort an in-progress stream when navigating away.
    // The SSE keeps running in the background; backend persists the final
    // message so the user sees the completed answer when they return.
    // The composer stays disabled across all threads while a stream runs
    // (matches Claude.ai — only one in-flight request at a time).

    const leavingStreamingThread =
      currentThreadId !== null && isStreaming && streamingThreadId === currentThreadId;

    if (id === null) {
      // Going to a non-thread view (/projects, /new, etc). If we're leaving
      // a thread that's still streaming, keep its messages + title in state
      // so the live SSE handlers keep writing to them and the user sees the
      // in-progress stream when they return. Clearing currentThreadId is
      // enough to drop sidebar highlight.
      if (leavingStreamingThread) {
        log('leave-to-null-preserve-stream');
        set({ currentThreadId: null });
        return;
      }
      log('leave-to-null-clear');
      set({
        currentThreadId: null,
        currentMessages: [],
        currentThreadTitle: null,
        currentThreadStarred: false,
        currentThreadProjectId: null,
      });
      return;
    }

    // Switching to a specific thread. If we previously preserved messages
    // while leaving a streaming thread, and we're returning to THAT same
    // thread, keep them so the page renders instantly without re-fetching.
    const returningToPreservedStream =
      currentThreadId === null &&
      isStreaming &&
      streamingThreadId === id &&
      currentMessages.length > 0;

    if (returningToPreservedStream) {
      log('return-to-preserved-stream');
      set({ currentThreadId: id });
      return;
    }

    // Otherwise (different thread, or no preserved state), clear the old
    // thread's display so fetchThread shows its skeleton and populates
    // the new thread's messages.
    log('switch-thread-clear');
    set({
      currentThreadId: id,
      currentMessages: [],
      currentThreadTitle: null,
      currentThreadStarred: false,
      currentThreadProjectId: null,
    });
  },

  setSearchQuery: (query) => {
    set({ searchQuery: query });
    if (searchTimer) clearTimeout(searchTimer);
    searchTimer = setTimeout(() => {
      get().fetchRecents({ search: query });
    }, 300);
  },

  setPendingQuestion: (question) => {
    set({ pendingQuestion: question });
  },

  toggleThreadSelection: (id) => {
    const selected = new Set(get().selectedThreadIds);
    if (selected.has(id)) {
      selected.delete(id);
    } else {
      selected.add(id);
    }
    set({ selectedThreadIds: selected });
  },

  selectAllThreads: () => {
    set({ selectedThreadIds: new Set(get().threads.map((t) => t.id)) });
  },

  clearSelection: () => {
    set({ selectedThreadIds: new Set() });
  },
}), {
  name: 'quest-threads-cache',
  storage: createJSONStorage(() => localStorage),
  // Only persist cacheable list data — never persist streaming/SSE state,
  // active controllers, or per-thread message buffers (those refresh on
  // demand from the backend).
  partialize: (state) => ({
    threads: state.threads,
    threadsLastFetched: state.threadsLastFetched,
    threadsOffset: state.threadsOffset,
    hasMore: state.hasMore,
  }),
}));
