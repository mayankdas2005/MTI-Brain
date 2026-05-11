'use client';

import { useState, useEffect } from 'react';
import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';

interface ChangelogEntry {
  type: 'new' | 'improvement' | 'fix';
  title: string;
  body: string;
}

interface ChangelogRelease {
  version: string;
  date: string;
  entries: ChangelogEntry[];
}

const STORAGE_KEY = 'mti_changelog_seen';

const TYPE_LABEL: Record<ChangelogEntry['type'], string> = {
  new: 'New',
  improvement: 'Improved',
  fix: 'Fixed',
};

const TYPE_CLASS: Record<ChangelogEntry['type'], string> = {
  new: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-400',
  improvement: 'bg-primary/10 text-primary',
  fix: 'bg-muted text-muted-foreground',
};

let cachedChangelog: ChangelogRelease[] | null = null;

export async function fetchChangelog(): Promise<ChangelogRelease[]> {
  if (cachedChangelog) return cachedChangelog;
  try {
    const res = await fetch('/changelog.json', { cache: 'no-store' });
    if (!res.ok) return [];
    const data: ChangelogRelease[] = await res.json();
    cachedChangelog = data;
    return data;
  } catch {
    return [];
  }
}

export function getSeenVersion(): string | null {
  try { return localStorage.getItem(STORAGE_KEY); } catch { return null; }
}

export function markChangelogSeen(version: string) {
  try { localStorage.setItem(STORAGE_KEY, version); } catch { /* ignore */ }
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent('mti-brain:changelog-read'));
  }
}

export function useChangelogUnread() {
  const [hasUnread, setHasUnread] = useState(false);

  useEffect(() => {
    const check = () => {
      void fetchChangelog().then((releases) => {
        if (!releases.length) return;
        setHasUnread(getSeenVersion() !== releases[0].version);
      });
    };
    check();
    window.addEventListener('mti-brain:changelog-read', check);
    return () => window.removeEventListener('mti-brain:changelog-read', check);
  }, []);

  return hasUnread;
}

interface WhatsNewDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function WhatsNewDialog({ open, onOpenChange }: WhatsNewDialogProps) {
  const [releases, setReleases] = useState<ChangelogRelease[]>([]);

  useEffect(() => {
    void fetchChangelog().then(setReleases);
  }, []);

  const handleDismiss = () => {
    if (releases.length) markChangelogSeen(releases[0].version);
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={(o) => { if (!o) handleDismiss(); else onOpenChange(true); }}>
      <DialogContent className="sm:max-w-lg p-6 gap-0 max-h-[80vh] flex flex-col">
        <DialogTitle className="text-lg font-semibold mb-1">What&apos;s New</DialogTitle>
        <DialogDescription className="text-sm text-muted-foreground mb-5">
          Recent improvements to MTI Brain.
        </DialogDescription>

        <div className="overflow-y-auto flex-1 space-y-6 pr-1">
          {releases.map((release) => (
            <div key={release.version}>
              <div className="flex items-center gap-2 mb-3">
                <span className="text-xs font-semibold text-foreground">v{release.version}</span>
                <span className="text-xs text-muted-foreground">
                  {new Date(release.date).toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' })}
                </span>
              </div>
              <ul className="space-y-2.5">
                {release.entries.map((entry, i) => (
                  <li key={i} className="flex gap-2.5 items-start">
                    <span className={`mt-0.5 shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide ${TYPE_CLASS[entry.type]}`}>
                      {TYPE_LABEL[entry.type]}
                    </span>
                    <div>
                      <p className="text-sm font-medium text-foreground leading-snug">{entry.title}</p>
                      <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{entry.body}</p>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          ))}
          {releases.length === 0 && (
            <p className="text-sm text-muted-foreground text-center py-6">Loading…</p>
          )}
        </div>

        <div className="mt-5 flex justify-end">
          <Button size="sm" onClick={handleDismiss}>Got it</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
