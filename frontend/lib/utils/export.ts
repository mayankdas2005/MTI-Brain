import type { Message } from '@/lib/store/threads';

// ─── Version resolution ───────────────────────────────────────────────────────
function getVisibleMessages(messages: Message[]): Message[] {
  const byConvId = new Map<string, Message[]>();
  const versionMap = new Map<string, string[]>();
  const isChildVersion = new Set<string>();

  for (const msg of messages) {
    const cid = msg.conversation_id;
    if (!cid) continue;
    if (!byConvId.has(cid)) byConvId.set(cid, []);
    byConvId.get(cid)!.push(msg);
    if (msg.parent_conversation_id) {
      const root = msg.parent_conversation_id;
      if (!versionMap.has(root)) versionMap.set(root, [root]);
      const versions = versionMap.get(root)!;
      if (!versions.includes(cid)) versions.push(cid);
      isChildVersion.add(cid);
    }
  }

  const turns: Array<{ versions: string[]; allMessages: Map<string, Message[]>; sourceConvId?: string }> = [];
  const seen = new Set<string>();

  for (const msg of messages) {
    const cid = msg.conversation_id;
    if (!cid || seen.has(cid)) continue;
    seen.add(cid);
    if (isChildVersion.has(cid)) continue;
    const versions = versionMap.get(cid) ?? [cid];
    for (const v of versions) seen.add(v);
    const allMessages = new Map<string, Message[]>();
    for (const v of versions) allMessages.set(v, byConvId.get(v) ?? []);
    const rootMsgs = byConvId.get(cid) ?? [];
    const sourceConvId = rootMsgs.find(m => m.role === 'user')?.source_conversation_id;
    turns.push({ versions, allMessages, sourceConvId });
  }

  const result: Message[] = [];
  const activeConvIds = new Set<string>();
  let truncated = false;

  for (const turn of turns) {
    const visible = turn.sourceConvId ? activeConvIds.has(turn.sourceConvId) : !truncated;
    if (!visible) continue;
    const latestId = turn.versions[turn.versions.length - 1];
    activeConvIds.add(latestId);
    let msgs = turn.allMessages.get(latestId) ?? [];
    if (turn.versions.length > 1 && !msgs.some(m => m.role === 'user')) {
      const rootUser = (turn.allMessages.get(turn.versions[0]) ?? []).find(m => m.role === 'user');
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

// ─── Constants ────────────────────────────────────────────────────────────────
const MAX_NARROW_COLS = 8;
const MAX_WIDE_COLS_SHOWN = 10;
const MAX_ROWS_NARROW = 15;
const MAX_ROWS_WIDE = 10;
const SQL_LINE_LIMIT = 60;

// ─── Table ────────────────────────────────────────────────────────────────────
function renderTable(columns: string[], rows: unknown[][], rowCount: number, isWide: boolean): string {
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

  const notes: string[] = [];
  if (shownRows.length < rowCount) {
    notes.push(`Showing top ${shownRows.length.toLocaleString()} of ${rowCount.toLocaleString()} rows`);
  } else {
    notes.push(`${rowCount.toLocaleString()} row${rowCount !== 1 ? 's' : ''}`);
  }
  if (columns.length > colLimit) notes.push(`${colLimit} of ${columns.length} columns shown`);

  return `
    <table class="${isWide ? 'wide' : ''}">
      <thead><tr>${headerCells}</tr></thead>
      <tbody>${bodyRows}</tbody>
    </table>
    <div class="table-note">${notes.join('  ·  ')}</div>`;
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
  const note = `${Math.min(data.length, 15)} of ${data.length} data points`;
  const title = chartSpec.title ? `${esc(String(chartSpec.title))}  ·  ` : '';
  return `
    <div class="data-section">
      <div class="section-label">${title}Chart Data</div>
      <table><thead><tr>${headerCells}</tr></thead><tbody>${bodyRows}</tbody></table>
      <div class="table-note">${note}</div>
    </div>`;
}

// ─── SQL appendix ─────────────────────────────────────────────────────────────
function renderSqlBlock(sql: string, questionText: string, index: number, showNum: boolean): string {
  const lines = sql.split('\n');
  const truncated = lines.length > SQL_LINE_LIMIT;
  const sqlText = esc(truncated ? lines.slice(0, SQL_LINE_LIMIT).join('\n') : sql);
  const truncNote = truncated
    ? `<div class="appendix-trunc">… ${(lines.length - SQL_LINE_LIMIT).toLocaleString()} more lines</div>` : '';
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

function renderExchange(exchange: Exchange, index: number, showNum: boolean, isWide: boolean): string {
  const { question, answer } = exchange;
  const qTime = new Date(question.created_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });

  const numBadge = showNum ? `<span class="q-badge">Q${index + 1}</span>` : '';
  const timeBadge = `<span class="q-time">${qTime}</span>`;

  let answerHtml = '';
  if (answer) {
    const aTime = new Date(answer.created_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    const columns = answer.metadata_?.columns;
    const rows = answer.metadata_?.rows;
    const rowCount = answer.metadata_?.row_count ?? 0;
    const chartSpec = answer.metadata_?.chart_spec as Record<string, unknown> | undefined;
    const hasTable = columns && columns.length > 0 && rows && rows.length > 0;
    const rawAnswer = answer.content ? stripMarkdown(answer.content) : '';

    const summaryHtml = rawAnswer
      ? `<div class="answer-summary">${esc(rawAnswer).replace(/\n/g, '<br>')}</div>` : '';

    const tableHtml = hasTable ? `
      <div class="data-section">
        <div class="section-label">Results</div>
        ${renderTable(columns!, rows!, rowCount, isWide)}
      </div>` : '';

    const chartHtml = chartSpec && !hasTable ? renderChartData(chartSpec) : '';

    answerHtml = `
      <div class="answer-block">
        ${summaryHtml}
        ${tableHtml}
        ${chartHtml}
        <div class="turn-time">${aTime}</div>
      </div>`;
  }

  return `
    <div class="exchange">
      <div class="exchange-meta">${numBadge}${timeBadge}</div>
      <div class="question-block">
        <p class="question-text">${esc(question.content)}</p>
      </div>
      ${answerHtml}
    </div>`;
}

// ─── Export ───────────────────────────────────────────────────────────────────
export function exportThread(title: string, messages: Message[]): void {
  const date = new Date().toLocaleDateString([], { dateStyle: 'long' });
  const timestamp = new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });

  const visible = getVisibleMessages(messages);
  const exchanges = groupIntoExchanges(visible);
  const sqlCount = visible.filter(m => m.metadata_?.sql).length;
  const totalRows = visible.reduce((s, m) => s + (m.metadata_?.row_count ?? 0), 0);
  const showNum = exchanges.length > 1;
  const hasWideTable = exchanges.some(ex => (ex.answer?.metadata_?.columns?.length ?? 0) > MAX_NARROW_COLS);

  const bodyContent = exchanges.map((ex, i) => {
    const block = renderExchange(ex, i, showNum, hasWideTable);
    const divider = i < exchanges.length - 1 ? '<div class="divider"></div>' : '';
    return block + divider;
  }).join('\n');

  const sqlEntries = exchanges
    .map((ex, i) => ({ sql: ex.answer?.metadata_?.sql, question: ex.question.content, index: i }))
    .filter(e => e.sql);

  const appendixHtml = sqlEntries.length > 0 ? `
    <section class="appendix">
      <div class="appendix-heading">Technical Appendix · SQL Queries</div>
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
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    /* ── Print setup instruction - screen only ── */
    .print-setup {
      position: fixed; inset: 0; z-index: 9999;
      background: linear-gradient(135deg, #060f1c 0%, #0f1b2d 100%);
      color: #eaf2ff;
      display: flex; flex-direction: column;
      align-items: center; justify-content: center;
      gap: 18px;
      padding: 24px;
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    }
    .ps-eyebrow {
      font-size: 10px;
      font-weight: 800;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: #1B76B8;
      margin-bottom: 4px;
    }
    .ps-title {
      font-size: 22px;
      font-weight: 700;
      color: #ffffff;
      letter-spacing: -0.02em;
    }
    .ps-sub {
      font-size: 13px;
      color: #8aa8c8;
      max-width: 460px;
      text-align: center;
      line-height: 1.55;
      margin-bottom: 8px;
    }
    .ps-sub strong { color: #cde0f4; font-weight: 600; }

    .checklist {
      display: flex; flex-direction: column; gap: 10px;
      width: 100%; max-width: 460px;
    }
    .check-row {
      display: flex; align-items: flex-start; gap: 14px;
      background: #1a2535;
      border: 1px solid #243246;
      border-radius: 10px;
      padding: 14px 16px;
    }
    .checkbox {
      width: 20px; height: 20px;
      border-radius: 4px;
      flex-shrink: 0;
      display: flex; align-items: center; justify-content: center;
      font-size: 12px; font-weight: 800;
      margin-top: 1px;
    }
    .checkbox.checked {
      background: #1B76B8;
      color: #fff;
    }
    .checkbox.unchecked {
      background: transparent;
      border: 1.5px solid #4d637f;
    }
    .check-label {
      font-size: 13px;
      font-weight: 600;
      color: #eef4fb;
      margin-bottom: 2px;
    }
    .check-state-on { color: #4ade80; font-weight: 700; font-size: 11px; letter-spacing: 0.04em; text-transform: uppercase; }
    .check-state-off { color: #f87171; font-weight: 700; font-size: 11px; letter-spacing: 0.04em; text-transform: uppercase; }
    .check-hint {
      font-size: 11.5px;
      color: #7e96b1;
      line-height: 1.5;
      margin-top: 2px;
    }
    .ps-where {
      font-size: 11px;
      color: #6a849e;
      max-width: 460px;
      text-align: center;
      letter-spacing: 0.01em;
    }
    .ps-where strong { color: #a8c0db; }

    .print-btn {
      background: #1B76B8; color: #fff;
      border: none; border-radius: 8px;
      padding: 13px 36px; font-size: 14px; font-weight: 600;
      cursor: pointer; margin-top: 10px;
      transition: opacity 0.15s, transform 0.15s;
      letter-spacing: 0.01em;
    }
    .print-btn:hover { opacity: 0.92; }
    .print-btn:active { transform: scale(0.97); }

    /* ── Report body ── */
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

    /* ── Cover band ── */
    .cover-band {
      margin: 0 -52px 40px;
      padding: 34px 52px;
      background: linear-gradient(125deg, #060f1c 0%, #0f1b2d 52%, #142840 100%);
      position: relative;
      overflow: hidden;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }
    /* Dot-grid texture */
    .cover-band::before {
      content: '';
      position: absolute;
      inset: 0;
      background-image: radial-gradient(circle, rgba(255,255,255,0.055) 1px, transparent 1px);
      background-size: 22px 22px;
      pointer-events: none;
    }
    /* Glow orb - top right */
    .cover-band::after {
      content: '';
      position: absolute;
      right: -60px;
      top: -70px;
      width: 240px;
      height: 240px;
      background: radial-gradient(circle, rgba(27,118,184,0.18) 0%, transparent 70%);
      pointer-events: none;
    }
    .cover-left {
      position: relative;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .cover-wordmark {
      font-size: 14px;
      font-weight: 800;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: #ffffff;
    }
    .cover-subtitle {
      font-size: 11px;
      font-weight: 500;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: rgba(255,255,255,0.42);
    }
    .cover-date {
      position: relative;
      font-size: 12px;
      color: rgba(255,255,255,0.44);
      letter-spacing: 0.04em;
      align-self: flex-end;
    }

    /* ── Title ── */
    .report-header {
      padding: 28px 0 24px;
      margin-bottom: 32px;
      border-bottom: 2px solid #e2ecf7;
    }
    .report-title {
      font-size: 30px;
      font-weight: 700;
      color: #0f1b2d;
      line-height: 1.2;
      letter-spacing: -0.025em;
    }

    /* ── Exchange ── */
    .exchange { margin-bottom: 0; page-break-inside: auto; }
    .exchange-meta {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 8px;
    }
    .q-badge {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 24px; height: 24px;
      background: #0f1b2d;
      color: #fff;
      border-radius: 50%;
      font-size: 10px;
      font-weight: 800;
      letter-spacing: 0.02em;
      flex-shrink: 0;
    }
    .q-time { font-size: 12px; color: #7a90a8; }

    /* ── Question block ── */
    .question-block {
      break-inside: avoid;
      background: #f0f6fd;
      border-left: 3px solid #1B76B8;
      border-radius: 0 7px 7px 0;
      padding: 15px 19px;
      margin-bottom: 18px;
      ${showNum ? 'margin-left: 32px;' : ''}
    }
    .question-text {
      font-size: 15px;
      font-weight: 600;
      color: #0f1b2d;
      white-space: pre-wrap;
      word-break: break-word;
      line-height: 1.5;
    }

    /* ── Answer block ── */
    .answer-block {
      ${showNum ? 'margin-left: 32px;' : ''}
    }
    .answer-summary {
      font-size: 14px;
      color: #2c4a6e;
      background: #f8fbff;
      border: 1px solid #d8e8f5;
      border-radius: 7px;
      padding: 16px 19px;
      margin-bottom: 16px;
      line-height: 1.7;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .turn-time { font-size: 11px; color: #9aadbc; margin-top: 10px; }

    /* ── Data sections ── */
    .data-section {
      break-inside: avoid;
      border: 1px solid #c8daea;
      border-radius: 7px;
      overflow: hidden;
      margin-bottom: 16px;
    }
    .section-label {
      font-size: 10.5px;
      font-weight: 800;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: #ffffff;
      background: #0f1b2d;
      padding: 9px 16px;
    }

    /* ── Tables ── */
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 13px;
    }
    table.wide { font-size: 11px; }
    th {
      padding: 10px 16px;
      font-size: 10.5px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: #4d637f;
      background: #eaf2fb;
      border-bottom: 1px solid #c8daea;
      text-align: left;
      white-space: nowrap;
    }
    table.wide th { padding: 7px 10px; font-size: 9px; }
    th.num { text-align: right; }
    td {
      padding: 11px 16px;
      border-bottom: 1px solid #edf3f9;
      color: #0f1b2d;
      vertical-align: middle;
    }
    table.wide td { padding: 7px 10px; }
    td.num { text-align: right; font-weight: 600; font-feature-settings: "tnum"; }
    tr.alt td { background: #f7fbff; }
    tr:last-child td { border-bottom: none; }
    .table-note {
      font-size: 11px;
      color: #7a90a8;
      padding: 8px 16px;
      background: #f4f9ff;
      border-top: 1px solid #dce8f5;
    }

    /* ── Divider ── */
    .divider {
      height: 1px;
      background: #d4e4f5;
      margin: 36px 0;
      break-after: avoid;
    }

    /* ── Appendix ── */
    .appendix { break-before: page; padding-top: 4px; }
    .appendix-heading {
      font-size: 14px;
      font-weight: 700;
      letter-spacing: -0.01em;
      color: #0f1b2d;
      padding-bottom: 12px;
      margin-bottom: 24px;
      border-bottom: 2px solid #0f1b2d;
    }
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
    .appendix-trunc {
      font-size: 10px; color: #9aadbc;
      font-style: italic; margin-top: 6px; padding-left: 2px;
    }

    /* ── Footer ── */
    .report-footer {
      margin-top: 48px;
      padding-top: 14px;
      border-top: 1px solid #c8daea;
      display: flex;
      justify-content: space-between;
      align-items: center;
      font-size: 10px;
      color: #7a90a8;
    }
    .confidential {
      color: #b91c1c;
      font-weight: 800;
      letter-spacing: 0.1em;
      font-size: 9.5px;
      text-transform: uppercase;
    }

    /* ── Print rules ── */
    @media print {
      .print-setup { display: none !important; }
      body { padding: 0; max-width: 100%; font-size: 13px; line-height: 1.6; }
      @page { margin: 1.4cm 1.5cm 1.6cm; size: ${pageSize}; }
      .cover-band { margin: 0 -1.5cm 32px; padding: 26px 1.5cm; }
      .report-header { padding: 24px 0 20px; margin-bottom: 28px; }
      .report-title { font-size: 26px; }
      .question-text { font-size: 14px; }
      .answer-summary { font-size: 13px; padding: 14px 17px; }
      table { font-size: 12px; }
      th { padding: 8px 14px; font-size: 9.5px; }
      td { padding: 9px 14px; }
      .divider { margin: 28px 0; }
      .exchange { break-inside: auto; }
      .question-block { break-inside: avoid; }
      .data-section { break-inside: avoid; }
      .divider { break-after: avoid; }
      .appendix { break-before: page; }
      .appendix-item { break-inside: auto; }
    }
  </style>
</head>
<body>

  <!-- Print setup overlay - hidden when printing -->
  <div class="print-setup" id="setup">
    <div>
      <div class="ps-eyebrow">MTI Brain · Save as PDF</div>
      <div class="ps-title">Two settings to verify</div>
    </div>
    <p class="ps-sub">Both controls live under <strong>More settings</strong> in the print dialog.</p>

    <div class="checklist">
      <div class="check-row">
        <div class="checkbox checked">✓</div>
        <div>
          <div class="check-label">Background graphics &nbsp; <span class="check-state-on">On</span></div>
          <div class="check-hint">Renders the cover band, table headers, and accent colors. Without this, the report prints as plain black-and-white text.</div>
        </div>
      </div>
      <div class="check-row">
        <div class="checkbox unchecked"></div>
        <div>
          <div class="check-label">Headers and footers &nbsp; <span class="check-state-off">Off</span></div>
          <div class="check-hint">Removes the browser&rsquo;s URL and timestamp from each page edge.</div>
        </div>
      </div>
    </div>

    <div class="ps-where">Then choose <strong>Save as PDF</strong> as the destination and click Save.</div>

    <button class="print-btn" onclick="document.getElementById('setup').remove(); window.print();">
      Open Print Dialog
    </button>
  </div>

  <!-- Branded cover band -->
  <div class="cover-band">
    <div class="cover-left">
      <div class="cover-wordmark">MTI Brain</div>
      <div class="cover-subtitle">Analysis Report</div>
    </div>
    <div class="cover-date">${date}</div>
  </div>

  <!-- Title -->
  <header class="report-header">
    <h1 class="report-title">${esc(title)}</h1>
  </header>

  ${bodyContent}

  ${appendixHtml}

  <footer class="report-footer">
    <span>Generated by MTI Brain &nbsp;&middot;&nbsp; <span class="confidential">Confidential</span></span>
    <span>${timestamp}</span>
  </footer>

</body>
</html>`;

  const win = window.open('', '_blank');
  if (!win) return;
  win.document.write(html);
  win.document.close();
}
