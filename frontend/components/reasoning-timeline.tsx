'use client';

import { useState, useEffect, useCallback, useMemo } from 'react';
import { Check, X, ChevronDown } from 'lucide-react';
import { MarkdownRenderer } from './markdown-renderer';
import type { StreamingStep } from '@/lib/store/threads';
import type { TokenUsage } from '@/lib/types/api';
import React from 'react';

// ─── Step reasoning content ───

export function StepReasoning({ text, active }: { text: string; active: boolean }) {
  return (
    <div className="mt-3 pr-2 text-sm leading-[1.7] text-foreground/75 reasoning-prose">
      <MarkdownRenderer content={text} />
      {active && <span className="streaming-cursor" aria-hidden />}
    </div>
  );
}

// ─── Legacy reasoning content ───

export const ReasoningContent = React.forwardRef<HTMLDivElement, { isStreaming?: boolean; content: string }>(
  ({ isStreaming, content }, ref) => {
    const active = !!(isStreaming && content);
    return (
      <div ref={ref} className="px-5 py-4 text-sm leading-[1.7] text-foreground/75 reasoning-prose">
        <MarkdownRenderer content={content} />
        {active && <span className="streaming-cursor" aria-hidden />}
      </div>
    );
  }
);
ReasoningContent.displayName = 'ReasoningContent';

// ─── Vertical step timeline ───

export function PipelineTimeline({
  steps,
  isStreaming,
  tokenUsage,
}: {
  steps: StreamingStep[];
  isStreaming?: boolean;
  tokenUsage?: TokenUsage | null;
}) {
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

  // Backfill missing per-step total_tokens from tokenUsage.by_node for older messages
  const enrichedSteps = useMemo(() => {
    if (!tokenUsage?.by_node?.length) return steps;
    const byNode: Record<string, number[]> = {};
    for (const n of tokenUsage.by_node) {
      if (!byNode[n.node]) byNode[n.node] = [];
      byNode[n.node].push(n.total_tokens);
    }
    const visitIdx: Record<string, number> = {};
    return steps.map(s => {
      if (s.total_tokens) return s;
      const idx = visitIdx[s.node] ?? 0;
      visitIdx[s.node] = idx + 1;
      const tok = byNode[s.node]?.[idx];
      return tok ? { ...s, total_tokens: tok } : s;
    });
  }, [steps, tokenUsage]);

  return (
    <div className="px-4 pb-3 pt-1 border-t border-border/40">
      {enrichedSteps.map((step, i) => {
        const isLast = i === enrichedSteps.length - 1;
        const isActive = step.status === 'active';
        const isDone = step.status === 'done';
        const isSkipped = step.status === 'skipped';
        const isError = step.status === 'error';

        const cleanedReasoning = (step.reasoning || '').trim();

        const showDuration =
          step.duration_ms != null && step.duration_ms >= 0
            ? `${(step.duration_ms / 1000).toFixed(1).padStart(4, '0')}s`
            : isActive
            ? 'live'
            : '';

        const showTokens =
          step.total_tokens && step.total_tokens > 0
            ? step.total_tokens >= 1000
              ? `${parseFloat((step.total_tokens / 1000).toFixed(2))}K`
              : `${step.total_tokens}`
            : '';

        const isExpandable = (isDone || isError) && !!cleanedReasoning;
        const isExpanded = isActive || expandedSteps.has(i);

        return (
          <div key={step.node + i} className="relative pl-10" style={{ paddingTop: i === 0 ? 0 : '1.35rem' }}>
            {/* Connector line */}
            {!isLast && (
              <span
                className="absolute left-[11px] w-[2px] bg-border/60"
                style={{ top: i === 0 ? '1.75rem' : 0, bottom: 0 }}
              />
            )}

            {/* Step row: dot + label aligned together */}
            <div
              className={`flex items-center justify-between gap-3 ${isExpandable ? 'cursor-pointer select-none group' : ''}`}
              onClick={isExpandable ? () => toggleStep(i) : undefined}
            >
              <div className="flex items-center gap-3 min-w-0">
                {/* Status dot */}
                <span
                  className={`absolute left-0 flex items-center justify-center w-[22px] h-[22px] rounded-full shrink-0 transition-colors ${
                    isActive
                      ? 'bg-primary step-active'
                      : isDone
                      ? 'bg-primary/20 dark:bg-primary/40'
                      : isError
                      ? 'bg-destructive/20 dark:bg-destructive/40'
                      : 'bg-muted'
                  }`}
                  aria-hidden="true"
                >
                  {isDone  && <Check className="w-3.5 h-3.5 text-primary dark:text-primary/90" strokeWidth={3} />}
                  {isError && <X className="w-3.5 h-3.5 text-destructive" strokeWidth={3} />}
                </span>

                {/* Step label */}
                <span
                  className={`text-[1rem] leading-[22px] tracking-[-0.01em] font-bold ${
                    isActive
                      ? 'text-foreground'
                      : isError
                      ? 'text-destructive'
                      : isSkipped
                      ? 'text-foreground/35 line-through'
                      : isDone
                      ? 'text-foreground/80'
                      : 'text-foreground/60'
                  }`}
                >
                  {step.message || step.node}
                </span>
              </div>

              <div className="flex items-center gap-2 shrink-0">
                {showDuration && (
                  <span
                    className={`text-xs font-medium tabular-nums ${
                      isActive ? 'text-primary' : isError ? 'text-destructive/70' : 'text-foreground/40'
                    }`}
                  >
                    {showDuration}
                  </span>
                )}
                <span className="w-3.5 h-3.5 flex items-center justify-center">
                  {isExpandable && (
                    <ChevronDown
                      className={`w-3.5 h-3.5 text-foreground/30 group-hover:text-foreground/60 transition-transform duration-200 ${
                        expandedSteps.has(i) ? 'rotate-180' : ''
                      }`}
                    />
                  )}
                </span>
              </div>
            </div>

            {/* Per-step reasoning */}
            {cleanedReasoning && isExpanded && (
              <>
                {showTokens && (
                  <p className="mt-1.5 text-[11px] tabular-nums text-foreground/35">{showTokens} tokens</p>
                )}
                <StepReasoning text={cleanedReasoning} active={isActive} />
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
