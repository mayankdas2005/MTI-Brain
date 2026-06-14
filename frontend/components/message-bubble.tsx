'use client';

import { Message, StreamingStep, useThreadStore } from '@/lib/store/threads';
import { usePreferencesStore } from '@/lib/store/preferences';
import { Button } from '@/components/ui/button';
import { Copy, RotateCcw, ChevronLeft, ChevronRight, ChevronDown, Pencil, X, Check, Code2, TableIcon, Info, MoreHorizontal, Pin, LayoutDashboard, Loader2, Network } from 'lucide-react';
import { usePinnedMetricsStore } from '@/lib/store/pinned-metrics';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/dialog';
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { SqlBlock } from './sql-block';
import { useState, useRef, useEffect, useCallback } from 'react';
import { toast } from '@/lib/toast';
import { copyText } from '@/lib/utils';
import { generateDashboard, getDashboard, downloadDashboard } from '@/lib/api/dashboard';
import { useDashboardStore, DASHBOARD_TIMEOUT_MS } from '@/lib/store/dashboard';
import { generateGraphContext, getGraphContext, downloadGraphContext } from '@/lib/api/graph_context';
import { useGraphContextStore, GRAPH_CONTEXT_TIMEOUT_MS } from '@/lib/store/graph_context';
import { MarkdownRenderer } from './markdown-renderer';
import { FeedbackWidget } from './feedback-widget';
import { FollowUpChips } from './follow-up-chips';
import { MessageVisualization } from './message-visualization';
import { DataTable } from './data-table';
import { ThinkingWords } from './thinking-words';
import { TrustStrip } from './messages/trust-strip';
import { Skeleton } from '@/components/ui/skeleton';
import { AboutPanel } from './messages/about-panel';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';

/** Live elapsed-time counter that ticks every 100ms while streaming.
 *  When a timing anchor from the backend is available, elapsed time
 *  is derived from the server's clock so it matches the persisted
 *  duration_ms on completion. */
function LiveTimer({ startTime, anchor }: {
  startTime: number;
  anchor?: { serverElapsedMs: number; clientReceivedAt: number };
}) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const id = setInterval(() => {
      if (anchor) {
        // server elapsed at receipt + time since receipt
        setElapsed(anchor.serverElapsedMs + (Date.now() - anchor.clientReceivedAt));
      } else {
        setElapsed(Date.now() - startTime);
      }
    }, 100);
    return () => clearInterval(id);
  }, [startTime, anchor]);
  return <span>{(elapsed / 1000).toFixed(1)}s</span>;
}
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from '@/components/ui/accordion';

export interface VersionNav {
  current: number;
  total: number;
  onPrev: () => void;
  onNext: () => void;
  hasPrev: boolean;
  hasNext: boolean;
}

interface MessageBubbleProps {
  message: Message;
  threadId: string;
  versionNav?: VersionNav;
}

export function MessageBubble({ message, threadId, versionNav }: MessageBubbleProps) {
  const [editing, setEditing] = useState(false);

  const [editText, setEditText] = useState(message.content);
  const [dataView, setDataView] = useState<'sql' | 'table'>(() => {
    const pref = usePreferencesStore.getState();
    const md = message.metadata_;
    const hasRows = !!(md?.rows && (md.rows as unknown[]).length > 0);
    if (!hasRows && md?.sql) return 'sql';
    if (!pref.showSQL) return 'table';
    return pref.defaultDataView;
  });
  const [aboutOpen, setAboutOpen] = useState(false);
  const [aboutQuestion, setAboutQuestion] = useState<string | null>(null);
  const reasoningRef = useRef<HTMLDivElement>(null);
  const reasoningUserScrolledRef = useRef(false);

  // Resolve the user's question for the About panel at click time. We
  // read from the store imperatively so the bubble doesn't re-render on
  // every store update - currentMessages thrashes during streaming.
  const handleOpenAbout = () => {
    const all = useThreadStore.getState().currentMessages;
    const userMsg = all.find(
      (m) => m.role === 'user' && m.conversation_id === message.conversation_id,
    );
    setAboutQuestion(userMsg?.content ?? null);
    setAboutOpen(true);
  };

  // Auto-scroll reasoning block while streaming (only when user hasn't scrolled up)
  useEffect(() => {
    if (message.isStreaming && reasoningRef.current && !reasoningUserScrolledRef.current) {
      reasoningRef.current.scrollTop = reasoningRef.current.scrollHeight;
    }
  }, [message.reasoning, message.isStreaming]);

  const retryResponse = useThreadStore((s) => s.retryResponse);
  const askQuestion = useThreadStore((s) => s.askQuestion);
  const editQuestion = useThreadStore((s) => s.editQuestion);
  const isStreaming = useThreadStore((s) => s.isStreaming);

  const pinMetric = usePinnedMetricsStore((s) => s.pinMetric);
  const [pinDialogOpen, setPinDialogOpen] = useState(false);
  const [pinLabel, setPinLabel] = useState('');
  const pinSubmittingRef = useRef(false);

  // Find the user's question that prompted this assistant response -
  // that's what we save as source_query so "Re-run" repeats the right query.
  const allMessages = useThreadStore((s) => s.currentMessages);
  const sourceQuestion = (() => {
    const idx = allMessages.findIndex((m) => m.id === message.id);
    if (idx <= 0) return message.content;
    return (
      allMessages.slice(0, idx).reverse().find((m) => m.role === 'user')?.content ??
      message.content
    );
  })();

  const handlePin = () => {
    if (!pinLabel.trim() || pinSubmittingRef.current) return;
    pinSubmittingRef.current = true;
    void pinMetric(pinLabel.trim(), sourceQuestion)
      .then(() => {
        toast.success(`"${pinLabel.trim()}" pinned to home`);
        setPinDialogOpen(false);
      })
      .catch(() => toast.error('Failed to pin metric.'))
      .finally(() => { pinSubmittingRef.current = false; });
  };

  const copyToClipboard = async () => {
    const ok = await copyText(message.content);
    if (ok) toast.success('Copied to clipboard');
    else toast.error('Copy failed');
  };

  const handleRetry = () => {
    if (isStreaming) return;
    if (message.conversation_id) {
      retryResponse(threadId, message.conversation_id);
    } else {
      // Error case: no conversation_id was assigned — re-ask the preceding user message
      const msgs = useThreadStore.getState().currentMessages;
      const idx = msgs.findIndex((m) => m.id === message.id);
      const userMsg = idx > 0 ? msgs.slice(0, idx).reverse().find((m) => m.role === 'user') : undefined;
      if (userMsg?.content) askQuestion(threadId, userMsg.content);
    }
  };

  const handleEdit = () => {
    setEditText(message.content);
    setEditing(true);
  };

  // Synchronous lockout so a fast double-click can't fire two edits before
  // isStreaming flips. Cleared when the editor opens again.
  const editSubmittingRef = useRef(false);
  const handleEditSubmit = () => {
    const trimmed = editText.trim();
    if (!trimmed || !message.conversation_id || isStreaming || editSubmittingRef.current) return;
    editSubmittingRef.current = true;
    editQuestion(threadId, message.conversation_id, trimmed);
    setEditing(false);
    // Release the lock on the next tick - by then the store has flipped
    // isStreaming, which prevents subsequent submits via the existing guard.
    queueMicrotask(() => {
      editSubmittingRef.current = false;
    });
  };

  const isUser = message.role === 'user';

  if (isUser) {
    if (editing) {
      return (
        <div className="flex justify-end px-4 py-1">
          <div className="max-w-[92%] md:max-w-[75%] w-full">
            <textarea
              value={editText}
              onChange={(e) => setEditText(e.target.value)}
              className="w-full rounded-2xl px-4 py-3 text-sm leading-relaxed bg-muted border border-border resize-none focus:outline-none focus:ring-1 focus:ring-ring min-h-[60px]"
              maxLength={2000}
              autoFocus
            />
            <div className="flex gap-1 mt-1 justify-end">
              <Button size="sm" variant="ghost" onClick={() => setEditing(false)} className="h-7 px-2">
                <X className="w-3.5 h-3.5" />
              </Button>
              <Button size="sm" onClick={handleEditSubmit} className="h-7 px-2" disabled={!editText.trim() || isStreaming}>
                <Check className="w-3.5 h-3.5" />
              </Button>
            </div>
          </div>
        </div>
      );
    }

    const messageDate = new Date(message.created_at);
    const timeStr = `${messageDate.toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${messageDate.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`;

    return (
      <div className="flex flex-col items-end px-4 py-1 group">
        {/* Persistent version pill - always visible when this turn has alternates */}
        {versionNav && (
          <div
            key={versionNav.total}
            className="flex items-center gap-0.5 mb-1 mr-1 rounded-full border border-border bg-background/80 backdrop-blur-sm px-1 py-0.5 shadow-sm animate-pop-in"
          >
            <Button
              variant="ghost"
              size="sm"
              className="h-5 w-5 p-0 text-muted-foreground hover:text-foreground hover:bg-accent rounded-full"
              onClick={versionNav.onPrev}
              disabled={!versionNav.hasPrev}
            >
              <ChevronLeft className="w-3 h-3" />
            </Button>
            <span className="text-[10px] font-medium text-muted-foreground tabular-nums px-1">
              v{versionNav.current}/{versionNav.total}
            </span>
            <Button
              variant="ghost"
              size="sm"
              className="h-5 w-5 p-0 text-muted-foreground hover:text-foreground hover:bg-accent rounded-full"
              onClick={versionNav.onNext}
              disabled={!versionNav.hasNext}
            >
              <ChevronRight className="w-3 h-3" />
            </Button>
          </div>
        )}

        {/* Actions row - visible at low opacity by default (mobile-friendly),
            full opacity on hover. Mirrors the assistant-message action pills. */}
        <div className="flex items-center gap-1.5 mb-1 mr-1 opacity-60 hover:opacity-100 transition-opacity duration-150">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                onClick={async () => {
                  const ok = await copyText(message.content);
                  if (ok) toast.success('Copied to clipboard');
                  else toast.error('Copy failed');
                }}
                className="tap-44 h-6 w-6 p-0 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent"
              >
                <Copy className="w-3 h-3" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="top">Copy</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                onClick={handleRetry}
                className="tap-44 h-6 w-6 p-0 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent"
                disabled={isStreaming}
              >
                <RotateCcw className="w-3 h-3" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="top">Retry</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                onClick={handleEdit}
                className="tap-44 h-6 w-6 p-0 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent"
                disabled={isStreaming}
              >
                <Pencil className="w-3 h-3" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="top">Edit</TooltipContent>
          </Tooltip>
        </div>

        {/* Refinement context — only shown for "Refine this query" messages,
            not follow-up chips which also carry source_conversation_id */}
        {message.source_conversation_id && message.metadata_?.is_refinement && (() => {
          const all = useThreadStore.getState().currentMessages;
          const origin = all.find(
            (m) => m.conversation_id === message.source_conversation_id && m.role === 'user',
          );
          if (!origin) return null;
          const preview = origin.content.length > 60
            ? origin.content.slice(0, 60).trimEnd() + '…'
            : origin.content;
          return (
            <div className="flex items-center gap-1 mb-1 mr-1 max-w-[80%] text-right">
              <span className="text-[11px] text-muted-foreground/50 truncate">
                ↳ Refining: <span className="italic">{preview}</span>
              </span>
            </div>
          );
        })()}

        {/* Message bubble */}
        <div
          className="max-w-[92%] md:max-w-[80%] rounded-2xl px-5 py-3 text-sm leading-relaxed"
          style={{
            backgroundColor: 'var(--user-bubble)',
            color: 'var(--user-bubble-foreground)',
          }}
        >
          <p className="whitespace-pre-wrap break-words">{message.content}</p>
          <Tooltip>
            <TooltipTrigger asChild>
              <p className="text-[11px] opacity-40 mt-1.5 text-right cursor-default">{timeStr}</p>
            </TooltipTrigger>
            <TooltipContent side="left">
              {messageDate.toLocaleString([], { dateStyle: 'long', timeStyle: 'short' })}
            </TooltipContent>
          </Tooltip>
        </div>
      </div>
    );
  }

  // Assistant message
  const sql = message.metadata_?.sql;
  const columns = message.metadata_?.columns;
  const rows = message.metadata_?.rows;
  const rowCount = message.metadata_?.row_count;
  const exportFilename = `query-results-${(message.conversation_id ?? '').slice(0, 8)}-${new Date().toISOString().slice(0, 10)}`;
  const followUps = message.metadata_?.follow_ups;
  const hasTableData = !!(columns && columns.length > 0 && rows && rows.length > 0);

  // Dashboard state — persisted in Zustand across page navigations
  const convId = message.conversation_id ?? '';
  const dashEntry  = useDashboardStore((s) => s.get(convId));
  const setDash    = useDashboardStore((s) => s.set);
  const dashStatus = dashEntry?.status ?? 'idle';
  // Treat as timed-out if pending for >5 min — shows Retry instead of forever spinner
  const dashTimedOut = dashStatus === 'pending' && !!dashEntry && (Date.now() - dashEntry.queuedAt) > DASHBOARD_TIMEOUT_MS;
  // Only assistant messages can have dashboards; user messages never do.
  const showDashEntry  = !!convId && message.role === 'assistant' && !!message.content;
  const showDashButton = showDashEntry && (rowCount ?? 0) >= 2;

  // On mount: sync dashboard state with server.
  // Runs for both 'idle' (restore) and 'ready'/'failed' (verify still exists).
  // Clears stale localStorage entries when DB record is gone (404).
  const removeDash = useDashboardStore((s) => s.remove);
  const dashCheckFiredRef = useRef(false);
  useEffect(() => {
    if (!convId || !showDashButton) return;
    if (dashStatus === 'idle') return;
    if (dashStatus === 'pending' && !dashTimedOut) return;
    // Deduplicate: React StrictMode fires effects twice in dev; ref prevents the double GET
    if (dashCheckFiredRef.current) return;
    dashCheckFiredRef.current = true;
    getDashboard(convId)
      .then((res) => {
        if (res.status === 'ready') {
          setDash(convId, { status: 'ready',   url: res.url ?? null, queuedAt: Date.now() });
        } else if (res.status === 'pending') {
          setDash(convId, { status: 'pending', url: null,            queuedAt: Date.now() });
        } else if (res.status === 'failed') {
          setDash(convId, { status: 'failed',  url: null,            queuedAt: Date.now() });
        }
      })
      .catch(() => {
        // 404 = DB record deleted or never created → clear stale localStorage
        removeDash(convId);
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [convId, showDashButton]);

  // Graph Context state — persisted in Zustand
  const gcEntry      = useGraphContextStore((s) => s.get(convId));
  const setGC        = useGraphContextStore((s) => s.set);
  const removeGC     = useGraphContextStore((s) => s.remove);
  const gcStatus     = gcEntry?.status ?? 'idle';
  const gcTimedOut   = gcStatus === 'pending' && !!gcEntry && (Date.now() - gcEntry.queuedAt) > GRAPH_CONTEXT_TIMEOUT_MS;
  const showGCButton = !!convId && message.role === 'assistant' && !!message.content && !!sql;

  const gcCheckFiredRef = useRef(false);
  useEffect(() => {
    if (!showGCButton) return;
    if (gcStatus === 'idle') return;
    if (gcStatus === 'pending' && !gcTimedOut) return;
    if (gcCheckFiredRef.current) return;
    gcCheckFiredRef.current = true;
    getGraphContext(convId)
      .then((res) => {
        if (res.status === 'ready') {
          setGC(convId, { status: 'ready',   url: res.url ?? null, queuedAt: Date.now() });
        } else if (res.status === 'pending') {
          setGC(convId, { status: 'pending', url: null,            queuedAt: Date.now() });
        } else if (res.status === 'failed') {
          setGC(convId, { status: 'failed',  url: null,            queuedAt: Date.now() });
        }
      })
      .catch(() => {
        removeGC(convId);
      });
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [convId, showGCButton]);

  // User preferences
  const prefAutoCharts = usePreferencesStore((s) => s.autoShowCharts);
  const prefShowFollowUps = usePreferencesStore((s) => s.showFollowUps);
  const prefShowReasoning = usePreferencesStore((s) => s.showReasoning);

  const showSQLTab  = !!(sql);
  const hasDataView = !!(showSQLTab || hasTableData);

  // When SQL arrives with 0 rows (during or after streaming), auto-switch to
  // SQL view so the user can see what query ran instead of an empty table.
  useEffect(() => {
    if (sql && !hasTableData) {
      setDataView('sql');
    }
  }, [message.isStreaming, sql, hasTableData]);

  const answerSynthesisDone = message.streamingSteps?.some(
    (s) => s.node === 'synthesis' && s.status === 'done'
  ) ?? false;

  return (
    <div
      id={`msg-${message.id}`}
      className="flex flex-col gap-[var(--density-row-gap)] px-4 py-[var(--density-pad-y)] animate-fade-in"
    >
      {/* Reasoning Block - vertical step timeline. The horizontal strip is gone:
          all per-step state (running, done, reasoning) lives inside this panel
          so it scales as the pipeline grows from 4 to 20+ nodes. */}
      {prefShowReasoning && (message.isStreaming || (message.streamingSteps?.length ?? 0) > 0 || message.reasoning || message.metadata_?.duration_ms != null) && (
        <ReasoningPanel
          message={message}
          reasoningRef={reasoningRef}
        />
      )}

      {/* SQL / Data toggle */}
      {hasDataView && (
        <div className="mb-2 space-y-2 animate-fade-only">
          <div className="flex items-center gap-1">
            {showSQLTab && (
              <Button
                variant={dataView === 'sql' ? 'secondary' : 'ghost'}
                size="sm"
                className="h-7 px-2.5 text-xs gap-1.5"
                onClick={() => setDataView('sql')}
              >
                <Code2 className="w-3.5 h-3.5" />
                SQL
              </Button>
            )}
            {hasTableData && (
              <Button
                variant={dataView === 'table' ? 'secondary' : 'ghost'}
                size="sm"
                className="h-7 px-2.5 text-xs gap-1.5"
                onClick={() => setDataView('table')}
              >
                <TableIcon className="w-3.5 h-3.5" />
                Data
              </Button>
            )}
          </div>
          {dataView === 'sql' && showSQLTab && (
            <div className="rounded-lg border border-border bg-muted/50 overflow-hidden max-h-96 flex flex-col">
              <div className="flex items-center justify-end px-3 py-1.5 border-b border-border bg-muted/30">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 w-6 p-0 text-muted-foreground"
                  onClick={async () => {
                    const ok = await copyText(sql!);
                    if (ok) toast.success('SQL copied');
                    else toast.error('Copy failed');
                  }}
                >
                  <Copy className="w-3.5 h-3.5" />
                </Button>
              </div>
              <div className="flex-1 overflow-y-auto">
                <SqlBlock code={sql!} />
              </div>
            </div>
          )}
          {dataView === 'table' && hasTableData && (
            <DataTable columns={columns!} rows={rows!} rowCount={rowCount} filename={exportFilename} isStreaming={message.isStreaming} />
          )}
        </div>
      )}

      {/* Message Content */}
      {!!message.content && (
        <StreamingContent isStreaming={message.isStreaming} hasContent>
          <MarkdownRenderer content={message.content} isUser={false} />
        </StreamingContent>
      )}

      {/* Chart skeleton — only shown after answer_synthesis is done, right before visualization fires */}
      {message.isStreaming && answerSynthesisDone && !message.chartReady && prefAutoCharts && hasTableData && message.willVisualize !== false && (
        <div className="mt-3 rounded-xl border border-border bg-sidebar px-4 pt-5 pb-5 animate-fade-in">
          <Skeleton className="h-3 w-48 rounded mb-1.5" />
          <Skeleton className="h-2.5 w-28 rounded mb-5 opacity-60" />
          <div className="flex items-end gap-2.5 h-56">
            {[45, 72, 38, 88, 60, 95, 52, 78, 42, 68, 83, 35].map((h, i) => (
              <Skeleton key={i} className="flex-1 rounded-sm" style={{ height: `${h}%` }} />
            ))}
          </div>
          <div className="flex justify-center mt-4">
            <Skeleton className="h-2.5 w-32 rounded opacity-50" />
          </div>
        </div>
      )}

      {/* Chart Visualization — appears as soon as chart event fires */}
      {!!(message.chartReady ?? !message.isStreaming) && prefAutoCharts && !!message.metadata_?.chart_spec && (
        <MessageVisualization
          columns={columns}
          rows={rows}
          chartSpec={message.metadata_.chart_spec}
          primaryChartType={message.metadata_?.chart_type}
          alternativeChartSpecs={message.metadata_?.alternative_chart_specs}
          conversationId={message.conversation_id}
        />
      )}

      {/* Stopped indicator — inline dot after content, or standalone if no content */}
      {!message.isStreaming && !!message.metadata_?.stopped && (
        message.content
          ? <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground/40 mt-1">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-muted-foreground/30" />
              Generation stopped
            </div>
          : <div className="flex items-center gap-1.5 text-sm text-muted-foreground/60 mt-1">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-muted-foreground/40" />
              Response stopped before content was generated.
            </div>
      )}

      {/* Follow-up Chips + Refine — appear as soon as follow_ups event fires */}
      {!!(message.followUpsReady ?? !message.isStreaming) && prefShowFollowUps && followUps && followUps.length > 0 && (
        <FollowUpChips threadId={threadId} followUps={followUps} conversationId={message.conversation_id} />
      )}
      {!message.isStreaming && message.role === 'assistant' && !!message.content &&
        !!(message.metadata_ as Record<string, unknown> | null)?.sql && (
        <RefineInput threadId={threadId} conversationId={message.conversation_id} />
      )}

      {/* Trust strip — only for SQL-backed answers. Only shown after streaming
          is fully done so row count never appears mid-generation. We gate on
          sql being non-empty so conversational responses never show 0 rows. */}
      {!message.isStreaming && (() => {
        const m = message.metadata_ as Record<string, unknown> | null;
        if (!m) return null;
        const sql = (m.sql as string | null | undefined) ?? '';
        if (!sql.trim()) return null;
        const metric = m.metric_name
          ? { name: m.metric_name as string, owner: m.metric_owner as string | null, definedAt: m.metric_defined_at as string | null }
          : null;
        return (
          <div className="mt-1.5">
            <TrustStrip
              sources={m.source_tables as string[] | null}
              freshnessAt={m.data_freshness_at as string | null}
              metric={metric}
              rowCount={m.row_count as number | null}
              confidence={m.confidence as { score: number; label: 'High' | 'Medium' | 'Low'; explanation: string } | null | undefined}
            />
          </div>
        );
      })()}

      {/* Timestamp + Actions row */}
      {/* Always rendered — opacity-0 during streaming prevents layout blink on done */}
      <div className={`flex items-center gap-1.5 mt-1 transition-opacity duration-150 ${message.isStreaming ? 'opacity-0 pointer-events-none' : ''}`}>
          {message.created_at && (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="text-[11px] text-muted-foreground/70 cursor-default">
                  {`${new Date(message.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${new Date(message.created_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`}
                </span>
              </TooltipTrigger>
              <TooltipContent side="right">
                {new Date(message.created_at).toLocaleString([], { dateStyle: 'long', timeStyle: 'short' })}
              </TooltipContent>
            </Tooltip>
          )}
          {/* Actions sit immediately after the timestamp on the LEFT.
              Visible at low opacity by default (mobile-friendly), full opacity
              on hover. */}
          <div className="flex items-center gap-0.5 opacity-60 hover:opacity-100 transition-opacity duration-150">
            {/* Primary: Copy */}
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="sm" onClick={copyToClipboard} aria-label="Copy response" className="h-7 w-7 p-0 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent">
                  <Copy className="w-3.5 h-3.5" aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom">Copy</TooltipContent>
            </Tooltip>

            {/* Primary: Retry */}
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="sm" onClick={handleRetry} aria-label="Regenerate response" className="h-7 w-7 p-0 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent" disabled={isStreaming}>
                  <RotateCcw className="w-3.5 h-3.5" aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom">Retry</TooltipContent>
            </Tooltip>

            {/* Primary: Feedback (thumbs) */}
            {message.conversation_id && (
              <FeedbackWidget threadId={threadId} conversationId={message.conversation_id} feedback={message.feedback} />
            )}

            {/* Overflow: TTS, Share, Pin, About */}
            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" size="sm" aria-label="More actions" className="h-7 w-7 p-0 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent">
                    <MoreHorizontal className="w-3.5 h-3.5" aria-hidden />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="start" className="min-w-[180px]">
                  {/* Read aloud - hidden */}
                  {/* {ttsAvailable && message.content && (
                    <DropdownMenuItem onClick={() => isSpeaking ? ttsStop() : ttsSpeak(message.content)} className="gap-2">
                      {isSpeaking ? <Square className="w-4 h-4 fill-current" /> : <Volume2 className="w-4 h-4" />}
                      {isSpeaking ? 'Stop reading' : 'Read aloud'}
                    </DropdownMenuItem>
                  )} */}
                  {message.content && (
                    <DropdownMenuItem onClick={() => { setPinLabel(''); setPinDialogOpen(true); }} className="gap-2">
                      <Pin className="w-4 h-4" />
                      Pin to home
                    </DropdownMenuItem>
                  )}
                  {/* Knowledge Graph Context */}
                  {message.role === 'assistant' && !!message.content && (
                    showGCButton ? (
                      <DropdownMenuItem
                        disabled={gcStatus === 'pending' && !gcTimedOut}
                        onClick={() => {
                          if (gcStatus === 'ready') {
                            void getGraphContext(convId).then((res) => {
                              if (res.url) {
                                window.open(res.url, '_blank', 'noopener');
                                void downloadGraphContext(convId).then(({ blob, filename }) => {
                                  const blobUrl = URL.createObjectURL(blob);
                                  const a = document.createElement('a');
                                  a.href = blobUrl;
                                  a.download = filename;
                                  document.body.appendChild(a);
                                  a.click();
                                  document.body.removeChild(a);
                                  URL.revokeObjectURL(blobUrl);
                                }).catch(() => {});
                              } else {
                                removeGC(convId);
                                toast.info('These sources have expired — click to reload.');
                              }
                            }).catch(() => {
                              removeGC(convId);
                              toast.info('These sources have expired — click to reload.');
                            });
                            return;
                          }
                          setGC(convId, { status: 'pending', url: null, queuedAt: Date.now() });
                          toast.info('Fetching query sources…', {
                            id: `gc-${convId}`,
                            description: 'Looking up the data behind your answer.',
                          });
                          void generateGraphContext(convId).catch((err: unknown) => {
                            setGC(convId, { status: 'failed', url: null, queuedAt: Date.now() });
                            toast.warning(err instanceof Error ? err.message : 'Couldn\'t fetch the query sources.', { id: `gc-${convId}` });
                          });
                        }}
                        className="gap-2"
                      >
                        {gcStatus === 'pending' && !gcTimedOut
                          ? <Loader2 className="w-4 h-4 animate-spin" />
                          : <Network className="w-4 h-4" />
                        }
                        {gcStatus === 'ready'                     ? 'View Graph Context'    :
                         gcStatus === 'pending' && !gcTimedOut    ? 'Generating…'           :
                         gcStatus === 'failed'  || gcTimedOut     ? 'Rebuild Graph Context' :
                                                                    'Build Graph Context'}
                      </DropdownMenuItem>
                    ) : (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <div className="cursor-not-allowed">
                            <DropdownMenuItem disabled className="gap-2 pointer-events-none">
                              <Network className="w-4 h-4" />
                              Build Graph Context
                            </DropdownMenuItem>
                          </div>
                        </TooltipTrigger>
                        <TooltipContent side="right">No knowledge graph query for this answer</TooltipContent>
                      </Tooltip>
                    )
                  )}
                  {showDashEntry && (
                    showDashButton ? (
                      <DropdownMenuItem
                        disabled={dashStatus === 'pending' && !dashTimedOut}
                        onClick={() => {
                          if (dashStatus === 'ready') {
                            // Always fetch a fresh presigned URL — cached URL expires after 7 days
                            void getDashboard(convId).then((res) => {
                              if (res.url) {
                                // Open to display in new tab
                                window.open(res.url, '_blank', 'noopener');
                                // Download via backend (includes auth, avoids S3 CORS)
                                void downloadDashboard(convId).then(({ blob, filename }) => {
                                  const blobUrl = URL.createObjectURL(blob);
                                  const a = document.createElement('a');
                                  a.href = blobUrl;
                                  a.download = filename;
                                  document.body.appendChild(a);
                                  a.click();
                                  document.body.removeChild(a);
                                  URL.revokeObjectURL(blobUrl);
                                }).catch(() => {});
                              } else {
                                // DB record gone — clear stale state, let user regenerate
                                removeDash(convId);
                                toast.info('This report has expired — click to rebuild.');
                              }
                            }).catch(() => {
                              // 404 — DB record deleted, reset to idle
                              removeDash(convId);
                              toast.info('This report has expired — click to rebuild.');
                            });
                            return;
                          }
                          setDash(convId, { status: 'pending', url: null, queuedAt: Date.now() });
                          toast.info('Building your report…', {
                            id: `dash-${convId}`,
                            description: 'We\'re putting this together as an executive report.',
                          });
                          void generateDashboard(convId).catch((err: unknown) => {
                            setDash(convId, { status: 'failed', url: null, queuedAt: Date.now() });
                            toast.warning(err instanceof Error ? err.message : 'Couldn\'t build the report.', { id: `dash-${convId}` });
                          });
                        }}
                        className="gap-2"
                      >
                        {dashStatus === 'pending' && !dashTimedOut
                          ? <Loader2 className="w-4 h-4 animate-spin" />
                          : <LayoutDashboard className="w-4 h-4" />
                        }
                        {dashStatus === 'ready'                     ? 'View Report'       :
                         dashStatus === 'pending' && !dashTimedOut  ? 'Generating…'       :
                         dashStatus === 'failed' || dashTimedOut    ? 'Rebuild Report'    :
                                                    'Build Report'}
                      </DropdownMenuItem>
                    ) : (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <div className="cursor-not-allowed">
                            <DropdownMenuItem disabled className="gap-2 pointer-events-none">
                              <LayoutDashboard className="w-4 h-4" />
                              Build Report
                            </DropdownMenuItem>
                          </div>
                        </TooltipTrigger>
                        <TooltipContent side="right">Requires at least 2 rows of data</TooltipContent>
                      </Tooltip>
                    )
                  )}
                  <DropdownMenuSeparator />
                  <DropdownMenuItem onClick={handleOpenAbout} className="gap-2">
                    <Info className="w-4 h-4" />
                    About this answer
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            <Dialog open={pinDialogOpen} onOpenChange={(o) => { if (!o) setPinDialogOpen(false); }}>
              <DialogContent className="sm:max-w-sm p-6 gap-0">
                <DialogTitle className="text-base font-semibold mb-1">Pin to home</DialogTitle>
                <DialogDescription className="text-sm text-muted-foreground mb-4">
                  Give this metric a name. It will appear on your home page.
                </DialogDescription>
                <input
                  autoFocus
                  value={pinLabel}
                  onChange={(e) => setPinLabel(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault(); // stop Enter from clicking the button
                      handlePin();
                    }
                  }}
                  placeholder="e.g. Daily cash position"
                  className="w-full rounded-xl border border-border bg-muted/50 px-4 py-3 text-sm focus:outline-none focus:ring-1 focus:ring-ring"
                />
                <DialogFooter className="mt-4">
                  <button onClick={() => setPinDialogOpen(false)} className="text-sm text-muted-foreground hover:text-foreground px-3 py-2">Cancel</button>
                  <button onClick={handlePin} className="rounded-xl bg-primary text-primary-foreground text-sm px-4 py-2 hover:bg-primary/90">
                    Pin
                  </button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </div>
        </div>
      <AboutPanel
        open={aboutOpen}
        onOpenChange={setAboutOpen}
        message={message}
        question={aboutQuestion}
      />
    </div>
  );
}

import React from 'react';


// ─── Reasoning content (italic, dim) ───

const ReasoningContent = React.forwardRef<HTMLDivElement, { isStreaming?: boolean; content: string }>(
  ({ isStreaming, content }, ref) => {
    const active = !!(isStreaming && content);
    return (
      <div ref={ref} className="px-3 pb-2 border-t border-border/40 pt-2 text-sm text-foreground/75 leading-relaxed italic">
        <MarkdownRenderer content={content} />
        {active && <span className="streaming-cursor" aria-hidden />}
      </div>
    );
  }
);
ReasoningContent.displayName = 'ReasoningContent';

// ─── Reasoning panel: collapsible header + vertical step timeline ───

function ReasoningPanel({
  message,
  reasoningRef,
}: {
  message: Message;
  reasoningRef: React.RefObject<HTMLDivElement | null>;
}) {
  const steps = message.streamingSteps;
  const hasSteps = !!steps && steps.length > 0;

  // Auto-open when streaming starts; auto-close 400ms after it ends.
  // Historical messages start collapsed (streaming never fired here).
  const [open, setOpen] = useState(hasSteps && !!message.isStreaming);
  const prevHasStepsRef = useRef(hasSteps);
  const prevStreamingRef = useRef<boolean>(!!message.isStreaming);
  useEffect(() => {
    if (!prevHasStepsRef.current && hasSteps) setOpen(true);
    prevHasStepsRef.current = hasSteps;
  }, [hasSteps]);
  useEffect(() => {
    const wasStreaming = prevStreamingRef.current;
    const nowStreaming = !!message.isStreaming;
    if (wasStreaming && !nowStreaming) {
      const t = setTimeout(() => setOpen(false), 400);
      prevStreamingRef.current = nowStreaming;
      timelineUserScrolledRef.current = false;
      return () => clearTimeout(t);
    }
    prevStreamingRef.current = nowStreaming;
  }, [message.isStreaming]);

  // During streaming the content is height-capped so new steps scroll inside
  // the box instead of pushing SQL/Data downward. Auto-scroll keeps the
  // latest active step visible without the user needing to scroll manually.
  const streamScrollRef = useRef<HTMLDivElement>(null);
  const timelineUserScrolledRef = useRef(false);
  useEffect(() => {
    if (message.isStreaming && streamScrollRef.current && !timelineUserScrolledRef.current) {
      streamScrollRef.current.scrollTop = streamScrollRef.current.scrollHeight;
    }
  }, [steps, message.isStreaming]);
  const activeStep = steps?.slice().reverse().find((s) => s.status === 'active');
  const lastStep = steps && steps.length > 0 ? steps[steps.length - 1] : undefined;
  // step.node arrives instantly at node.start; step.message is the prose body
  // the LLM streams later. Fall back to node so the header has a label
  // immediately, matching the timeline at PipelineTimeline line 643.
  const activeLabelStep = activeStep ?? lastStep;
  const activeLabel = activeLabelStep?.message || activeLabelStep?.node;

  // Legacy reasoning text (pre-pipeline_steps messages) used as a fallback
  // when the timeline can't be rendered.
  const legacyReasoning = (message.reasoning || '')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\*\*[^*]+\*\*\s*$/gm, '')
    .replace(/\n---\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();

  return (
    <Accordion
      type="single"
      collapsible
      value={open ? 'reasoning' : ''}
      onValueChange={(v) => setOpen(v === 'reasoning')}
      className="mb-2"
    >
      <AccordionItem
        value="reasoning"
        className={`rounded-lg ${
          message.isStreaming ? 'thinking-glow border border-primary/20' : 'reasoning-complete'
        }`}
      >
        <AccordionTrigger
          className="py-2 px-3 text-xs text-foreground/65 hover:text-foreground hover:no-underline"
          disabled={!hasSteps && !legacyReasoning}
        >
          {message.isStreaming ? (
            <span className="flex items-center gap-1.5">
              <ThinkingWords label={activeLabel} />
              {hasSteps && (
                <span className="tabular-nums text-foreground/50">
                  <LiveTimer
                    startTime={new Date(message.created_at).getTime()}
                    anchor={message._timingAnchor}
                  />
                </span>
              )}
            </span>
          ) : (
            <span>
              Thought
              {message.metadata_?.duration_ms != null &&
                ` for ${(message.metadata_.duration_ms / 1000).toFixed(1)}s`}
            </span>
          )}
        </AccordionTrigger>
        <AccordionContent>
          <div
            ref={streamScrollRef}
            className={message.isStreaming ? 'max-h-56 overflow-y-auto scrollbar-hide' : undefined}
            onScroll={(e) => {
              const el = e.currentTarget;
              timelineUserScrolledRef.current = el.scrollHeight - el.scrollTop - el.clientHeight > 60;
            }}
          >
            {hasSteps ? (
              <PipelineTimeline steps={steps!} isStreaming={!!message.isStreaming} />
            ) : legacyReasoning ? (
              <ReasoningContent
                ref={reasoningRef}
                isStreaming={message.isStreaming}
                content={legacyReasoning}
              />
            ) : null}
          </div>
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  );
}

// ─── Vertical step timeline ───

function PipelineTimeline({ steps, isStreaming }: { steps: StreamingStep[]; isStreaming?: boolean }) {
  // Tracks which completed step indices the user has manually expanded.
  // Active steps are always shown; all collapse automatically when streaming ends.
  const [expandedSteps, setExpandedSteps] = useState<Set<number>>(new Set());

  useEffect(() => {
    if (!isStreaming) setExpandedSteps(new Set());
  }, [isStreaming]);

  const toggleStep = useCallback((idx: number) => {
    setExpandedSteps(prev => {
      const next = new Set(prev);
      if (next.has(idx)) next.delete(idx); else next.add(idx);
      return next;
    });
  }, []);

  return (
    <div className="px-4 pb-3 pt-1 border-t border-border/40">
      {steps.map((step, i) => {
        const isLast = i === steps.length - 1;
        const isActive = step.status === 'active';
        const isDone = step.status === 'done';
        const isSkipped = step.status === 'skipped';
        const isError = step.status === 'error';

        const cleanedReasoning = (step.reasoning || '')
          .replace(/^#{1,6}\s+/gm, '')
          .replace(/^\*\*[^*]+\*\*\s*$/gm, '')
          .replace(/\n---\n/g, '\n')
          .replace(/\n{3,}/g, '\n\n')
          .trim();

        const showDuration =
          step.duration_ms != null && step.duration_ms >= 0
            ? `${(step.duration_ms / 1000).toFixed(1)}s`
            : isActive
            ? 'live'
            : '';

        // Expandable = a completed (or error) step that has reasoning text the user can drill into.
        const isExpandable = (isDone || isError) && !!cleanedReasoning;
        const isExpanded = isActive || expandedSteps.has(i);

        return (
          <div key={step.node + i} className="relative pl-6 pt-2.5">
            {/* Connector line */}
            {!isLast && (
              <span className="absolute left-[7px] top-[1.3rem] w-px bg-border" style={{ bottom: '-0.625rem' }} />
            )}

            {/* Status dot */}
            <span
              className={`absolute left-0 top-[9px] flex items-center justify-center w-3.5 h-3.5 rounded-full transition-colors ${
                isActive
                  ? 'bg-primary step-active'
                  : isDone
                  ? 'bg-primary/30 dark:bg-primary/50'
                  : isError
                  ? 'bg-destructive/20 dark:bg-destructive/40'
                  : 'bg-muted'
              }`}
              aria-hidden="true"
            >
              {isDone  && <Check className="w-2 h-2 text-primary dark:text-primary/90" strokeWidth={3.5} />}
              {isError && <X className="w-2 h-2 text-destructive" strokeWidth={3} />}
            </span>

            {/* Step label + duration + optional expand chevron */}
            <div
              className={`flex items-center justify-between gap-3 ${isExpandable ? 'cursor-pointer select-none' : ''}`}
              onClick={isExpandable ? () => toggleStep(i) : undefined}
            >
              <span
                className={`text-xs leading-none ${
                  isActive
                    ? 'text-foreground font-medium'
                    : isError
                    ? 'text-destructive font-medium'
                    : isSkipped
                    ? 'text-foreground/35 line-through'
                    : 'text-foreground/70'
                }`}
              >
                {step.message || step.node}
              </span>
              <div className="flex items-center gap-1.5 shrink-0">
                {showDuration && (
                  <span
                    className={`text-[10px] tabular-nums ${
                      isActive ? 'text-primary' : isError ? 'text-destructive/70' : 'text-foreground/45'
                    }`}
                  >
                    {showDuration}
                  </span>
                )}
                {isExpandable && (
                  <ChevronDown
                    className={`w-3 h-3 text-foreground/40 transition-transform duration-150 ${
                      expandedSteps.has(i) ? 'rotate-180' : ''
                    }`}
                  />
                )}
              </div>
            </div>

            {/* Per-step reasoning — always visible for active step; collapsed by default for completed steps */}
            {cleanedReasoning && isExpanded && (
              <StepReasoning text={cleanedReasoning} active={isActive} />
            )}

          </div>
        );
      })}
    </div>
  );
}

// ─── Message content with streaming cursor ───

function StreamingContent({ isStreaming, hasContent, children }: { isStreaming?: boolean; hasContent: boolean; children: React.ReactNode }) {
  const active = !!(isStreaming && hasContent);
  return (
    <div className="text-sm leading-relaxed text-foreground">
      {children}
      {active && <span className="streaming-cursor" aria-hidden />}
    </div>
  );
}

function StepReasoning({ text, active }: { text: string; active: boolean }) {
  return (
    <div className="mt-1.5 pr-1 text-xs leading-relaxed text-foreground/80">
      <MarkdownRenderer content={text} />
      {active && <span className="streaming-cursor" aria-hidden />}
    </div>
  );
}

// ─── Inline Query Refinement ───

import { SlidersHorizontal, ArrowUp } from 'lucide-react';

function RefineInput({ threadId, conversationId }: { threadId: string; conversationId: string }) {
  const [open, setOpen] = useState(false);
  const [text, setText] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const isStreaming = useThreadStore((s) => s.isStreaming);

  const handleSubmit = useCallback(() => {
    const refinement = text.trim();
    if (!refinement || isStreaming) return;

    // Find the SQL from the specific answer being refined (not necessarily
    // the last one - user can refine any prior answer in the thread).
    const messages = useThreadStore.getState().currentMessages;
    const assistantMsg = messages.find(
      (m) => m.conversation_id === conversationId && m.role === 'assistant',
    );
    const sql = (assistantMsg?.metadata_ as Record<string, unknown> | null)?.sql as string || undefined;

    // Send the user's instruction as-is. The backend receives `prior_sql`
    // (the SQL being refined) and `source_conversation_id` (the conversation
    // to branch from) — those carry the refinement context, so the question
    // text doesn't need a mechanical prefix.
    useThreadStore.getState().askQuestion(threadId, refinement, conversationId, sql);
    setText('');
    setOpen(false);
  }, [text, isStreaming, threadId, conversationId]);

  if (!open) {
    return (
      <button
        onClick={() => { setOpen(true); setTimeout(() => inputRef.current?.focus(), 0); }}
        className={`flex items-center gap-1.5 mt-2 text-xs text-muted-foreground/60 hover:text-muted-foreground transition-[colors,opacity] duration-150 ${isStreaming ? 'opacity-0 pointer-events-none' : ''}`}
      >
        <SlidersHorizontal className="w-3 h-3" />
        Refine this query
      </button>
    );
  }

  return (
    <div className="flex items-center gap-2 mt-2">
      <input
        ref={inputRef}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => { if (e.key === 'Enter') handleSubmit(); if (e.key === 'Escape') { setOpen(false); setText(''); } }}
        placeholder="Add filters or modify..."
        aria-label="Refine this query"
        className="flex-1 text-xs bg-transparent border-b border-border focus:border-primary outline-none py-1 text-foreground placeholder:text-muted-foreground/50 transition-colors"
      />
      <button
        onClick={handleSubmit}
        disabled={!text.trim()}
        aria-label="Submit refinement"
        className="flex items-center justify-center h-6 w-6 rounded-md bg-foreground text-background disabled:opacity-25 hover:opacity-80 transition-opacity outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
      >
        <ArrowUp className="w-3 h-3" />
      </button>
    </div>
  );
}
