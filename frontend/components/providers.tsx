'use client';

import { ThemeProvider } from 'next-themes';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Toaster } from '@/components/ui/sonner';
import { ErrorBoundary } from '@/components/error-boundary';
import { AnalyticsBridge } from '@/components/analytics-bridge';
import { ReactNode } from 'react';

export function Providers({ children }: { children: ReactNode }) {
  return (
    <ThemeProvider attribute="class" defaultTheme="light" enableSystem disableTransitionOnChange>
      <ErrorBoundary>
        <TooltipProvider delayDuration={300}>
          <AnalyticsBridge />
          {children}
        </TooltipProvider>
      </ErrorBoundary>
      <Toaster />
    </ThemeProvider>
  );
}
