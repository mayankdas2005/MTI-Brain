export function formatRelativeTime(dateStr: string, now: Date = new Date()): string {
  const date = new Date(dateStr);
  const diffMs = now.getTime() - date.getTime();

  if (diffMs < 0) return 'just now';

  const diffMins = Math.floor(diffMs / 60_000);
  if (diffMins < 1) return 'just now';
  if (diffMins < 60) return `${diffMins}m ago`;

  const diffHours = Math.floor(diffMs / 3_600_000);
  if (diffHours < 24) return `${diffHours}h ago`;

  const diffDays = Math.floor(diffMs / 86_400_000);
  if (diffDays < 7) return `${diffDays}d ago`;

  const diffWeeks = Math.floor(diffDays / 7);
  if (diffWeeks < 5) return `${diffWeeks}w ago`;

  const diffMonths = Math.floor(diffDays / 30);
  if (diffMonths < 12) return `${diffMonths}mo ago`;

  const diffYears = Math.floor(diffDays / 365);
  return `${diffYears}y ago`;
}

// ─── Bucket grouping ───
// Recency buckets used by the sidebar / chats list. Order is intentional -
// rendering iterates buckets in this sequence and shows headings only for
// buckets that have items.

export type RecencyBucket = 'today' | 'yesterday' | 'this-week' | 'this-month' | 'older';

export const RECENCY_BUCKET_LABELS: Record<RecencyBucket, string> = {
  today: 'Today',
  yesterday: 'Yesterday',
  'this-week': 'This week',
  'this-month': 'This month',
  older: 'Older',
};

export const RECENCY_BUCKET_ORDER: RecencyBucket[] = [
  'today',
  'yesterday',
  'this-week',
  'this-month',
  'older',
];

function startOfLocalDay(d: Date): Date {
  const out = new Date(d);
  out.setHours(0, 0, 0, 0);
  return out;
}

export function bucketOf(dateStr: string, now: Date = new Date()): RecencyBucket {
  const date = new Date(dateStr);
  const today = startOfLocalDay(now);
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  const weekAgo = new Date(today);
  weekAgo.setDate(weekAgo.getDate() - 7);
  const monthAgo = new Date(today);
  monthAgo.setDate(monthAgo.getDate() - 30);

  if (date >= today) return 'today';
  if (date >= yesterday) return 'yesterday';
  if (date >= weekAgo) return 'this-week';
  if (date >= monthAgo) return 'this-month';
  return 'older';
}

/**
 * Groups items by recency bucket while preserving the input order inside
 * each bucket. Returns ONLY buckets that actually contain items, so the
 * caller can render headings without checking for empties.
 */
export function groupByRecencyBucket<T>(
  items: T[],
  getDate: (item: T) => string,
  now: Date = new Date(),
): Array<{ bucket: RecencyBucket; label: string; items: T[] }> {
  const map = new Map<RecencyBucket, T[]>();
  for (const item of items) {
    const b = bucketOf(getDate(item), now);
    const arr = map.get(b);
    if (arr) arr.push(item);
    else map.set(b, [item]);
  }
  return RECENCY_BUCKET_ORDER.filter((b) => map.has(b)).map((b) => ({
    bucket: b,
    label: RECENCY_BUCKET_LABELS[b],
    items: map.get(b)!,
  }));
}
