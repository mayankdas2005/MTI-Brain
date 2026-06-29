import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import * as api from '../api';

// Gate that chat-composer awaits before firing askQuestion on a new thread.
// Set by new-chat-composer when it pre-generates a thread ID and navigates
// before the creation API has responded. Cleared after consumption.
let _threadCreationGate: Promise<void> | null = null;
export function setThreadCreationGate(p: Promise<void> | null) { _threadCreationGate = p; }
export function getThreadCreationGate() { return _threadCreationGate; }
export function isThreadCreationPending() { return _threadCreationGate !== null; }
import { streamSSE, SSEHandlers } from '../api/sse';
import type {
  ThreadSummary,
  SearchResult,
  MessageMetadata,
  PipelineStep,
  TokenUsage,
  PreferenceSummary,
} from '../types/api';

// ─── Local Message type (superset of MessageOut for streaming state) ───

export interface StreamingStep {
  node: string;
  message: string;
  status: 'active' | 'done' | 'skipped' | 'error';
  /** Wall-clock timestamp the client received node.start (used as a tiebreaker). */
  timestamp: number;
  /** Server-relative ms when this step started. Authoritative for ordering. */
  started_at_ms?: number;
  /** Final duration in ms once the step closes. Null while active. */
  duration_ms?: number | null;
  /** Total LLM tokens consumed by this step (input + output). 0 for deterministic nodes. */
  total_tokens?: number;
  /** Reasoning text emitted while this step was active. */
  reasoning?: string;
  /** @deprecated context_summary removed — preference_summary is on MessageMetadata */
  context_summary?: never;
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
  dataReady?: boolean;
  chartReady?: boolean;
  willVisualize?: boolean;
  followUpsReady?: boolean;
  streamingSteps?: StreamingStep[];
  feedback?: { liked: boolean; comment?: string };
  /** Timing anchor from backend timing.sync event - lets LiveTimer
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
 * Parse reasoning from backend - may be a JSON array of step objects or a plain string.
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
 * Hydrate streamingSteps from a persisted message.
 *
 * The backend stores authoritative pipeline_steps on metadata_. This is the
 * preferred source - one entry per node with timing and per-step reasoning.
 *
 * For older messages persisted before pipeline_steps existed, fall back to
 * a heuristic reconstruction from the legacy reasoning array + metadata.
 */
function extractSteps(
  raw: unknown,
  content?: string,
  metadata?: MessageMetadata | null,
): StreamingStep[] | undefined {
  // Preferred path: authoritative pipeline_steps from backend.
  const pipeline = metadata?.pipeline_steps;
  if (pipeline && pipeline.length > 0) {
    return pipeline.map((s: PipelineStep) => ({
      node: s.node,
      message: s.message,
      status: s.status,
      timestamp: s.started_at_ms,
      started_at_ms: s.started_at_ms,
      duration_ms: s.duration_ms,
      total_tokens: s.total_tokens,
      reasoning: s.reasoning,
    }));
  }

  // Legacy fallback for messages saved before pipeline_steps existed.
  const arr = parseReasoningArray(raw);
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

  addStep('classify', 'Understanding your question');
  if (reasoningLabels.has('Resolving entities')) addStep('resolve', 'Resolving entities');
  if (reasoningLabels.has('Analyzing question and building query')) {
    addStep('generate_sql', 'Analyzing question and building query');
  }
  if (metadata?.sql) addStep('validate_sql', 'Validating SQL syntax');
  if (metadata?.columns && metadata.columns.length > 0) addStep('run_query', 'Fetching results');
  if (reasoningLabels.has('Validating results')) addStep('validate_results', 'Validating results');
  for (const label of reasoningLabels) {
    if (label.startsWith('Fixing the query')) addStep('fix_query', label);
  }
  if (content) addStep('respond', 'Preparing your answer');

  return steps.length > 1 ? steps : undefined;
}

// ─── Step helpers used by SSE handlers ───

/** Append a new active step, closing whatever was previously active. */
function appendActiveStep(
  steps: StreamingStep[] | undefined,
  node: string,
  message: string,
  startedAtMs: number,
): StreamingStep[] {
  const closed = (steps || []).map((s) =>
    s.status === 'active'
      ? {
          ...s,
          status: 'done' as const,
          duration_ms: Math.max(0, startedAtMs - (s.started_at_ms ?? 0)),
        }
      : s,
  );
  return [
    ...closed,
    {
      node,
      message,
      status: 'active' as const,
      timestamp: Date.now(),
      started_at_ms: startedAtMs,
      duration_ms: null,
      reasoning: '',
    },
  ];
}

/** Append reasoning text to whatever step is currently active. */
function appendStepReasoning(
  steps: StreamingStep[] | undefined,
  text: string,
  node?: string,
): StreamingStep[] | undefined {
  if (!steps || steps.length === 0) return steps;
  const next = [...steps];
  // Prefer the last step whose node matches — this prevents late-arriving
  // tokens from a slow LLM node landing under a faster node that started after.
  if (node) {
    for (let i = next.length - 1; i >= 0; i--) {
      if (next[i].node === node) {
        next[i] = { ...next[i], reasoning: (next[i].reasoning || '') + text };
        return next;
      }
    }
  }
  // Fallback: last active step
  for (let i = next.length - 1; i >= 0; i--) {
    if (next[i].status === 'active') {
      next[i] = { ...next[i], reasoning: (next[i].reasoning || '') + text };
      return next;
    }
  }
  return steps;
}

/** Mark a specific node's step as done or error with its authoritative duration. */
function markStepDone(
  steps: StreamingStep[] | undefined,
  node: string,
  duration_ms: number,
  status: 'done' | 'error' = 'done',
  total_tokens?: number,
): StreamingStep[] | undefined {
  if (!steps) return steps;
  let found = false;
  const next = steps.map((s) => {
    if (!found && s.node === node && s.status === 'active') {
      found = true;
      return { ...s, status, duration_ms, ...(total_tokens ? { total_tokens } : {}) };
    }
    return s;
  });
  return found ? next : steps;
}

function markStepError(steps: StreamingStep[] | undefined): StreamingStep[] | undefined {
  if (!steps) return steps;
  return steps.map((s) =>
    s.status === 'active' ? { ...s, status: 'error' as const } : s,
  );
}

/** Update total_tokens on the active step for a node (live streaming progress). */
function updateStepTokens(
  steps: StreamingStep[] | undefined,
  node: string,
  tokens: number,
): StreamingStep[] | undefined {
  if (!steps) return steps;
  let found = false;
  const next = steps.map((s) => {
    if (!found && s.node === node && s.status === 'active') {
      found = true;
      return { ...s, total_tokens: tokens };
    }
    return s;
  });
  return found ? next : steps;
}

/** Replace streamingSteps with the authoritative final list from `done`. */
function finalizeStepsFromDone(rawSteps: unknown): StreamingStep[] | undefined {
  if (!Array.isArray(rawSteps) || rawSteps.length === 0) return undefined;
  return (rawSteps as PipelineStep[]).map((s) => ({
    node: s.node,
    message: s.message,
    status: s.status,
    timestamp: s.started_at_ms,
    started_at_ms: s.started_at_ms,
    duration_ms: s.duration_ms,
    total_tokens: s.total_tokens,
    reasoning: s.reasoning,
  }));
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
  isStopping: boolean;
  streamingMessageId: string | null;
  // Thread currently receiving the stream. Used by the chat page guard
  // so navigating to a different thread while one is generating doesn't
  // block the new thread's fetch - only skip when streaming INTO the
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

  // Origin of the current stream: 'ask' (new question), 'retry', or 'edit'.
  // Used by the chat page to decide scroll behavior.
  streamingOrigin: 'ask' | 'retry' | 'edit' | null;

  // Pending question (from /new page)
  pendingQuestion: string | null;
  pendingDeepAnalysis: boolean;

  // Selection (bulk ops)
  selectedThreadIds: Set<string>;

  // In-memory message map for instant re-visit (not persisted, cleared on page refresh)
  threadMessageMap: Record<string, Message[]>;

  // Active version index per turn, keyed threadId → versionsKey → idx.
  // versionsKey is the `versions.join(',')` produced by message-list grouping.
  // Lifted into the store so the composer can read the visible-active version
  // when deriving source_conversation_id for new questions.
  activeVersions: Record<string, Record<string, number>>;

  // ─── Actions ───

  // Fetch
  fetchRecents: (params?: { search?: string; project_id?: string; append?: boolean }) => Promise<void>;
  fetchThread: (threadId: string) => Promise<void>;

  // Thread CRUD
  createThread: (title?: string, projectId?: string, pregenId?: string) => Promise<string>;
  deleteThread: (threadId: string) => Promise<boolean>;
  starThread: (threadId: string) => Promise<void>;
  renameThread: (threadId: string, title: string) => Promise<void>;
  moveThread: (threadId: string, projectId: string | null) => Promise<void>;

  // Bulk
  bulkDeleteThreads: () => Promise<void>;
  bulkMoveThreads: (projectId: string | null, threadIds?: string[]) => Promise<void>;

  // Streaming
  askQuestion: (threadId: string, question: string, sourceConversationId?: string, priorSql?: string, deepAnalysis?: boolean) => Promise<void>;
  retryResponse: (threadId: string, conversationId: string) => Promise<void>;
  editQuestion: (threadId: string, conversationId: string, question: string) => Promise<void>;
  stopGeneration: (threadId: string) => Promise<void>;

  // Feedback
  submitFeedback: (threadId: string, conversationId: string, liked: boolean, comment?: string, feedbackType?: string) => Promise<void>;

  // Local UI
  setCurrentThread: (id: string | null) => void;
  setSearchQuery: (query: string) => void;
  setPendingQuestion: (question: string | null, deepAnalysis?: boolean) => void;
  toggleThreadSelection: (id: string) => void;
  selectAllThreads: () => void;
  clearSelection: () => void;
  setActiveVersion: (threadId: string, versionsKey: string, idx: number) => void;
  clearActiveVersionsForThread: (threadId: string) => void;
}

// ─── threadMessageMap LRU eviction ───
// Keep the in-memory message cache bounded. Without this, a long session
// browsing many threads accumulates ~1MB+ of cached message arrays.
const THREAD_CACHE_MAX = 25;
const threadCacheAccess = new Map<string, number>();

function recordCacheAccess(threadId: string) {
  threadCacheAccess.set(threadId, Date.now());
}

function pruneThreadCache<T>(map: Record<string, T>): Record<string, T> {
  const keys = Object.keys(map);
  if (keys.length <= THREAD_CACHE_MAX) return map;
  // Evict the least-recently-accessed entries until we're under the cap.
  // Threads with no recorded access (just inserted) score Infinity so they
  // survive - newer entries are kept over older idle ones.
  const sorted = keys
    .map((id) => [id, threadCacheAccess.get(id) ?? Infinity] as const)
    .sort((a, b) => a[1] - b[1]);
  const toEvict = sorted.slice(0, keys.length - THREAD_CACHE_MAX).map(([id]) => id);
  const next = { ...map };
  for (const id of toEvict) {
    delete next[id];
    threadCacheAccess.delete(id);
  }
  return next;
}

// ─── Debounce helper ───

// Stored on globalThis so HMR (which re-evaluates this module) doesn't leak
// timers across reloads. A plain `let` would orphan the previous timer when
// the module re-evaluates.
const SEARCH_TIMER_KEY = '__mtiBrainSearchTimer';
type GlobalWithTimer = typeof globalThis & {
  [SEARCH_TIMER_KEY]?: ReturnType<typeof setTimeout> | null;
};
function getSearchTimer(): ReturnType<typeof setTimeout> | null {
  return (globalThis as GlobalWithTimer)[SEARCH_TIMER_KEY] ?? null;
}
function setSearchTimer(t: ReturnType<typeof setTimeout> | null) {
  (globalThis as GlobalWithTimer)[SEARCH_TIMER_KEY] = t;
}

// In-flight deduplication for default (no-search, no-append) fetchRecents calls.
// Layout fires fetchRecents on every navigation; mutations also call it after
// they complete. Without this guard, two near-simultaneous calls fire two
// network requests and the slower one's stale result can overwrite the faster one.
let fetchRecentsFlight: Promise<void> | null = null;

// Per-threadId in-flight dedup. React StrictMode double-mounts effects in dev,
// and the chat page's effect runs alongside any layout-level priming - both
// hit the same threadId within ms. Coalesce overlapping calls to one request.
const fetchThreadFlights = new Map<string, Promise<void>>();

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
  isStopping: false,
  streamingMessageId: null,
  streamingThreadId: null,
  streamingMessages: [],
  abortController: null,
  streamingOrigin: null,
  pendingQuestion: null,
  pendingDeepAnalysis: false,
  selectedThreadIds: new Set(),
  threadMessageMap: {},
  activeVersions: {},

  // ─── Fetch ───

  fetchRecents: async (params) => {
    const isDefaultFetch = !params?.search && !params?.append && !params?.project_id;
    if (isDefaultFetch && fetchRecentsFlight) return fetchRecentsFlight;

    const run = async () => {
      const { searchQuery, threadsOffset, threads } = get();
      const search = params?.search ?? searchQuery;
      const append = params?.append ?? false;
      const offset = append ? threadsOffset : 0;
      const limit = 20;

      set({ threadsLoading: get().threads.length === 0, isSearching: !!search });
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
          const currentThreads = get().threads;
          // Merge: preserve starred threads not returned by the server (they age
          // out of the top-20 recency window but must stay visible in the sidebar).
          // Also preserve any optimistic starred flag set during an in-flight
          // starThread() call that hasn't persisted to the server yet.
          const responseIds = new Set(items.map((i) => i.id));
          const starredOrphans = append
            ? []
            : currentThreads.filter((t) => t.starred && !responseIds.has(t.id));
          const mergedItems = items.map((item) => {
            const existing = currentThreads.find((t) => t.id === item.id);
            return existing ? { ...item, starred: existing.starred } : item;
          });
          set({
            threads: append ? [...threads, ...mergedItems] : [...mergedItems, ...starredOrphans],
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
    };

    const p = run();
    if (isDefaultFetch) {
      fetchRecentsFlight = p.finally(() => { fetchRecentsFlight = null; });
    }
    return p;
  },

  fetchThread: async (threadId) => {
    const inflight = fetchThreadFlights.get(threadId);
    if (inflight) return inflight;

    const run = async () => {
      // Show skeleton only when no messages are available (neither current nor map)
      const hasMessages =
        (get().currentThreadId === threadId && get().currentMessages.length > 0) ||
        (get().threadMessageMap[threadId]?.length ?? 0) > 0;
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
          preference_summary: m.metadata_?.preference_summary,
          feedback: m.feedback ? { liked: m.feedback.liked, comment: m.feedback.comment ?? undefined } : undefined,
          created_at: m.created_at,
        }));
        // Don't overwrite messages if askQuestion already populated them (race with pendingQuestion)
        const current = get().currentMessages;
        const shouldKeepMessages = current.length > 0 && messages.length === 0 && get().currentThreadId === threadId;
        const resolved = shouldKeepMessages ? current : messages;
        // Race guard: user may have navigated to a different thread while
        // this fetch was in flight. Always update the cache map, but only
        // commit to current* state if we're still viewing this thread.
        const stillViewing = get().currentThreadId === threadId;
        recordCacheAccess(threadId);
        if (stillViewing) {
          set((state) => ({
            currentMessages: resolved,
            currentThreadTitle: detail.title,
            currentThreadStarred: detail.starred,
            currentThreadProjectId: detail.project_id,
            messagesLoading: false,
            threadMessageMap: pruneThreadCache({
              ...state.threadMessageMap,
              [threadId]: resolved,
            }),
          }));
        } else {
          // Still cache for future visits, but don't touch current view
          set((state) => ({
            threadMessageMap: pruneThreadCache({
              ...state.threadMessageMap,
              [threadId]: resolved,
            }),
          }));
        }
      } catch {
        // Only clear loading if we're still on this thread
        if (get().currentThreadId === threadId) {
          set({ messagesLoading: false });
        }
        throw new Error('Thread not found');
      }
    };

    const p = run();
    fetchThreadFlights.set(threadId, p.finally(() => fetchThreadFlights.delete(threadId)));
    return p;
  },

  // ─── Thread CRUD ───

  createThread: async (title, projectId, pregenId) => {
    const res = await api.createThread({
      thread_id: pregenId,
      title,
      project_id: projectId,
    });
    // Refresh sidebar
    get().fetchRecents();
    // For threads created inside a project, optimistically prepend the new
    // thread (with the real thread_id from the API response) into the
    // project detail. This way the user sees it instantly if they're on
    // the project page, and if they navigate away and back, the cached
    // entry already has it - no skeleton flash. We don't invalidate here
    // because that would clobber the optimistic insert; the next natural
    // fetch (e.g. revisiting the page later) will reconcile against the
    // server, and fetchProjects below refreshes counts.
    if (projectId) {
      const { useProjectStore } = await import('./projects');
      const now = new Date().toISOString();
      useProjectStore.getState().mutateProjectDetail(projectId, (p) => ({
        ...p,
        threads: [
          {
            id: res.thread_id,
            project_id: projectId,
            title: res.title ?? title ?? null,
            starred: false,
            last_message: null,
            created_at: now,
            updated_at: now,
          },
          ...p.threads,
        ],
      }));
      useProjectStore.getState().fetchProjects();
    }
    return res.thread_id;
  },

  deleteThread: async (threadId) => {
    const prev = get().threads;
    const prevSearchResults = get().searchResults;
    const thread = prev.find((t) => t.id === threadId);
    const affectedProjectId = thread?.project_id ?? null;
    const wasCurrent = get().currentThreadId === threadId;
    // Optimistic: remove from list + search results, clear if active, evict from map
    set((state) => {
      const { [threadId]: _evict, ...remainingMap } = state.threadMessageMap;
      const { [threadId]: _evictVersions, ...remainingVersions } = state.activeVersions;
      return {
        threads: prev.filter((t) => t.id !== threadId),
        searchResults: prevSearchResults.filter((r) => r.thread_id !== threadId),
        selectedThreadIds: new Set([...state.selectedThreadIds].filter((id) => id !== threadId)),
        threadMessageMap: remainingMap,
        activeVersions: remainingVersions,
        ...(wasCurrent ? { currentThreadId: null, currentMessages: [] } : {}),
      };
    });
    // Mirror the optimistic delete into the project detail (open page +
    // any cached entry) so the parent project's list updates instantly,
    // not after the post-API refetch finishes. Same pattern as starThread.
    if (affectedProjectId) {
      const { useProjectStore } = await import('./projects');
      useProjectStore.getState().mutateProjectDetail(affectedProjectId, (p) => ({
        ...p,
        threads: p.threads.filter((t) => t.id !== threadId),
      }));
    }
    try {
      await api.deleteThread(threadId);
      // Refresh project list (counts) + drop stale detail cache + refresh
      // detail if the parent project page is currently open.
      const { useProjectStore } = await import('./projects');
      useProjectStore.getState().fetchProjects();
      if (affectedProjectId) {
        useProjectStore.getState().invalidateProjectDetail(affectedProjectId);
        useProjectStore.getState().refreshCurrentProjectIfMatches(affectedProjectId);
      }
    } catch (err) {
      set({ threads: prev, searchResults: prevSearchResults });
      if (affectedProjectId) {
        const { useProjectStore } = await import('./projects');
        useProjectStore.getState().invalidateProjectDetail(affectedProjectId);
        useProjectStore.getState().refreshCurrentProjectIfMatches(affectedProjectId);
      }
      throw err;
    }
    return wasCurrent;
  },

  starThread: async (threadId) => {
    const prev = get().threads;
    const prevStarred = get().currentThreadStarred;
    // Optimistic toggle
    set({
      threads: prev.map((t) =>
        t.id === threadId ? { ...t, starred: !t.starred } : t,
      ),
    });
    if (get().currentThreadId === threadId) {
      set({ currentThreadStarred: !get().currentThreadStarred });
    }
    // Mirror the toggle into the project detail cache (and the open detail
    // page if it matches). Fall back to currentThreadProjectId when the
    // thread isn't in the sidebar list (e.g. paginated beyond view).
    const affectedProjectId =
      prev.find((t) => t.id === threadId)?.project_id
      ?? (get().currentThreadId === threadId ? get().currentThreadProjectId : null);
    if (affectedProjectId) {
      const { useProjectStore } = await import('./projects');
      useProjectStore.getState().mutateProjectDetail(affectedProjectId, (p) => ({
        ...p,
        threads: p.threads.map((t) =>
          t.id === threadId ? { ...t, starred: !t.starred } : t,
        ),
      }));
    }
    try {
      await api.starThread(threadId);
    } catch (err) {
      set({ threads: prev });
      if (get().currentThreadId === threadId) {
        set({ currentThreadStarred: prevStarred });
      }
      if (affectedProjectId) {
        const { useProjectStore } = await import('./projects');
        useProjectStore.getState().mutateProjectDetail(affectedProjectId, (p) => ({
          ...p,
          threads: p.threads.map((t) =>
            t.id === threadId ? { ...t, starred: !t.starred } : t,
          ),
        }));
      }
      throw err;
    }
  },

  renameThread: async (threadId, title) => {
    const prev = get().threads;
    const prevTitle = get().currentThreadTitle;
    set({
      threads: prev.map((t) =>
        t.id === threadId ? { ...t, title } : t,
      ),
    });
    if (get().currentThreadId === threadId) {
      set({ currentThreadTitle: title });
    }
    // Mirror rename into the project detail cache (and the open detail page
    // if it matches). Fall back to currentThreadProjectId when the thread
    // isn't in sidebar.
    const affectedProjectId =
      prev.find((t) => t.id === threadId)?.project_id
      ?? (get().currentThreadId === threadId ? get().currentThreadProjectId : null);
    if (affectedProjectId) {
      const { useProjectStore } = await import('./projects');
      useProjectStore.getState().mutateProjectDetail(affectedProjectId, (p) => ({
        ...p,
        threads: p.threads.map((t) =>
          t.id === threadId ? { ...t, title } : t,
        ),
      }));
    }
    try {
      await api.renameThread(threadId, title);
    } catch (err) {
      set({ threads: prev });
      if (get().currentThreadId === threadId) {
        set({ currentThreadTitle: prevTitle });
      }
      if (affectedProjectId) {
        const originalTitle = prev.find((t) => t.id === threadId)?.title ?? null;
        const { useProjectStore } = await import('./projects');
        useProjectStore.getState().mutateProjectDetail(affectedProjectId, (p) => ({
          ...p,
          threads: p.threads.map((t) =>
            t.id === threadId ? { ...t, title: originalTitle } : t,
          ),
        }));
      }
      throw err;
    }
  },

  moveThread: async (threadId, projectId) => {
    const prev = get().threads;
    const movedThread = prev.find((t) => t.id === threadId);
    const fromProjectId = movedThread?.project_id ?? null;
    // Optimistic: update sidebar list and current thread meta immediately
    set({ threads: prev.map((t) => t.id === threadId ? { ...t, project_id: projectId } : t) });
    if (get().currentThreadId === threadId) set({ currentThreadProjectId: projectId });
    // Mirror the move into project detail caches so both the source and
    // destination project pages reflect the change instantly.
    const { useProjectStore } = await import('./projects');
    if (fromProjectId) {
      useProjectStore.getState().mutateProjectDetail(fromProjectId, (p) => ({
        ...p,
        threads: p.threads.filter((t) => t.id !== threadId),
      }));
    }
    if (projectId && movedThread) {
      useProjectStore.getState().mutateProjectDetail(projectId, (p) => ({
        ...p,
        threads: [
          { ...movedThread, project_id: projectId },
          ...p.threads.filter((t) => t.id !== threadId),
        ],
      }));
    }
    try {
      await api.moveThread(threadId, projectId);
      // Counts shifted; refresh project list (sidebar/projects page).
      get().fetchRecents();
      useProjectStore.getState().fetchProjects();
    } catch (err) {
      set({ threads: prev });
      if (get().currentThreadId === threadId) set({ currentThreadProjectId: fromProjectId });
      if (fromProjectId) {
        useProjectStore.getState().invalidateProjectDetail(fromProjectId);
        useProjectStore.getState().refreshCurrentProjectIfMatches(fromProjectId);
      }
      if (projectId) {
        useProjectStore.getState().invalidateProjectDetail(projectId);
        useProjectStore.getState().refreshCurrentProjectIfMatches(projectId);
      }
      throw err;
    }
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
    const idSet = new Set(ids);
    set({
      threads: prev.filter((t) => !idSet.has(t.id)),
      searchResults: prevSearchResults.filter((r) => !idSet.has(r.thread_id)),
      selectedThreadIds: new Set(),
    });
    // Mirror the optimistic delete into project detail caches so the open
    // project page (and any cached detail) reflects the removal instantly.
    if (affectedProjectIds.size > 0) {
      const { useProjectStore } = await import('./projects');
      for (const pid of affectedProjectIds) {
        useProjectStore.getState().mutateProjectDetail(pid, (p) => ({
          ...p,
          threads: p.threads.filter((t) => !idSet.has(t.id)),
        }));
      }
    }
    try {
      await api.bulkDeleteThreads(ids);
      if (idSet.has(get().currentThreadId ?? '')) {
        set({ currentThreadId: null, currentMessages: [] });
      }
      // Evict cached message data for every deleted thread so a stale id
      // doesn't render after the rows are gone.
      set((state) => {
        const map = { ...state.threadMessageMap };
        for (const id of ids) delete map[id];
        return { threadMessageMap: map };
      });
      // Refresh project counts, drop affected detail caches, refresh open detail
      const { useProjectStore } = await import('./projects');
      useProjectStore.getState().fetchProjects();
      for (const pid of affectedProjectIds) {
        useProjectStore.getState().invalidateProjectDetail(pid);
        useProjectStore.getState().refreshCurrentProjectIfMatches(pid);
      }
    } catch (err) {
      set({ threads: prev, searchResults: prevSearchResults });
      if (affectedProjectIds.size > 0) {
        const { useProjectStore } = await import('./projects');
        for (const pid of affectedProjectIds) {
          useProjectStore.getState().invalidateProjectDetail(pid);
          useProjectStore.getState().refreshCurrentProjectIfMatches(pid);
        }
      }
      throw err;
    }
  },

  bulkMoveThreads: async (projectId, threadIds?) => {
    const ids = threadIds ?? [...get().selectedThreadIds];
    if (ids.length === 0) return;
    const prev = get().threads;
    const idSet = new Set(ids);
    const movedThreads = prev.filter((t) => idSet.has(t.id));
    const affectedProjectIds = new Set(
      movedThreads.map((t) => t.project_id).filter(Boolean) as string[],
    );
    // Optimistic: update project_id on all selected threads immediately
    set({
      threads: prev.map((t) => idSet.has(t.id) ? { ...t, project_id: projectId } : t),
      selectedThreadIds: new Set(),
    });
    // Mirror the move into project detail caches so source and destination
    // project pages reflect the change instantly.
    const { useProjectStore } = await import('./projects');
    for (const pid of affectedProjectIds) {
      useProjectStore.getState().mutateProjectDetail(pid, (p) => ({
        ...p,
        threads: p.threads.filter((t) => !idSet.has(t.id)),
      }));
    }
    if (projectId && movedThreads.length > 0) {
      useProjectStore.getState().mutateProjectDetail(projectId, (p) => ({
        ...p,
        threads: [
          ...movedThreads.map((t) => ({ ...t, project_id: projectId })),
          ...p.threads.filter((t) => !idSet.has(t.id)),
        ],
      }));
    }
    try {
      await api.bulkMoveThreads(ids, projectId);
      get().fetchRecents();
      useProjectStore.getState().fetchProjects();
    } catch (err) {
      set({ threads: prev });
      for (const pid of affectedProjectIds) {
        useProjectStore.getState().invalidateProjectDetail(pid);
        useProjectStore.getState().refreshCurrentProjectIfMatches(pid);
      }
      if (projectId) {
        useProjectStore.getState().invalidateProjectDetail(projectId);
        useProjectStore.getState().refreshCurrentProjectIfMatches(projectId);
      }
      throw err;
    }
  },

  // ─── Streaming ───

  askQuestion: async (threadId, question, sourceConversationId?, priorSql?, deepAnalysis?) => {
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
      // Mark refine-query messages so the UI shows "↳ Refining: …" only for
      // these, not for follow-up chips that also carry source_conversation_id.
      metadata_: priorSql ? { is_refinement: true } : null,
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
    // Client-side fallback origin for step timing if backend omits started_at_ms.
    const streamStartedAt = Date.now();

    // Optimistic title - set before streaming so the sidebar never shows
    // "Untitled" even if the title.generated SSE event is stopped before it
    // fires. The backend saves the real title via save_message_and_touch
    // before SSE begins; onStopped/onDone calls fetchRecents() to sync it.
    const existingTitle = get().currentThreadTitle || get().threads.find((t) => t.id === threadId)?.title;
    if (!existingTitle) {
      const autoTitle = question.trim().split(/\s+/).slice(0, 6).join(' ');
      set((state) => ({
        currentThreadTitle: autoTitle,
        threads: state.threads.map((t) => t.id === threadId ? { ...t, title: autoTitle } : t),
      }));
    }

    // streamingMessages holds the FULL thread (history + new turn) so the
    // chat page can render the whole conversation from this slot while the
    // stream is in flight. Only the new user/assistant messages change
    // inside - the historical ones pass through unchanged.
    const nextMessages = [...currentMessages, userMsg, assistantMsg];
    set({
      currentMessages: nextMessages,
      streamingMessages: nextMessages,
      isStreaming: true,
      streamingMessageId: assistantMsgId,
      streamingThreadId: threadId,
      streamingOrigin: 'ask',
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
        const startedAtMs = data.started_at_ms ?? Date.now() - streamStartedAt;
        mapMsgs((m) => {
          if (m.id !== assistantMsgId) return m;
          const update: Partial<typeof m> = {
            streamingSteps: appendActiveStep(m.streamingSteps, data.node, data.message, startedAtMs),
          };
          // Retry: executor starting again means a new execution cycle —
          // reset data/chart readiness so stale 0-row results are cleared.
          if (data.node === 'executor' && m.dataReady) {
            update.dataReady = false;
            update.chartReady = false;
          }
          return { ...m, ...update };
        });
      },
      onSparqlGenerated: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, metadata_: { ...m.metadata_, sql: data.sql } }
            : m,
        );
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
            ? {
                ...m,
                reasoning: (m.reasoning || '') + data.text,
                reasoningPending: false,
                streamingSteps: appendStepReasoning(m.streamingSteps, data.text, data.node as string | undefined),
              }
            : m,
        );
      },
      onExecuteDone: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? {
                ...m,
                dataReady: true,
                willVisualize: data.will_visualize ?? false,
                metadata_: {
                  ...m.metadata_,
                  sql: data.sql,
                  columns: data.columns,
                  rows: data.rows,
                  row_count: data.row_count,
                  source_tables: (data.source_tables as string[] | undefined) ?? m.metadata_?.source_tables,
                  data_freshness_at: (data.data_freshness_at as string | undefined) ?? m.metadata_?.data_freshness_at,
                  metric_name: (data.metric_name as string | undefined) ?? m.metadata_?.metric_name,
                  metric_owner: (data.metric_owner as string | undefined) ?? m.metadata_?.metric_owner,
                  metric_defined_at: (data.metric_defined_at as string | undefined) ?? m.metadata_?.metric_defined_at,
                },
              }
            : m,
        );
      },
      onChart: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, chartReady: true, metadata_: { ...m.metadata_, chart_spec: data.spec, chart_type: data.chart_type as string | undefined, alternative_chart_specs: data.alternative_chart_specs as { chart_type: string; spec: Record<string, unknown> }[] | undefined } }
            : m,
        );
      },
      onConfidence: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, metadata_: { ...m.metadata_, confidence: data } }
            : m,
        );
      },
      onTribalFacts: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, metadata_: { ...m.metadata_, tribal_facts: data.facts } }
            : m,
        );
      },
      onVizSkip: () => {
        mapMsgs((m) =>
          m.id === assistantMsgId ? { ...m, chartReady: true } : m,
        );
      },
      onNodeDone: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, streamingSteps: markStepDone(m.streamingSteps, data.node, data.duration_ms, data.status ?? 'done', data.total_tokens as number | undefined) }
            : m,
        );
      },
      onNodeTokens: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, streamingSteps: updateStepTokens(m.streamingSteps, data.node, data.tokens) }
            : m,
        );
      },
      onFollowUps: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, followUpsReady: true, metadata_: { ...m.metadata_, follow_ups: data.questions } }
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
              // Replace with authoritative timeline (accurate durations + per-step reasoning)
              // when the backend included pipeline_steps; otherwise just close the active step.
              streamingSteps:
                finalizeStepsFromDone(data.pipeline_steps) ??
                (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })),
              metadata_: {
                ...m.metadata_,
                run_id: data.run_id as string,
                sql: (data.sql as string) ?? m.metadata_?.sql,
                columns: (data.columns as string[]) ?? m.metadata_?.columns,
                rows: (data.rows as unknown[][]) ?? m.metadata_?.rows,
                row_count: (data.row_count as number) ?? m.metadata_?.row_count,
                chart_spec: (data.chart_spec as Record<string, unknown>) ?? m.metadata_?.chart_spec,
                chart_type: (data.chart_type as string | undefined) ?? m.metadata_?.chart_type,
                alternative_chart_specs: (data.alternative_chart_specs as { chart_type: string; spec: Record<string, unknown> }[] | undefined) ?? m.metadata_?.alternative_chart_specs,
                follow_ups: (data.follow_ups as string[]) ?? m.metadata_?.follow_ups,
                duration_ms: (data.duration_ms as number) ?? m.metadata_?.duration_ms,
                pipeline_steps:
                  (data.pipeline_steps as PipelineStep[] | undefined) ?? m.metadata_?.pipeline_steps,
                source_tables: (data.source_tables as string[] | undefined) ?? m.metadata_?.source_tables,
                data_freshness_at: (data.data_freshness_at as string | undefined) ?? m.metadata_?.data_freshness_at,
                metric_name: (data.metric_name as string | undefined) ?? m.metadata_?.metric_name,
                metric_owner: (data.metric_owner as string | undefined) ?? m.metadata_?.metric_owner,
                metric_defined_at: (data.metric_defined_at as string | undefined) ?? m.metadata_?.metric_defined_at,
                token_usage: (data.token_usage as TokenUsage | undefined) ?? m.metadata_?.token_usage,
                langfuse_trace_id: (data.langfuse_trace_id as string | undefined) ?? m.metadata_?.langfuse_trace_id,
                langfuse_trace_url: (data.langfuse_trace_url as string | undefined) ?? m.metadata_?.langfuse_trace_url,
                intent: (data.intent as string | undefined) ?? m.metadata_?.intent,
                preference_summary: (data.preference_summary as PreferenceSummary | undefined) ?? m.metadata_?.preference_summary,
                tribal_facts: (data.tribal_facts as import('../types/api').TribalFact[] | undefined) ?? m.metadata_?.tribal_facts,
                deep_analysis: (data.deep_analysis as boolean | undefined) ?? m.metadata_?.deep_analysis,
              },
            };
          }
          if (m.id === userMsgId) return { ...m, conversation_id: convId };
          return m;
        };
        set((state) => {
          const updated = state.currentMessages.map(mapper);
          return {
            currentMessages: updated,
            threadMessageMap: { ...state.threadMessageMap, [threadId]: updated },
            streamingMessages: [],
            isStreaming: false,
            streamingMessageId: null,
            streamingThreadId: null,
            abortController: null,
          };
        });
        get().fetchRecents();
        // The project's thread updated_at (and possibly thread_count for a
        // freshly-created thread) shifted - drop the detail cache so the
        // next visit re-fetches, and refresh now if the page is open.
        const pid = get().currentThreadProjectId;
        if (pid) {
          import('./projects').then(({ useProjectStore }) => {
            useProjectStore.getState().invalidateProjectDetail(pid);
            useProjectStore.getState().refreshCurrentProjectIfMatches(pid);
          });
        }
      },
      onStopped: (data) => {
        const convId = (data.conversation_id as string) || '';
        const durationMs = data.duration_ms as number | undefined;
        const finalSteps = (data.pipeline_steps as StreamingStep[] | undefined);
        set((state) => {
          const updated = state.currentMessages.map((m) => {
            if (m.id === assistantMsgId) return {
              ...m,
              ...(convId ? { conversation_id: convId } : {}),
              isStreaming: false,
              metadata_: {
                ...m.metadata_,
                stopped: true,
                ...(durationMs != null ? { duration_ms: durationMs } : {}),
              },
              streamingSteps: finalSteps
                ? finalSteps.map((s) => ({ ...s, status: 'done' as const }))
                : (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })),
            };
            if (convId && m.id === userMsgId) return { ...m, conversation_id: convId };
            return m;
          });
          return {
            currentMessages: updated,
            threadMessageMap: { ...state.threadMessageMap, [threadId]: updated },
            streamingMessages: [],
            isStreaming: false,
            isStopping: false,
            streamingMessageId: null,
            streamingThreadId: null,
            abortController: null,
          };
        });
        get().fetchRecents();
      },
      onError: (data) => {
        const convId = (data.conversation_id as string) || '';
        set((state) => {
          const updated = state.currentMessages.map((m) => {
            if (m.id === assistantMsgId) {
              return {
                ...m,
                content: data.message || 'Something went wrong. Please try again.',
                isStreaming: false,
                conversation_id: convId,
                streamingSteps: markStepError(m.streamingSteps),
              };
            }
            if (m.id === userMsgId && convId && !m.conversation_id) {
              return { ...m, conversation_id: convId };
            }
            return m;
          });
          return {
            currentMessages: updated,
            threadMessageMap: { ...state.threadMessageMap, [threadId]: updated },
            streamingMessages: [],
            isStreaming: false,
            isStopping: false,
            streamingMessageId: null,
            streamingThreadId: null,
            abortController: null,
          };
        });
        get().fetchRecents();
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
        deep_analysis: deepAnalysis ?? false,
      };
      if (sourceConversationId) askBody.source_conversation_id = sourceConversationId;
      if (priorSql) askBody.prior_sql = priorSql;
      await streamSSE(`/chat/${threadId}/ask`, askBody, handlers, controller.signal);
    } catch (err: unknown) {
      if ((err as Error).name === 'AbortError') {
        // User cancelled - already handled by onStopped if backend confirmed
      } else {
        // Stream broke mid-flight. If reasoning or answer already streamed,
        // preserve it and mark the message as interrupted instead of wiping
        // everything to a generic error.
        const msg = get().currentMessages.find((m) => m.id === assistantMsgId);
        const hasPartial =
          !!(msg?.content && msg.content.trim()) ||
          !!(msg?.reasoning && msg.reasoning.trim());
        if (hasPartial) {
          set((state) => {
            const updated = state.currentMessages.map((m) =>
              m.id === assistantMsgId
                ? {
                    ...m,
                    isStreaming: false,
                    metadata_: { ...m.metadata_, interrupted: true },
                    streamingSteps: markStepError(m.streamingSteps),
                  }
                : m,
            );
            return {
              currentMessages: updated,
              threadMessageMap: { ...state.threadMessageMap, [threadId]: updated },
              streamingMessages: [],
              isStreaming: false,
              streamingMessageId: null,
              streamingThreadId: null,
              abortController: null,
            };
          });
        } else {
          handlers.onError?.({ message: 'Failed to get a response' });
        }
      }
    } finally {
      // Safety net: if stream ended but onDone/onError/onStopped never fired, clean up
      if (get().streamingMessageId === assistantMsgId) {
        set((state) => {
          const updated = state.currentMessages.map((m) =>
            m.id === assistantMsgId
              ? { ...m, isStreaming: false, streamingSteps: (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })) }
              : m,
          );
          return {
            currentMessages: updated,
            threadMessageMap: { ...state.threadMessageMap, [threadId]: updated },
            streamingMessages: [],
            isStreaming: false,
            streamingMessageId: null,
            streamingThreadId: null,
            abortController: null,
          };
        });
        get().fetchRecents();
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
    // Provenance: copy source_conversation_id from the ROOT user message so
    // the new branch keeps cascading-visibility parity with its siblings.
    const rootUserMsg = currentMessages.find(
      (m) => m.conversation_id === rootConvId && m.role === 'user',
    );
    const sourceConversationId = rootUserMsg?.source_conversation_id;

    const userMsgId = randomId();
    const assistantMsgId = randomId();

    const userMsg: Message = {
      id: userMsgId,
      conversation_id: '',
      parent_conversation_id: rootConvId,
      role: 'user',
      content: originalUserMsg?.content || '',
      created_at: new Date().toISOString(),
      source_conversation_id: sourceConversationId,
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
    const streamStartedAt = Date.now();

    // streamingMessages holds the FULL thread (history + new turn) so the
    // chat page can render the whole conversation from this slot while the
    // stream is in flight. Only the new user/assistant messages change
    // inside - the historical ones pass through unchanged.
    const nextMessages = [...currentMessages, userMsg, assistantMsg];
    set({
      currentMessages: nextMessages,
      streamingMessages: nextMessages,
      isStreaming: true,
      streamingMessageId: assistantMsgId,
      streamingThreadId: threadId,
      streamingOrigin: 'retry',
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
        const startedAtMs = data.started_at_ms ?? Date.now() - streamStartedAt;
        mapMsgs((m) => {
          if (m.id !== assistantMsgId) return m;
          const update: Partial<typeof m> = {
            streamingSteps: appendActiveStep(m.streamingSteps, data.node, data.message, startedAtMs),
          };
          // Retry: executor starting again means a new execution cycle —
          // reset data/chart readiness so stale 0-row results are cleared.
          if (data.node === 'executor' && m.dataReady) {
            update.dataReady = false;
            update.chartReady = false;
          }
          return { ...m, ...update };
        });
      },
      onSparqlGenerated: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, metadata_: { ...m.metadata_, sql: data.sql } }
            : m,
        );
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
            ? {
                ...m,
                reasoning: (m.reasoning || '') + data.text,
                reasoningPending: false,
                streamingSteps: appendStepReasoning(m.streamingSteps, data.text, data.node as string | undefined),
              }
            : m,
        );
      },
      onExecuteDone: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? {
                ...m,
                dataReady: true,
                willVisualize: data.will_visualize ?? false,
                metadata_: {
                  ...m.metadata_,
                  sql: data.sql,
                  columns: data.columns,
                  rows: data.rows,
                  row_count: data.row_count,
                  source_tables: (data.source_tables as string[] | undefined) ?? m.metadata_?.source_tables,
                  data_freshness_at: (data.data_freshness_at as string | undefined) ?? m.metadata_?.data_freshness_at,
                  metric_name: (data.metric_name as string | undefined) ?? m.metadata_?.metric_name,
                  metric_owner: (data.metric_owner as string | undefined) ?? m.metadata_?.metric_owner,
                  metric_defined_at: (data.metric_defined_at as string | undefined) ?? m.metadata_?.metric_defined_at,
                },
              }
            : m,
        );
      },
      onChart: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId ? { ...m, chartReady: true, metadata_: { ...m.metadata_, chart_spec: data.spec, chart_type: data.chart_type as string | undefined, alternative_chart_specs: data.alternative_chart_specs as { chart_type: string; spec: Record<string, unknown> }[] | undefined } } : m,
        );
      },
      onConfidence: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId ? { ...m, metadata_: { ...m.metadata_, confidence: data } } : m,
        );
      },
      onTribalFacts: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId ? { ...m, metadata_: { ...m.metadata_, tribal_facts: data.facts } } : m,
        );
      },
      onVizSkip: () => {
        mapMsgs((m) =>
          m.id === assistantMsgId ? { ...m, chartReady: true } : m,
        );
      },
      onNodeDone: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, streamingSteps: markStepDone(m.streamingSteps, data.node, data.duration_ms, data.status ?? 'done', data.total_tokens as number | undefined) }
            : m,
        );
      },
      onNodeTokens: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, streamingSteps: updateStepTokens(m.streamingSteps, data.node, data.tokens) }
            : m,
        );
      },
      onFollowUps: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId ? { ...m, followUpsReady: true, metadata_: { ...m.metadata_, follow_ups: data.questions } } : m,
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
              streamingSteps:
                finalizeStepsFromDone(data.pipeline_steps) ??
                (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })),
              metadata_: {
                ...m.metadata_,
                run_id: data.run_id as string,
                sql: (data.sql as string) ?? m.metadata_?.sql,
                columns: (data.columns as string[]) ?? m.metadata_?.columns,
                rows: (data.rows as unknown[][]) ?? m.metadata_?.rows,
                row_count: (data.row_count as number) ?? m.metadata_?.row_count,
                chart_spec: (data.chart_spec as Record<string, unknown>) ?? m.metadata_?.chart_spec,
                chart_type: (data.chart_type as string | undefined) ?? m.metadata_?.chart_type,
                alternative_chart_specs: (data.alternative_chart_specs as { chart_type: string; spec: Record<string, unknown> }[] | undefined) ?? m.metadata_?.alternative_chart_specs,
                follow_ups: (data.follow_ups as string[]) ?? m.metadata_?.follow_ups,
                duration_ms: (data.duration_ms as number) ?? m.metadata_?.duration_ms,
                pipeline_steps:
                  (data.pipeline_steps as PipelineStep[] | undefined) ?? m.metadata_?.pipeline_steps,
                source_tables: (data.source_tables as string[] | undefined) ?? m.metadata_?.source_tables,
                data_freshness_at: (data.data_freshness_at as string | undefined) ?? m.metadata_?.data_freshness_at,
                metric_name: (data.metric_name as string | undefined) ?? m.metadata_?.metric_name,
                metric_owner: (data.metric_owner as string | undefined) ?? m.metadata_?.metric_owner,
                metric_defined_at: (data.metric_defined_at as string | undefined) ?? m.metadata_?.metric_defined_at,
                token_usage: (data.token_usage as TokenUsage | undefined) ?? m.metadata_?.token_usage,
                langfuse_trace_id: (data.langfuse_trace_id as string | undefined) ?? m.metadata_?.langfuse_trace_id,
                langfuse_trace_url: (data.langfuse_trace_url as string | undefined) ?? m.metadata_?.langfuse_trace_url,
                intent: (data.intent as string | undefined) ?? m.metadata_?.intent,
                preference_summary: (data.preference_summary as PreferenceSummary | undefined) ?? m.metadata_?.preference_summary,
                tribal_facts: (data.tribal_facts as import('../types/api').TribalFact[] | undefined) ?? m.metadata_?.tribal_facts,
                deep_analysis: (data.deep_analysis as boolean | undefined) ?? m.metadata_?.deep_analysis,
              },
            };
          }
          if (m.id === userMsgId) return { ...m, conversation_id: convId };
          return m;
        };
        set((state) => {
          const updated = state.currentMessages.map(mapper);
          return {
            currentMessages: updated,
            threadMessageMap: { ...state.threadMessageMap, [threadId]: updated },
            streamingMessages: [],
            isStreaming: false,
            isStopping: false,
            streamingMessageId: null,
            streamingThreadId: null,
            abortController: null,
          };
        });
        get().fetchRecents();
      },
      onStopped: (data) => {
        const convId = (data.conversation_id as string) || '';
        const durationMs = data.duration_ms as number | undefined;
        const finalSteps = (data.pipeline_steps as StreamingStep[] | undefined);
        set((state) => {
          const updated = state.currentMessages.map((m) => {
            if (m.id === assistantMsgId) return {
              ...m,
              ...(convId ? { conversation_id: convId } : {}),
              isStreaming: false,
              metadata_: {
                ...m.metadata_,
                stopped: true,
                ...(durationMs != null ? { duration_ms: durationMs } : {}),
              },
              streamingSteps: finalSteps
                ? finalSteps.map((s) => ({ ...s, status: 'done' as const }))
                : (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })),
            };
            if (convId && m.id === userMsgId) return { ...m, conversation_id: convId };
            return m;
          });
          return {
            currentMessages: updated,
            threadMessageMap: { ...state.threadMessageMap, [threadId]: updated },
            streamingMessages: [],
            isStreaming: false,
            isStopping: false,
            streamingMessageId: null,
            streamingThreadId: null,
            abortController: null,
          };
        });
        get().fetchRecents();
      },
      onError: (data) => {
        set((state) => ({
          currentMessages: state.currentMessages.map((m) =>
            m.id === assistantMsgId
              ? { ...m, content: data.message || 'Retry failed.', isStreaming: false, streamingSteps: markStepError(m.streamingSteps) }
              : m,
          ),
          streamingMessages: [],
          isStreaming: false,
          isStopping: false,
          streamingMessageId: null,
          streamingThreadId: null,
          abortController: null,
        }));
        get().fetchRecents();
      },
    };

    try {
      const { usePreferencesStore: retryPrefs } = await import('./preferences');
      const retryPrefsState = retryPrefs.getState();
      const retryBody: Record<string, unknown> = {
        conversation_id: conversationId,
        response_tone: retryPrefsState.responseTone,
        max_rows: retryPrefsState.maxResultRows,
        deep_analysis: retryPrefsState.deepAnalysis ?? false,
      };
      if (sourceConversationId) retryBody.source_conversation_id = sourceConversationId;
      await streamSSE(`/chat/${threadId}/retry`, retryBody, handlers, controller.signal);
    } catch (err: unknown) {
      if ((err as Error).name !== 'AbortError') {
        handlers.onError?.({ message: 'Retry failed' });
      }
    } finally {
      if (get().streamingMessageId === assistantMsgId) {
        set((state) => {
          const updated = state.currentMessages.map((m) =>
            m.id === assistantMsgId
              ? { ...m, isStreaming: false, streamingSteps: (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })) }
              : m,
          );
          return {
            currentMessages: updated,
            threadMessageMap: { ...state.threadMessageMap, [threadId]: updated },
            streamingMessages: [],
            isStreaming: false,
            streamingMessageId: null,
            streamingThreadId: null,
            abortController: null,
          };
        });
        get().fetchRecents();
      }
    }
  },

  editQuestion: async (threadId, conversationId, question) => {
    const { currentMessages } = get();

    // Resolve to root conversation_id for version branching
    const anyMsg = currentMessages.find((m) => m.conversation_id === conversationId);
    const rootConvId = anyMsg?.parent_conversation_id || conversationId;
    // Provenance: copy source_conversation_id from the ROOT user message so
    // the new edit branch keeps cascading-visibility parity with its siblings.
    const rootUserMsg = currentMessages.find(
      (m) => m.conversation_id === rootConvId && m.role === 'user',
    );
    const sourceConversationId = rootUserMsg?.source_conversation_id;

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
      source_conversation_id: sourceConversationId,
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
    const streamStartedAt = Date.now();

    // streamingMessages holds the FULL thread (history + new turn) so the
    // chat page can render the whole conversation from this slot while the
    // stream is in flight. Only the new user/assistant messages change
    // inside - the historical ones pass through unchanged.
    const nextMessages = [...currentMessages, userMsg, assistantMsg];
    set({
      currentMessages: nextMessages,
      streamingMessages: nextMessages,
      isStreaming: true,
      streamingMessageId: assistantMsgId,
      streamingThreadId: threadId,
      streamingOrigin: 'edit',
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
        const startedAtMs = data.started_at_ms ?? Date.now() - streamStartedAt;
        mapMsgs((m) => {
          if (m.id !== assistantMsgId) return m;
          const update: Partial<typeof m> = {
            streamingSteps: appendActiveStep(m.streamingSteps, data.node, data.message, startedAtMs),
          };
          // Retry: executor starting again means a new execution cycle —
          // reset data/chart readiness so stale 0-row results are cleared.
          if (data.node === 'executor' && m.dataReady) {
            update.dataReady = false;
            update.chartReady = false;
          }
          return { ...m, ...update };
        });
      },
      onSparqlGenerated: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, metadata_: { ...m.metadata_, sql: data.sql } }
            : m,
        );
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
            ? {
                ...m,
                reasoning: (m.reasoning || '') + data.text,
                reasoningPending: false,
                streamingSteps: appendStepReasoning(m.streamingSteps, data.text, data.node as string | undefined),
              }
            : m,
        );
      },
      onExecuteDone: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? {
                ...m,
                dataReady: true,
                willVisualize: data.will_visualize ?? false,
                metadata_: {
                  ...m.metadata_,
                  sql: data.sql,
                  columns: data.columns,
                  rows: data.rows,
                  row_count: data.row_count,
                  source_tables: (data.source_tables as string[] | undefined) ?? m.metadata_?.source_tables,
                  data_freshness_at: (data.data_freshness_at as string | undefined) ?? m.metadata_?.data_freshness_at,
                  metric_name: (data.metric_name as string | undefined) ?? m.metadata_?.metric_name,
                  metric_owner: (data.metric_owner as string | undefined) ?? m.metadata_?.metric_owner,
                  metric_defined_at: (data.metric_defined_at as string | undefined) ?? m.metadata_?.metric_defined_at,
                },
              }
            : m,
        );
      },
      onChart: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId ? { ...m, chartReady: true, metadata_: { ...m.metadata_, chart_spec: data.spec, chart_type: data.chart_type as string | undefined, alternative_chart_specs: data.alternative_chart_specs as { chart_type: string; spec: Record<string, unknown> }[] | undefined } } : m,
        );
      },
      onConfidence: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId ? { ...m, metadata_: { ...m.metadata_, confidence: data } } : m,
        );
      },
      onTribalFacts: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId ? { ...m, metadata_: { ...m.metadata_, tribal_facts: data.facts } } : m,
        );
      },
      onVizSkip: () => {
        mapMsgs((m) =>
          m.id === assistantMsgId ? { ...m, chartReady: true } : m,
        );
      },
      onNodeDone: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, streamingSteps: markStepDone(m.streamingSteps, data.node, data.duration_ms, data.status ?? 'done', data.total_tokens as number | undefined) }
            : m,
        );
      },
      onNodeTokens: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId
            ? { ...m, streamingSteps: updateStepTokens(m.streamingSteps, data.node, data.tokens) }
            : m,
        );
      },
      onFollowUps: (data) => {
        mapMsgs((m) =>
          m.id === assistantMsgId ? { ...m, followUpsReady: true, metadata_: { ...m.metadata_, follow_ups: data.questions } } : m,
        );
      },
      onDone: (data) => {
        const convId = data.conversation_id as string;
        const mapper = (m: Message): Message => {
          if (m.id === assistantMsgId) return {
            ...m,
            conversation_id: convId,
            content: m.content || ((data.answer as string) ?? m.content),
            isStreaming: false,
            streamingSteps:
              finalizeStepsFromDone(data.pipeline_steps) ??
              (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })),
            metadata_: {
              ...m.metadata_,
              sql: (data.sql as string) ?? m.metadata_?.sql,
              columns: (data.columns as string[]) ?? m.metadata_?.columns,
              rows: (data.rows as unknown[][]) ?? m.metadata_?.rows,
              row_count: (data.row_count as number) ?? m.metadata_?.row_count,
              duration_ms: (data.duration_ms as number) ?? m.metadata_?.duration_ms,
              pipeline_steps:
                (data.pipeline_steps as PipelineStep[] | undefined) ?? m.metadata_?.pipeline_steps,
              source_tables: (data.source_tables as string[] | undefined) ?? m.metadata_?.source_tables,
              data_freshness_at: (data.data_freshness_at as string | undefined) ?? m.metadata_?.data_freshness_at,
              metric_name: (data.metric_name as string | undefined) ?? m.metadata_?.metric_name,
              metric_owner: (data.metric_owner as string | undefined) ?? m.metadata_?.metric_owner,
              metric_defined_at: (data.metric_defined_at as string | undefined) ?? m.metadata_?.metric_defined_at,
              token_usage: (data.token_usage as TokenUsage | undefined) ?? m.metadata_?.token_usage,
              langfuse_trace_id: (data.langfuse_trace_id as string | undefined) ?? m.metadata_?.langfuse_trace_id,
              langfuse_trace_url: (data.langfuse_trace_url as string | undefined) ?? m.metadata_?.langfuse_trace_url,
              intent: (data.intent as string | undefined) ?? m.metadata_?.intent,
              preference_summary: (data.preference_summary as PreferenceSummary | undefined) ?? m.metadata_?.preference_summary,
            },
          };
          if (m.id === userMsgId) return { ...m, conversation_id: convId };
          return m;
        };
        set((state) => {
          const updated = state.currentMessages.map(mapper);
          return {
            currentMessages: updated,
            threadMessageMap: { ...state.threadMessageMap, [threadId]: updated },
            streamingMessages: [],
            isStreaming: false,
            isStopping: false,
            streamingMessageId: null,
            streamingThreadId: null,
            abortController: null,
          };
        });
        get().fetchRecents();
      },
      onStopped: (data) => {
        const convId = (data.conversation_id as string) || '';
        const durationMs = data.duration_ms as number | undefined;
        const finalSteps = (data.pipeline_steps as StreamingStep[] | undefined);
        set((state) => {
          const updated = state.currentMessages.map((m) => {
            if (m.id === assistantMsgId) return {
              ...m,
              ...(convId ? { conversation_id: convId } : {}),
              isStreaming: false,
              metadata_: {
                ...m.metadata_,
                stopped: true,
                ...(durationMs != null ? { duration_ms: durationMs } : {}),
              },
              streamingSteps: finalSteps
                ? finalSteps.map((s) => ({ ...s, status: 'done' as const }))
                : (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })),
            };
            if (convId && m.id === userMsgId) return { ...m, conversation_id: convId };
            return m;
          });
          return {
            currentMessages: updated,
            threadMessageMap: { ...state.threadMessageMap, [threadId]: updated },
            streamingMessages: [],
            isStreaming: false,
            isStopping: false,
            streamingMessageId: null,
            streamingThreadId: null,
            abortController: null,
          };
        });
        get().fetchRecents();
      },
      onError: (data) => {
        set((state) => ({
          currentMessages: state.currentMessages.map((m) =>
            m.id === assistantMsgId
              ? { ...m, content: data.message || 'Edit failed.', isStreaming: false, streamingSteps: markStepError(m.streamingSteps) }
              : m,
          ),
          streamingMessages: [],
          isStreaming: false,
          isStopping: false,
          streamingMessageId: null,
          streamingThreadId: null,
          abortController: null,
        }));
        get().fetchRecents();
      },
    };

    try {
      const { usePreferencesStore: editPrefs } = await import('./preferences');
      const editPrefsState = editPrefs.getState();
      const editBody: Record<string, unknown> = {
        conversation_id: conversationId,
        question,
        response_tone: editPrefsState.responseTone,
        max_rows: editPrefsState.maxResultRows,
        deep_analysis: editPrefsState.deepAnalysis ?? false,
      };
      if (sourceConversationId) editBody.source_conversation_id = sourceConversationId;
      await streamSSE(
        `/chat/${threadId}/edit`,
        editBody,
        handlers,
        controller.signal,
      );
    } catch (err: unknown) {
      if ((err as Error).name !== 'AbortError') {
        handlers.onError?.({ message: 'Edit failed' });
      }
    } finally {
      if (get().streamingMessageId === assistantMsgId) {
        set((state) => {
          const updated = state.currentMessages.map((m) =>
            m.id === assistantMsgId
              ? { ...m, isStreaming: false, streamingSteps: (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })) }
              : m,
          );
          return {
            currentMessages: updated,
            threadMessageMap: { ...state.threadMessageMap, [threadId]: updated },
            streamingMessages: [],
            isStreaming: false,
            streamingMessageId: null,
            streamingThreadId: null,
            abortController: null,
          };
        });
        get().fetchRecents();
      }
    }
  },

  stopGeneration: async (threadId) => {
    // Mark as stopping immediately so the stop button disables.
    set({ isStopping: true });

    // Tell the backend to stop. The generator will hit the next _check()
    // and yield a `stopped` event through the LIVE SSE connection.
    // We deliberately do NOT abort the SSE connection here — aborting
    // kills the stream before the `stopped` event can arrive, which is
    // the root cause of stop not working reliably.
    try {
      await api.stopGeneration(threadId);
    } catch {
      // Network error on the stop request — fall through to 5s timeout
    }

    // 5-second timeout fallback: if the backend never sends stopped/done
    // (e.g. the stop request was lost or the process crashed), force-abort
    // the SSE connection and finalize the message locally.
    const { abortController, streamingMessageId } = get();
    const stopTimeout = setTimeout(() => {
      if (!get().isStreaming) return; // onStopped already fired — nothing to do
      abortController?.abort();
      set((state) => {
        const updated = state.currentMessages.map((m) =>
          m.id === streamingMessageId
            ? {
                ...m,
                isStreaming: false,
                isStopping: false,
                metadata_: { ...m.metadata_, stopped: true },
                streamingSteps: (m.streamingSteps || []).map((s) => ({ ...s, status: 'done' as const })),
              }
            : m,
        );
        return {
          currentMessages: updated,
          threadMessageMap: { ...state.threadMessageMap, [threadId]: updated },
          streamingMessages: [],
          isStreaming: false,
          isStopping: false,
          streamingMessageId: null,
          streamingThreadId: null,
          abortController: null,
        };
      });
      get().fetchRecents();
    }, 5000);

    // Clean up the timeout when onStopped / onDone fires naturally.
    // We attach it to the store so the SSE handlers can clear it.
    set({ _stopTimeout: stopTimeout } as never);
  },

  // ─── Feedback ───

  submitFeedback: async (threadId, conversationId, liked, comment, feedbackType) => {
    const result = await api.submitFeedback(threadId, conversationId, { liked, comment, feedback_type: feedbackType });
    // Update both currentMessages (live render) and threadMessageMap (cache)
    // so navigating away and back doesn't reset the feedback indicator.
    const apply = (m: Message) =>
      m.conversation_id === conversationId && m.role === 'assistant'
        ? { ...m, feedback: { liked: result.liked, comment: result.comment ?? undefined } }
        : m;
    set((state) => {
      const cached = state.threadMessageMap[threadId];
      return {
        currentMessages: state.currentMessages.map(apply),
        ...(cached
          ? { threadMessageMap: { ...state.threadMessageMap, [threadId]: cached.map(apply) } }
          : {}),
      };
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
    // (matches Claude.ai - only one in-flight request at a time).

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

    // Otherwise (different thread, or no preserved state). Load from the
    // in-memory map for instant render; fetchThread will background-refresh.
    recordCacheAccess(id);
    const cached = get().threadMessageMap[id];
    // Look up metadata from the sidebar threads list so we don't flash
    // null/false defaults while fetchThread loads the full detail.
    const threadMeta = get().threads.find((t) => t.id === id);
    if (cached?.length) {
      log('switch-thread-from-map');
      set({
        currentThreadId: id,
        currentMessages: cached,
        currentThreadTitle: threadMeta?.title ?? null,
        currentThreadStarred: threadMeta?.starred ?? false,
        currentThreadProjectId: threadMeta?.project_id ?? null,
        messagesLoading: false,
      });
    } else {
      log('switch-thread-clear');
      set({
        currentThreadId: id,
        currentMessages: [],
        currentThreadTitle: threadMeta?.title ?? null,
        currentThreadStarred: threadMeta?.starred ?? false,
        currentThreadProjectId: threadMeta?.project_id ?? null,
      });
    }
  },

  setSearchQuery: (query) => {
    set({ searchQuery: query });
    const existing = getSearchTimer();
    if (existing) clearTimeout(existing);
    setSearchTimer(
      setTimeout(() => {
        setSearchTimer(null);
        get().fetchRecents({ search: query });
      }, 300),
    );
  },

  setPendingQuestion: (question, deepAnalysis = false) => {
    set({ pendingQuestion: question, pendingDeepAnalysis: deepAnalysis });
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

  setActiveVersion: (threadId, versionsKey, idx) => {
    set((state) => ({
      activeVersions: {
        ...state.activeVersions,
        [threadId]: { ...(state.activeVersions[threadId] ?? {}), [versionsKey]: idx },
      },
    }));
  },

  clearActiveVersionsForThread: (threadId) => {
    set((state) => {
      if (!(threadId in state.activeVersions)) return state;
      const next = { ...state.activeVersions };
      delete next[threadId];
      return { activeVersions: next };
    });
  },
}), {
  name: 'mti-brain-threads-cache',
  storage: createJSONStorage(() => localStorage),
  // Only persist cacheable list data - never persist streaming/SSE state,
  // active controllers, or per-thread message buffers (those refresh on
  // demand from the backend).
  partialize: (state) => ({
    threads: state.threads,
    threadsLastFetched: state.threadsLastFetched,
    threadsOffset: state.threadsOffset,
    hasMore: state.hasMore,
    activeVersions: state.activeVersions,
  }),
}));
