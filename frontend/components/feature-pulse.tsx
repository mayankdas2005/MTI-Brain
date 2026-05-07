'use client';

import { useEffect, useState, type ReactNode } from 'react';

const STORAGE_KEY = 'mti-brain:feature-seen';

/**
 * Wraps a child with a small breathing dot that signals "new feature here".
 * The dot retires permanently the first time the child is interacted with
 * (click or focus inside) - interaction is the signal that the user has
 * discovered the feature, no need to keep nagging.
 *
 * State is per-feature-id, persisted to localStorage. Shared across tabs
 * via storage events so dismissing in one tab clears the dot in others.
 */
interface FeaturePulseProps {
  /** Stable feature key. Once dismissed for this id, the dot never returns. */
  featureId: string;
  children: ReactNode;
  /** Optional className applied to the wrapper. */
  className?: string;
}

function readSeen(): Record<string, true> {
  if (typeof window === 'undefined') return {};
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch {
    return {};
  }
}

function writeSeen(seen: Record<string, true>): void {
  if (typeof window === 'undefined') return;
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(seen));
  } catch {
    // Quota / private mode - nothing to do; the pulse will reappear next mount.
  }
}

export function FeaturePulse({ featureId, children, className }: FeaturePulseProps) {
  // Render-mismatch protection: start `seen=true` (don't show the dot) on
  // SSR + first client render, then flip to the real value in an effect.
  // This prevents a flash of the pulse on hydration for users who already
  // dismissed it.
  const [seen, setSeen] = useState(true);

  useEffect(() => {
    setSeen(!!readSeen()[featureId]);
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) setSeen(!!readSeen()[featureId]);
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [featureId]);

  const dismiss = () => {
    if (seen) return;
    const next = { ...readSeen(), [featureId]: true as const };
    writeSeen(next);
    setSeen(true);
  };

  return (
    <span
      className={`relative inline-flex ${className ?? ''}`}
      onClickCapture={dismiss}
      onFocusCapture={dismiss}
    >
      {children}
      {!seen && (
        <span
          aria-hidden
          className="pointer-events-none absolute -top-0.5 -right-0.5 h-2 w-2 rounded-full bg-primary shadow-[0_0_6px_1px_color-mix(in_oklch,var(--primary)_40%,transparent)]"
          style={{ animation: 'cursor-breathe 1.4s ease-in-out infinite' }}
        />
      )}
    </span>
  );
}
