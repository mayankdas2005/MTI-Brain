'use client';

import { Message, StreamingStep, useThreadStore } from '@/lib/store/threads';
import { usePreferencesStore } from '@/lib/store/preferences';
import { Button } from '@/components/ui/button';
import { Copy, RotateCcw, ChevronLeft, ChevronRight, Pencil, X, Check, Code2, TableIcon, Info, MoreHorizontal, Pin, LayoutDashboard } from 'lucide-react';
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
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { useTheme } from 'next-themes';

const _base = { background: 'transparent', margin: 0, padding: 0 };
const sparqlDark: Record<string, React.CSSProperties> = {
  'pre[class*="language-"]': _base,
  'code[class*="language-"]': { ..._base, color: '#cbd5e1' },
  keyword:     { color: '#7dd3fc' },
  builtin:     { color: '#a5b4fc' },
  string:      { color: '#86efac' },
  url:         { color: '#6ee7b7' },
  variable:    { color: '#f9a8d4' },
  comment:     { color: '#475569', fontStyle: 'italic' },
  number:      { color: '#fcd34d' },
  operator:    { color: '#94a3b8' },
  punctuation: { color: '#64748b' },
  'class-name':{ color: '#c4b5fd' },
};
const sparqlLight: Record<string, React.CSSProperties> = {
  'pre[class*="language-"]': _base,
  'code[class*="language-"]': { ..._base, color: '#1e293b' },
  keyword:     { color: '#2563eb' },
  builtin:     { color: '#7c3aed' },
  string:      { color: '#16a34a' },
  url:         { color: '#0891b2' },
  variable:    { color: '#9333ea' },
  comment:     { color: '#94a3b8', fontStyle: 'italic' },
  number:      { color: '#d97706' },
  operator:    { color: '#475569' },
  punctuation: { color: '#64748b' },
  'class-name':{ color: '#7c3aed' },
};
import { useState, useRef, useEffect, useCallback } from 'react';
import { toast } from '@/lib/toast';
import { copyText } from '@/lib/utils';
import { generateDashboard } from '@/lib/api/dashboard';
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
  const { resolvedTheme } = useTheme();
  const [editing, setEditing] = useState(false);

  const [editText, setEditText] = useState(message.content);
  const [dataView, setDataView] = useState<'sql' | 'table'>(() => {
    const pref = usePreferencesStore.getState();
    // If SQL is hidden, always default to table
    return pref.showSQL ? pref.defaultDataView : 'table';
  });
  const [aboutOpen, setAboutOpen] = useState(false);
  const [aboutQuestion, setAboutQuestion] = useState<string | null>(null);
  const reasoningRef = useRef<HTMLDivElement>(null);

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

  // Auto-scroll reasoning block while streaming
  useEffect(() => {
    if (message.isStreaming && reasoningRef.current) {
      reasoningRef.current.scrollTop = reasoningRef.current.scrollHeight;
    }
  }, [message.reasoning, message.isStreaming]);

  const retryResponse = useThreadStore((s) => s.retryResponse);
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
    if (!message.conversation_id || isStreaming) return;
    retryResponse(threadId, message.conversation_id);
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
                onClick={() => {
                  if (message.conversation_id && !isStreaming) {
                    retryResponse(threadId, message.conversation_id);
                  }
                }}
                className="tap-44 h-6 w-6 p-0 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent"
                disabled={isStreaming || !message.conversation_id}
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
              <span className="text-[10px] text-muted-foreground/50 truncate">
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
  const followUps = message.metadata_?.follow_ups;
  const hasTableData = !!(columns && columns.length > 0 && rows && rows.length > 0);

  // User preferences
  const prefShowSQL = usePreferencesStore((s) => s.showSQL);
  const prefAutoCharts = usePreferencesStore((s) => s.autoShowCharts);
  const prefShowFollowUps = usePreferencesStore((s) => s.showFollowUps);
  const prefShowReasoning = usePreferencesStore((s) => s.showReasoning);

  const showSQLTab = !!(prefShowSQL && sql);
  const hasDataView = !!(message.dataReady ?? !message.isStreaming) && !!(showSQLTab || hasTableData);
  // Show data skeleton once SQL generation step has begun but data hasn't arrived yet
  const sqlStepStarted = message.isStreaming && !message.dataReady &&
    (message.streamingSteps?.some((s) => ['generate_sql', 'execute', 'respond'].includes(s.node)) ?? false);

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

      {/* Data skeleton — table shape while SQL executes */}
      {sqlStepStarted && (
        <div className="mb-2 animate-fade-in">
          {prefShowSQL && (
            <div className="flex items-center gap-1 mb-2">
              <Skeleton className="h-7 w-20 rounded-md" />
              <Skeleton className="h-7 w-24 rounded-md" />
            </div>
          )}
          <div className="rounded-lg border border-border overflow-hidden">
            <div className="flex gap-4 px-3 py-2 border-b border-border bg-muted/30">
              <Skeleton className="h-3 w-24 rounded" />
              <Skeleton className="h-3 w-16 rounded ml-auto" />
            </div>
            {[0, 1, 2].map((i) => (
              <div key={i} className="flex gap-4 px-3 py-2 border-b border-border/50 last:border-0">
                <Skeleton className="h-3 rounded" style={{ width: `${[45, 55, 50][i]}%` }} />
                <Skeleton className="h-3 w-16 rounded ml-auto" />
              </div>
            ))}
          </div>
        </div>
      )}

      {/* SQL Query / Data Table toggle */}
      {hasDataView && (
        <div className="mb-2 space-y-2">
          {showSQLTab && hasTableData && (
          <div className="flex items-center gap-1">
            {showSQLTab && (
              <Button
                variant={dataView === 'sql' ? 'secondary' : 'ghost'}
                size="sm"
                className="h-7 px-2.5 text-xs gap-1.5"
                onClick={() => setDataView('sql')}
              >
                <Code2 className="w-3.5 h-3.5" />
                SPARQL Query
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
                Data Table
              </Button>
            )}
          </div>
          )}

          {dataView === 'sql' && showSQLTab && (
            <div className="rounded-lg border border-border bg-muted/50 overflow-hidden max-h-96 flex flex-col">
              <div className="flex items-center justify-end px-3 py-1.5 border-b border-border bg-muted/30">
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-6 w-6 p-0 text-muted-foreground"
                  onClick={async () => {
                    const ok = await copyText(sql);
                    if (!ok) {
                      toast.error('Copy failed');
                      return;
                    }
                    toast.success('SPARQL copied');
                  }}
                >
                  <Copy className="w-3.5 h-3.5" />
                </Button>
              </div>
              <div className="flex-1 overflow-y-auto">
                <SyntaxHighlighter
                  language="sparql"
                  style={resolvedTheme === 'dark' ? sparqlDark : sparqlLight}
                  customStyle={{ margin: 0, padding: '12px', fontSize: '12px', lineHeight: '1.6', background: 'transparent' }}
                  wrapLongLines
                >
                  {sql ?? ''}
                </SyntaxHighlighter>
              </div>
            </div>
          )}

          {dataView === 'table' && hasTableData && (
            <DataTable columns={columns!} rows={rows!} rowCount={rowCount} />
          )}
        </div>
      )}

      {/* Message Content */}
      {!!message.content && (
        <StreamingContent isStreaming={message.isStreaming} hasContent>
          <MarkdownRenderer content={message.content} isUser={false} />
        </StreamingContent>
      )}

      {/* Chart skeleton — shown after data arrives but before chart spec fires */}
      {message.isStreaming && message.dataReady && !message.chartReady && prefAutoCharts && hasTableData && (
        <div className="mt-3 rounded-xl border border-border bg-sidebar px-4 pt-4 pb-3 animate-fade-in">
          <Skeleton className="h-3 w-40 rounded mb-4" />
          <div className="flex items-end gap-2 h-24">
            {[55, 80, 45, 95, 65, 70, 40].map((h, i) => (
              <Skeleton key={i} className="flex-1 rounded-sm" style={{ height: `${h}%` }} />
            ))}
          </div>
        </div>
      )}

      {/* Chart Visualization — appears as soon as chart event fires */}
      {!!(message.chartReady ?? !message.isStreaming) && prefAutoCharts && !!message.metadata_?.chart_spec && (
        <MessageVisualization
          columns={columns}
          rows={rows}
          chartSpec={message.metadata_.chart_spec}
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
      {!!(message.dataReady ?? !message.isStreaming) && message.role === 'assistant' && !!message.content &&
        !!(message.metadata_ as Record<string, unknown> | null)?.sql && (
        <RefineInput threadId={threadId} conversationId={message.conversation_id} />
      )}

      {/* Trust strip — only for SQL-backed answers. We gate on sql being
          non-empty so conversational responses ("Hi", "Sure, here's a plan")
          never show 0 rows / freshness timestamps. */}
      {!!(message.dataReady ?? !message.isStreaming) && (() => {
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
                <Button variant="ghost" size="sm" onClick={handleRetry} aria-label="Regenerate response" className="h-7 w-7 p-0 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent" disabled={isStreaming || !message.conversation_id}>
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
                  {message.conversation_id && !!(message.metadata_ as Record<string, unknown> | null)?.sql && (
                    <DropdownMenuItem
                      onClick={() => {
                        toast.info('Dashboard generation started.');
                        void generateDashboard(message.conversation_id).catch((err: unknown) => {
                          const msg =
                            err instanceof Error ? err.message : 'Failed to generate dashboard.';
                          toast.error(msg);
                        });
                      }}
                      className="gap-2"
                    >
                      <LayoutDashboard className="w-4 h-4" />
                      Generate Dashboard
                    </DropdownMenuItem>
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
      <div ref={ref} className="px-3 pb-2 border-t border-border/40 pt-2 text-sm text-muted-foreground leading-relaxed italic">
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

  // Closed until first step arrives from backend — avoids empty body flash.
  // Auto-opens when steps land; auto-closes when streaming ends.
  const [open, setOpen] = useState(hasSteps);
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
      return () => clearTimeout(t);
    }
    prevStreamingRef.current = nowStreaming;
  }, [message.isStreaming]);
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
          className="py-2 px-3 text-xs text-muted-foreground hover:text-foreground hover:no-underline"
          disabled={!hasSteps && !legacyReasoning}
        >
          {message.isStreaming ? (
            <span className="flex items-center gap-1.5">
              <ThinkingWords label={activeLabel} />
              {hasSteps && (
                <span className="tabular-nums text-muted-foreground/60">
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
          {hasSteps ? (
            <PipelineTimeline steps={steps!} />
          ) : legacyReasoning ? (
            <ReasoningContent
              ref={reasoningRef}
              isStreaming={message.isStreaming}
              content={legacyReasoning}
            />
          ) : null}
        </AccordionContent>
      </AccordionItem>
    </Accordion>
  );
}

// ─── Vertical step timeline ───

function PipelineTimeline({ steps }: { steps: StreamingStep[] }) {
  return (
    <div className="px-4 pb-3 pt-1 border-t border-border/40">
      {steps.map((step, i) => {
        const isLast = i === steps.length - 1;
        const isActive = step.status === 'active';
        const isDone = step.status === 'done';
        const isSkipped = step.status === 'skipped';

        const cleanedReasoning = (step.reasoning || '')
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

        return (
          <div key={step.node + i} className="relative pl-6 pt-2.5">
            {/* Connector line - drawn from the dot to the next step */}
            {!isLast && (
              <span className="absolute left-[7px] top-[1.3rem] w-px bg-border" style={{ bottom: '-0.625rem' }} />
            )}

            {/* Status dot */}
            <span
              className={`absolute left-0 top-[9px] flex items-center justify-center w-3.5 h-3.5 rounded-full transition-colors ${
                isActive
                  ? 'bg-primary step-active'
                  : isDone
                  ? 'bg-primary/25'
                  : 'bg-muted'
              }`}
              aria-hidden="true"
            >
              {isDone && <Check className="w-2 h-2 text-primary" strokeWidth={3.5} />}
            </span>

            {/* Step label + duration */}
            <div className="flex items-center justify-between gap-3">
              <span
                className={`text-xs leading-none ${
                  isActive
                    ? 'text-foreground font-medium'
                    : isSkipped
                    ? 'text-muted-foreground/50 line-through'
                    : 'text-muted-foreground'
                }`}
              >
                {step.message || step.node}
              </span>
              {showDuration && (
                <span
                  className={`text-[10px] tabular-nums shrink-0 ${
                    isActive ? 'text-primary' : 'text-muted-foreground/50'
                  }`}
                >
                  {showDuration}
                </span>
              )}
            </div>

            {/* Per-step reasoning. When the step is active we mount the
                streaming cursor inside the deepest last-leaf so the user
                sees a live breathing caret at the end of the streaming
                reasoning text. */}
            {cleanedReasoning && (
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
    <div className="mt-1.5 pr-1 text-xs leading-relaxed text-muted-foreground/85">
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
