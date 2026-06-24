'use client';

import { useState, type ComponentType, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import {
  Info,
  Copy,
  ChevronDown,
  ChevronRight,
  Database,
  Hash,
  Clock,
  Target,
  ListOrdered,
  ScrollText,
  ExternalLink,
  ShieldCheck,
  MessageSquare,
  ThumbsUp,
  ThumbsDown,
  BookOpen,
  BrainCircuit,
} from 'lucide-react';
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from '@/components/ui/sheet';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { SqlBlock } from '@/components/sql-block';
import { MarkdownRenderer } from '@/components/markdown-renderer';
import { copyText } from '@/lib/utils';
import { toast } from '@/lib/toast';
import { formatRelativeTime } from '@/lib/utils/relative-time';
import { useNow } from '@/lib/hooks/use-now';
import type { Message } from '@/lib/store/threads';


function MarkdownText({ children }: { children: string }) {
  return (
    <div className="text-sm text-foreground/90 leading-relaxed prose prose-base dark:prose-invert max-w-none [&_strong]:font-semibold [&_em]:italic [&_ul]:list-disc [&_ul]:pl-4 [&_li]:my-1">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}

interface AboutPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  message: Message;
  /** The user's question that produced this answer. Found by the parent
   *  via the matching conversation_id; we don't reach into the store
   *  here so the panel is trivially testable in isolation. */
  question?: string | null;
}

/**
 * About-this-answer panel - slides in from the right, shows the receipts
 * for an assistant response: timing, sources, SQL, pipeline trace,
 * resolved filters, and the run identifiers an analyst would need to
 * reproduce or cite the answer.
 *
 * Design intent: every section hides cleanly when its data is missing.
 * The backend is still evolving - fields like metric_owner and
 * source_tables may arrive only once the warehouse integration ships -
 * so we render forward-compatibly and never invent receipts on the
 * client.
 */
export function AboutPanel({ open, onOpenChange, message, question }: AboutPanelProps) {
  const m = message.metadata_;
  const now = useNow();
  const handleCopy = async (text: string, label: string) => {
    const ok = await copyText(text);
    if (ok) toast.success(`${label} copied`);
    else toast.error('Copy failed');
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-lg p-0 flex flex-col gap-0"
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <SheetHeader className="px-5 py-4 border-b border-border gap-1">
          <SheetTitle className="text-base font-semibold tracking-tight">
            About this answer
          </SheetTitle>
          <SheetDescription className="text-sm">
            Provenance, execution, and timing.
          </SheetDescription>
        </SheetHeader>

        <div className="flex-1 overflow-y-auto">
          {/* Run - always shown; created_at is guaranteed. */}
          <Section title="Run" icon={Clock}>
            <KV
              label="Answered"
              value={
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span className="cursor-default">
                      {formatRelativeTime(message.created_at, now)}
                    </span>
                  </TooltipTrigger>
                  <TooltipContent side="left">
                    {new Date(message.created_at).toLocaleString([], {
                      dateStyle: 'long',
                      timeStyle: 'short',
                    })}
                  </TooltipContent>
                </Tooltip>
              }
            />
            {m?.duration_ms != null && (
              <KV
                label="Duration"
                value={`${(m.duration_ms / 1000).toFixed(2)}s`}
                mono
              />
            )}
            {m?.run_id && (
              <KV
                label="Run ID"
                value={
                  <CopyableId
                    id={m.run_id}
                    onCopy={() => handleCopy(m.run_id!, 'Run ID')}
                  />
                }
              />
            )}
            {message.conversation_id && (
              <KV
                label="Conversation"
                value={
                  <CopyableId
                    id={message.conversation_id}
                    onCopy={() => handleCopy(message.conversation_id, 'Conversation ID')}
                  />
                }
              />
            )}
            {m?.langfuse_trace_url && (
              <KV
                label="Trace"
                value={
                  <a
                    href={m.langfuse_trace_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="inline-flex items-center gap-1 text-primary hover:underline"
                  >
                    View in Langfuse
                    <ExternalLink className="w-4 h-4" />
                  </a>
                }
              />
            )}
            {m?.deep_analysis != null && (
              <KV
                label="Deep Analysis"
                value={
                  <span className={`inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded-full ${
                    m.deep_analysis
                      ? 'bg-primary/10 text-primary'
                      : 'bg-muted text-muted-foreground'
                  }`}>
                    <BrainCircuit className="w-3 h-3" />
                    {m.deep_analysis ? 'True' : 'False'}
                  </span>
                }
              />
            )}
          </Section>

          {/* Interpretation - only when we have something interpretive to show. */}
          {(question || m?.intent || m?.resolved_filters) && (
            <Section title="Interpretation" icon={Target}>
              {question && (
                <div className="text-sm text-foreground/90 leading-relaxed mb-2.5 pl-2 border-l-2 border-border italic">
                  {question}
                </div>
              )}
              {m?.intent && (
                <KV
                  label="Intent"
                  value={<MarkdownText>{m.intent}</MarkdownText>}
                  wrap
                />
              )}
              {m?.resolved_filters && (
                <KV label="Filters" value={m.resolved_filters} wrap />
              )}
            </Section>
          )}

          {/* Tribal Knowledge — only shown when deep analysis retrieved facts */}
          {(m?.tribal_facts?.length ?? 0) > 0 && (
            <Collapsible title="Tribal Knowledge" icon={BookOpen}>
              <TribalKnowledgeList facts={m!.tribal_facts!} />
            </Collapsible>
          )}

          {/* Feedback — ratings and memory applied to this response */}
          {m?.preference_summary && (
            <Section title="Feedback" icon={MessageSquare}>
              {/* Status banner */}
              <div className={`mb-3 flex items-center gap-2 rounded-md px-3 py-2 text-xs font-medium ${
                m.preference_summary.feedback_applied
                  ? 'bg-emerald-50 dark:bg-emerald-950/30 text-emerald-700 dark:text-emerald-400 border border-emerald-200 dark:border-emerald-800/50'
                  : 'bg-muted/30 text-muted-foreground border border-border/50'
              }`}>
                <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                  m.preference_summary.feedback_applied ? 'bg-emerald-500' : 'bg-muted-foreground/40'
                }`} />
                {m.preference_summary.feedback_applied
                  ? 'Prior feedback applied to this response'
                  : 'No prior feedback found — responded without feedback context'}
              </div>

              {/* Distilled behavioural rules — show the actual rules when a profile is active */}
              {m.preference_summary.distilled_active && (m.preference_summary.distilled_rules?.length ?? 0) > 0 && (
                <div className="mb-3">
                  <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/50 mb-2">
                    Behavioural Rules Applied
                  </p>
                  <div className="rounded-md border border-border/40 bg-muted/10 px-3 py-2 space-y-1.5">
                    {m.preference_summary.distilled_rules!.map((rule, i) => (
                      <div key={i} className="flex items-start gap-2 text-xs text-foreground/80 leading-relaxed">
                        <span className="text-primary/60 shrink-0 font-bold mt-px">·</span>
                        <span>{rule}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Signal breakdown grid — only show columns that have data */}
              {(m.preference_summary.thread_feedback_count > 0 ||
                m.preference_summary.similar_feedback_count > 0 ||
                m.preference_summary.long_term_memory_count > 0) && (
                <div className={`grid gap-2 mb-3 ${[
                  m.preference_summary.thread_feedback_count > 0,
                  m.preference_summary.similar_feedback_count > 0,
                  m.preference_summary.long_term_memory_count > 0,
                ].filter(Boolean).length === 1 ? 'grid-cols-1' :
                  [
                    m.preference_summary.thread_feedback_count > 0,
                    m.preference_summary.similar_feedback_count > 0,
                    m.preference_summary.long_term_memory_count > 0,
                  ].filter(Boolean).length === 2 ? 'grid-cols-2' : 'grid-cols-3'}`}>
                  {m.preference_summary.thread_feedback_count > 0 && (
                    <div className="rounded-md border border-border/50 bg-muted/10 px-2.5 py-2 text-center">
                      <div className="text-lg font-semibold tabular-nums text-foreground">
                        {m.preference_summary.thread_feedback_count}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 leading-tight mt-0.5">
                        This thread<br />ratings
                      </div>
                    </div>
                  )}
                  {m.preference_summary.similar_feedback_count > 0 && (
                    <div className="rounded-md border border-border/50 bg-muted/10 px-2.5 py-2 text-center">
                      <div className="text-lg font-semibold tabular-nums text-foreground">
                        {m.preference_summary.similar_feedback_count}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 leading-tight mt-0.5">
                        Other threads<br />matches
                      </div>
                    </div>
                  )}
                  {m.preference_summary.long_term_memory_count > 0 && (
                    <div className="rounded-md border border-border/50 bg-muted/10 px-2.5 py-2 text-center">
                      <div className="text-lg font-semibold tabular-nums text-foreground">
                        {m.preference_summary.long_term_memory_count}
                      </div>
                      <div className="text-[10px] text-muted-foreground/70 leading-tight mt-0.5">
                        Memory<br />interactions
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* Feedback signal details */}
              {m.preference_summary.feedback_items.length > 0 && (
                <FeedbackSignalList items={m.preference_summary.feedback_items} />
              )}
            </Section>
          )}

          {/* Source */}
          {((m?.source_tables?.length ?? 0) > 0 || m?.data_freshness_at) && (
            <Section
              title={
                (m?.source_tables?.length ?? 0) > 1
                  ? `Source · ${m!.source_tables!.length} tables`
                  : 'Source'
              }
              icon={Database}
            >
              {m?.source_tables?.map((tbl) => (
                <div
                  key={tbl}
                  className="font-mono text-sm text-foreground/85 py-1 break-all leading-relaxed border-b border-border/40 last:border-0"
                >
                  {tbl}
                </div>
              ))}
              {m?.data_freshness_at && (
                <KV
                  label="Freshness"
                  value={
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <span className="cursor-default">
                          as of {formatRelativeTime(m.data_freshness_at, now)}
                        </span>
                      </TooltipTrigger>
                      <TooltipContent side="left">
                        {new Date(m.data_freshness_at).toLocaleString([], {
                          dateStyle: 'long',
                          timeStyle: 'short',
                        })}
                      </TooltipContent>
                    </Tooltip>
                  }
                />
              )}
            </Section>
          )}

          {/* Result — only shown when query actually returned data */}
          {((m?.row_count ?? 0) > 0 || (m?.columns?.length ?? 0) > 0) && (
            <Section title="Result" icon={Hash}>
              {m?.row_count != null && (
                <KV label="Rows" value={m.row_count.toLocaleString()} mono />
              )}
              {(m?.columns?.length ?? 0) > 0 && (
                <KV label="Columns" value={String(m!.columns!.length)} mono />
              )}
            </Section>
          )}

          {/* Confidence — only present for SQL-backed answers when backend returns it */}
          {(() => {
            const confidence = m?.confidence ?? null;
            return confidence ? (
            <Section title="Confidence" icon={ShieldCheck}>
              <KV label="Score" value={`${confidence.score} / 100`} mono />
              <KV
                label="Label"
                value={
                  <span className={
                    confidence.label === 'High'   ? 'text-emerald-600 dark:text-emerald-400 font-medium' :
                    confidence.label === 'Medium' ? 'text-amber-600 dark:text-amber-400 font-medium'     :
                                                    'text-red-600 dark:text-red-400 font-medium'
                  }>
                    {confidence.label}
                  </span>
                }
              />
              <div className="mt-2">
                <MarkdownText>{confidence.explanation}</MarkdownText>
              </div>
            </Section>
          ) : null;
          })()}

          {/* Metric definition */}
          {m?.metric_name && (
            <Section title="Metric" icon={Info}>
              <KV label="Name" value={m.metric_name} />
              {m.metric_owner && <KV label="Owner" value={m.metric_owner} />}
              {m.metric_defined_at && (
                <KV
                  label="Defined"
                  value={formatRelativeTime(m.metric_defined_at, now)}
                />
              )}
            </Section>
          )}

          {/* Pipeline — enriched with per-step cost when available */}
          {(m?.pipeline_steps?.length ?? 0) > 0 && (() => {
            const byNode = m?.token_usage?.by_node ?? [];
            type R = typeof byNode[number];

            // Group records by node in order so repeated nodes (e.g. executor
            // called 3×) can be matched to their pipeline step by visit index.
            const groups = new Map<string, R[]>();
            for (const r of byNode) {
              const arr = groups.get(r.node);
              if (arr) arr.push(r); else groups.set(r.node, [r]);
            }

            // Count how many times each node appears in the pipeline.
            const pipelineCount = new Map<string, number>();
            for (const s of m!.pipeline_steps!) {
              pipelineCount.set(s.node, (pipelineCount.get(s.node) ?? 0) + 1);
            }

            // Build a per-step cost array:
            //   • Nodes that appear once  → aggregate ALL their records (e.g. synthesis
            //     runs narrative + chart concurrently — show combined cost).
            //   • Nodes that appear multiple times → match nth step to nth record.
            const visit = new Map<string, number>();
            const stepCosts = m!.pipeline_steps!.map(s => {
              const recs = groups.get(s.node);
              if (!recs?.length) return undefined;
              const v = visit.get(s.node) ?? 0;
              visit.set(s.node, v + 1);
              if ((pipelineCount.get(s.node) ?? 1) === 1 && recs.length > 1) {
                return recs.slice(1).reduce<R>(
                  (acc, r) => ({
                    ...acc,
                    cost_usd: acc.cost_usd + r.cost_usd,
                    input_tokens: acc.input_tokens + r.input_tokens,
                    output_tokens: acc.output_tokens + r.output_tokens,
                    total_tokens: acc.total_tokens + r.total_tokens,
                  }),
                  recs[0],
                );
              }
              return recs[v];
            });

            const totalCost = m?.token_usage?.total_cost_usd;
            const hasCost = byNode.length > 0;

            return (
              <Collapsible title="Pipeline" icon={ListOrdered}>
                {/* Column headers — only when cost data is present */}
                {hasCost && (
                  <div className="flex items-center gap-1 pb-1 mb-1 border-b border-border/60 text-xs font-medium text-muted-foreground/50 uppercase tracking-wide">
                    <span className="flex-1">Step</span>
                    <span className="w-14 text-right">Cost</span>
                    <span className="w-12 text-right">Time</span>
                  </div>
                )}

                <div className="space-y-1">
                  {m!.pipeline_steps!.map((step, i) => {
                    const dim = step.status === 'skipped';
                    const cost = stepCosts[i];
                    return (
                      <div key={`${step.node}-${i}`} className="flex items-center gap-2 py-1 text-sm">
                        <span className={`flex-1 truncate ${dim ? 'text-muted-foreground/50 line-through' : 'text-foreground/85'}`}>
                          {step.message || step.node}
                        </span>
                        {hasCost && (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span className="w-14 text-right font-mono tabular-nums text-foreground/70 text-xs cursor-default">
                                {cost ? `$${cost.cost_usd.toFixed(4)}` : '—'}
                              </span>
                            </TooltipTrigger>
                            {cost && (
                              <TooltipContent side="left" className="text-xs min-w-[160px]">
                                <div className="space-y-1.5">
                                  <div className="flex justify-between gap-4 font-medium">
                                    <span>Input</span>
                                    <span className="tabular-nums">{cost.input_tokens.toLocaleString()}</span>
                                  </div>
                                  {(cost.cache_creation_tokens ?? 0) > 0 && (
                                    <div className="flex justify-between gap-4 text-muted-foreground/70 pl-2">
                                      <span>cache write</span>
                                      <span className="tabular-nums">{cost.cache_creation_tokens!.toLocaleString()}</span>
                                    </div>
                                  )}
                                  {(cost.cache_read_tokens ?? 0) > 0 && (
                                    <div className="flex justify-between gap-4 text-muted-foreground/70 pl-2">
                                      <span>cache read</span>
                                      <span className="tabular-nums">{cost.cache_read_tokens!.toLocaleString()}</span>
                                    </div>
                                  )}
                                  <div className="flex justify-between gap-4 font-medium">
                                    <span>Output</span>
                                    <span className="tabular-nums">{cost.output_tokens.toLocaleString()}</span>
                                  </div>
                                  <div className="flex justify-between gap-4 border-t border-border/60 pt-1 font-semibold">
                                    <span>Total</span>
                                    <span className="tabular-nums">{cost.total_tokens.toLocaleString()}</span>
                                  </div>
                                </div>
                              </TooltipContent>
                            )}
                          </Tooltip>
                        )}
                        <span className="w-12 text-right font-mono tabular-nums text-muted-foreground/70 text-xs shrink-0">
                          {step.duration_ms != null ? `${(step.duration_ms / 1000).toFixed(2)}s` : '-'}
                        </span>
                      </div>
                    );
                  })}
                </div>

                {/* Totals footer */}
                {totalCost != null && (
                  <div className="pt-1.5 mt-1 border-t border-border/60 space-y-0.5">
                    <div className="flex items-center gap-1 text-xs text-muted-foreground/70">
                      <span className="flex-1">Total tokens</span>
                      <span className="font-mono tabular-nums">
                        {m!.token_usage!.total_tokens.toLocaleString()}
                        <span className="text-muted-foreground/40 ml-1">
                          ({m!.token_usage!.total_input_tokens.toLocaleString()} in / {m!.token_usage!.total_output_tokens.toLocaleString()} out)
                        </span>
                      </span>
                    </div>
                    <div className="flex items-center gap-1 text-sm font-medium">
                      <span className="flex-1 text-foreground/60">Total cost</span>
                      <span className="font-mono tabular-nums text-primary text-xs">
                        ${totalCost.toFixed(4)}
                      </span>
                    </div>
                  </div>
                )}

                {/* Cache hint */}
                {((m?.token_usage?.cache_read_tokens ?? 0) > 0 || (m?.token_usage?.cache_creation_tokens ?? 0) > 0) && (
                  <div className="mt-1 flex gap-3 text-xs text-muted-foreground/50">
                    {(m!.token_usage!.cache_read_tokens > 0) && (
                      <span>Cache read: {m!.token_usage!.cache_read_tokens.toLocaleString()}</span>
                    )}
                    {(m!.token_usage!.cache_creation_tokens > 0) && (
                      <span>Cache write: {m!.token_usage!.cache_creation_tokens.toLocaleString()}</span>
                    )}
                  </div>
                )}
              </Collapsible>
            );
          })()}

          {/* SQL - collapsible, copyable, syntax-highlighted. */}
          {m?.sql && (
            <Collapsible
              title="SQL"
              icon={Database}
              actions={
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleCopy(m.sql!, 'SQL');
                  }}
                  className="p-1 rounded text-muted-foreground"
                  aria-label="Copy SQL"
                >
                  <Copy className="w-3.5 h-3.5" />
                </button>
              }
            >
              <div className="rounded-md border border-border bg-muted/40 max-h-72 overflow-y-auto">
                <SqlBlock code={m.sql} />
              </div>
            </Collapsible>
          )}

          {/* Reasoning trace - grouped by node when pipeline_steps available */}
          {(() => {
            const stepsWithReasoning = m?.pipeline_steps?.filter((s) => s.reasoning?.trim());
            if (stepsWithReasoning && stepsWithReasoning.length > 0) {
              const fullText = stepsWithReasoning
                .map((s) => `## ${s.message || s.node}\n\n${s.reasoning}`)
                .join('\n\n---\n\n');
              return (
                <Collapsible
                  title="Reasoning trace"
                  icon={ScrollText}
                  actions={
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleCopy(fullText, 'Reasoning trace');
                      }}
                      className="p-1 rounded text-muted-foreground"
                      aria-label="Copy reasoning trace"
                    >
                      <Copy className="w-3.5 h-3.5" />
                    </button>
                  }
                >
                  <div className="space-y-3 max-h-[28rem] overflow-y-auto">
                    {stepsWithReasoning.map((step, i) => (
                      <div key={`${step.node}-${i}`} className="rounded-md border border-border bg-muted/40 overflow-hidden">
                        <div className="px-3 py-1.5 bg-muted/60 border-b border-border/60 flex items-center gap-2">
                          <span className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                            {step.message || step.node}
                          </span>
                          {step.duration_ms != null && (
                            <span className="ml-auto text-[10px] font-mono tabular-nums text-muted-foreground/60">
                              {(step.duration_ms / 1000).toFixed(2)}s
                            </span>
                          )}
                        </div>
                        <div className="p-2.5">
                          <MarkdownRenderer content={step.reasoning!} />
                        </div>
                      </div>
                    ))}
                  </div>
                </Collapsible>
              );
            }
            // Fallback: legacy flat reasoning string
            if (message.reasoning) {
              return (
                <Collapsible
                  title="Reasoning trace"
                  icon={ScrollText}
                  actions={
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleCopy(message.reasoning!, 'Reasoning trace');
                      }}
                      className="p-1 rounded text-muted-foreground"
                      aria-label="Copy reasoning trace"
                    >
                      <Copy className="w-3.5 h-3.5" />
                    </button>
                  }
                >
                  <div className="rounded-md border border-border bg-muted/40 p-2.5 max-h-72 overflow-y-auto">
                    <MarkdownRenderer content={message.reasoning!} />
                  </div>
                </Collapsible>
              );
            }
            return null;
          })()}
        </div>
      </SheetContent>
    </Sheet>
  );
}

type TribalFact = { label: string; value: string };

function TribalKnowledgeList({ facts }: { facts: TribalFact[] }) {
  const [openFacts, setOpenFacts] = useState<Set<number>>(new Set([0]));

  const toggle = (i: number) =>
    setOpenFacts(prev => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i); else next.add(i);
      return next;
    });

  return (
    <div className="space-y-1">
      {facts.map((fact, i) => {
        const isOpen = openFacts.has(i);
        return (
          <div key={i} className="rounded-md border border-border/40 overflow-hidden">
            <button
              onClick={() => toggle(i)}
              className="w-full flex items-center gap-2 px-2.5 py-2 text-left bg-muted/10 hover:bg-muted/20 transition-colors"
            >
              {isOpen
                ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground/60 shrink-0" />
                : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground/60 shrink-0" />}
              <span className="text-xs font-semibold text-foreground/80 truncate flex-1">
                {fact.label}
              </span>
            </button>
            {isOpen && (
              <div className="border-t border-border/30 p-2.5">
                <MarkdownRenderer content={fact.value} />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

const FEEDBACK_VISIBLE_DEFAULT = 3;

type FeedbackItem = {
  liked: boolean;
  comment?: string | null;
  source: string;
  question_preview?: string;
  similarity?: number | null;
};

function FeedbackSignalList({ items }: { items: FeedbackItem[] }) {
  const [expanded, setExpanded] = useState(false);
  const visible = expanded ? items : items.slice(0, FEEDBACK_VISIBLE_DEFAULT);
  const hidden = items.length - FEEDBACK_VISIBLE_DEFAULT;

  return (
    <div className="space-y-2">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground/50 mb-1">
        Feedback Signals
      </p>
      {visible.map((item, i) => (
        <div key={i} className="rounded-md border border-border/40 bg-muted/10 overflow-hidden">
          <div className={`flex items-center gap-2 px-2.5 py-1.5 border-b border-border/30 ${
            item.liked ? 'bg-emerald-50/50 dark:bg-emerald-950/20' : 'bg-red-50/50 dark:bg-red-950/20'
          }`}>
            {item.liked ? (
              <ThumbsUp className="w-3 h-3 shrink-0 text-emerald-600 dark:text-emerald-400" />
            ) : (
              <ThumbsDown className="w-3 h-3 shrink-0 text-red-500 dark:text-red-400" />
            )}
            <span className={`text-[10px] font-semibold uppercase tracking-wide ${
              item.liked ? 'text-emerald-700 dark:text-emerald-400' : 'text-red-600 dark:text-red-400'
            }`}>
              {item.liked ? 'Keep doing' : 'Avoid'}
            </span>
            <span className="ml-auto text-[10px] text-muted-foreground/50 font-medium">
              {item.source === 'similar' && item.similarity != null
                ? `other thread · ${Math.round(item.similarity * 100)}% match`
                : 'this thread'}
            </span>
          </div>
          <div className="px-2.5 py-2 text-xs space-y-1">
            {item.comment ? (
              <p className="text-foreground/85 leading-relaxed">{item.comment}</p>
            ) : (
              <p className="text-muted-foreground/50 italic">
                {item.liked ? 'Thumbs up — no specific comment' : 'Thumbs down — no specific comment'}
              </p>
            )}
            {item.question_preview && (
              <p className="text-[10px] text-muted-foreground/50 truncate border-t border-border/30 pt-1 mt-1">
                On: &ldquo;{item.question_preview}&rdquo;
              </p>
            )}
          </div>
        </div>
      ))}
      {hidden > 0 && (
        <button
          onClick={() => setExpanded(e => !e)}
          className="w-full text-[10px] text-muted-foreground/60 hover:text-muted-foreground py-1.5 border border-dashed border-border/40 rounded-md transition-colors"
        >
          {expanded ? 'Show less' : `Show ${hidden} more signal${hidden !== 1 ? 's' : ''}`}
        </button>
      )}
    </div>
  );
}

function Section({
  title,
  icon: Icon,
  children,
}: {
  title: ReactNode;
  icon?: ComponentType<{ className?: string }>;
  children: ReactNode;
}) {
  return (
    <section className="px-5 py-4 border-b border-border last:border-b-0">
      <div className="flex items-center gap-2 mb-3">
        {Icon && <Icon className="w-4 h-4 text-muted-foreground/70" />}
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </h3>
      </div>
      <div>{children}</div>
    </section>
  );
}

function Collapsible({
  title,
  icon: Icon,
  children,
  actions,
}: {
  title: string;
  icon?: ComponentType<{ className?: string }>;
  children: ReactNode;
  actions?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="border-b border-border last:border-b-0">
      <div className="flex items-center px-5 py-4">
        <button
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-1.5 flex-1 text-left"
          aria-expanded={open}
        >
          {open ? (
            <ChevronDown className="w-4 h-4 text-muted-foreground/70" />
          ) : (
            <ChevronRight className="w-4 h-4 text-muted-foreground/70" />
          )}
          {Icon && <Icon className="w-4 h-4 text-muted-foreground/70" />}
          <h3 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            {title}
          </h3>
        </button>
        {actions && <div className="ml-2">{actions}</div>}
      </div>
      {open && <div className="px-5 pb-4">{children}</div>}
    </section>
  );
}

function KV({
  label,
  value,
  mono,
  wrap,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
  wrap?: boolean;
}) {
  if (wrap) {
    return (
      <div className="py-1.5 text-sm">
        <span className="block text-muted-foreground/80 mb-1 font-medium">{label}</span>
        <span
          className={`block text-foreground/90 leading-relaxed break-words ${
            mono ? 'font-mono tabular-nums' : ''
          }`}
        >
          {value}
        </span>
      </div>
    );
  }
  return (
    <div className="flex items-baseline justify-between gap-3 py-1 text-sm">
      <span className="text-muted-foreground/80 shrink-0">{label}</span>
      <span
        className={`text-foreground/90 text-right break-all ${
          mono ? 'font-mono tabular-nums' : ''
        }`}
      >
        {value}
      </span>
    </div>
  );
}

/** Compact ID display: shows first 8 + last 4 chars, tooltip with full id,
 *  click copies. Pattern lifted from how Stripe / Linear render request ids. */
function CopyableId({ id, onCopy }: { id: string; onCopy: () => void }) {
  const display =
    id.length <= 14 ? id : `${id.slice(0, 8)}…${id.slice(-4)}`;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          onClick={onCopy}
          className="font-mono text-sm hover:text-foreground transition-colors inline-flex items-center gap-1"
        >
          {display}
          <Copy className="w-3.5 h-3.5 opacity-60" />
        </button>
      </TooltipTrigger>
      <TooltipContent side="left">
        <span className="font-mono text-xs">{id}</span>
      </TooltipContent>
    </Tooltip>
  );
}
