'use client';

import { Database, Clock, Target, Rows3, ShieldCheck } from 'lucide-react';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { formatRelativeTime } from '@/lib/utils/relative-time';

/**
 * Trust strip - the row of metadata under every assistant answer that says
 * "this number is provably correct." Renders only fields that the backend
 * has populated; absent fields just hide their cell - we never invent or
 * regex-infer trust data on the client.
 *
 * Source tables come from the backend's SQL analysis (sqlglot or
 * Snowflake INFORMATION_SCHEMA). When there are many tables (joins, CTEs,
 * subqueries) we show the primary one inline and roll up the rest behind
 * a "+N more" pill so the strip never overflows the answer column.
 */

export interface TrustStripProps {
  /** Source tables touched by the query, primary first. The first is
   *  rendered inline; remaining tables show as a "+N more" pill with
   *  the full list in a tooltip. */
  sources?: string[] | null;
  /** ISO timestamp of when the underlying data was last refreshed. */
  freshnessAt?: string | null;
  /** Metric catalog entry. Tooltip shows owner + defined-at when available. */
  metric?: {
    name: string;
    owner?: string | null;
    definedAt?: string | null;
  } | null;
  /** Authoritative row count from the query. */
  rowCount?: number | null;
  /** Confidence score from the backend — only present for SQL-backed answers. */
  confidence?: { score: number; label: 'High' | 'Medium' | 'Low'; explanation: string } | null;
}

/** Always show only the first table inline; the rest collapse into a "+N"
 *  pill. With many tables or long names, showing two inline can overflow
 *  the strip. One name + pill is consistently compact. */
const INLINE_SOURCE_LIMIT = 1;

function MetadataCell({
  icon,
  children,
  ariaLabel,
  tooltip,
}: {
  icon: React.ReactNode;
  children: React.ReactNode;
  ariaLabel: string;
  tooltip?: React.ReactNode;
}) {
  const cell = (
    <span
      aria-label={ariaLabel}
      className="inline-flex items-center gap-1 text-muted-foreground"
    >
      <span className="shrink-0 opacity-70" aria-hidden>
        {icon}
      </span>
      {children}
    </span>
  );
  if (!tooltip) return cell;
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="cursor-default">{cell}</span>
      </TooltipTrigger>
      <TooltipContent side="bottom" className="max-w-sm">
        {tooltip}
      </TooltipContent>
    </Tooltip>
  );
}

function SourceCell({ sources }: { sources: string[] }) {
  const inline = sources.slice(0, INLINE_SOURCE_LIMIT);
  const overflow = sources.slice(INLINE_SOURCE_LIMIT);
  const hasOverflow = overflow.length > 0;

  const tooltipBody = (
    <div className="flex flex-col gap-1">
      <span className="text-[10px] uppercase tracking-wider opacity-60">
        {sources.length} {sources.length === 1 ? 'table' : 'tables'}
      </span>
      <span className="font-mono text-[11px] leading-relaxed break-all">
        {sources.join(', ')}
      </span>
    </div>
  );

  return (
    <MetadataCell
      icon={<Database className="w-3 h-3" />}
      ariaLabel={
        sources.length === 1
          ? `Source table ${sources[0]}`
          : `Source tables: ${sources.join(', ')}`
      }
      tooltip={tooltipBody}
    >
      <span className="inline-flex items-center gap-1 min-w-0">
        {inline.map((tbl, i) => (
          <span key={tbl} className="inline-flex items-center gap-1 min-w-0">
            {i > 0 && (
              <span aria-hidden className="text-muted-foreground/40">,</span>
            )}
            <span className="font-mono text-[10.5px] truncate max-w-[14rem]">
              {tbl}
            </span>
          </span>
        ))}
      </span>
      {hasOverflow && (
        <span
          className="ml-1 inline-flex items-center rounded-full border border-border bg-muted px-1.5 py-px text-[10px] tabular-nums leading-none text-muted-foreground"
          aria-label={`${overflow.length} more table${overflow.length === 1 ? '' : 's'}`}
        >
          +{overflow.length}
        </span>
      )}
    </MetadataCell>
  );
}

const STALE_THRESHOLD_MS = 24 * 60 * 60 * 1000;


export function TrustStrip({
  sources,
  freshnessAt,
  metric,
  rowCount,
  confidence,
}: TrustStripProps) {
  const isStale =
    freshnessAt != null &&
    Date.now() - new Date(freshnessAt).getTime() > STALE_THRESHOLD_MS;

  const cells: React.ReactNode[] = [];

  if (sources && sources.length > 0) {
    cells.push(<SourceCell key="source" sources={sources} />);
  }

  if (freshnessAt) {
    cells.push(
      <MetadataCell
        key="freshness"
        icon={<Clock className={`w-3 h-3 ${isStale ? 'text-amber-500' : ''}`} />}
        ariaLabel="Data freshness"
        tooltip={
          <div className="flex flex-col gap-0.5">
            <span className="whitespace-nowrap">
              Last refreshed{' '}
              {new Date(freshnessAt).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}
            </span>
            {isStale && (
              <span className="text-amber-400">May not reflect today&apos;s activity</span>
            )}
          </div>
        }
      >
        <span className={isStale ? 'text-amber-600 dark:text-amber-400 font-medium' : ''}>
          as of {formatRelativeTime(freshnessAt)}
          {isStale && ' ⚠'}
        </span>
      </MetadataCell>,
    );
  }

  if (metric?.name) {
    const tooltipParts: React.ReactNode[] = [
      <span key="n">
        Metric: <strong>{metric.name}</strong>
      </span>,
    ];
    if (metric.owner) tooltipParts.push(<span key="o">Owner: {metric.owner}</span>);
    if (metric.definedAt)
      tooltipParts.push(
        <span key="d">Defined {formatRelativeTime(metric.definedAt)}</span>,
      );
    cells.push(
      <MetadataCell
        key="metric"
        icon={<Target className="w-3 h-3" />}
        ariaLabel={`Metric ${metric.name}`}
        tooltip={<div className="flex flex-col gap-1">{tooltipParts}</div>}
      >
        <span>{metric.name}</span>
      </MetadataCell>,
    );
  }

  if (rowCount != null && rowCount >= 0) {
    cells.push(
      <MetadataCell
        key="rows"
        icon={<Rows3 className="w-3 h-3" />}
        ariaLabel={`${rowCount} rows`}
      >
        <span className="tabular-nums">
          {rowCount.toLocaleString('en-US')} {rowCount === 1 ? 'row' : 'rows'}
        </span>
      </MetadataCell>,
    );
  }

  if (confidence) {
    const labelColor =
      confidence.label === 'High'   ? 'text-emerald-600 dark:text-emerald-400' :
      confidence.label === 'Medium' ? 'text-amber-600 dark:text-amber-400'     :
                                      'text-red-600 dark:text-red-400';
    cells.push(
      <MetadataCell
        key="confidence"
        icon={<ShieldCheck className="w-3 h-3" />}
        ariaLabel={`Confidence: ${confidence.label}`}
        tooltip={
          <div className="flex flex-col gap-1 max-w-xs">
            <span className="text-[10px] uppercase tracking-wider opacity-60">
              Confidence · {confidence.score} / 100
            </span>
            <span className="leading-relaxed">{confidence.explanation}</span>
          </div>
        }
      >
        <span className={`font-medium ${labelColor}`}>{confidence.label}</span>
      </MetadataCell>,
    );
  }

  if (cells.length === 0) return null;

  return (
    <div
      role="group"
      aria-label="Answer provenance"
      className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] leading-relaxed"
    >
      {cells.map((cell, i) => (
        <span key={i} className="inline-flex items-center gap-3">
          {i > 0 && (
            <span aria-hidden className="text-muted-foreground/40 select-none">·</span>
          )}
          {cell}
        </span>
      ))}
    </div>
  );
}
