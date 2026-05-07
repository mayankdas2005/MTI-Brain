import type { Message } from '@/lib/store/threads';
import { useThreadStore } from '@/lib/store/threads';
import { getStoredUser } from '@/lib/auth';
import {
  groupConversationTurns,
  computeVisibility,
  getActiveIdx,
} from '@/lib/utils/conversation-tree';

// ─── Version resolution ───────────────────────────────────────────────────────
// Mirrors the on-screen rendering: for each turn the export picks whichever
// version is currently active in MessageList (read from activeVersions) and
// applies the same source-link visibility cascade. So if the user has
// prev-arrowed back to v1 of a turn, the PDF reflects v1, not the latest.
function getVisibleMessages(
  messages: Message[],
  activeVersions: Record<string, number> | undefined,
): Message[] {
  const turns = groupConversationTurns(messages);
  const visible = computeVisibility(turns, activeVersions);
  const result: Message[] = [];

  for (let i = 0; i < turns.length; i++) {
    if (!visible[i]) continue;
    const turn = turns[i];
    const idx = getActiveIdx(turn, activeVersions);
    const activeConvId = turn.versions[idx];
    let msgs = turn.allMessages.get(activeConvId) ?? [];
    if (turn.versions.length > 1 && !msgs.some((m) => m.role === 'user')) {
      const rootUser = (turn.allMessages.get(turn.versions[0]) ?? []).find(
        (m) => m.role === 'user',
      );
      if (rootUser) msgs = [rootUser, ...msgs];
    }
    result.push(...msgs);
  }
  return result;
}

// ─── Utilities ────────────────────────────────────────────────────────────────
function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined || value === '') return '-';
  const str = String(value);
  const num = Number(str.replace(/,/g, ''));
  if (!isNaN(num) && str.trim() !== '' && /^-?[\d.]+$/.test(str.trim())) {
    return Number.isInteger(num)
      ? num.toLocaleString('en-US')
      : num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 4 });
  }
  return esc(str);
}

function isNumericCol(rows: unknown[][], colIndex: number): boolean {
  const sample = rows.slice(0, 10).filter(r => r[colIndex] !== null && r[colIndex] !== undefined && r[colIndex] !== '');
  if (sample.length === 0) return false;
  return sample.filter(r => !isNaN(Number(String(r[colIndex]).replace(/,/g, '')))).length >= Math.min(sample.length, 3);
}

function stripMarkdown(text: string): string {
  return text
    .replace(/#{1,6}\s+(.+)/g, '$1')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/\*(.+?)\*/g, '$1')
    .replace(/`{3}[\s\S]*?`{3}/gm, '')
    .replace(/`(.+?)`/g, '$1')
    .replace(/\[(.+?)\]\(.+?\)/g, '$1')
    .replace(/^[-*+]\s+/gm, '• ')
    .trim();
}

function plural(n: number, singular: string, plural?: string): string {
  return n === 1 ? singular : (plural ?? `${singular}s`);
}

function formatTs(iso: string | undefined | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

// ─── Constants ────────────────────────────────────────────────────────────────
const MAX_NARROW_COLS = 8;
const MAX_WIDE_COLS_SHOWN = 10;
const MAX_ROWS_NARROW = 30;
const MAX_ROWS_WIDE = 25;
const MAX_VERY_WIDE_COLS = 15;
const WIDE_CARD_ROWS = 5;
const WIDE_CARD_COLS = 8;
const SQL_LINE_LIMIT = 60;

// ─── Source line ──────────────────────────────────────────────────────────────
// Citation rendered under every chart, table, or wide-card. Source table
// names are intentionally omitted - exposing schema in a c-suite deliverable
// is undesirable, especially in production where many real tables exist.
// Each segment drops gracefully when its underlying value is missing.
function renderSourceLine(
  answer: Message | null,
  rowsShown?: number,
  totalRows?: number,
  totalCols?: number,
  shownCols?: number,
): string {
  if (!answer) return '';
  const parts: string[] = [];

  if (typeof totalRows === 'number' && totalRows > 0) {
    if (typeof rowsShown === 'number' && rowsShown < totalRows) {
      parts.push(`Showing top ${rowsShown.toLocaleString('en-US')} of ${totalRows.toLocaleString('en-US')} ${plural(totalRows, 'row')}`);
    } else {
      parts.push(`n=${totalRows.toLocaleString('en-US')}`);
    }
  }

  if (typeof totalCols === 'number' && typeof shownCols === 'number' && shownCols < totalCols) {
    parts.push(`${shownCols} of ${totalCols} columns shown`);
  }

  const ts = formatTs(answer.created_at);
  if (ts) parts.push(`as of ${ts}`);

  if (parts.length === 0) return '';
  return `<div class="source-line">${parts.join('  ·  ')}</div>`;
}

// ─── Table ────────────────────────────────────────────────────────────────────
function renderTable(columns: string[], rows: unknown[][], rowCount: number, isWide: boolean, answer: Message | null): string {
  const colLimit = isWide ? MAX_WIDE_COLS_SHOWN : columns.length;
  const rowLimit = isWide ? MAX_ROWS_WIDE : MAX_ROWS_NARROW;
  const visibleCols = columns.slice(0, colLimit);
  const shownRows = rows.slice(0, rowLimit);
  const numericCols = visibleCols.map((_, i) => isNumericCol(rows, i));

  const maxNameLen = isWide ? 13 : 22;
  const headerCells = visibleCols.map((c, i) => {
    const label = c.replace(/_/g, ' ');
    const truncated = label.length > maxNameLen ? label.slice(0, maxNameLen - 1) + '…' : label;
    return `<th class="${numericCols[i] ? 'num' : ''}">${esc(truncated)}</th>`;
  }).join('');

  const bodyRows = shownRows.map((row, ri) => {
    const cells = visibleCols.map((_, ci) =>
      `<td class="${numericCols[ci] ? 'num' : ''}">${formatCell((row as unknown[])[ci])}</td>`
    ).join('');
    return `<tr class="${ri % 2 === 0 ? '' : 'alt'}">${cells}</tr>`;
  }).join('');

  return `
    <table class="${isWide ? 'wide' : ''}">
      <thead><tr>${headerCells}</tr></thead>
      <tbody>${bodyRows}</tbody>
    </table>
    ${renderSourceLine(answer, shownRows.length, rowCount, columns.length, visibleCols.length)}`;
}

// ─── Wide-dataset summary card ─────────────────────────────────────────────────
function renderWideDatasetCard(columns: string[], rows: unknown[][], rowCount: number, answer: Message | null): string {
  const previewCols = columns.slice(0, WIDE_CARD_COLS);
  const previewRows = rows.slice(0, WIDE_CARD_ROWS);
  const numericCols = previewCols.map((_, i) => isNumericCol(rows, i));
  const headerCells = previewCols.map((c, i) => {
    const label = c.replace(/_/g, ' ');
    const truncated = label.length > 18 ? label.slice(0, 17) + '…' : label;
    return `<th class="${numericCols[i] ? 'num' : ''}">${esc(truncated)}</th>`;
  }).join('');
  const bodyRows = previewRows.map((row, ri) => {
    const cells = previewCols.map((_, ci) =>
      `<td class="${numericCols[ci] ? 'num' : ''}">${formatCell((row as unknown[])[ci])}</td>`
    ).join('');
    return `<tr class="${ri % 2 === 0 ? '' : 'alt'}">${cells}</tr>`;
  }).join('');

  // Production-scale enhancement: list ALL column names so a 200-column
  // dataset still tells the reader the full schema even when only 8 cols
  // of data fit in the preview. Column names are pure strings (no values),
  // so this stays compact and never reveals row content.
  const inventoryHtml = columns.length > WIDE_CARD_COLS
    ? `<div class="wide-card-inventory">
        <div class="wide-card-inventory-label">All ${columns.length} columns</div>
        <div class="wide-card-inventory-list">${columns.map(c => esc(c.replace(/_/g, ' '))).join(', ')}</div>
      </div>`
    : '';

  return `
    <div class="result-block">
      <div class="wide-card">
        <div class="wide-card-stats">
          <span class="wide-card-stat-val">${rowCount.toLocaleString('en-US')}</span>
          <span class="wide-card-stat-lbl">rows</span>
          <span class="wide-card-stat-x">×</span>
          <span class="wide-card-stat-val">${columns.length}</span>
          <span class="wide-card-stat-lbl">columns</span>
        </div>
        <div class="wide-card-preview-label">First ${previewRows.length} rows · ${previewCols.length} of ${columns.length} columns</div>
        <table class="wide-card-table">
          <thead><tr>${headerCells}</tr></thead>
          <tbody>${bodyRows}</tbody>
        </table>
        ${inventoryHtml}
        <div class="wide-card-note">Full grid available in the live thread.</div>
      </div>
      ${renderSourceLine(answer, previewRows.length, rowCount, columns.length, previewCols.length)}
    </div>`;
}

function renderChartData(chartSpec: Record<string, unknown>): string {
  const data = chartSpec.data as Record<string, unknown>[] | undefined;
  if (!data || data.length === 0) return '';
  const keys = Object.keys(data[0]);
  const numericKeys = new Set(keys.filter(k => data.some(r => !isNaN(Number(String(r[k]))))));
  const headerCells = keys.map(k => `<th class="${numericKeys.has(k) ? 'num' : ''}">${esc(k.replace(/_/g, ' '))}</th>`).join('');
  const bodyRows = data.slice(0, 15).map((row, ri) => {
    const cells = keys.map(k => `<td class="${numericKeys.has(k) ? 'num' : ''}">${formatCell(row[k])}</td>`).join('');
    return `<tr class="${ri % 2 === 0 ? '' : 'alt'}">${cells}</tr>`;
  }).join('');
  return `
    <div class="data-section">
      <table><thead><tr>${headerCells}</tr></thead><tbody>${bodyRows}</tbody></table>
    </div>`;
}

// ─── SQL appendix ─────────────────────────────────────────────────────────────
function renderSqlBlock(sql: string, questionText: string, index: number, showNum: boolean): string {
  const lines = sql.split('\n');
  const truncated = lines.length > SQL_LINE_LIMIT;
  const sqlText = esc(truncated ? lines.slice(0, SQL_LINE_LIMIT).join('\n') : sql);
  const truncNote = truncated
    ? `<div class="appendix-trunc">… ${(lines.length - SQL_LINE_LIMIT).toLocaleString('en-US')} more lines</div>` : '';
  const prefix = showNum ? `Q${index + 1}  -  ` : '';
  const label = `${prefix}${questionText.slice(0, 72)}${questionText.length > 72 ? '…' : ''}`;
  return `
    <div class="appendix-item">
      <div class="appendix-label">${esc(label)}</div>
      <pre class="appendix-sql">${sqlText}</pre>
      ${truncNote}
    </div>`;
}

// ─── Exchange ─────────────────────────────────────────────────────────────────
interface Exchange { question: Message; answer: Message | null; }

function groupIntoExchanges(messages: Message[]): Exchange[] {
  const exchanges: Exchange[] = [];
  for (let i = 0; i < messages.length; i++) {
    if (messages[i].role === 'user') {
      const answer = messages[i + 1]?.role === 'assistant' ? messages[i + 1] : null;
      exchanges.push({ question: messages[i], answer });
      if (answer) i++;
    }
  }
  return exchanges;
}

function renderExchange(
  exchange: Exchange,
  index: number,
  showNum: boolean,
  isWide: boolean,
  chartImages?: Map<string, string>,
): string {
  const { question, answer } = exchange;
  const eyebrow = showNum
    ? `<div class="q-eyebrow">Question ${index + 1}</div>`
    : '';

  let answerHtml = '';
  if (answer) {
    const columns = answer.metadata_?.columns;
    const rows = answer.metadata_?.rows;
    const rowCount = answer.metadata_?.row_count ?? 0;
    const chartSpec = answer.metadata_?.chart_spec as Record<string, unknown> | undefined;
    const hasTable = columns && columns.length > 0 && rows && rows.length > 0;
    const isVeryWide = !!columns && columns.length > MAX_VERY_WIDE_COLS;
    const rawAnswer = answer.content ? stripMarkdown(answer.content) : '';
    const capturedChart = chartImages?.get(answer.conversation_id);

    const summaryHtml = rawAnswer
      ? `<div class="answer-prose">${esc(rawAnswer).replace(/\n/g, '<br>')}</div>` : '';

    // Chart: prefer captured PNG (the actual rendered visual). Fall back to a
    // textual chart-data block only when no image was captured AND the answer
    // has no separate result table. Source line attached in either case.
    let chartHtml = '';
    if (chartSpec && capturedChart) {
      chartHtml = `
      <div class="result-block">
        <img class="chart-image" src="${capturedChart}" alt="Chart">
        ${!hasTable ? renderSourceLine(answer, undefined, rowCount) : ''}
      </div>`;
    } else if (chartSpec && !hasTable) {
      chartHtml = `
      <div class="result-block">
        ${renderChartData(chartSpec)}
        ${renderSourceLine(answer, undefined, rowCount)}
      </div>`;
    }

    const tableHtml = hasTable
      ? (isVeryWide
          ? renderWideDatasetCard(columns!, rows!, rowCount, answer)
          : `
      <div class="result-block">
        ${renderTable(columns!, rows!, rowCount, isWide, answer)}
      </div>`)
      : '';

    answerHtml = `
      <div class="answer-block">
        ${summaryHtml}
        ${chartHtml}
        ${tableHtml}
      </div>`;
  }

  return `
    <section class="exchange">
      ${eyebrow}
      <h2 class="question-text">${esc(question.content)}</h2>
      ${answerHtml}
    </section>`;
}

// ─── Metrics ──────────────────────────────────────────────────────────────────
// Pure derivation from chat data - no IO, no LLM, no hardcoded values.
// Anything the cover, exec summary, or methodology page displays comes
// out of this object.
interface Metrics {
  questionCount: number;
  chartCount: number;
  tableCount: number;
  totalRows: number;
  sqlCount: number;
  earliestTs?: string;
  latestTs?: string;
}

function computeMetrics(
  exchanges: Exchange[],
  chartImages: Map<string, string> | undefined,
): Metrics {
  let chartCount = 0;
  let tableCount = 0;
  let totalRows = 0;
  let sqlCount = 0;
  let earliest: number | null = null;
  let latest: number | null = null;

  for (const ex of exchanges) {
    const a = ex.answer;
    if (!a) continue;
    const md = a.metadata_;
    const hasTable = (md?.columns?.length ?? 0) > 0 && (md?.rows?.length ?? 0) > 0;
    const hasChart = !!chartImages?.has(a.conversation_id) || !!md?.chart_spec;
    if (hasChart) chartCount++;
    if (hasTable) tableCount++;
    totalRows += (md?.row_count ?? 0) as number;
    if (md?.sql) sqlCount++;

    if (a.created_at) {
      const t = new Date(a.created_at).getTime();
      if (!isNaN(t)) {
        if (earliest === null || t < earliest) earliest = t;
        if (latest === null || t > latest) latest = t;
      }
    }
  }

  return {
    questionCount: exchanges.length,
    chartCount,
    tableCount,
    totalRows,
    sqlCount,
    earliestTs: earliest !== null ? new Date(earliest).toISOString() : undefined,
    latestTs: latest !== null ? new Date(latest).toISOString() : undefined,
  };
}

// ─── Cover ────────────────────────────────────────────────────────────────────
function renderCover(
  title: string,
  preparedBy: string,
  date: string,
  timestamp: string,
  metrics: Metrics,
): string {
  const stat = (n: number) => (n > 0 ? n.toLocaleString('en-US') : '-');
  return `
    <section class="cover">
      <div class="cover-masthead">
        <div class="masthead-brand">MTI Brain</div>
        <div class="masthead-confidential">Confidential</div>
      </div>

      <div class="cover-spacer"></div>

      <div class="cover-eyebrow">Analysis Report</div>
      <h1 class="cover-title">${esc(title)}</h1>

      <div class="cover-meta">
        ${preparedBy ? `<span>${esc(preparedBy)}</span><span class="cover-meta-sep">·</span>` : ''}
        <span>${date}</span>
      </div>

      <div class="cover-rule"></div>

      <div class="cover-stats">
        <div class="cover-stat">
          <div class="cover-stat-val">${stat(metrics.questionCount)}</div>
          <div class="cover-stat-lbl">${plural(metrics.questionCount, 'Question')}</div>
        </div>
        <div class="cover-stat">
          <div class="cover-stat-val">${stat(metrics.chartCount)}</div>
          <div class="cover-stat-lbl">${plural(metrics.chartCount, 'Chart')}</div>
        </div>
        <div class="cover-stat">
          <div class="cover-stat-val">${stat(metrics.tableCount)}</div>
          <div class="cover-stat-lbl">${plural(metrics.tableCount, 'Table')}</div>
        </div>
        <div class="cover-stat">
          <div class="cover-stat-val">${stat(metrics.totalRows)}</div>
          <div class="cover-stat-lbl">${plural(metrics.totalRows, 'Row')} analyzed</div>
        </div>
      </div>

      <div class="cover-foot">
        <div>Generated ${timestamp}</div>
        <div>Generated by MTI Brain</div>
      </div>
    </section>`;
}

// ─── Executive Summary ────────────────────────────────────────────────────────
// Structurally rich, but every value comes from the metrics block.
// No findings prose - that would require LLM synthesis, which we don't have.
function renderExecSummary(title: string, metrics: Metrics): string {
  const rows: Array<{ label: string; value: string }> = [];

  rows.push({
    label: 'Scope',
    value: `This report addresses ${metrics.questionCount.toLocaleString('en-US')} ${plural(metrics.questionCount, 'question')} about <span class="exec-italic">${esc(title)}</span>.`,
  });

  const coverageParts: string[] = [];
  if (metrics.tableCount > 0) coverageParts.push(`${metrics.tableCount} ${plural(metrics.tableCount, 'table')}`);
  if (metrics.chartCount > 0) coverageParts.push(`${metrics.chartCount} ${plural(metrics.chartCount, 'chart')}`);
  if (metrics.totalRows > 0) coverageParts.push(`${metrics.totalRows.toLocaleString('en-US')} ${plural(metrics.totalRows, 'row')} analyzed`);
  if (coverageParts.length > 0) {
    rows.push({ label: 'Coverage', value: coverageParts.join(', ') + '.' });
  }

  if (metrics.sqlCount > 0) {
    rows.push({
      label: 'Methods',
      value: `${metrics.sqlCount} SQL ${plural(metrics.sqlCount, 'query', 'queries')} executed against the data warehouse.`,
    });
  }

  if (metrics.earliestTs && metrics.latestTs) {
    const e = new Date(metrics.earliestTs).getTime();
    const l = new Date(metrics.latestTs).getTime();
    if (l - e > 60 * 60 * 1000) {
      rows.push({
        label: 'Period',
        value: `Data queried from ${formatTs(metrics.earliestTs)} through ${formatTs(metrics.latestTs)}.`,
      });
    }
  }

  const rowsHtml = rows.map(r => `
    <div class="exec-row">
      <div class="exec-label">${r.label}</div>
      <div class="exec-value">${r.value}</div>
    </div>`).join('');

  return `
    <section class="exec-summary">
      <div class="section-heading">Executive Summary</div>
      ${rowsHtml}
    </section>`;
}

// ─── Methodology ──────────────────────────────────────────────────────────────
// Structural boilerplate (acceptable - not data). The two timestamps in
// "Point in time" are derived from the chat.
function renderMethodology(metrics: Metrics): string {
  const rows: Array<{ label: string; value: string }> = [];

  rows.push({
    label: 'Data sources',
    value: 'Results in this report were generated from queries executed against the configured data warehouse via MTI Brain&rsquo;s text-to-SQL pipeline. Each chart and table cites its underlying source table(s) directly beneath it.',
  });

  if (metrics.earliestTs && metrics.latestTs) {
    rows.push({
      label: 'Point in time',
      value: `Figures reflect the state of the underlying data at query time (${formatTs(metrics.earliestTs)} through ${formatTs(metrics.latestTs)}). Subsequent updates to the source tables are not reflected.`,
    });
  } else {
    rows.push({
      label: 'Point in time',
      value: 'Figures reflect the state of the underlying data at query time. Subsequent updates to the source tables are not reflected.',
    });
  }

  rows.push({
    label: 'Truncation',
    value: `Tables containing more than ${MAX_ROWS_NARROW} rows or ${MAX_VERY_WIDE_COLS} columns are truncated for print, with the truncation explicitly noted in the source line. The full grid remains available in the live thread.`,
  });

  rows.push({
    label: 'Confidentiality',
    value: 'This document contains information confidential to the recipient organization. Do not redistribute.',
  });

  const rowsHtml = rows.map(r => `
    <div class="exec-row">
      <div class="exec-label">${r.label}</div>
      <div class="exec-value">${r.value}</div>
    </div>`).join('');

  return `
    <section class="methodology">
      <div class="section-heading">Methodology &amp; Limitations</div>
      ${rowsHtml}
    </section>`;
}

// ─── Export ───────────────────────────────────────────────────────────────────
export function exportThread(
  threadId: string,
  title: string,
  messages: Message[],
  chartImages?: Map<string, string>,
): void {
  const date = new Date().toLocaleDateString([], { dateStyle: 'long' });
  const timestamp = new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });

  const user = getStoredUser();
  const preparedBy = user?.name || user?.email || '';

  const activeVersions = useThreadStore.getState().activeVersions[threadId];
  const visible = getVisibleMessages(messages, activeVersions);
  const exchanges = groupIntoExchanges(visible);
  const showNum = exchanges.length > 1;
  const hasWideTable = exchanges.some(ex => (ex.answer?.metadata_?.columns?.length ?? 0) > MAX_NARROW_COLS);

  const metrics = computeMetrics(exchanges, chartImages);

  const bodyContent = exchanges.map((ex, i) => renderExchange(ex, i, showNum, hasWideTable, chartImages)).join('\n');

  const sqlEntries = exchanges
    .map((ex, i) => ({ sql: ex.answer?.metadata_?.sql, question: ex.question.content, index: i }))
    .filter(e => e.sql);

  const appendixHtml = sqlEntries.length > 0 ? `
    <section class="appendix">
      <div class="section-heading">Technical Appendix &middot; SQL Queries</div>
      ${sqlEntries.map(e => renderSqlBlock(e.sql!, e.question, e.index, showNum)).join('\n')}
    </section>` : '';

  const pageSize = hasWideTable ? 'A4 landscape' : 'A4';
  const bodyWidth = hasWideTable ? '1060px' : '800px';

  const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>${esc(title)}</title>
  <style>
    *, *::before, *::after {
      box-sizing: border-box; margin: 0; padding: 0;
      -webkit-print-color-adjust: exact !important;
      print-color-adjust: exact !important;
    }

    /* ── Page rules: counter on every page from 2, blank on the cover ── */
    @page {
      margin: 1.4cm 1.5cm 1.7cm;
      size: ${pageSize};
      @bottom-right {
        content: counter(page) "  /  " counter(pages);
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
        font-size: 9px;
        color: #7a90a8;
        letter-spacing: 0.05em;
      }
      @bottom-left {
        content: "MTI Brain  ·  Confidential";
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
        font-size: 9px;
        color: #7a90a8;
        letter-spacing: 0.05em;
      }
    }
    @page :first {
      @bottom-right { content: ""; }
      @bottom-left { content: ""; }
    }

    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'Helvetica Neue', Arial, sans-serif;
      font-size: 14px;
      line-height: 1.7;
      color: #0f1b2d;
      background: #ffffff;
      max-width: ${bodyWidth};
      margin: 0 auto;
      padding: 0 52px 52px;
      font-variant-numeric: tabular-nums;
    }

    /* ── Cover (full page) ── */
    .cover {
      display: flex;
      flex-direction: column;
      min-height: calc(100vh - 4cm);
      page-break-after: always;
      break-after: page;
    }
    .cover-masthead {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 4px 0 22px;
      border-bottom: 1px solid #d4e4f5;
    }
    .masthead-brand {
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.22em;
      text-transform: uppercase;
      color: #0f1b2d;
    }
    .masthead-confidential {
      display: inline-flex;
      align-items: center;
      gap: 6px;
      font-size: 9px;
      font-weight: 700;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: #b91c1c;
    }
    .masthead-confidential::before {
      content: '';
      width: 5px; height: 5px;
      border-radius: 50%;
      background: #dc2626;
    }
    .cover-spacer { flex: 0 1 18%; min-height: 60px; }
    .cover-eyebrow {
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.22em;
      text-transform: uppercase;
      color: #4d637f;
      margin-bottom: 14px;
    }
    .cover-title {
      font-family: 'Source Serif 4', 'Source Serif Pro', 'Charter', 'Iowan Old Style', Georgia, serif;
      font-size: 38px;
      font-weight: 700;
      color: #0f1b2d;
      line-height: 1.18;
      letter-spacing: -0.015em;
      margin-bottom: 22px;
    }
    .cover-meta {
      display: flex;
      align-items: center;
      gap: 10px;
      font-size: 12.5px;
      color: #4d637f;
      letter-spacing: 0.01em;
      margin-bottom: 38px;
    }
    .cover-meta-sep { color: #c8daea; }
    .cover-rule {
      height: 1px;
      background: #d4e4f5;
      margin-bottom: 32px;
    }
    .cover-stats {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 0;
      border-top: 1px solid #e2ecf7;
      border-bottom: 1px solid #e2ecf7;
    }
    .cover-stat {
      padding: 22px 18px 20px;
      border-right: 1px solid #e2ecf7;
    }
    .cover-stat:last-child { border-right: none; }
    .cover-stat-val {
      font-family: 'Source Serif 4', 'Source Serif Pro', 'Charter', Georgia, serif;
      font-size: 32px;
      font-weight: 700;
      color: #0f1b2d;
      line-height: 1;
      letter-spacing: -0.02em;
      margin-bottom: 6px;
    }
    .cover-stat-lbl {
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: #7a90a8;
    }
    .cover-foot {
      margin-top: auto;
      padding-top: 28px;
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      font-size: 10px;
      color: #7a90a8;
      letter-spacing: 0.04em;
    }

    /* ── Section heading (used by exec summary, methodology, appendix) ── */
    .section-heading {
      font-family: 'Source Serif 4', 'Source Serif Pro', 'Charter', Georgia, serif;
      font-size: 22px;
      font-weight: 700;
      letter-spacing: -0.01em;
      color: #0f1b2d;
      padding-bottom: 14px;
      margin-bottom: 26px;
      border-bottom: 2px solid #0f1b2d;
    }

    /* ── Exec summary + methodology share the labeled-row layout ── */
    .exec-summary, .methodology {
      padding-top: 8px;
      margin-bottom: 32px;
      break-inside: auto;
    }
    .methodology {
      break-before: page;
    }
    .exec-row {
      display: grid;
      grid-template-columns: 140px 1fr;
      gap: 24px;
      padding: 14px 0;
      border-bottom: 1px solid #eef4fa;
      break-inside: avoid;
    }
    .exec-row:last-child { border-bottom: none; }
    .exec-label {
      font-size: 9.5px;
      font-weight: 700;
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: #4d637f;
      padding-top: 4px;
    }
    .exec-value {
      font-size: 13.5px;
      line-height: 1.65;
      color: #1f2d44;
    }
    .exec-italic { font-style: italic; color: #0f1b2d; }
    .exec-mono {
      font-family: 'Menlo', 'Monaco', 'Consolas', monospace;
      font-size: 12px;
      color: #1B76B8;
    }

    /* ── Manual print fallback ── */
    .manual-print-fallback {
      display: block;
      margin: 28px auto 0;
      padding: 8px 18px;
      background: transparent;
      border: 1px solid #c8daea;
      border-radius: 8px;
      color: #4d637f;
      font-size: 12px;
      cursor: pointer;
      transition: background 0.15s, border-color 0.15s;
    }
    .manual-print-fallback:hover {
      background: #f4f9ff;
      border-color: #1B76B8;
      color: #1B76B8;
    }
    @media print {
      .manual-print-fallback { display: none !important; }
    }

    /* ── Exchange ── */
    .exchange {
      padding: 24px 0 22px;
      border-top: 1px solid #d4e4f5;
      break-inside: avoid;
    }
    .exchange:first-of-type {
      border-top: none;
      padding-top: 8px;
    }
    .q-eyebrow {
      font-size: 9.5px;
      font-weight: 700;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: #1B76B8;
      margin-bottom: 8px;
    }
    .question-text {
      font-size: 17px;
      font-weight: 600;
      color: #0f1b2d;
      line-height: 1.4;
      letter-spacing: -0.005em;
      margin-bottom: 16px;
      white-space: pre-wrap;
      word-break: break-word;
      break-after: avoid;
    }
    .answer-prose {
      font-size: 13.5px;
      color: #1f2d44;
      line-height: 1.75;
      margin-bottom: 18px;
      white-space: pre-wrap;
      word-break: break-word;
    }

    /* ── Result block ── */
    .result-block { margin-bottom: 22px; }

    /* ── Source line (replaces tooly CHART/RESULT labels and 5 rows note) ── */
    .source-line {
      font-size: 10.5px;
      color: #7a90a8;
      padding: 8px 0 0;
      letter-spacing: 0.01em;
      break-before: avoid;
    }

    /* ── Chart image ── */
    .chart-image {
      display: block;
      max-width: 100%;
      width: 100%;
      height: auto;
      border: 1px solid #e2ecf7;
      border-radius: 4px;
      background: #ffffff;
    }

    /* ── Wide-dataset summary card ── */
    .wide-card {
      border: 1px solid #d4e4f5;
      border-left: 3px solid #1B76B8;
      padding: 18px 20px 16px;
      background: #f8fbff;
      break-inside: avoid;
    }
    .wide-card-stats {
      display: flex;
      align-items: baseline;
      gap: 6px;
      margin-bottom: 10px;
    }
    .wide-card-stat-val {
      font-size: 22px;
      font-weight: 700;
      color: #0f1b2d;
      letter-spacing: -0.02em;
    }
    .wide-card-stat-lbl { font-size: 11px; color: #4d637f; letter-spacing: 0.04em; }
    .wide-card-stat-x { font-size: 16px; color: #7a90a8; margin: 0 8px; }
    .wide-card-preview-label {
      font-size: 10px;
      font-weight: 600;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: #7a90a8;
      margin-bottom: 8px;
    }
    .wide-card-table {
      border-top: 1px solid #c8daea;
      border-bottom: 1px solid #c8daea;
      font-size: 11px;
    }
    .wide-card-table th { padding: 7px 10px; font-size: 9px; letter-spacing: 0.1em; border-bottom: 1px solid #c8daea; }
    .wide-card-table td { padding: 6px 10px; font-size: 11px; }
    .wide-card-note { font-size: 10.5px; color: #7a90a8; font-style: italic; margin-top: 10px; }
    .wide-card-inventory {
      margin-top: 14px;
      padding-top: 12px;
      border-top: 1px solid #d4e4f5;
    }
    .wide-card-inventory-label {
      font-size: 9px;
      font-weight: 700;
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: #7a90a8;
      margin-bottom: 6px;
    }
    .wide-card-inventory-list {
      font-size: 10.5px;
      line-height: 1.6;
      color: #4d637f;
      word-break: break-word;
    }

    /* ── Tables ── */
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
      border-top: 2px solid #0f1b2d;
      border-bottom: 2px solid #0f1b2d;
    }
    table.wide { font-size: 11px; }
    th {
      padding: 11px 14px 10px;
      font-size: 9.5px;
      font-weight: 700;
      letter-spacing: 0.12em;
      text-transform: uppercase;
      color: #0f1b2d;
      background: transparent;
      border-bottom: 1px solid #0f1b2d;
      text-align: left;
      white-space: nowrap;
    }
    table.wide th { padding: 8px 10px; font-size: 8.5px; }
    th.num { text-align: right; }
    td {
      padding: 10px 14px;
      border-bottom: 1px solid #e2ecf7;
      color: #0f1b2d;
      vertical-align: middle;
    }
    table.wide td { padding: 7px 10px; }
    td.num { text-align: right; font-feature-settings: "tnum"; font-variant-numeric: tabular-nums; }
    tr:last-child td { border-bottom: none; }

    /* ── Appendix ── */
    .appendix { break-before: page; padding-top: 4px; }
    .appendix-item { margin-bottom: 24px; break-inside: auto; }
    .appendix-label {
      font-size: 9.5px;
      font-weight: 800;
      letter-spacing: 0.09em;
      text-transform: uppercase;
      color: #4d637f;
      margin-bottom: 7px;
    }
    .appendix-sql {
      font-family: 'Menlo', 'Monaco', 'Consolas', monospace;
      font-size: 10.5px;
      line-height: 1.65;
      color: #1B76B8;
      background: #f7fbff;
      border: 1px solid #c8daea;
      border-radius: 6px;
      padding: 14px 16px;
      white-space: pre-wrap;
      word-break: break-all;
    }
    .appendix-trunc { font-size: 10px; color: #9aadbc; font-style: italic; margin-top: 6px; padding-left: 2px; }

    /* ── Print-flow rules ── */
    .exchange { break-inside: avoid; }
    .data-section { break-inside: avoid; }
    .appendix-item { break-inside: avoid; }
    thead { display: table-header-group; }
    tr { break-inside: avoid; }
  </style>
</head>
<body>

  ${renderCover(title, preparedBy, date, timestamp, metrics)}

  ${renderExecSummary(title, metrics)}

  ${bodyContent}

  ${renderMethodology(metrics)}

  ${appendixHtml}

  <button type="button" class="manual-print-fallback" onclick="window.print()">Print this report</button>

  <script>
    window.addEventListener('load', function () {
      setTimeout(function () { window.print(); }, 80);
    });
    window.addEventListener('afterprint', function () {
      setTimeout(function () { window.close(); }, 120);
    });
  </script>

</body>
</html>`;

  const win = window.open('', '_blank');
  if (!win) return;
  win.document.open();
  win.document.write(html);
  win.document.close();
}
