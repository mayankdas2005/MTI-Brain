import Link from 'next/link';

export default function NotFound() {
  return (
    <div className="flex items-center justify-center min-h-screen bg-background overflow-hidden">
      <div className="text-center space-y-4 max-w-md px-6">
        {/* Radar pulse animation */}
        <div className="relative flex flex-col items-center">
          <div className="animate-fade-up">
            <p className="text-6xl">🔍</p>
          </div>
        </div>

        <h1 className="text-2xl font-semibold text-foreground animate-fade-up" style={{ animationDelay: '1.2s', animationFillMode: 'both', opacity: 0 }}>
          Page not found
        </h1>
        <p className="text-sm text-muted-foreground leading-relaxed animate-fade-up" style={{ animationDelay: '1.4s', animationFillMode: 'both', opacity: 0 }}>
          We couldn&apos;t locate this page. Milestone Technologies operates
          across 35 countries - but this URL isn&apos;t one of them.
          Let&apos;s get you back on track.
        </p>
        <div className="animate-fade-up" style={{ animationDelay: '1.6s', animationFillMode: 'both', opacity: 0 }}>
          <Link
            href="/new"
            className="inline-block text-sm text-primary hover:underline font-medium"
          >
            Back to Quest
          </Link>
        </div>
      </div>
    </div>
  );
}
