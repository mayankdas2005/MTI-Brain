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
    <div className="mt-1.5 pr-1 text-xs leading-relaxed text-foreground/80">
      <MarkdownRenderer content={text} />
      {active && <span className="streaming-cursor" aria-hidden />}
    </div>
  );
}

// ─── Legacy reasoning content (italic, dim) ───

export const ReasoningContent = React.forwardRef<HTMLDivElement, { isStreaming?: boolean; content: string }>(
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

        const showTokens =
          step.total_tokens && step.total_tokens > 0
            ? step.total_tokens >= 1000
              ? `${parseFloat((step.total_tokens / 1000).toFixed(2))}K`
              : `${step.total_tokens}`
            : '';

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
                {showDuration && showTokens && (
                  <span className="text-[10px] text-foreground/30 mx-0.5">·</span>
                )}
                {showTokens && (
                  <span className="text-[10px] tabular-nums text-foreground/35">
                    {showTokens} tokens
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

            {/* Per-step reasoning */}
            {cleanedReasoning && isExpanded && (
              <StepReasoning text={cleanedReasoning} active={isActive} />
            )}
          </div>
        );
      })}
    </div>
  );
}
