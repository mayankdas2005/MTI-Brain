'use client';

import { type LucideIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';

interface EmptyStateProps {
  icon: LucideIcon;
  headline: string;
  subtext?: string;
  action?: {
    label: string;
    onClick: () => void;
  };
  className?: string;
}

export function EmptyState({ icon: Icon, headline, subtext, action, className = '' }: EmptyStateProps) {
  return (
    <div className={`flex flex-col items-center justify-center gap-4 py-12 px-6 text-center ${className}`}>
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl border border-border bg-muted/50">
        <Icon className="w-6 h-6 text-muted-foreground/60" aria-hidden />
      </div>
      <div className="space-y-1">
        <p className="text-sm font-medium text-foreground">{headline}</p>
        {subtext && (
          <p className="text-xs text-muted-foreground max-w-xs leading-relaxed">{subtext}</p>
        )}
      </div>
      {action && (
        <Button size="sm" variant="outline" onClick={action.onClick}>
          {action.label}
        </Button>
      )}
    </div>
  );
}
