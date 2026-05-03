'use client';

import { useState, type ComponentType, type ReactNode } from 'react';
import {
  Info,
  Copy,
  ChevronDown,
  ChevronRight,
  Database,
  Layers,
  Hash,
  Clock,
  Target,
  ListOrdered,
  ScrollText,
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
import hljs from 'highlight.js/lib/core';
import sqlLang from 'highlight.js/lib/languages/sql';
import { copyText } from '@/lib/utils';
import { toast } from '@/lib/toast';
import { formatRelativeTime } from '@/lib/utils/relative-time';
import { useNow } from '@/lib/hooks/use-now';
import type { Message } from '@/lib/store/threads';

hljs.registerLanguage('sql', sqlLang);

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
 * About-this-answer panel — slides in from the right, shows the receipts
 * for an assistant response: timing, sources, SQL, pipeline trace,
 * resolved filters, and the run identifiers an analyst would need to
 * reproduce or cite the answer.
 *
 * Design intent: every section hides cleanly when its data is missing.
 * The backend is still evolving — fields like metric_owner and
 * source_tables may arrive only once the warehouse integration ships —
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
        className="w-full sm:max-w-md p-0 flex flex-col gap-0"
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
          {/* Run — always shown; created_at is guaranteed. */}
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
          </Section>

          {/* Interpretation — only when we have something interpretive to show. */}
          {(question || m?.intent || m?.resolved_filters) && (
            <Section title="Interpretation" icon={Target}>
              {question && (
                <div className="text-[12px] text-foreground/90 leading-relaxed mb-2.5 pl-2 border-l-2 border-border italic">
                  {question}
                </div>
              )}
              {m?.intent && <KV label="Intent" value={m.intent} />}
              {m?.resolved_filters && (
                <KV label="Filters" value={m.resolved_filters} />
              )}
            </Section>
          )}

          {/* Source */}
          {((m?.source_tables?.length ?? 0) > 0 || m?.data_freshness_at) && (
            <Section title="Source" icon={Database}>
              {m?.source_tables?.map((tbl) => (
                <div
                  key={tbl}
                  className="font-mono text-[11px] text-foreground/85 mb-1 break-all leading-relaxed"
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

          {/* Result */}
          {(m?.row_count != null || (m?.columns?.length ?? 0) > 0) && (
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

          {/* Pipeline trace — collapsible because it can be long. */}
          {(m?.pipeline_steps?.length ?? 0) > 0 && (
            <Collapsible title="Pipeline" icon={ListOrdered}>
              <div className="space-y-0.5">
                {m!.pipeline_steps!.map((step, i) => {
                  const dim = step.status === 'skipped';
                  return (
                    <div
                      key={`${step.node}-${i}`}
                      className="flex items-center justify-between gap-3 py-0.5"
                    >
                      <span
                        className={`text-[11px] truncate ${
                          dim
                            ? 'text-muted-foreground/50 line-through'
                            : 'text-foreground/85'
                        }`}
                      >
                        {step.message || step.node}
                      </span>
                      <span className="font-mono text-[10px] tabular-nums text-muted-foreground/70 shrink-0">
                        {step.duration_ms != null
                          ? `${(step.duration_ms / 1000).toFixed(2)}s`
                          : '—'}
                      </span>
                    </div>
                  );
                })}
              </div>
            </Collapsible>
          )}

          {/* SQL — collapsible, copyable, syntax-highlighted. */}
          {m?.sql && (
            <Collapsible
              title="SQL"
              icon={Database}
              actions={
                <Tooltip>
                  <TooltipTrigger asChild>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleCopy(m.sql!, 'SQL');
                      }}
                      className="p-1 rounded text-muted-foreground hover:text-foreground hover:bg-accent transition-colors"
                      aria-label="Copy SQL"
                    >
                      <Copy className="w-3.5 h-3.5" />
                    </button>
                  </TooltipTrigger>
                  <TooltipContent side="left">Copy SQL</TooltipContent>
                </Tooltip>
              }
            >
              <pre
                className="text-[11px] font-mono leading-relaxed whitespace-pre-wrap break-words rounded-md border border-border bg-muted/40 p-2.5 max-h-72 overflow-y-auto"
                dangerouslySetInnerHTML={{
                  __html: hljs.highlight(m.sql, { language: 'sql' }).value,
                }}
              />
            </Collapsible>
          )}

          {/* Reasoning trace — full text the LLM produced, for debugging. */}
          {message.reasoning && (
            <Collapsible title="Reasoning trace" icon={ScrollText}>
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
  title: string;
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
