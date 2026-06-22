'use client';

import { useRef, useEffect, useCallback, useState } from 'react';
import { X, Brain, GripVertical, Loader2 } from 'lucide-react';
import { useUIStore } from '@/lib/store/ui';
import { useThreadStore, type Message } from '@/lib/store/threads';
import { usePreferencesStore } from '@/lib/store/preferences';
import { ThinkingWords } from './thinking-words';
import { PipelineTimeline, ReasoningContent } from './reasoning-timeline';
import { LiveTimer } from './message-bubble';

const MIN_WIDTH = 320;
const MAX_WIDTH = 680;

export function ThinkingSidePanel() {
  const thinkingPlacement = usePreferencesStore((s) => s.thinkingPlacement);
  const panelOpen = useUIStore((s) => s.thinkingPanelOpen);
  const panelMessageId = useUIStore((s) => s.thinkingPanelMessageId);
  const panelWidth = useUIStore((s) => s.thinkingPanelWidth);
  const closePanel = useUIStore((s) => s.closeThinkingPanel);
  const setPanelWidth = useUIStore((s) => s.setThinkingPanelWidth);

  const isStreaming = useThreadStore((s) => s.isStreaming);
  const currentMessages = useThreadStore((s) => s.currentMessages);
  const streamingMessages = useThreadStore((s) => s.streamingMessages);
  const currentThreadId = useThreadStore((s) => s.currentThreadId);
  const streamingThreadId = useThreadStore((s) => s.streamingThreadId);

  // Resolve the message to display.
  // Always search currentMessages first (the displayed thread), then fall back
  // to streamingMessages only when the current thread IS the streaming thread.
  // This prevents cross-thread bleed where streamingMessages from Thread B are
  // shown while the user is viewing Thread A.
  const onStreamingThread = currentThreadId === streamingThreadId;
  const message: Message | undefined = panelMessageId
    ? (currentMessages.find((m) => m.id === panelMessageId) ??
       (onStreamingThread ? streamingMessages.find((m) => m.id === panelMessageId) : undefined))
    : undefined;

  // Auto-close the panel when the user navigates to a different thread.
  // currentMessages changes whenever setCurrentThread fires. We only allow
  // streamingMessages as a valid source when still on the streaming thread —
  // otherwise leaving a streaming thread would keep the panel open because
  // streamingMessages still holds the in-flight message.
  useEffect(() => {
    if (!panelOpen || !panelMessageId) return;
    const inCurrent = currentMessages.some((m) => m.id === panelMessageId);
    const inStreaming = onStreamingThread && streamingMessages.some((m) => m.id === panelMessageId);
    if (!inCurrent && !inStreaming) closePanel();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentMessages]);

  // Drag resize state
  const panelRef = useRef<HTMLDivElement>(null);
  const dragging = useRef(false);
  const startX = useRef(0);
  const startWidth = useRef(panelWidth);

  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    startX.current = e.clientX;
    startWidth.current = panelWidth;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
  }, [panelWidth]);

  useEffect(() => {
    const onMouseMove = (e: MouseEvent) => {
      if (!dragging.current) return;
      // Dragging leftward makes the panel wider
      const delta = startX.current - e.clientX;
      const newWidth = Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, startWidth.current + delta));
      setPanelWidth(newWidth);
    };
    const onMouseUp = () => {
      if (dragging.current) {
        dragging.current = false;
        document.body.style.cursor = '';
        document.body.style.userSelect = '';
      }
    };
    window.addEventListener('mousemove', onMouseMove);
    window.addEventListener('mouseup', onMouseUp);
    return () => {
      window.removeEventListener('mousemove', onMouseMove);
      window.removeEventListener('mouseup', onMouseUp);
    };
  }, [setPanelWidth]);

  // Auto-scroll during streaming
  const scrollRef = useRef<HTMLDivElement>(null);
  const [userScrolled, setUserScrolled] = useState(false);

  useEffect(() => {
    if (message?.isStreaming && scrollRef.current && !userScrolled) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [message?.streamingSteps, message?.isStreaming, userScrolled]);

  // Reset user-scrolled flag when message changes
  useEffect(() => {
    setUserScrolled(false);
  }, [panelMessageId]);

  // Don't render if not in sidebar mode or panel is closed or no message
  if (thinkingPlacement !== 'sidebar' || !panelOpen || !message) {
    return null;
  }

  const steps = message.streamingSteps;
  const hasSteps = !!steps && steps.length > 0;
  const activeStep = steps?.slice().reverse().find((s) => s.status === 'active');
  const lastStep = steps && steps.length > 0 ? steps[steps.length - 1] : undefined;
  const activeLabelStep = activeStep ?? lastStep;
  const activeLabel = activeLabelStep?.message || activeLabelStep?.node;

  const legacyReasoning = (message.reasoning || '')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/^\*\*[^*]+\*\*\s*$/gm, '')
    .replace(/\n---\n/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();

  const hasContent = hasSteps || !!legacyReasoning;
  const waitingForReasoning = (message.isStreaming || isStreaming) && !hasContent;

  return (
    <div
      ref={panelRef}
      className="h-full flex flex-col border-l border-border bg-background shrink-0 relative"
      style={{ width: panelWidth }}
    >
      {/* Drag handle */}
      <div
        className="absolute left-0 top-0 bottom-0 w-3 cursor-col-resize hover:bg-primary/10 active:bg-primary/20 transition-colors z-10 flex items-center justify-center group"
        onMouseDown={onMouseDown}
      >
        <GripVertical className="w-3 h-3 text-muted-foreground/40 group-hover:text-muted-foreground/70 transition-colors" />
      </div>

      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3.5 border-b border-border shrink-0">
        <Brain className="w-[18px] h-[18px] text-primary shrink-0" />

        {/* Label — truncates when narrow */}
        <div className="flex-1 min-w-0 overflow-hidden">
          {message.isStreaming ? (
            <ThinkingWords label={activeLabel} />
          ) : (
            <span className="text-sm font-semibold tracking-[-0.02em] text-foreground truncate block">
              Reasoning
            </span>
          )}
        </div>

        {/* Timer + tokens — always visible, never truncated */}
        <div className="flex items-center gap-1.5 shrink-0 text-xs tabular-nums text-foreground/50">
          {message.isStreaming ? (
            <>
              <LiveTimer
                startTime={new Date(message.created_at).getTime()}
                anchor={message._timingAnchor}
              />
              {(() => {
                const tok = (steps || []).reduce((s, st) => s + (st.total_tokens || 0), 0);
                if (!tok) return null;
                const fmt = tok >= 1000 ? `${parseFloat((tok / 1000).toFixed(2))}K` : `${tok}`;
                return <span className="text-foreground/40">· {fmt} tokens</span>;
              })()}
            </>
          ) : (
            <>
              {message.metadata_?.duration_ms != null &&
                <span>{(message.metadata_.duration_ms / 1000).toFixed(1)}s</span>}
              {(() => {
                const tok = message.metadata_?.token_usage?.total_tokens
                  ?? (steps || []).reduce((s, st) => s + (st.total_tokens || 0), 0);
                if (!tok) return null;
                const fmt = tok >= 1000 ? `${parseFloat((tok / 1000).toFixed(2))}K` : `${tok}`;
                return <span className="text-foreground/40">· {fmt} tokens</span>;
              })()}
            </>
          )}
        </div>

        <button
          onClick={closePanel}
          className="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors shrink-0"
          aria-label="Close thinking panel"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Content */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto"
        onScroll={(e) => {
          const el = e.currentTarget;
          const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
          setUserScrolled(distFromBottom > 40);
        }}
      >
        {hasContent ? (
          hasSteps ? (
            <PipelineTimeline steps={steps!} isStreaming={!!message.isStreaming} tokenUsage={message.metadata_?.token_usage} />
          ) : (
            <ReasoningContent
              isStreaming={message.isStreaming}
              content={legacyReasoning}
            />
          )
        ) : waitingForReasoning ? (
          <div className="flex h-full items-center justify-center px-4">
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              <span>Loading reasoning steps...</span>
            </div>
          </div>
        ) : (
          <div className="flex items-center justify-center h-full px-4">
            <p className="text-sm text-muted-foreground text-center">
              No reasoning steps available for this message.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
