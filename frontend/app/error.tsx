'use client';

import { useEffect } from 'react';

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    console.error('[global-error]', error);
  }, [error]);

  return (
    <html>
      <body className="flex flex-col items-center justify-center min-h-screen gap-4 p-8 text-center font-sans bg-background text-foreground">
        <p className="text-sm font-medium">Something went wrong</p>
        <p className="text-xs text-muted-foreground max-w-sm">
          An unexpected error occurred. Refresh the page to continue.
        </p>
        <div className="flex gap-2">
          <button
            onClick={reset}
            className="px-3 py-1.5 text-xs rounded-md border border-border hover:bg-accent transition-colors"
          >
            Try again
          </button>
          <button
            onClick={() => window.location.reload()}
            className="px-3 py-1.5 text-xs rounded-md bg-foreground text-background hover:bg-foreground/90 transition-colors"
          >
            Refresh
          </button>
        </div>
      </body>
    </html>
  );
}
