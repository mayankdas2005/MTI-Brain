'use client';

import { Component, ReactNode } from 'react';
import { usePathname } from 'next/navigation';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { AlertTriangle } from 'lucide-react';

interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode;
}

interface ErrorBoundaryState {
  error: Error | null;
}

/**
 * Real React error boundary. Catches render-time errors via the class API
 * and also listens for `window.onerror` so non-React JS exceptions surface
 * here too. Resets automatically on route change so the user isn't stuck
 * on the error UI after navigating away.
 */
class ErrorBoundaryClass extends Component<
  ErrorBoundaryProps & { pathname: string | null },
  ErrorBoundaryState
> {
  state: ErrorBoundaryState = { error: null };
  private removeWindowListener: (() => void) | null = null;

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: React.ErrorInfo) {
    // Keep technical details in the console for developers; the user-facing
    // card never surfaces stack traces.
    console.error('[ErrorBoundary]', error, info);
  }

  componentDidMount() {
    if (typeof window === 'undefined') return;
    const handler = (event: ErrorEvent) => {
      if (event.error instanceof Error) {
        console.error('[ErrorBoundary] window error', event.error);
        this.setState({ error: event.error });
      }
    };
    window.addEventListener('error', handler);
    this.removeWindowListener = () => window.removeEventListener('error', handler);
  }

  componentDidUpdate(prevProps: ErrorBoundaryProps & { pathname: string | null }) {
    // Reset when the user navigates so they aren't stuck on the error UI.
    if (this.state.error && prevProps.pathname !== this.props.pathname) {
      this.setState({ error: null });
    }
  }

  componentWillUnmount() {
    this.removeWindowListener?.();
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    const { children, fallback } = this.props;
    if (!error) return children;

    if (fallback) return fallback;

    return (
      <div className="flex items-center justify-center min-h-screen bg-background p-4">
        <Card className="w-full max-w-md">
          <CardHeader>
            <div className="flex items-center gap-2">
              <AlertTriangle className="w-5 h-5 text-destructive" aria-hidden />
              <CardTitle>Something went wrong</CardTitle>
            </div>
            <CardDescription>
              An unexpected error occurred. Try again, or refresh the page.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="flex gap-2">
              <Button onClick={this.reset} variant="outline" className="flex-1">
                Try again
              </Button>
              <Button onClick={() => window.location.reload()} className="flex-1">
                Refresh page
              </Button>
            </div>
          </CardContent>
        </Card>
      </div>
    );
  }
}

export function ErrorBoundary({ children, fallback }: ErrorBoundaryProps) {
  // usePathname is a client hook; this wrapper exists so the class
  // component can react to route changes (it can't call hooks itself).
  const pathname = usePathname();
  return (
    <ErrorBoundaryClass pathname={pathname} fallback={fallback}>
      {children}
    </ErrorBoundaryClass>
  );
}

// Keep a separate non-router-aware variant available for callers that need
// the boundary outside of the Next.js app router (e.g. unit tests).
export function ErrorBoundaryStandalone({ children, fallback }: ErrorBoundaryProps) {
  return (
    <ErrorBoundaryClass pathname={null} fallback={fallback}>
      {children}
    </ErrorBoundaryClass>
  );
}

