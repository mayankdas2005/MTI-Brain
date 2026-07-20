import type { Message } from '@/lib/store/threads';
import { useThreadStore } from '@/lib/store/threads';
import { safeFilename } from '@/lib/utils/export-table';
import {
  groupConversationTurns,
  computeVisibility,
  getActiveIdx,
} from '@/lib/utils/conversation-tree';

// ─── Version resolution ───────────────────────────────────────────────────────
// Mirrors the on-screen rendering: for each turn the export picks whichever
// version is currently active in MessageList (read from activeVersions) and
// applies the same source-link visibility cascade. So if the user has
// prev-arrowed back to v1 of a turn, the export reflects v1, not the latest.
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

// ─── Reasoning ────────────────────────────────────────────────────────────────
// pipeline_steps carries one entry per pipeline node with its own reasoning
// text (this is the authoritative source - same data the reasoning-timeline
// UI renders from). Falls back to the legacy flat `reasoning` string when
// pipeline_steps isn't available (older threads).
function renderReasoningSection(answer: Message): string {
  const steps = answer.metadata_?.pipeline_steps;
  if (steps && steps.length > 0) {
    const parts = steps
      .filter((s) => s.reasoning && s.reasoning.trim())
      .map((s) => `#### ${s.message || s.node}\n\n${s.reasoning!.trim()}`);
    if (parts.length > 0) return `### Reasoning\n\n${parts.join('\n\n')}`;
  }
  if (answer.reasoning && answer.reasoning.trim()) {
    return `### Reasoning\n\n${answer.reasoning.trim()}`;
  }
  return '';
}

function renderSqlSection(answer: Message): string {
  const sql = answer.metadata_?.sql;
  if (!sql || !sql.trim()) return '';
  return `### SQL\n\n\`\`\`sql\n${sql.trim()}\n\`\`\``;
}

function renderExchange(exchange: Exchange, index: number, showNum: boolean): string {
  const { question, answer } = exchange;
  const heading = showNum ? `## Question ${index + 1}` : '## Question';

  const sections = [`${heading}\n\n${question.content.trim()}`];

  if (answer) {
    const reasoning = renderReasoningSection(answer);
    if (reasoning) sections.push(reasoning);

    const sql = renderSqlSection(answer);
    if (sql) sections.push(sql);

    if (answer.content && answer.content.trim()) {
      sections.push(`### Answer\n\n${answer.content.trim()}`);
    }
  }

  return sections.join('\n\n');
}

// ─── Export ───────────────────────────────────────────────────────────────────
export function exportThread(
  threadId: string,
  title: string,
  messages: Message[],
): void {
  const timestamp = new Date().toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });

  const activeVersions = useThreadStore.getState().activeVersions[threadId];
  const visible = getVisibleMessages(messages, activeVersions);
  const exchanges = groupIntoExchanges(visible);
  const showNum = exchanges.length > 1;

  const body = exchanges
    .map((ex, i) => renderExchange(ex, i, showNum))
    .join('\n\n---\n\n');

  const markdown = `# ${title}\n\n_Exported ${timestamp}_\n\n---\n\n${body}\n`;

  const blob = new Blob([markdown], { type: 'text/markdown;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `${safeFilename(title)}.md`;
  a.click();
  URL.revokeObjectURL(url);
}
