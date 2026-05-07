'use client';

import { useEffect, useState } from 'react';
import { Download, X } from 'lucide-react';
import { useStreak } from '@/lib/store/activity';
import { useInstallStore } from '@/lib/store/install';
import { track } from '@/lib/analytics';

const DISMISS_KEY = 'mti-brain:install-prompt-dismissed';
const MIN_DAYS_BEFORE_PROMPT = 3;

/**
 * Auto-surfacing "Install MTI Brain" toast.
 *
 * Habit-loop logic: never bug a user on day 1. Only surface after they've
 * been active for ≥ 3 distinct days. Users who dismiss are not asked again
 * until they explicitly clear the localStorage flag - but they can still
 * trigger an install manually from the user menu (see sidebar.tsx).
 */
export function InstallPrompt() {
  const [visible, setVisible] = useState(false);
  const canInstall = useInstallStore((s) => s.canInstall);
  const promptInstall = useInstallStore((s) => s.promptInstall);
  const { daysActive } = useStreak();

  useEffect(() => {
    if (!canInstall) return;
    if (daysActive < MIN_DAYS_BEFORE_PROMPT) return;
    try {
      if (localStorage.getItem(DISMISS_KEY)) return;
    } catch {
      // localStorage unavailable - better to show than silently skip.
    }
    setVisible(true);
  }, [canInstall, daysActive]);

  if (!visible || !canInstall) return null;

  const handleInstall = async () => {
    track('install_prompt_clicked');
    const accepted = await promptInstall();
    track('install_prompt_resolved', { outcome: accepted ? 'accepted' : 'dismissed' });
    setVisible(false);
  };

  const handleDismiss = () => {
    try {
      localStorage.setItem(DISMISS_KEY, '1');
    } catch {
      // ignore
    }
    track('install_prompt_dismissed');
    setVisible(false);
  };

  return (
    <div
      role="dialog"
      aria-label="Install MTI Brain"
      className="fixed left-3 right-3 sm:left-auto sm:right-6 z-40 w-auto sm:w-[20rem] rounded-xl border border-border bg-popover text-popover-foreground shadow-2xl shadow-black/15 p-4"
      style={{
        bottom: 'max(1.5rem, calc(env(safe-area-inset-bottom) + 1rem) + var(--vv-bottom-inset, 0px))',
      }}
    >
      <div className="flex items-start gap-3">
        <div className="rounded-lg bg-primary/10 p-2 text-primary">
          <Download className="w-4 h-4" aria-hidden />
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium">Install MTI Brain</p>
          <p className="text-xs text-muted-foreground mt-0.5">
            Open it in its own window - feels native, launches faster.
          </p>
          <div className="flex items-center gap-2 mt-3">
            <button
              type="button"
              onClick={handleInstall}
              className="rounded-md bg-foreground text-background text-xs px-3 py-1.5 font-medium hover:opacity-85 transition-opacity"
            >
              Install
            </button>
            <button
              type="button"
              onClick={handleDismiss}
              className="rounded-md text-xs px-3 py-1.5 text-muted-foreground hover:text-foreground transition-colors"
            >
              Not now
            </button>
          </div>
        </div>
        <button
          type="button"
          onClick={handleDismiss}
          aria-label="Dismiss install prompt"
          className="text-muted-foreground hover:text-foreground transition-colors"
        >
          <X className="w-3.5 h-3.5" aria-hidden />
        </button>
      </div>
    </div>
  );
}
