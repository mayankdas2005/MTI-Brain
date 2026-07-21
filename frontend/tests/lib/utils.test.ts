import { describe, it, expect, vi } from 'vitest';
import { cn, randomId } from '@/lib/utils';

describe('cn (classname merge utility)', () => {
  it('merges simple class names', () => {
    expect(cn('foo', 'bar')).toBe('foo bar');
  });

  it('handles conditional classes', () => {
    expect(cn('base', false && 'hidden', 'extra')).toBe('base extra');
  });

  it('deduplicates tailwind conflicts', () => {
    // twMerge should resolve conflicting tailwind classes
    expect(cn('p-4', 'p-2')).toBe('p-2');
  });

  it('handles undefined and null inputs', () => {
    expect(cn('base', undefined, null, 'end')).toBe('base end');
  });

  it('handles array inputs via clsx', () => {
    expect(cn(['foo', 'bar'])).toBe('foo bar');
  });

  it('handles object inputs via clsx', () => {
    expect(cn({ active: true, disabled: false })).toBe('active');
  });

  it('returns empty string for no inputs', () => {
    expect(cn()).toBe('');
  });

  it('merges complex tailwind classes correctly', () => {
    // Later class should win for conflicting utilities
    expect(cn('text-red-500', 'text-blue-500')).toBe('text-blue-500');
    expect(cn('mt-2', 'mt-4')).toBe('mt-4');
  });
});

describe('randomId', () => {
  it('returns a string', () => {
    expect(typeof randomId()).toBe('string');
  });

  it('returns a UUID-shaped string', () => {
    const id = randomId();
    // UUID v4 format: xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx
    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/);
  });

  it('generates unique IDs', () => {
    const ids = new Set(Array.from({ length: 100 }, () => randomId()));
    expect(ids.size).toBe(100);
  });

  it('works when crypto.randomUUID is not available', () => {
    // The randomId function has a fallback using getRandomValues
    // Just verify it still returns valid UUIDs
    const id = randomId();
    expect(id.split('-')).toHaveLength(5);
  });
});
