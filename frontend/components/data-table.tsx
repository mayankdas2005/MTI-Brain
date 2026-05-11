'use client';

import { useState, useMemo, useCallback } from 'react';
import { Button } from '@/components/ui/button';
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import {
  Table, TableHeader, TableBody, TableHead, TableRow, TableCell,
} from '@/components/ui/table';
import {
  ContextMenu, ContextMenuContent, ContextMenuItem, ContextMenuTrigger,
} from '@/components/ui/context-menu';
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, FileText, FileSpreadsheet, ArrowUp, ArrowDown, ArrowUpDown, Copy, AlertTriangle } from 'lucide-react';
import { toast } from '@/lib/toast';
import { copyText } from '@/lib/utils';
import { formatNumber, formatNumberWithDecimals } from '@/lib/utils/number';
import { downloadCSV, downloadXLSX, safeFilename } from '@/lib/utils/export-table';

const ROWS_PER_PAGE = 10;
const MIN_ROWS_FOR_OUTLIER = 5;

function formatLabel(s: string) {
  return s.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

type ColType = 'int' | 'float' | 'text' | 'bool' | 'date' | 'timestamp' | 'null';

const TYPE_LABEL: Record<ColType, string> = {
  int:       'integer',
  float:     'decimal',
  text:      'text',
  bool:      'boolean',
  date:      'date',
  timestamp: 'timestamp',
  null:      '',
};

/** Infer column type from sample values. */
function inferColType(rows: unknown[][], colIndex: number): ColType {
  let hasInt = false;
  let hasFloat = false;
  let hasText = false;
  let hasBool = false;
  let hasDate = false;
  let hasTimestamp = false;
  let samples = 0;

  for (let r = 0; r < Math.min(rows.length, 20); r++) {
    const val = rows[r][colIndex];
    if (val === null || val === undefined || val === '') continue;
    samples++;

    if (typeof val === 'boolean') { hasBool = true; continue; }
    if (typeof val === 'number') {
      Number.isInteger(val) ? (hasInt = true) : (hasFloat = true);
      continue;
    }
    const s = String(val);
    if (s === 'true' || s === 'false') { hasBool = true; continue; }
    const n = Number(s);
    if (!isNaN(n) && s.trim() !== '') {
      s.includes('.') ? (hasFloat = true) : (hasInt = true);
      continue;
    }
    if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(s)) { hasTimestamp = true; continue; }
    if (/^\d{4}-\d{2}-\d{2}$/.test(s) || /^\d{1,2}\/\d{1,2}\/\d{2,4}$/.test(s)) { hasDate = true; continue; }
    hasText = true;
  }

  if (samples === 0) return 'null';
  if (hasBool && !hasText && !hasInt && !hasFloat) return 'bool';
  if (hasTimestamp && !hasText && !hasInt && !hasFloat) return 'timestamp';
  if (hasDate && !hasText && !hasInt && !hasFloat) return 'date';
  if (hasFloat) return 'float';
  if (hasInt && !hasText) return 'int';
  return 'text';
}

/** IQR outlier detection. Returns Set of row indices (within the provided rows array) that are outliers. */
function detectOutliers(rows: unknown[][], colIndex: number): Set<number> {
  const vals: { idx: number; v: number }[] = [];
  rows.forEach((row, i) => {
    const raw = row[colIndex];
    const n = typeof raw === 'number' ? raw : Number(raw);
    if (!isNaN(n) && raw !== null && raw !== undefined && raw !== '') {
      vals.push({ idx: i, v: n });
    }
  });
  if (vals.length < MIN_ROWS_FOR_OUTLIER) return new Set();
  const sorted = [...vals].sort((a, b) => a.v - b.v);
  const q1 = sorted[Math.floor(sorted.length * 0.25)].v;
  const q3 = sorted[Math.floor(sorted.length * 0.75)].v;
  const iqr = q3 - q1;
  if (iqr === 0) return new Set();
  const lower = q1 - 1.5 * iqr;
  const upper = q3 + 1.5 * iqr;
  return new Set(vals.filter(({ v }) => v < lower || v > upper).map(({ idx }) => idx));
}

/** Compare two cell values for sorting. */
function compareCells(a: unknown, b: unknown, colType: ColType): number {
  if (a === null || a === undefined) return 1;
  if (b === null || b === undefined) return -1;
  if (colType === 'int' || colType === 'float') return Number(a) - Number(b);
  return String(a).localeCompare(String(b), 'en-US', { numeric: true, sensitivity: 'base' });
}

interface DataTableProps {
  columns: string[];
  rows: unknown[][];
  rowCount?: number;
  filename?: string;
}

export function DataTable({ columns, rows, rowCount, filename }: DataTableProps) {
  const [page, setPage] = useState(0);
  const [sortCol, setSortCol] = useState<number | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const [showAnomalies, setShowAnomalies] = useState(true);

  const totalRows = rowCount ?? rows.length;

  const colTypes = useMemo(
    () => columns.map((_, i) => inferColType(rows, i)),
    [columns, rows],
  );

  const sortedRows = useMemo(() => {
    if (sortCol === null) return rows;
    const ct = colTypes[sortCol];
    const sorted = [...rows].sort((a, b) => compareCells(a[sortCol], b[sortCol], ct));
    return sortDir === 'desc' ? sorted.reverse() : sorted;
  }, [rows, sortCol, sortDir, colTypes]);

  // Outlier sets per column, keyed against sortedRows indices.
  const outliersByCol = useMemo(() => {
    if (!showAnomalies) return new Map<number, Set<number>>();
    const map = new Map<number, Set<number>>();
    columns.forEach((_, ci) => {
      if (colTypes[ci] === 'int' || colTypes[ci] === 'float') {
        const set = detectOutliers(sortedRows, ci);
        if (set.size > 0) map.set(ci, set);
      }
    });
    return map;
  }, [sortedRows, columns, colTypes, showAnomalies]);

  const hasAnyOutliers = outliersByCol.size > 0;

  const totalPages = Math.ceil(sortedRows.length / ROWS_PER_PAGE);
  const start = page * ROWS_PER_PAGE;
  const pagedRows = sortedRows.slice(start, start + ROWS_PER_PAGE);

  const handleSort = useCallback((ci: number) => {
    if (sortCol === ci) {
      if (sortDir === 'asc') setSortDir('desc');
      else { setSortCol(null); setSortDir('asc'); }
    } else {
      setSortCol(ci);
      setSortDir('asc');
    }
    setPage(0);
  }, [sortCol, sortDir]);

  const resolvedFilename = filename ?? safeFilename(null);

  return (
    <div className="rounded-xl border border-border overflow-hidden max-h-[60vh] md:max-h-96 flex flex-col">
      <div className="flex-1 overflow-auto overscroll-x-contain [&_[data-slot=table-container]]:overflow-visible">
        <Table>
          <TableHeader className="sticky top-0 z-10">
            <TableRow className="border-b border-border">
              {columns.map((col, ci) => {
                const isNumeric = colTypes[ci] === 'int' || colTypes[ci] === 'float';
                const isFirst = ci === 0;
                return (
                  <TableHead
                    key={col}
                    className={`whitespace-nowrap py-[var(--density-pad-y)] cursor-pointer select-none hover:bg-accent/50 transition-colors bg-background ${isNumeric ? 'text-right' : ''} ${isFirst ? 'sticky left-0 z-20 md:static' : ''}`}
                    onClick={() => handleSort(ci)}
                  >
                    <div className={`flex items-center gap-1 ${isNumeric ? 'justify-end' : ''}`}>
                      <span className="text-[10px] uppercase tracking-widest font-medium text-muted-foreground">
                        {formatLabel(col)}
                      </span>
                      <span className="text-muted-foreground/40 shrink-0">
                        {sortCol === ci ? (
                          sortDir === 'asc' ? <ArrowUp className="w-2.5 h-2.5 text-foreground" /> : <ArrowDown className="w-2.5 h-2.5 text-foreground" />
                        ) : (
                          <ArrowUpDown className="w-2.5 h-2.5" />
                        )}
                      </span>
                    </div>
                  </TableHead>
                );
              })}
            </TableRow>
          </TableHeader>
          <TableBody>
            {pagedRows.map((row, ri) => {
              const absoluteRowIdx = start + ri;
              return (
                <TableRow key={absoluteRowIdx} className={ri % 2 === 1 ? 'bg-muted/15' : ''}>
                  {row.map((cell, ci) => {
                    const cellStr = cell === null || cell === undefined ? '' : String(cell);
                    const isNumeric = colTypes[ci] === 'int' || colTypes[ci] === 'float';
                    const isFirst = ci === 0;
                    const rowBg = ri % 2 === 1 ? 'bg-[color-mix(in_srgb,var(--muted)_15%,var(--background))]' : 'bg-background';
                    const isOutlier = showAnomalies && (outliersByCol.get(ci)?.has(absoluteRowIdx) ?? false);
                    return (
                      <ContextMenu key={ci}>
                        <ContextMenuTrigger asChild>
                          <TableCell
                            className={`text-xs whitespace-nowrap py-[var(--density-pad-y)] tabular-nums transition-colors ${isNumeric ? 'text-right font-medium' : ''} ${isFirst ? `sticky left-0 z-10 ${rowBg} md:static md:bg-transparent` : ''} ${isOutlier ? 'bg-warning-soft/50 text-warning-foreground' : ''}`}
                          >
                            <span className="inline-flex items-center gap-1 justify-end">
                              {cell === null || cell === undefined ? (
                                <span className="text-muted-foreground/70 italic font-normal" aria-label="empty cell">-</span>
                              ) : isNumeric && typeof cell === 'number' ? (
                                Number.isInteger(cell)
                                  ? formatNumber(cell)
                                  : formatNumberWithDecimals(cell, { minimumFractionDigits: 2, maximumFractionDigits: 4 })
                              ) : (
                                cellStr
                              )}
                              {isOutlier && (
                                <Tooltip>
                                  <TooltipTrigger asChild>
                                    <AlertTriangle className="w-3 h-3 shrink-0 text-warning" aria-label="Unusual value" />
                                  </TooltipTrigger>
                                  <TooltipContent side="top">
                                    Unusual value - outside normal range for this column
                                  </TooltipContent>
                                </Tooltip>
                              )}
                            </span>
                          </TableCell>
                        </ContextMenuTrigger>
                        <ContextMenuContent>
                          <ContextMenuItem
                            onClick={async () => { const ok = await copyText(cellStr); toast[ok ? 'success' : 'error'](ok ? 'Copied' : 'Copy failed'); }}
                            className="gap-2 text-xs"
                          >
                            <Copy className="w-3.5 h-3.5" /> Copy value
                          </ContextMenuItem>
                        </ContextMenuContent>
                      </ContextMenu>
                    );
                  })}
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      {/* Footer: row count + anomaly toggle + pagination + download */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5 border-t border-border bg-muted/30 text-xs text-muted-foreground">
        <span className="min-w-0 truncate">
          {totalRows > rows.length
            ? `Showing ${rows.length} of ${totalRows.toLocaleString('en-US')} rows`
            : rows.length > ROWS_PER_PAGE
              ? `${start + 1}–${Math.min(start + ROWS_PER_PAGE, sortedRows.length)} of ${sortedRows.length.toLocaleString('en-US')} rows`
              : `${totalRows.toLocaleString('en-US')} row${totalRows === 1 ? '' : 's'}`}
        </span>
        <div className="flex items-center gap-1 ml-auto">
          {hasAnyOutliers && (
            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="sm"
                  aria-pressed={showAnomalies}
                  onClick={() => setShowAnomalies((v) => !v)}
                  className={`h-6 gap-1 px-2 text-[10px] font-medium ${showAnomalies ? 'text-warning bg-warning-soft/40' : ''}`}
                >
                  <AlertTriangle className="w-3 h-3" />
                  Anomalies
                </Button>
              </TooltipTrigger>
              <TooltipContent side="left">
                {showAnomalies ? 'Hide anomaly highlights' : 'Show anomaly highlights'}
              </TooltipContent>
            </Tooltip>
          )}
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="sm" className="h-6 gap-1 px-2 text-[10px] font-medium" onClick={() => downloadCSV(columns, sortedRows, resolvedFilename)}>
                <FileText className="w-3 h-3" />
                CSV
              </Button>
            </TooltipTrigger>
            <TooltipContent side="left">Download CSV</TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button variant="ghost" size="sm" className="h-6 gap-1 px-2 text-[10px] font-medium" onClick={async () => { try { await downloadXLSX(columns, sortedRows, resolvedFilename); } catch { toast.error('Failed to export Excel file.'); } }}>
                <FileSpreadsheet className="w-3 h-3" />
                Excel
              </Button>
            </TooltipTrigger>
            <TooltipContent side="left">Download Excel</TooltipContent>
          </Tooltip>
          {totalPages > 1 && (
            <>
              <Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={() => setPage(0)} disabled={page === 0} title="First page"><ChevronsLeft className="w-3.5 h-3.5" /></Button>
              <Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={() => setPage((p) => p - 1)} disabled={page === 0} title="Previous page"><ChevronLeft className="w-3.5 h-3.5" /></Button>
              <span className="px-1 tabular-nums">{page + 1} / {totalPages}</span>
              <Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={() => setPage((p) => p + 1)} disabled={page >= totalPages - 1} title="Next page"><ChevronRight className="w-3.5 h-3.5" /></Button>
              <Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={() => setPage(totalPages - 1)} disabled={page >= totalPages - 1} title="Last page"><ChevronsRight className="w-3.5 h-3.5" /></Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
