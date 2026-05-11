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
  const highContrast = usePreferencesStore((s) => s.highContrast);
  useEffect(() => {
    document.documentElement.setAttribute('data-density', density);
  }, [density]);
  useEffect(() => {
    document.documentElement.classList.toggle('high-contrast', !!highContrast);
  }, [highContrast]);
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
