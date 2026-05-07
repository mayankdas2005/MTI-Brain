'use client';

import { useEffect, useState } from 'react';

/**
 * Returns `true` while the tab is visible, `false` while it's hidden.
 * Use to pause animations, polling, and clock ticks when the user isn't
 * watching - saves CPU and battery on backgrounded tabs.
 */
export function usePageVisibility(): boolean {
  const [visible, setVisible] = useState(() => {
    if (typeof document === 'undefined') return true;
    return document.visibilityState !== 'hidden';
  });

  useEffect(() => {
    if (typeof document === 'undefined') return;
    const handler = () => setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', handler);
    return () => document.removeEventListener('visibilitychange', handler);
  }, []);

  return visible;
}
