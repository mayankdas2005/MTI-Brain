import { Fragment, type ReactNode } from 'react';

const HIGHLIGHT_TAG_RE = /<\/?b\s*>/gi;

export function renderHighlightedSnippet(
  snippet: string | null | undefined,
  highlightClassName = 'bg-transparent text-foreground font-semibold'
): ReactNode {
  if (!snippet) return null;

  const parts: ReactNode[] = [];
  let cursor = 0;
  let inHighlight = false;
  let key = 0;

  HIGHLIGHT_TAG_RE.lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = HIGHLIGHT_TAG_RE.exec(snippet)) !== null) {
    const text = snippet.slice(cursor, match.index);
    if (text) {
      parts.push(
        inHighlight ? (
          <mark key={key++} className={highlightClassName}>
            {text}
          </mark>
        ) : (
          <Fragment key={key++}>{text}</Fragment>
        )
      );
    }
    inHighlight = !match[0].startsWith('</');
    cursor = match.index + match[0].length;
  }

  const tail = snippet.slice(cursor);
  if (tail) {
    parts.push(
      inHighlight ? (
        <mark key={key++} className={highlightClassName}>
          {tail}
        </mark>
      ) : (
        <Fragment key={key++}>{tail}</Fragment>
      )
    );
  }

  return parts;
}
