'use client';

import { useEffect, useRef } from 'react';
import { useGraphContextStore } from '@/lib/store/graph_context';
import { getGraphContext, downloadGraphContext } from '@/lib/api/graph_context';
import { toast } from '@/lib/toast';
import { playPing, notify, getPermission } from '@/lib/utils/notifications';
import { usePreferencesStore } from '@/lib/store/preferences';

const POLL_INTERVAL_MS = 10_000;

export function useGraphContextNotice() {
  const getPending  = useGraphContextStore((s) => s.getPending);
  const set         = useGraphContextStore((s) => s.set);
  const expireStale = useGraphContextStore((s) => s.expireStale);
  const notifySound = usePreferencesStore((s) => s.notifySound);
  const timerRef    = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    const poll = async () => {
      expireStale();

      const pending = getPending();
      if (pending.length === 0) return;

      await Promise.allSettled(
        pending.map(async (convId) => {
          try {
            const res = await getGraphContext(convId);
            if (res.status === 'ready') {
              set(convId, { status: 'ready', url: res.url ?? null, queuedAt: Date.now() });

              const visible = typeof document !== 'undefined' && document.visibilityState === 'visible';

              if (visible) {
                toast.info('Query context ready', {
                  id: `gc-ready-${convId}`,
                  description: 'The knowledge graph context for this answer is ready to view.',
                  duration: 12_000,
                  action: {
                    label: 'Open',
                    onClick: () => {
                      void getGraphContext(convId).then((fresh) => {
                        if (fresh.url) {
                          window.open(fresh.url, '_blank', 'noopener');
                          void downloadGraphContext(convId).then(({ blob, filename }) => {
                            const blobUrl = URL.createObjectURL(blob);
                            const a = document.createElement('a');
                            a.href = blobUrl;
                            a.download = filename;
                            document.body.appendChild(a);
                            a.click();
                            document.body.removeChild(a);
                            URL.revokeObjectURL(blobUrl);
                          }).catch(() => {});
                        }
                      });
                    },
                  },
                });
              } else {
                if (getPermission() === 'granted') {
                  notify('Query context ready', {
                    body: 'The knowledge graph context for this answer is ready to view.',
                    silent: true,
                  });
                } else {
                  toast.info('Graph context ready', {
                    id: `gc-ready-${convId}`,
                    description: 'The knowledge graph for your query is ready to view.',
                    duration: 0,
                    action: {
                      label: 'Open',
                      onClick: () => {
                        void getGraphContext(convId).then((fresh) => {
                          if (fresh.url) {
                            window.open(fresh.url, '_blank', 'noopener');
                            void downloadGraphContext(convId).then(({ blob, filename }) => {
                              const blobUrl = URL.createObjectURL(blob);
                              const a = document.createElement('a');
                              a.href = blobUrl;
                              a.download = filename;
                              document.body.appendChild(a);
                              a.click();
                              document.body.removeChild(a);
                              URL.revokeObjectURL(blobUrl);
                            }).catch(() => {});
                          }
                        });
                      },
                    },
                  });
                }
              }

              if (notifySound) playPing();
            } else if (res.status === 'failed') {
              set(convId, { status: 'failed', url: null, queuedAt: Date.now() });
              toast.warning('Failed to fetch query context', {
                id: `gc-fail-${convId}`,
                description: 'Please try again.',
              });
            }
          } catch {
            // 404 or network error — silently skip, retry next cycle
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
