'use client';

import { useEffect, useRef } from 'react';
import { useDashboardStore } from '@/lib/store/dashboard';
import { getDashboard } from '@/lib/api/dashboard';
import { toast } from '@/lib/toast';
import { playPing, notify, getPermission } from '@/lib/utils/notifications';
import { usePreferencesStore } from '@/lib/store/preferences';

const POLL_INTERVAL_MS = 30_000;

export function useDashboardNotice() {
  const getPending   = useDashboardStore((s) => s.getPending);
  const set          = useDashboardStore((s) => s.set);
  const expireStale  = useDashboardStore((s) => s.expireStale);
  const notifySound  = usePreferencesStore((s) => s.notifySound);
  const timerRef     = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const poll = async () => {
      expireStale();

      const pending = getPending();
      if (pending.length === 0) return;

      await Promise.allSettled(
        pending.map(async (convId) => {
          try {
            const res = await getDashboard(convId);
            if (res.status === 'ready') {
              set(convId, { status: 'ready', url: res.url ?? null, queuedAt: Date.now() });

              const visible = typeof document !== 'undefined' && document.visibilityState === 'visible';

              if (visible) {
                toast.info('Dashboard ready', {
                  id: `dash-ready-${convId}`,
                  description: 'Your executive dashboard has been generated.',
                  duration: 12_000,
                  action: {
                    label: 'Open',
                    onClick: () => {
                      void getDashboard(convId).then((fresh) => {
                        if (fresh.url) window.open(fresh.url, '_blank', 'noopener');
                      });
                    },
                  },
                });
              } else {
                // Tab hidden — fire OS notification
                if (getPermission() === 'granted') {
                  notify('Dashboard ready', {
                    body: 'Your executive dashboard has been generated.',
                    silent: true,
                  });
                } else {
                  // Fallback: toast so it's waiting when user returns
                  toast.info('Dashboard ready', {
                    id: `dash-ready-${convId}`,
                    description: 'Your executive dashboard has been generated.',
                    duration: 0,
                    action: {
                      label: 'Open',
                      onClick: () => {
                        void getDashboard(convId).then((fresh) => {
                          if (fresh.url) window.open(fresh.url, '_blank', 'noopener');
                        });
                      },
                    },
                  });
                }
              }

              if (notifySound) playPing();
            } else if (res.status === 'failed') {
              set(convId, { status: 'failed', url: null, queuedAt: Date.now() });
              toast.error('Dashboard generation failed', {
                id: `dash-fail-${convId}`,
                description: 'Please try generating again.',
              });
            }
          } catch {
            // 404 or network error — silently skip, will retry next cycle
          }
        }),
      );
    };

    timerRef.current = setInterval(poll, POLL_INTERVAL_MS);
    void poll();

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [getPending, set, expireStale, notifySound]);
}
