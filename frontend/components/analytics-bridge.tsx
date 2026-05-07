'use client';

import { useEffect } from 'react';
import { usePathname } from 'next/navigation';
import { getStoredUser } from '@/lib/auth';
import { identify, trackPageview } from '@/lib/analytics';
import { ensureInstallListeners } from '@/lib/store/install';

/**
 * Identifies the logged-in user with PostHog, reports page views, and
 * registers the service worker.
 *
 * Mounted under `<Providers>` so it runs on every authenticated route.
 * Both jobs no-op cleanly when their dependencies are missing - analytics
 * skips when `NEXT_PUBLIC_POSTHOG_KEY` is unset; SW registration skips
 * outside production.
 */
export function AnalyticsBridge() {
  const pathname = usePathname();

  useEffect(() => {
    ensureInstallListeners();
    const user = getStoredUser();
    if (user?.user_id) {
      identify(user.user_id, { email: user.email, name: user.name });
    }
  }, []);

  useEffect(() => {
    if (!pathname) return;
    trackPageview(pathname);
  }, [pathname]);

  // Register the SW lazily after the page is interactive. Runs in BOTH dev
  // and production so the install flow can be tested without a prod build.
  // Safe in dev because `public/sw.js` only intercepts navigation requests
  // (network-first) and ignores `_next/...` HMR/asset traffic entirely.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    if (!('serviceWorker' in navigator)) return;
    const onLoad = () => {
      navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {
        // Registration failures are silent; PWA install just won't be offered.
      });
    };
    if (document.readyState === 'complete') onLoad();
    else window.addEventListener('load', onLoad, { once: true });
  }, []);

  return null;
}
