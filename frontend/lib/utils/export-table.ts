/**
 * Export utilities for data tables.
 * CSV: pure string manipulation, no dependencies.
 * XLSX: SheetJS community edition, dynamically imported to keep initial bundle lean.
 */

function escapeCSVCell(value: unknown): string {
  const s = String(value ?? '');
  if (s.includes(',') || s.includes('"') || s.includes('\n')) {
    return `"${s.replace(/"/g, '""')}"`;
  }
  return s;
}

export function downloadCSV(
  columns: string[],
  rows: unknown[][],
  filename: string,
): void {
  const header = columns.join(',');
  const body = rows.map((row) => row.map(escapeCSVCell).join(',')).join('\n');
  const blob = new Blob([`${header}\n${body}`], { type: 'text/csv;charset=utf-8;' });
  triggerDownload(blob, `${filename}.csv`);
}

export async function downloadXLSX(
  columns: string[],
  rows: unknown[][],
  filename: string,
): Promise<void> {
  const { utils, writeFile } = await import('xlsx');
  const data = rows.map((row) =>
    Object.fromEntries(columns.map((col, i) => [col, row[i] ?? ''])),
  );
  const ws = utils.json_to_sheet(data);
  ws['!cols'] = columns.map((col) => ({ wch: Math.max(col.length + 2, 12) }));
  const wb = utils.book_new();
  utils.book_append_sheet(wb, ws, 'Results');
  writeFile(wb, `${filename}.xlsx`);
}

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

export function safeFilename(threadTitle: string | null | undefined): string {
  const base = (threadTitle ?? 'query-results')
    .replace(/[^a-zA-Z0-9 _-]/g, '')
    .trim()
    .replace(/\s+/g, '-')
    .slice(0, 60);
  const date = new Date().toISOString().slice(0, 10);
  return `${base || 'query-results'}-${date}`;
}
