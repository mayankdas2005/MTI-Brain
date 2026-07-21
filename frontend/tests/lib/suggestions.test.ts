import { describe, it, expect } from 'vitest';
import { pickRandom, pickSuggestions, GHOST_PROMPTS, GHOST_PROMPTS_BY_TONE, SIMPLE, COMPLEX, ADVANCED } from '@/lib/suggestions';

describe('pickRandom', () => {
  it('returns the requested number of items', () => {
    const arr = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
    const result = pickRandom(arr, 3);
    expect(result).toHaveLength(3);
  });

  it('returns all items when n >= array length', () => {
    const arr = [1, 2, 3];
    const result = pickRandom(arr, 5);
    expect(result).toHaveLength(3);
  });

  it('returns empty array when input is empty', () => {
    expect(pickRandom([], 5)).toEqual([]);
  });

  it('does not mutate the original array', () => {
    const arr = [1, 2, 3, 4, 5];
    const original = [...arr];
    pickRandom(arr, 3);
    expect(arr).toEqual(original);
  });

  it('returns items from the original array', () => {
    const arr = ['a', 'b', 'c', 'd', 'e'];
    const result = pickRandom(arr, 2);
    for (const item of result) {
      expect(arr).toContain(item);
    }
  });

  it('returns unique items (no duplicates)', () => {
    const arr = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10];
    const result = pickRandom(arr, 5);
    const unique = new Set(result);
    expect(unique.size).toBe(5);
  });
});

describe('pickSuggestions', () => {
  it('returns exactly 3 suggestions', () => {
    const suggestions = pickSuggestions();
    expect(suggestions).toHaveLength(3);
  });

  it('each suggestion has icon, label, and prompt', () => {
    const suggestions = pickSuggestions();
    for (const s of suggestions) {
      expect(s.icon).toBeDefined();
      expect(s.label).toBeTruthy();
      expect(s.prompt).toBeTruthy();
    }
  });

  it('returns one from each difficulty tier', () => {
    const suggestions = pickSuggestions();
    // First should be from SIMPLE, second from COMPLEX, third from ADVANCED
    expect(SIMPLE).toContainEqual(suggestions[0]);
    expect(COMPLEX).toContainEqual(suggestions[1]);
    expect(ADVANCED).toContainEqual(suggestions[2]);
  });
});

describe('GHOST_PROMPTS', () => {
  it('is a non-empty array of strings', () => {
    expect(GHOST_PROMPTS.length).toBeGreaterThan(0);
    for (const prompt of GHOST_PROMPTS) {
      expect(typeof prompt).toBe('string');
      expect(prompt.length).toBeGreaterThan(0);
    }
  });
});

describe('GHOST_PROMPTS_BY_TONE', () => {
  it('has entries for all tones', () => {
    expect(GHOST_PROMPTS_BY_TONE.analyst).toBeDefined();
    expect(GHOST_PROMPTS_BY_TONE.manager).toBeDefined();
    expect(GHOST_PROMPTS_BY_TONE.director).toBeDefined();
    expect(GHOST_PROMPTS_BY_TONE.executive).toBeDefined();
  });

  it('all tones reference the same array', () => {
    expect(GHOST_PROMPTS_BY_TONE.analyst).toBe(GHOST_PROMPTS);
    expect(GHOST_PROMPTS_BY_TONE.manager).toBe(GHOST_PROMPTS);
    expect(GHOST_PROMPTS_BY_TONE.director).toBe(GHOST_PROMPTS);
    expect(GHOST_PROMPTS_BY_TONE.executive).toBe(GHOST_PROMPTS);
  });
});
