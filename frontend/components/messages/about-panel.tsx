'use client';

import { useState, type ComponentType, type ReactNode } from 'react';
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
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { useTheme } from 'next-themes';
import { copyText } from '@/lib/utils';
import { toast } from '@/lib/toast';
import { formatRelativeTime } from '@/lib/utils/relative-time';
import { useNow } from '@/lib/hooks/use-now';
import type { Message } from '@/lib/store/threads';

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
  const { resolvedTheme } = useTheme();

  const handleCopy = async (text: string, label: string) => {
    const ok = await copyText(text);
    if (ok) toast.success(`${label} copied`);
    else toast.error('Copy failed');
  };

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side="right"
        className="w-full sm:max-w-md p-0 flex flex-col gap-0"
        onOpenAutoFocus={(e) => e.preventDefault()}
      >
        <SheetHeader className="px-5 py-4 border-b border-border gap-1">
          <SheetTitle className="text-sm font-semibold tracking-tight">
            About this answer
          </SheetTitle>
          <SheetDescription className="text-xs">
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
                    <ExternalLink className="w-3 h-3" />
                  </a>
                }
              />
            )}
          </Section>

          {/* Interpretation - only when we have something interpretive to show. */}
          {(question || m?.intent || m?.resolved_filters) && (
            <Section title="Interpretation" icon={Target}>
              {question && (
                <div className="text-[12px] text-foreground/90 leading-relaxed mb-2.5 pl-2 border-l-2 border-border italic">
                  {question}
                </div>
              )}
              {m?.intent && (
                <KV
                  label="Intent"
                  value={m.intent}
                />
              )}
              {m?.resolved_filters && (
                <KV label="Filters" value={m.resolved_filters} />
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
                  className="font-mono text-[11px] text-foreground/85 py-0.5 break-all leading-relaxed border-b border-border/40 last:border-0"
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

            // Group records by node in order so repeated nodes (e.g. sparql_gen
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
                  <div className="flex items-center gap-1 pb-1 mb-1 border-b border-border/60 text-[10px] font-medium text-muted-foreground/50 uppercase tracking-wide">
                    <span className="flex-1">Step</span>
                    <span className="w-14 text-right">Cost</span>
                    <span className="w-12 text-right">Time</span>
                  </div>
                )}

                <div className="space-y-0.5">
                  {m!.pipeline_steps!.map((step, i) => {
                    const dim = step.status === 'skipped';
                    const cost = stepCosts[i];
                    return (
                      <div key={`${step.node}-${i}`} className="flex items-center gap-1 py-0.5 text-[11px]">
                        <span className={`flex-1 truncate ${dim ? 'text-muted-foreground/50 line-through' : 'text-foreground/85'}`}>
                          {step.message || step.node}
                        </span>
                        {hasCost && (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <span className="w-14 text-right font-mono tabular-nums text-foreground/70 text-[10px] cursor-default">
                                {cost ? `$${cost.cost_usd.toFixed(4)}` : '—'}
                              </span>
                            </TooltipTrigger>
                            {cost && (
                              <TooltipContent side="left" className="text-[10px] min-w-[160px]">
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
                        <span className="w-12 text-right font-mono tabular-nums text-muted-foreground/70 text-[10px] shrink-0">
                          {step.duration_ms != null ? `${(step.duration_ms / 1000).toFixed(2)}s` : '-'}
                        </span>
                      </div>
                    );
                  })}
                </div>

                {/* Totals footer */}
                {totalCost != null && (
                  <div className="pt-1.5 mt-1 border-t border-border/60 space-y-0.5">
                    <div className="flex items-center gap-1 text-[10px] text-muted-foreground/70">
                      <span className="flex-1">Total tokens</span>
                      <span className="font-mono tabular-nums">
                        {m!.token_usage!.total_tokens.toLocaleString()}
                        <span className="text-muted-foreground/40 ml-1">
                          ({m!.token_usage!.total_input_tokens.toLocaleString()} in / {m!.token_usage!.total_output_tokens.toLocaleString()} out)
                        </span>
                      </span>
                    </div>
                    <div className="flex items-center gap-1 text-[11px] font-medium">
                      <span className="flex-1 text-foreground/60">Total cost</span>
                      <span className="font-mono tabular-nums text-primary text-[10px]">
                        ${totalCost.toFixed(4)}
                      </span>
                    </div>
                  </div>
                )}

                {/* Cache hint */}
                {((m?.token_usage?.cache_read_tokens ?? 0) > 0 || (m?.token_usage?.cache_creation_tokens ?? 0) > 0) && (
                  <div className="mt-1 flex gap-3 text-[10px] text-muted-foreground/50">
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
              title="SPARQL"
              icon={Database}
              actions={
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleCopy(m.sql!, 'SPARQL');
                  }}
                  className="p-1 rounded text-muted-foreground"
                  aria-label="Copy SPARQL"
                >
                  <Copy className="w-3.5 h-3.5" />
                </button>
              }
            >
              <div className="rounded-md border border-border bg-muted/40 max-h-72 overflow-y-auto">
                <SyntaxHighlighter
                  language="sparql"
                  style={resolvedTheme === 'dark' ? sparqlDark : sparqlLight}
                  customStyle={{ margin: 0, padding: '10px', fontSize: '11px', lineHeight: '1.6', background: 'transparent' }}
                  wrapLongLines
                >
                  {m.sql}
                </SyntaxHighlighter>
              </div>
            </Collapsible>
          )}

          {/* Reasoning trace - full text the LLM produced, for debugging. */}
          {message.reasoning && (
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
              <div className="text-[11px] text-muted-foreground leading-relaxed whitespace-pre-wrap rounded-md border border-border bg-muted/40 p-2.5 max-h-72 overflow-y-auto">
                {message.reasoning}
              </div>
            </Collapsible>
          )}
        </div>
      </SheetContent>
    </Sheet>
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
    <section className="px-5 py-3.5 border-b border-border last:border-b-0">
      <div className="flex items-center gap-1.5 mb-2">
        {Icon && <Icon className="w-3 h-3 text-muted-foreground/70" />}
        <h3 className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
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
      <div className="flex items-center px-5 py-3.5">
        <button
          onClick={() => setOpen((o) => !o)}
          className="flex items-center gap-1.5 flex-1 text-left"
          aria-expanded={open}
        >
          {open ? (
            <ChevronDown className="w-3 h-3 text-muted-foreground/70" />
          ) : (
            <ChevronRight className="w-3 h-3 text-muted-foreground/70" />
          )}
          {Icon && <Icon className="w-3 h-3 text-muted-foreground/70" />}
          <h3 className="text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
            {title}
          </h3>
        </button>
        {actions && <div className="ml-2">{actions}</div>}
      </div>
      {open && <div className="px-5 pb-3.5">{children}</div>}
    </section>
  );
}

function KV({
  label,
  value,
  mono,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-0.5 text-[11px]">
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
          className="font-mono text-[11px] hover:text-foreground transition-colors inline-flex items-center gap-1"
        >
          {display}
          <Copy className="w-2.5 h-2.5 opacity-60" />
        </button>
      </TooltipTrigger>
      <TooltipContent side="left">
        <span className="font-mono text-[10px]">{id}</span>
      </TooltipContent>
    </Tooltip>
  );
}
