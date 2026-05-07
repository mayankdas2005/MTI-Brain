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
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Download, ArrowUp, ArrowDown, ArrowUpDown, Copy } from 'lucide-react';
import { toast } from '@/lib/toast';
import { copyText } from '@/lib/utils';
import { formatNumber, formatNumberWithDecimals } from '@/lib/utils/number';

const ROWS_PER_PAGE = 10;

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
    if (/^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(s)) {
      hasTimestamp = true;
      continue;
    }
    if (/^\d{4}-\d{2}-\d{2}$/.test(s) || /^\d{1,2}\/\d{1,2}\/\d{2,4}$/.test(s)) {
      hasDate = true;
      continue;
    }
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

/** Compare two cell values for sorting. */
function compareCells(a: unknown, b: unknown, colType: ColType): number {
  if (a === null || a === undefined) return 1;
  if (b === null || b === undefined) return -1;

  if (colType === 'int' || colType === 'float') {
    return Number(a) - Number(b);
  }
  return String(a).localeCompare(String(b), 'en-US', { numeric: true, sensitivity: 'base' });
}

interface DataTableProps {
  columns: string[];
  rows: unknown[][];
  rowCount?: number;
}

export function DataTable({ columns, rows, rowCount }: DataTableProps) {
  const [page, setPage] = useState(0);
  const [sortCol, setSortCol] = useState<number | null>(null);
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');

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

  const totalPages = Math.ceil(sortedRows.length / ROWS_PER_PAGE);
  const start = page * ROWS_PER_PAGE;
  const pagedRows = sortedRows.slice(start, start + ROWS_PER_PAGE);

  const handleSort = useCallback((ci: number) => {
    if (sortCol === ci) {
      if (sortDir === 'asc') setSortDir('desc');
      else { setSortCol(null); setSortDir('asc'); } // third click clears
    } else {
      setSortCol(ci);
      setSortDir('asc');
    }
    setPage(0);
  }, [sortCol, sortDir]);

  const handleDownloadCSV = () => {
    const header = columns.join(',');
    const body = sortedRows.map((row) =>
      row.map((cell) => {
        const s = String(cell ?? '');
        return s.includes(',') || s.includes('"') ? `"${s.replace(/"/g, '""')}"` : s;
      }).join(',')
    ).join('\n');
    const csv = `${header}\n${body}`;
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `query-results-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  };

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
            {pagedRows.map((row, ri) => (
              <TableRow key={start + ri} className={ri % 2 === 1 ? 'bg-muted/15' : ''}>
                {row.map((cell, ci) => {
                  const cellStr = cell === null || cell === undefined ? '' : String(cell);
                  const isNumeric = colTypes[ci] === 'int' || colTypes[ci] === 'float';
                  const isFirst = ci === 0;
                  const rowBg = ri % 2 === 1 ? 'bg-[color-mix(in_srgb,var(--muted)_15%,var(--background))]' : 'bg-background';
                  return (
                    <ContextMenu key={ci}>
                      <ContextMenuTrigger asChild>
                        <TableCell className={`text-xs whitespace-nowrap py-[var(--density-pad-y)] tabular-nums ${isNumeric ? 'text-right font-medium' : ''} ${isFirst ? `sticky left-0 z-10 ${rowBg} md:static md:bg-transparent` : ''}`}>
                          {cell === null || cell === undefined ? (
                            <span className="text-muted-foreground/70 italic font-normal" aria-label="empty cell">-</span>
                          ) : isNumeric && typeof cell === 'number' ? (
                            Number.isInteger(cell)
                              ? formatNumber(cell)
                              : formatNumberWithDecimals(cell, { minimumFractionDigits: 2, maximumFractionDigits: 4 })
                          ) : (
                            cellStr
                          )}
                        </TableCell>
                      </ContextMenuTrigger>
                      <ContextMenuContent>
                        <ContextMenuItem onClick={async () => { const ok = await copyText(cellStr); toast[ok ? 'success' : 'error'](ok ? 'Copied' : 'Copy failed'); }} className="gap-2 text-xs">
                          <Copy className="w-3.5 h-3.5" /> Copy value
                        </ContextMenuItem>
                      </ContextMenuContent>
                    </ContextMenu>
                  );
                })}
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>

      {/* Footer: row count + pagination + download */}
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-2.5 border-t border-border bg-muted/30 text-xs text-muted-foreground">
        <span className="min-w-0 truncate">
          {totalRows > rows.length
            ? `Showing ${rows.length} of ${totalRows.toLocaleString('en-US')} rows`
            : rows.length > ROWS_PER_PAGE
              ? `${start + 1}\u2013${Math.min(start + ROWS_PER_PAGE, sortedRows.length)} of ${sortedRows.length.toLocaleString('en-US')} rows`
              : `${totalRows.toLocaleString('en-US')} row${totalRows === 1 ? '' : 's'}`}
        </span>
        <div className="flex items-center gap-1 ml-auto">
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0"
                onClick={handleDownloadCSV}
              >
                <Download className="w-3.5 h-3.5" />
              </Button>
            </TooltipTrigger>
            <TooltipContent side="left">Download CSV</TooltipContent>
          </Tooltip>
          {totalPages > 1 && (
            <>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0"
                onClick={() => setPage(0)}
                disabled={page === 0}
                title="First page"
              >
                <ChevronsLeft className="w-3.5 h-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0"
                onClick={() => setPage((p) => p - 1)}
                disabled={page === 0}
                title="Previous page"
              >
                <ChevronLeft className="w-3.5 h-3.5" />
              </Button>
              <span className="px-1 tabular-nums">
                {page + 1} / {totalPages}
              </span>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0"
                onClick={() => setPage((p) => p + 1)}
                disabled={page >= totalPages - 1}
                title="Next page"
              >
                <ChevronRight className="w-3.5 h-3.5" />
              </Button>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0"
                onClick={() => setPage(totalPages - 1)}
                disabled={page >= totalPages - 1}
                title="Last page"
              >
                <ChevronsRight className="w-3.5 h-3.5" />
              </Button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
