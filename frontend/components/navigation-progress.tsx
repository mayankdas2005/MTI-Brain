'use client';

import { useEffect, useRef, useState } from 'react';
import { usePathname } from 'next/navigation';

/**
 * Thin primary-coloured bar at the very top of the viewport.
 * Animates in when the pathname changes (navigation starts) and
 * sweeps to 100% + fades out when the new page settles.
 *
 * No external dependencies - driven purely by pathname changes and
 * CSS animations defined in globals.css.
 */
export function NavigationProgress() {
  const pathname = usePathname();
  const [state, setState] = useState<'idle' | 'entering' | 'exiting'>('idle');
  const prevPathRef = useRef(pathname);
  const exitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (pathname === prevPathRef.current) return;
    prevPathRef.current = pathname;

    // Clear any pending exit
    if (exitTimerRef.current) clearTimeout(exitTimerRef.current);

    // Start entering
    setState('entering');

    // After the entering animation (~600ms), switch to exiting
    exitTimerRef.current = setTimeout(() => {
      setState('exiting');
      // Return to idle after exit animation (~300ms)
      exitTimerRef.current = setTimeout(() => setState('idle'), 350);
    }, 600);

    return () => {
      if (exitTimerRef.current) clearTimeout(exitTimerRef.current);
    };
  }, [pathname]);

  if (state === 'idle') return null;

  return (
    <div
      aria-hidden
      className="fixed top-0 left-0 z-[9999] h-[2px] bg-primary pointer-events-none"
      style={{ width: state === 'exiting' ? '100%' : '0%' }}
      key={state}
    >
      <div
        className={`h-full bg-primary origin-left ${
          state === 'entering' ? 'nav-progress-entering' : 'nav-progress-exiting'
        }`}
        style={{ width: '100%' }}
      />
    </div>
  );
}
