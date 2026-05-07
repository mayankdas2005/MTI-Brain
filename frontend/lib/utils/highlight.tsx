import { Fragment, type ReactNode } from 'react';

const HIGHLIGHT_TAG_RE = /<\/?b\s*>/gi;

/** Escape regex meta-characters so a user-typed query can be embedded
 *  in a regex without blowing up on `.`, `*`, `(`, `[`, etc. */
function escapeRegex(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/**
 * Client-side highlighter. Two modes:
 *
 * 1. **Query mode** (default): tokenises the user's typed `query` on
 *    whitespace and bolds each token in `text`. Good for in-memory
 *    filters where there's no server round-trip to consult.
 *
 * 2. **Backend-matched-terms mode**: pass `options.matchedTerms` (the
 *    `matched_terms` field on a SearchResult) to bold exactly the words
 *    the backend's FTS / Levenshtein / trigram engine matched. Catches
 *    typos like "stressss" → "stress" and stem matches like "ran" →
 *    "running" that pure substring matching would miss.
 *
 * If both `query` and `matchedTerms` are passed, `matchedTerms` wins -
 * server truth beats client guessing.
 */
export function highlightQueryInText(
  text: string | null | undefined,
  query: string | null | undefined,
  options?: {
    matchedTerms?: string[] | null;
    highlightClassName?: string;
  },
): ReactNode {
  if (!text) return null;

  const className = options?.highlightClassName ?? DEFAULT_HIGHLIGHT_CLASS;
  const terms = options?.matchedTerms?.filter((t) => t && t.length > 0);

  // Pick the term list: backend matches when present, else query tokens.
  let candidates: string[];
  if (terms && terms.length > 0) {
    candidates = terms;
  } else {
    const q = query?.trim();
    if (!q) return text;
    candidates = q.split(/\s+/).filter((t) => t.length > 0);
    if (candidates.length === 0) return text;
  }

  // Combine into one alternation regex. Longer tokens first so "testing"
  // wins over "test" where they overlap.
  const sorted = [...candidates].sort((a, b) => b.length - a.length);
  const re = new RegExp(`(${sorted.map(escapeRegex).join('|')})`, 'ig');
  const parts = text.split(re);
  if (parts.length === 1) return text;

  const tokenSet = new Set(candidates.map((t) => t.toLowerCase()));
  return parts.map((part, i) =>
    tokenSet.has(part.toLowerCase()) ? (
      <mark key={i} className={className}>{part}</mark>
    ) : (
      <Fragment key={i}>{part}</Fragment>
    ),
  );
}

/**
 * Render a search result snippet, combining BOTH highlight sources:
 *
 * 1. **Server `<b>`-tags** - anything Postgres `ts_headline` wrapped is
 *    rendered as a hit. Catches FTS stem matches the client can't see
 *    (e.g. "running" when the user typed "ran").
 * 2. **Client-side fallback on gap text** - between/around the server
 *    bold spans, also bolds any `matchedTerms` (backend's fuzzy/typo
 *    matches) and falls back to the user's query tokens. This catches
 *    stopwords like "how" (which Postgres filters out of FTS, leaving
 *    no `<b>` tags AND empty matchedTerms - but the result still
 *    matched via ILIKE substring, so the user expects to see it bolded).
 *
 * This is the unified renderer for any search-result content blob
 * (chat headlines today; tomorrow project descriptions, message
 * previews, etc.) that benefits from both signals.
 */
export function renderSearchSnippet(
  snippet: string | null | undefined,
  query?: string | null,
  options?: {
    matchedTerms?: string[] | null;
    highlightClassName?: string;
  },
): ReactNode {
  if (!snippet) return null;
  const className = options?.highlightClassName ?? DEFAULT_HIGHLIGHT_CLASS;
  const matchedTerms = options?.matchedTerms;

  const renderGap = (text: string, key: number): ReactNode => (
    <Fragment key={key}>
      {highlightQueryInText(text, query, {
        matchedTerms,
        highlightClassName: className,
      })}
    </Fragment>
  );

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
          <mark key={key++} className={className}>{text}</mark>
        ) : (
          renderGap(text, key++)
        ),
      );
    }
    inHighlight = !match[0].startsWith('</');
    cursor = match.index + match[0].length;
  }

  const tail = snippet.slice(cursor);
  if (tail) {
    parts.push(
      inHighlight ? (
        <mark key={key++} className={className}>{tail}</mark>
      ) : (
        renderGap(tail, key++)
      ),
    );
  }

  return parts;
}

/** Default highlight styling - a soft brand-tinted pill behind matched
 *  text so the hit is unmistakable but not garish. Used by both the
 *  server-side <b>-tag renderer and the client-side query highlighter. */
export const DEFAULT_HIGHLIGHT_CLASS =
  'rounded-sm bg-primary/20 px-0.5 text-foreground font-semibold';

export function renderHighlightedSnippet(
  snippet: string | null | undefined,
  highlightClassName: string = DEFAULT_HIGHLIGHT_CLASS,
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
