'use client';

import { useEffect, useState } from 'react';

/**
 * Returns `true` when the user has set their OS to "reduce motion."
 * Use to gate JS-driven animations (e.g. framer-motion variants) - the CSS
 * media query in globals.css handles CSS-driven animations automatically.
 */
export function usePrefersReducedMotion(): boolean {
  const [reduced, setReduced] = useState(() => {
    if (typeof window === 'undefined') return false;
    return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  });

  useEffect(() => {
    if (typeof window === 'undefined') return;
    const mql = window.matchMedia('(prefers-reduced-motion: reduce)');
    const handler = (e: MediaQueryListEvent) => setReduced(e.matches);
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }, []);

  return reduced;
}
