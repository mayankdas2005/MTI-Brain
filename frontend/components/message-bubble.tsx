'use client';

import { Message, StreamingStep, useThreadStore } from '@/lib/store/threads';
import { usePreferencesStore } from '@/lib/store/preferences';
import { Button } from '@/components/ui/button';
import { Copy, RotateCcw, ChevronLeft, ChevronRight, Pencil, X, Check, Code2, TableIcon, Info } from 'lucide-react';
import hljs from 'highlight.js/lib/core';
import sql from 'highlight.js/lib/languages/sql';
hljs.registerLanguage('sql', sql);
import { useState, useRef, useEffect, useCallback } from 'react';
import { toast } from '@/lib/toast';
import { copyText } from '@/lib/utils';
import { MarkdownRenderer } from './markdown-renderer';
import { FeedbackWidget } from './feedback-widget';
import { FollowUpChips } from './follow-up-chips';
import { MessageVisualization } from './message-visualization';
import { DataTable } from './data-table';
import { ThinkingWords } from './thinking-words';
import { TrustStrip } from './messages/trust-strip';
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
  const hasTableData = columns && columns.length > 0 && rows && rows.length > 0;

  // User preferences
  const prefShowSQL = usePreferencesStore((s) => s.showSQL);
  const prefAutoCharts = usePreferencesStore((s) => s.autoShowCharts);
  const prefShowFollowUps = usePreferencesStore((s) => s.showFollowUps);
  const prefShowReasoning = usePreferencesStore((s) => s.showReasoning);
  const prefDefaultView = usePreferencesStore((s) => s.defaultDataView);

  const showSQLTab = prefShowSQL && sql;
  const hasDataView = !message.isStreaming && (showSQLTab || hasTableData);

  return (
    <div
      id={`msg-${message.id}`}
      className="flex flex-col gap-[var(--density-row-gap)] px-4 py-[var(--density-pad-y)] animate-fade-in"
    >
      {/* Reasoning Block - vertical step timeline. The horizontal strip is gone:
          all per-step state (running, done, reasoning) lives inside this panel
          so it scales as the pipeline grows from 4 to 20+ nodes. */}
      {prefShowReasoning && (message.isStreaming || message.reasoning || (message.streamingSteps?.length ?? 0) > 0) && (
        <ReasoningPanel
          message={message}
          reasoningRef={reasoningRef}
        />
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
                SQL Query
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
              <div className="flex items-center justify-between px-3 py-1.5 border-b border-border bg-muted/30">
                <span className="text-xs font-medium text-muted-foreground">SQL</span>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground hover:bg-accent"
                      onClick={async () => {
                        const ok = await copyText(sql);
                        if (!ok) {
                          toast.error('Copy failed');
                          return;
                        }
                        toast.success('SQL copied to clipboard');
                      }}
                    >
                      <Copy className="w-3.5 h-3.5" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent side="bottom">Copy SQL</TooltipContent>
                </Tooltip>
              </div>
              <div className="flex-1 overflow-y-auto p-3">
                <pre
                  className="text-xs font-mono leading-relaxed whitespace-pre-wrap break-words"
                  dangerouslySetInnerHTML={{ __html: hljs.highlight(sql, { language: 'sql' }).value }}
                />
              </div>
            </div>
          )}

          {dataView === 'table' && hasTableData && (
            <DataTable columns={columns!} rows={rows!} rowCount={rowCount} />
          )}
        </div>
      )}

      {/* Message Content */}
      {message.content && (
        <StreamingContent isStreaming={message.isStreaming} hasContent>
          <MarkdownRenderer content={message.content} isUser={false} />
        </StreamingContent>
      )}

      {/* Chart Visualization */}
      {!message.isStreaming && prefAutoCharts && message.metadata_?.chart_spec && (
        <MessageVisualization
          columns={columns}
          rows={rows}
          chartSpec={message.metadata_.chart_spec}
          conversationId={message.conversation_id}
        />
      )}

      {/* Follow-up Chips + Refine */}
      {!message.isStreaming && prefShowFollowUps && followUps && followUps.length > 0 && (
        <FollowUpChips threadId={threadId} followUps={followUps} conversationId={message.conversation_id} />
      )}
      {!message.isStreaming && message.role === 'assistant' && message.content && (
        <RefineInput threadId={threadId} conversationId={message.conversation_id} />
      )}

      {/* Trust strip - provenance for the answer. The strip renders only
          the fields the backend has populated; missing fields just hide
          their cells (we never invent trust data on the client). All
          source tables come from backend SQL analysis, not the UI. */}
      {!message.isStreaming && (() => {
        const m = message.metadata_;
        if (!m) return null;
        const metric = m.metric_name
          ? { name: m.metric_name, owner: m.metric_owner, definedAt: m.metric_defined_at }
          : null;
        return (
          <div className="mt-1.5">
            <TrustStrip
              sources={m.source_tables}
              freshnessAt={m.data_freshness_at}
              metric={metric}
              rowCount={m.row_count}
            />
          </div>
        );
      })()}

      {/* Timestamp + Actions row */}
      {!message.isStreaming && (
        <div className="flex items-center gap-1.5 mt-1">
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
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={copyToClipboard}
                  aria-label="Copy response"
                  className="h-7 w-7 p-0 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent"
                >
                  <Copy className="w-3.5 h-3.5" aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom">Copy</TooltipContent>
            </Tooltip>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleRetry}
                  aria-label="Regenerate response"
                  className="h-7 w-7 p-0 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent"
                  disabled={isStreaming || !message.conversation_id}
                >
                  <RotateCcw className="w-3.5 h-3.5" aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom">Retry</TooltipContent>
            </Tooltip>
            {message.conversation_id && (
              <FeedbackWidget
                threadId={threadId}
                conversationId={message.conversation_id}
                feedback={message.feedback}
              />
            )}
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={handleOpenAbout}
                  aria-label="About this answer"
                  className="h-7 w-7 p-0 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent"
                >
                  <Info className="w-3.5 h-3.5" aria-hidden />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="bottom">About this answer</TooltipContent>
            </Tooltip>
          </div>
        </div>
      )}
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

// ─── Streaming cursor: stable DOM node, re-parented per render ───
// Why imperative: the cursor must sit INLINE inside the deepest last-leaf
// of streaming markdown (e.g. the last `<p>`'s last text node). React can't
// easily inject siblings into ReactMarkdown's output. CSS `::after` ends
// up on a new line below the prose container because Tailwind's prose
// styles force it to break. The fix: keep ONE persistent <span> element
// and `appendChild` it to the new last-leaf on each render - appendChild
// moves an existing node, so the breathe animation never restarts.
function useStreamingCursor(
  containerRef: React.RefObject<HTMLDivElement | null>,
  active: boolean,
) {
  const cursorRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    if (!active || !containerRef.current) {
      cursorRef.current?.remove();
      cursorRef.current = null;
      return;
    }
    if (!cursorRef.current) {
      const span = document.createElement('span');
      span.className = 'streaming-cursor';
      span.setAttribute('aria-hidden', 'true');
      cursorRef.current = span;
    }
    const cursor = cursorRef.current;
    // Walk to the deepest last-leaf descendant, IGNORING the cursor node
    // itself - otherwise on the next render we'd descend into the cursor
    // and try to append it to itself (HierarchyRequestError).
    let target: Element = containerRef.current;
    while (true) {
      let last = target.lastElementChild;
      if (last === cursor) last = last.previousElementSibling;
      if (!last) break;
      target = last;
    }
    if (target === cursor) return;
    // Already at end of the right parent? No-op (keeps animation continuous).
    if (cursor.parentElement === target && cursor === target.lastElementChild) return;
    // appendChild MOVES an existing node - animation stays alive.
    target.appendChild(cursor);
  });

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      cursorRef.current?.remove();
      cursorRef.current = null;
    };
  }, []);
}

// ─── Reasoning content (italic, dim) ───

const ReasoningContent = React.forwardRef<HTMLDivElement, { isStreaming?: boolean; content: string }>(
  ({ isStreaming, content }, ref) => {
    const innerRef = useRef<HTMLDivElement | null>(null);
    useStreamingCursor(innerRef, !!(isStreaming && content));
    return (
      <div
        ref={(el) => {
          innerRef.current = el;
          if (typeof ref === 'function') ref(el);
          else if (ref) (ref as React.MutableRefObject<HTMLDivElement | null>).current = el;
        }}
        data-streaming={isStreaming && content ? 'true' : undefined}
        className="px-3 pb-2 border-t border-border/40 pt-2 text-sm text-muted-foreground leading-relaxed italic"
      >
        <MarkdownRenderer content={content} />
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
  // Auto-expand while streaming, auto-collapse when it ends, but let the user
  // override either direction with manual clicks.
  const [open, setOpen] = useState<boolean>(!!message.isStreaming);
  const prevStreamingRef = useRef<boolean>(!!message.isStreaming);
  useEffect(() => {
    const wasStreaming = prevStreamingRef.current;
    const nowStreaming = !!message.isStreaming;
    if (!wasStreaming && nowStreaming) setOpen(true);
    if (wasStreaming && !nowStreaming) setOpen(false);
    prevStreamingRef.current = nowStreaming;
  }, [message.isStreaming]);

  const steps = message.streamingSteps;
  const hasSteps = !!steps && steps.length > 0;
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
        <AccordionTrigger className="py-2 px-3 text-xs text-muted-foreground hover:text-foreground hover:no-underline">
          {message.isStreaming ? (
            <span className="flex items-center gap-1.5">
              <ThinkingWords label={activeLabel} />
              <span className="tabular-nums text-muted-foreground/60">
                <LiveTimer
                  startTime={new Date(message.created_at).getTime()}
                  anchor={message._timingAnchor}
                />
              </span>
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
          ) : message.isStreaming ? (
            <div className="px-4 py-3 space-y-2">
              <div className="h-3 w-3/4 rounded-md skeleton-shimmer" />
              <div className="h-3 w-1/2 rounded-md skeleton-shimmer" style={{ animationDelay: '150ms' }} />
            </div>
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
          <div key={step.node + i} className="relative pl-6 pt-2.5 first:pt-0.5">
            {/* Connector line - drawn from the dot to the next step */}
            {!isLast && (
              <span className="absolute left-[7px] top-3.5 w-px bg-border" style={{ bottom: '-0.625rem' }} />
            )}

            {/* Status dot */}
            <span
              className={`absolute left-0 top-[0.6rem] flex items-center justify-center w-3.5 h-3.5 rounded-full transition-colors ${
                isActive
                  ? 'bg-primary ring-2 ring-primary/20 animate-pulse'
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
  const ref = useRef<HTMLDivElement>(null);
  const active = !!(isStreaming && hasContent);
  useStreamingCursor(ref, active);
  return (
    <div
      ref={ref}
      data-streaming={active ? 'true' : undefined}
      className="text-sm leading-relaxed text-foreground"
    >
      {children}
    </div>
  );
}

// Per-step reasoning block in the thinking panel. Owns its own container
// ref so the cursor lives inside the active step's deepest last-leaf.
function StepReasoning({ text, active }: { text: string; active: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useStreamingCursor(ref, active);
  return (
    <div
      ref={ref}
      data-streaming={active ? 'true' : undefined}
      className="mt-1.5 pr-1 text-[12px] leading-relaxed text-muted-foreground/85 italic"
    >
      <MarkdownRenderer content={text} />
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

    const instruction = `Refine the previous query: ${refinement}`;
    useThreadStore.getState().askQuestion(threadId, instruction, conversationId, sql);
    setText('');
    setOpen(false);
  }, [text, isStreaming, threadId, conversationId]);

  if (isStreaming) return null;

  if (!open) {
    return (
      <button
        onClick={() => { setOpen(true); setTimeout(() => inputRef.current?.focus(), 0); }}
        className="flex items-center gap-1.5 mt-2 text-xs text-muted-foreground/60 hover:text-muted-foreground transition-colors"
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
