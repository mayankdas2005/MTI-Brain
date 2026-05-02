'use client';

import Dexie, { type Table } from 'dexie';

/**
 * Per-thread composer drafts persisted to IndexedDB.
 *
 * Why dexie + not localStorage: drafts can be long (multi-paragraph),
 * survive across tabs, and IndexedDB is async (won't block typing).
 * Why not Zustand persist: zustand persist serializes the whole store
 * on every keystroke. We want O(1) writes scoped to one threadId.
 */
interface DraftRecord {
  threadId: string; // primary key. '__new__' for the /new page composer.
  text: string;
  updatedAt: number;
}

class DraftDB extends Dexie {
  drafts!: Table<DraftRecord, string>;
  constructor() {
    super('quest-drafts-v1');
    this.version(1).stores({ drafts: '&threadId, updatedAt' });
  }
}

let _db: DraftDB | null = null;
function db(): DraftDB | null {
  if (typeof window === 'undefined') return null;
  if (!_db) _db = new DraftDB();
  return _db;
}

export async function loadDraft(threadId: string): Promise<string> {
  try {
    const d = db();
    if (!d) return '';
    const row = await d.drafts.get(threadId);
    return row?.text ?? '';
  } catch {
    return '';
  }
}

export async function saveDraft(threadId: string, text: string): Promise<void> {
  try {
    const d = db();
    if (!d) return;
    if (!text.trim()) {
      await d.drafts.delete(threadId);
      return;
    }
    await d.drafts.put({ threadId, text, updatedAt: Date.now() });
  } catch {
    // Quota exceeded / Safari private mode — drafts are best-effort.
  }
}

export async function clearDraft(threadId: string): Promise<void> {
  try {
    const d = db();
    if (!d) return;
    await d.drafts.delete(threadId);
  } catch {
    // ignore
  }
}

/**
 * Background sweep: drop drafts older than the cutoff. Call from
 * non-critical paths (e.g. on app boot inside requestIdleCallback).
 */
export async function pruneOldDrafts(maxAgeMs = 30 * 24 * 60 * 60 * 1000): Promise<void> {
  try {
    const d = db();
    if (!d) return;
    const cutoff = Date.now() - maxAgeMs;
    await d.drafts.where('updatedAt').below(cutoff).delete();
  } catch {
    // ignore
  }
}
