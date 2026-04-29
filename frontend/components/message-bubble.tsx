'use client';

import { Message, useThreadStore } from '@/lib/store/threads';
import { usePreferencesStore } from '@/lib/store/preferences';
import { Button } from '@/components/ui/button';
import { Copy, RotateCcw, ChevronLeft, ChevronRight, Pencil, X, Check, Code2, TableIcon, CheckCheck } from 'lucide-react';
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
// Easter egg: SQL copy counter
let _sqlCopyCount = 0;

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
  const [showActions, setShowActions] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState(message.content);
  const [dataView, setDataView] = useState<'sql' | 'table'>(() => {
    const pref = usePreferencesStore.getState();
    // If SQL is hidden, always default to table
    return pref.showSQL ? pref.defaultDataView : 'table';
  });
  const reasoningRef = useRef<HTMLDivElement>(null);

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

  const handleEditSubmit = () => {
    const trimmed = editText.trim();
    if (!trimmed || !message.conversation_id || isStreaming) return;
    editQuestion(threadId, message.conversation_id, trimmed);
    setEditing(false);
  };

  const isUser = message.role === 'user';

  if (isUser) {
    if (editing) {
      return (
        <div className="flex justify-end px-4 py-1">
          <div className="max-w-[75%] w-full">
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
      <div
        className="flex flex-col items-end px-4 py-1 group"
        onMouseEnter={() => setShowActions(true)}
        onMouseLeave={() => setShowActions(false)}
      >
        {/* Actions + version nav row - only on hover */}
        <div className={`flex items-center gap-1.5 mb-1 mr-1 transition-opacity duration-150 ${showActions ? 'opacity-100' : 'opacity-0 pointer-events-none'}`}>
          <div className="flex items-center gap-1.5">
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
                  className="h-6 w-6 p-0 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent"
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
                  onClick={async () => {
                    const ok = await copyText(message.content);
                    if (ok) toast.success('Copied to clipboard');
                    else toast.error('Copy failed');
                  }}
                  className="h-6 w-6 p-0 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent"
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
                  onClick={handleEdit}
                  className="h-6 w-6 p-0 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent"
                  disabled={isStreaming}
                >
                  <Pencil className="w-3 h-3" />
                </Button>
              </TooltipTrigger>
              <TooltipContent side="top">Edit</TooltipContent>
            </Tooltip>
          </div>
          {versionNav && (
            <div className="flex items-center gap-0.5">
              <Button
                variant="ghost"
                size="sm"
                className="h-5 w-5 p-0 text-muted-foreground hover:text-foreground hover:bg-accent"
                onClick={versionNav.onPrev}
                disabled={!versionNav.hasPrev}
              >
                <ChevronLeft className="w-3 h-3" />
              </Button>
              <span className="text-[11px] text-muted-foreground tabular-nums">
                {versionNav.current}/{versionNav.total}
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="h-5 w-5 p-0 text-muted-foreground hover:text-foreground hover:bg-accent"
                onClick={versionNav.onNext}
                disabled={!versionNav.hasNext}
              >
                <ChevronRight className="w-3 h-3" />
              </Button>
            </div>
          )}
        </div>

        {/* Message bubble */}
        <div
          className="max-w-[80%] rounded-2xl px-5 py-3 text-sm leading-relaxed"
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
      className="flex flex-col gap-1 px-4 py-2 animate-fade-in"
      onMouseEnter={() => setShowActions(true)}
      onMouseLeave={() => setShowActions(false)}
    >
      {/* Streaming pipeline progress stepper - purely data-driven from backend node.start events */}
      {message.isStreaming && message.streamingSteps && message.streamingSteps.length > 0 && (
        <div className="flex items-center flex-wrap gap-x-0.5 gap-y-1 px-1 pb-2">
          {message.streamingSteps.map((step, i) => (
            <span key={step.node + i} className="flex items-center gap-1 animate-fade-in">
              {i > 0 && <span className="text-muted-foreground/30 text-[10px] mx-0.5">→</span>}
              {step.status === 'done' ? (
                <span className="flex items-center gap-0.5 text-[11px] text-muted-foreground">
                  <CheckCheck className="w-3 h-3 shrink-0" />
                  {step.message || step.node}
                </span>
              ) : (
                <span className="flex items-center gap-1 text-[11px] text-primary font-medium">
                  <span className="w-1.5 h-1.5 rounded-full bg-primary inline-block animate-pulse shrink-0" />
                  {step.message || step.node}
                </span>
              )}
            </span>
          ))}
        </div>
      )}

      {/* Reasoning Block - unified thinking indicator (like Claude) */}
      {prefShowReasoning && (message.isStreaming || message.reasoning) && (
        <Accordion type="single" collapsible defaultValue={message.isStreaming ? 'reasoning' : undefined} className="mb-2">
          <AccordionItem
            value="reasoning"
            className={`rounded-lg ${
              message.isStreaming
                ? 'thinking-glow border border-primary/20'
                : 'reasoning-complete'
            }`}
          >
            <AccordionTrigger className="py-2 px-3 text-xs text-muted-foreground hover:text-foreground hover:no-underline">
              {message.isStreaming ? (
                <span className="flex items-center gap-1.5">
                  <ThinkingWords interval={2500} />
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
              {(() => {
                const cleaned = (message.reasoning || '')
                  .replace(/^\*\*[^*]+\*\*\s*$/gm, '')
                  .replace(/\n---\n/g, '\n')
                  .replace(/\n{3,}/g, '\n\n')
                  .trim();
                return cleaned ? (
                  <ReasoningContent
                    ref={reasoningRef}
                    isStreaming={message.isStreaming}
                    content={cleaned}
                  />
                ) : message.isStreaming ? (
                  <div className="px-3 py-3 space-y-2">
                    <div className="h-3 w-3/4 rounded-md skeleton-shimmer" />
                    <div className="h-3 w-1/2 rounded-md skeleton-shimmer" style={{ animationDelay: '150ms' }} />
                  </div>
                ) : null;
              })()}
            </AccordionContent>
          </AccordionItem>
        </Accordion>
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
                        _sqlCopyCount++;
                        if (_sqlCopyCount === 5) {
                          toast.success('You really love SQL, don\'t you? 🤓');
                        } else {
                          toast.success('SQL copied to clipboard');
                        }
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
        />
      )}

      {/* Follow-up Chips + Refine */}
      {!message.isStreaming && prefShowFollowUps && followUps && followUps.length > 0 && (
        <FollowUpChips threadId={threadId} followUps={followUps} conversationId={message.conversation_id} />
      )}
      {!message.isStreaming && message.role === 'assistant' && message.content && (
        <RefineInput threadId={threadId} conversationId={message.conversation_id} />
      )}

      {/* Timestamp + Actions row */}
      {!message.isStreaming && (
        <div className="flex items-center gap-1.5 mt-1">
          {message.created_at && (
            <Tooltip>
              <TooltipTrigger asChild>
                <span className="text-[11px] text-muted-foreground/40 cursor-default">
                  {`${new Date(message.created_at).toLocaleDateString([], { month: 'short', day: 'numeric' })}, ${new Date(message.created_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })}`}
                </span>
              </TooltipTrigger>
              <TooltipContent side="right">
                {new Date(message.created_at).toLocaleString([], { dateStyle: 'long', timeStyle: 'short' })}
              </TooltipContent>
            </Tooltip>
          )}
          <div
            className={`flex items-center gap-0.5 transition-opacity duration-150 ${
              showActions ? 'opacity-100' : 'opacity-0'
            }`}
          >
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={copyToClipboard}
                  className="h-7 w-7 p-0 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent"
                >
                  <Copy className="w-3.5 h-3.5" />
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
                  className="h-7 w-7 p-0 rounded-lg text-muted-foreground hover:text-foreground hover:bg-accent"
                  disabled={isStreaming || !message.conversation_id}
                >
                  <RotateCcw className="w-3.5 h-3.5" />
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
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Shared: find last text node and inject cursor ───

import React from 'react';

function useStreamingCursor(containerRef: React.RefObject<HTMLDivElement | null>, active: boolean) {
  useEffect(() => {
    if (!active || !containerRef.current) return;

    // Walk the entire DOM tree to find the very last Text node with content
    const walker = document.createTreeWalker(containerRef.current, NodeFilter.SHOW_TEXT, {
      acceptNode: (node) => node.textContent?.trim() ? NodeFilter.FILTER_ACCEPT : NodeFilter.FILTER_SKIP,
    });
    let lastTextNode: Node | null = null;
    while (walker.nextNode()) lastTextNode = walker.currentNode;

    if (!lastTextNode?.parentElement) return;

    // Remove any existing cursors
    containerRef.current.querySelectorAll('.streaming-cursor').forEach((c) => c.remove());

    // Insert cursor right after the last text node
    const cursor = document.createElement('span');
    cursor.className = 'streaming-cursor';
    lastTextNode.parentElement.insertBefore(cursor, lastTextNode.nextSibling);

    return () => { cursor.remove(); };
  });
}

// ─── Reasoning content with streaming cursor ───

const ReasoningContent = React.forwardRef<HTMLDivElement, { isStreaming?: boolean; content: string }>(
  ({ isStreaming, content }, ref) => {
    const innerRef = useRef<HTMLDivElement>(null);
    useStreamingCursor(innerRef, !!(isStreaming && content));

    return (
      <div
        ref={(el) => {
          innerRef.current = el;
          if (typeof ref === 'function') ref(el);
          else if (ref) (ref as React.MutableRefObject<HTMLDivElement | null>).current = el;
        }}
        className="px-3 pb-2 border-t border-border/40 pt-2 text-sm text-muted-foreground leading-relaxed italic"
      >
        <MarkdownRenderer content={content} />
      </div>
    );
  }
);
ReasoningContent.displayName = 'ReasoningContent';

// ─── Message content with streaming cursor ───

function StreamingContent({ isStreaming, hasContent, children }: { isStreaming?: boolean; hasContent: boolean; children: React.ReactNode }) {
  const ref = useRef<HTMLDivElement>(null);
  useStreamingCursor(ref, !!(isStreaming && hasContent));

  return (
    <div ref={ref} className="text-sm leading-relaxed text-foreground">
      {children}
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
    useThreadStore.getState().askQuestion(threadId, instruction, undefined, sql);
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
        className="flex-1 text-xs bg-transparent border-b border-border focus:border-primary outline-none py-1 text-foreground placeholder:text-muted-foreground/50 transition-colors"
      />
      <button
        onClick={handleSubmit}
        disabled={!text.trim()}
        className="flex items-center justify-center h-6 w-6 rounded-md bg-foreground text-background disabled:opacity-25 hover:opacity-80 transition-opacity"
      >
        <ArrowUp className="w-3 h-3" />
      </button>
    </div>
  );
}
