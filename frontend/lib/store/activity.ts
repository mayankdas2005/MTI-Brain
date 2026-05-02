'use client';

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';

const MAX_DAYS = 365;

function localDateKey(d: Date | number = new Date()): string {
  const x = typeof d === 'number' ? new Date(d) : d;
  const y = x.getFullYear();
  const m = String(x.getMonth() + 1).padStart(2, '0');
  const day = String(x.getDate()).padStart(2, '0');
  return `${y}-${m}-${day}`;
}

function daysBetween(a: string, b: string): number {
  const [ay, am, ad] = a.split('-').map(Number);
  const [by, bm, bd] = b.split('-').map(Number);
  const da = Date.UTC(ay, am - 1, ad);
  const db = Date.UTC(by, bm - 1, bd);
  return Math.round((db - da) / 86_400_000);
}

interface ActivityState {
  activeDays: string[];
  questionsByDay: Record<string, number>;
  recordQuestion: () => { streakBefore: number; streakAfter: number; firstOfDay: boolean };
  seedFromUpdatedAts: (timestamps: string[]) => void;
}

export const useActivityStore = create<ActivityState>()(
  persist(
    (set, get) => ({
      activeDays: [],
      questionsByDay: {},

      recordQuestion: () => {
        const today = localDateKey();
        const before = get().activeDays;
        const wasActiveToday = before[before.length - 1] === today;
        const streakBefore = computeStreak(before);

        let nextActive = before;
        if (!wasActiveToday) {
          nextActive = [...before, today].slice(-MAX_DAYS);
        }
        const nextCounts = {
          ...get().questionsByDay,
          [today]: (get().questionsByDay[today] ?? 0) + 1,
        };
        set({ activeDays: nextActive, questionsByDay: nextCounts });

        return {
          streakBefore,
          streakAfter: computeStreak(nextActive),
          firstOfDay: !wasActiveToday,
        };
      },

      seedFromUpdatedAts: (timestamps) => {
        if (timestamps.length === 0) return;
        const seen = new Set(get().activeDays);
        for (const iso of timestamps) {
          const t = Date.parse(iso);
          if (Number.isNaN(t)) continue;
          seen.add(localDateKey(t));
        }
        const sorted = Array.from(seen).sort().slice(-MAX_DAYS);
        set({ activeDays: sorted });
      },
    }),
    {
      name: 'quest-activity',
      storage: createJSONStorage(() => localStorage),
      version: 1,
    },
  ),
);

function computeStreak(days: string[]): number {
  if (days.length === 0) return 0;
  const today = localDateKey();
  const yesterday = localDateKey(Date.now() - 86_400_000);
  const last = days[days.length - 1];
  if (last !== today && last !== yesterday) return 0;

  let streak = 1;
  for (let i = days.length - 2; i >= 0; i--) {
    const gap = daysBetween(days[i], days[i + 1]);
    if (gap === 1) streak++;
    else break;
  }
  return streak;
}

/**
 * Derived selectors — call from React components.
 *
 * Returns a snapshot rather than reactive state so changing today doesn't
 * cause every consumer to re-render — components opt in by reading
 * `activeDays` themselves.
 */
export function getStreakSnapshot() {
  const { activeDays, questionsByDay } = useActivityStore.getState();
  const today = localDateKey();
  return {
    currentStreak: computeStreak(activeDays),
    daysActive: activeDays.length,
    questionsToday: questionsByDay[today] ?? 0,
    isActiveToday: activeDays[activeDays.length - 1] === today,
  };
}

/** Hook helpers — re-read derived values when activeDays changes. */
export function useStreak() {
  const activeDays = useActivityStore((s) => s.activeDays);
  const questionsByDay = useActivityStore((s) => s.questionsByDay);
  const today = localDateKey();
  return {
    currentStreak: computeStreak(activeDays),
    daysActive: activeDays.length,
    questionsToday: questionsByDay[today] ?? 0,
    isActiveToday: activeDays[activeDays.length - 1] === today,
  };
}
