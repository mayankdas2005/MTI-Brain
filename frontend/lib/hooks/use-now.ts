'use client';

import { useState, useEffect } from 'react';

// Module-level shared "now" - all subscribers read the SAME value,
// so components mounted at different times never show different relative times.
let sharedNow = new Date();
const subscribers = new Set<() => void>();
let timerId: ReturnType<typeof setTimeout> | null = null;

function tick() {
  sharedNow = new Date();
  subscribers.forEach((cb) => cb());

  // Align the next tick to the next minute boundary (:00 seconds).
  // e.g. if it's 10:38:12, next tick fires in 48 s (at 10:39:00).
  // This ensures "37m ago" → "38m ago" happens simultaneously across all
  // surfaces (sidebar recents, starred, /chats, search) at the exact moment
  // the minute turns, not up to 59 s later.
  const msUntilNextMinute =
    60_000 - (sharedNow.getSeconds() * 1_000 + sharedNow.getMilliseconds());
  timerId = setTimeout(tick, msUntilNextMinute);
}

function ensureRunning() {
  if (timerId !== null) return;
  const now = new Date();
  const msUntilNextMinute =
    60_000 - (now.getSeconds() * 1_000 + now.getMilliseconds());
  timerId = setTimeout(tick, msUntilNextMinute);
}

export function useNow(): Date {
  // Initialize from the shared global - not `new Date()` - so every component
  // starts with the identical value regardless of when it mounts.
  const [now, setNow] = useState<Date>(() => sharedNow);

  useEffect(() => {
    // Sync immediately in case sharedNow advanced while the component was
    // rendering (tiny race window on slow devices).
    setNow(sharedNow);

    const update = () => setNow(sharedNow);
    subscribers.add(update);
    ensureRunning();

    return () => {
      subscribers.delete(update);
      if (subscribers.size === 0 && timerId !== null) {
        clearTimeout(timerId);
        timerId = null;
      }
    };
  }, []);

  return now;
}
