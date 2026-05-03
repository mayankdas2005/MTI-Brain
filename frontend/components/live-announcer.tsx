'use client';

import { useEffect, useState } from 'react';
import { useThreadStore } from '@/lib/store/threads';

/**
 * Single visually-hidden polite live region for screen readers.
 *
 * Why a global announcer rather than per-step aria-live: per-step narration
 * would overwhelm SR users (every reasoning chunk would interrupt). Instead
 * we announce only the high-signal transition — "Answer ready" when a
 * stream finishes. The visible UI keeps its own micro-states; this is
 * purely the audible companion.
 *
 * Mounted once in the authenticated layout. Subscribes imperatively so
 * we can compare previous-vs-next isStreaming without re-rendering on
 * every store update.
 */
export function LiveAnnouncer() {
  const [message, setMessage] = useState('');

  useEffect(() => {
    let prev = useThreadStore.getState().isStreaming;
    const unsub = useThreadStore.subscribe((state) => {
      const now = state.isStreaming;
      if (prev && !now) {
        setMessage('Answer ready');
        // Clear after a short delay so a subsequent identical message
        // can re-announce (the SR otherwise dedupes consecutive equals).
        setTimeout(() => setMessage(''), 1500);
      }
      prev = now;
    });
    return unsub;
  }, []);

  return (
    <div
      role="status"
      aria-live="polite"
      aria-atomic="true"
      className="sr-only"
    >
      {message}
    </div>
  );
}
