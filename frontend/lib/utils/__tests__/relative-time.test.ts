import { describe, it, expect } from 'vitest';
import {
  formatRelativeTime,
  bucketOf,
  groupByRecencyBucket,
  RECENCY_BUCKET_ORDER,
} from '../relative-time';

describe('formatRelativeTime', () => {
  const now = new Date('2024-06-15T12:00:00Z');

  it('returns "just now" for times less than a minute ago', () => {
    const date = new Date('2024-06-15T11:59:45Z').toISOString();
    expect(formatRelativeTime(date, now)).toBe('just now');
  });

  it('returns "just now" for future dates', () => {
    const date = new Date('2024-06-15T13:00:00Z').toISOString();
    expect(formatRelativeTime(date, now)).toBe('just now');
  });

  it('returns minutes ago', () => {
    const date = new Date('2024-06-15T11:45:00Z').toISOString();
    expect(formatRelativeTime(date, now)).toBe('15m ago');
  });

  it('returns hours ago', () => {
    const date = new Date('2024-06-15T09:00:00Z').toISOString();
    expect(formatRelativeTime(date, now)).toBe('3h ago');
  });

  it('returns days ago', () => {
    const date = new Date('2024-06-12T12:00:00Z').toISOString();
    expect(formatRelativeTime(date, now)).toBe('3d ago');
  });

  it('returns weeks ago', () => {
    const date = new Date('2024-06-01T12:00:00Z').toISOString();
    expect(formatRelativeTime(date, now)).toBe('2w ago');
  });

  it('returns months ago', () => {
    const date = new Date('2024-03-15T12:00:00Z').toISOString();
    expect(formatRelativeTime(date, now)).toBe('3mo ago');
  });

  it('returns years ago', () => {
    const date = new Date('2022-06-15T12:00:00Z').toISOString();
    expect(formatRelativeTime(date, now)).toBe('2y ago');
  });

  it('handles boundary at 60 minutes', () => {
    const date = new Date('2024-06-15T11:00:00Z').toISOString();
    expect(formatRelativeTime(date, now)).toBe('1h ago');
  });

  it('handles boundary at 24 hours', () => {
    const date = new Date('2024-06-14T12:00:00Z').toISOString();
    expect(formatRelativeTime(date, now)).toBe('1d ago');
  });
});

describe('bucketOf', () => {
  // Now is midday on June 15
  const now = new Date('2024-06-15T12:00:00Z');

  it('puts today items in today bucket', () => {
    expect(bucketOf('2024-06-15T10:00:00Z', now)).toBe('today');
  });

  it('puts yesterday items in yesterday bucket', () => {
    expect(bucketOf('2024-06-14T10:00:00Z', now)).toBe('yesterday');
  });

  it('puts last week items in this-week bucket', () => {
    expect(bucketOf('2024-06-10T10:00:00Z', now)).toBe('this-week');
  });

  it('puts last month items in this-month bucket', () => {
    expect(bucketOf('2024-05-20T10:00:00Z', now)).toBe('this-month');
  });

  it('puts old items in older bucket', () => {
    expect(bucketOf('2024-01-01T10:00:00Z', now)).toBe('older');
  });

  it('future items land in today', () => {
    expect(bucketOf('2024-06-16T10:00:00Z', now)).toBe('today');
  });
});

describe('groupByRecencyBucket', () => {
  const now = new Date('2024-06-15T12:00:00Z');

  interface Item { id: string; date: string }
  const items: Item[] = [
    { id: '1', date: '2024-06-15T10:00:00Z' },
    { id: '2', date: '2024-06-14T10:00:00Z' },
    { id: '3', date: '2024-06-15T08:00:00Z' },
    { id: '4', date: '2024-01-01T10:00:00Z' },
  ];

  it('groups items by recency bucket', () => {
    const groups = groupByRecencyBucket(items, (i) => i.date, now);

    expect(groups.length).toBeGreaterThan(0);
    const todayGroup = groups.find((g) => g.bucket === 'today');
    expect(todayGroup?.items).toHaveLength(2);
    expect(todayGroup?.items.map((i) => i.id)).toContain('1');
    expect(todayGroup?.items.map((i) => i.id)).toContain('3');
  });

  it('returns only buckets that have items', () => {
    const groups = groupByRecencyBucket(items, (i) => i.date, now);
    for (const group of groups) {
      expect(group.items.length).toBeGreaterThan(0);
    }
  });

  it('preserves order within buckets', () => {
    const groups = groupByRecencyBucket(items, (i) => i.date, now);
    const todayGroup = groups.find((g) => g.bucket === 'today');
    // items 1 and 3 should be in insertion order
    expect(todayGroup?.items[0].id).toBe('1');
    expect(todayGroup?.items[1].id).toBe('3');
  });

  it('returns groups in the correct bucket order', () => {
    const groups = groupByRecencyBucket(items, (i) => i.date, now);
    const buckets = groups.map((g) => g.bucket);
    const expectedOrder = RECENCY_BUCKET_ORDER.filter((b) => buckets.includes(b));
    expect(buckets).toEqual(expectedOrder);
  });

  it('returns empty array for empty input', () => {
    const groups = groupByRecencyBucket([], (i: Item) => i.date, now);
    expect(groups).toEqual([]);
  });

  it('includes correct labels', () => {
    const groups = groupByRecencyBucket(items, (i) => i.date, now);
    const todayGroup = groups.find((g) => g.bucket === 'today');
    expect(todayGroup?.label).toBe('Today');
  });
});
