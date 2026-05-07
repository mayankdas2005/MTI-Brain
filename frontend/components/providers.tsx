'use client';

import { ThemeProvider } from 'next-themes';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Toaster } from '@/components/ui/sonner';
import { ErrorBoundary } from '@/components/error-boundary';
import { AnalyticsBridge } from '@/components/analytics-bridge';
import { ReactNode, useEffect } from 'react';
import { usePreferencesStore } from '@/lib/store/preferences';

/** Mirror the user's density preference onto <html data-density>. The
 *  CSS vars in globals.css read off that attribute, so any descendant
 *  using --density-* gets recomputed when the user toggles. */
function DensitySync() {
  const density = usePreferencesStore((s) => s.density);
  useEffect(() => {
    document.documentElement.setAttribute('data-density', density);
    return () => {
      // Don't remove on unmount - Providers is mounted for the app's lifetime,
      // and clearing the attribute on HMR causes a one-frame flicker.
    };
  }, [density]);
  return null;
}

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem disableTransitionOnChange>
      <ErrorBoundary>
        <TooltipProvider delayDuration={300}>
          <DensitySync />
          <AnalyticsBridge />
          {children}
        </TooltipProvider>
      </ErrorBoundary>
      <Toaster />
    </ThemeProvider>
  );
}
