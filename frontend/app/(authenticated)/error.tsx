'use client';

import { useEffect } from 'react';
import { Button } from '@/components/ui/button';

export default function AuthenticatedError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error('[app-error]', error);
  }, [error]);

  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 p-8 text-center">
      <p className="text-sm font-medium text-foreground">Something went wrong</p>
      <p className="text-xs text-muted-foreground max-w-sm">
        An unexpected error occurred. You can try again or refresh the page.
      </p>
      <div className="flex gap-2">
        <Button variant="outline" size="sm" onClick={reset}>
          Try again
        </Button>
        <Button variant="ghost" size="sm" onClick={() => window.location.reload()}>
          Refresh
        </Button>
      </div>
    </div>
  );
}
