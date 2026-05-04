'use client';

import { useEffect, useRef, startTransition } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { useThreadStore } from '@/lib/store/threads';
import { usePreferencesStore } from '@/lib/store/preferences';
import { setFaviconDot, clearFaviconDot } from '@/lib/utils/favicon';
import {
  getPermission,
  notify,
  requestPermission,
} from '@/lib/utils/notifications';
import { toast } from '@/lib/toast';

const BASE_TITLE = 'MTI Brain';
const PING_SRC = '/notify.mp3';

// Audio priming is the key to playing custom sounds when the tab is hidden.
// Browsers throttle `Audio.play()` in background tabs UNLESS the audio element
// has been "user-activated" — meaning it played at least once in response to
// a user gesture (click, keydown, touchstart). Once primed, subsequent
// `play()` calls work even when the tab is hidden or the browser is
// minimized. We prime on the very first gesture after page load.
let pingAudio: HTMLAudioElement | null = null;
let pingPrimed = false;

function ensurePingAudio(): HTMLAudioElement | null {
  if (typeof window === 'undefined') return null;
  if (!pingAudio) {
    pingAudio = new Audio(PING_SRC);
    pingAudio.preload = 'auto';
  }
  return pingAudio;
}

function primePingOnGesture() {
  if (pingPrimed) return;
  const a = ensurePingAudio();
  if (!a) return;
  // Play once muted, immediately pause and reset. The browser still counts
  // this as a user-gesture-initiated playback, which marks the element as
  // user-activated for the rest of the session.
  a.muted = true;
  void a
    .play()
    .then(() => {
      a.pause();
      a.currentTime = 0;
      a.muted = false;
      pingPrimed = true;
    })
    .catch(() => {
      // Some browsers refuse silent priming on first try; a real later call
      // will retry and may succeed if the page has accumulated more
      // gestures. Don't flag primed so we keep listening.
    });
}

function attachPrimeListeners() {
  if (typeof window === 'undefined') return;
  const opts = { once: false, capture: true } as AddEventListenerOptions;
  const handler = () => {
    primePingOnGesture();
    if (pingPrimed) {
      window.removeEventListener('pointerdown', handler, opts);
      window.removeEventListener('keydown', handler, opts);
      window.removeEventListener('touchstart', handler, opts);
    }
  };
  window.addEventListener('pointerdown', handler, opts);
  window.addEventListener('keydown', handler, opts);
  window.addEventListener('touchstart', handler, opts);
}

function playPing() {
  const a = ensurePingAudio();
  if (!a) return;
  try {
    a.currentTime = 0;
    void a.play().catch(() => {
      // Background-tab throttling kicked in despite priming, or the file is
      // missing. Visual notification still fires; silent fail is fine.
    });
  } catch {
    // Older browsers / file missing — silent fail.
  }
}

/**
 * Single orchestrating hook for stream-completion notifications.
 *
 * Replaces the old `useTabTitleBadge`. Routes each completion to the right
 * channel based on tab visibility and the user's current route:
 *
 *  - Visible AND on /chat/[completedThreadId]  → no-op (user is watching it)
 *  - Visible AND on a different in-app route   → sonner toast with [Open]
 *  - Hidden                                    → tab title (N), favicon dot,
 *                                                OS notification, optional ping
 *
 * Mount once, in the authenticated layout. Listening at the store level
 * means we catch completions from any thread, not just the one the user
 * is currently viewing.
 */
export function useStreamCompletionNotice() {
  const isStreaming = useThreadStore((s) => s.isStreaming);
  const streamingThreadId = useThreadStore((s) => s.streamingThreadId);
  const router = useRouter();
  const pathname = usePathname();

  const wasStreamingRef = useRef(false);
  const lastStreamingThreadRef = useRef<string | null>(null);
  const unreadRef = useRef(0);

  // Track the most recent streamingThreadId so we know which thread completed
  // even after the store has nulled it out.
  useEffect(() => {
    if (streamingThreadId) lastStreamingThreadRef.current = streamingThreadId;
  }, [streamingThreadId]);

  // Detect streaming → done transitions and dispatch the right notification.
  useEffect(() => {
    const wasStreaming = wasStreamingRef.current;
    wasStreamingRef.current = isStreaming;
    if (typeof document === 'undefined') return;
    if (!(wasStreaming && !isStreaming)) return;

    const completedId = lastStreamingThreadRef.current;
    if (!completedId) return;

    const { threads, threadMessageMap, currentMessages, currentThreadId } =
      useThreadStore.getState();
    const threadTitle =
      threads.find((t) => t.id === completedId)?.title || 'New chat';
    // Notification body uses the user's question, not the thread title —
    // people remember what they asked, not which thread carried it.
    // Fall back to the title only if we somehow lost the messages.
    const messages =
      currentThreadId === completedId
        ? currentMessages
        : threadMessageMap[completedId] ?? [];
    const lastUserMsg = [...messages].reverse().find((m) => m.role === 'user');
    const rawQuestion = lastUserMsg?.content?.trim();
    const noticeBody = rawQuestion
      ? rawQuestion.length > 120
        ? rawQuestion.slice(0, 117) + '…'
        : rawQuestion
      : threadTitle;
    const prefs = usePreferencesStore.getState();

    if (prefs.notifyOnComplete === 'off') return;

    const visible = document.visibilityState === 'visible';
    const onMatchingChat = pathname === `/chat/${completedId}`;

    // Visible + viewing the thread → no notification needed.
    if (visible && onMatchingChat) return;

    // Visible + elsewhere in the app → quieter in-app toast with Open action.
    if (visible) {
      toast.info('Response ready', {
        description: noticeBody,
        id: `completion-${completedId}`,
        action: {
          label: 'Open',
          onClick: () => {
            startTransition(() => {
              router.push(`/chat/${completedId}`);
            });
          },
        },
      });
      if (prefs.notifySound) playPing();
      return;
    }

    // Hidden → tab title, favicon, OS notification + custom ping.
    unreadRef.current += 1;
    document.title = `(${unreadRef.current}) ${BASE_TITLE}`;
    setFaviconDot();

    const permission = getPermission();
    if (permission === 'granted') {
      // Suppress the OS chime (`silent: true`) and play our own /notify.mp3
      // instead. The audio element was primed on first user gesture (see
      // `attachPrimeListeners`), so `play()` works even when the tab is
      // hidden or the browser is minimized.
      notify('Response ready', {
        body: noticeBody,
        threadId: completedId,
        // Always suppress the OS chime so we don't double up. When the user
        // has sound on, we play /notify.mp3 ourselves; when off, no sound.
        silent: true,
      });
      if (prefs.notifySound) playPing();
    } else if (permission === 'default' && !prefs.softPromptShown) {
      // Soft-prompt at the moment of obvious value. Persist a flag so we
      // never auto-ask again.
      prefs.setSoftPromptShown(true);
      toast.info('Get a notification next time?', {
        description: 'Stay heads-down — we\'ll ping you when answers finish.',
        id: 'notify-soft-prompt',
        duration: 12_000,
        action: {
          label: 'Enable',
          onClick: () => {
            void requestPermission();
          },
        },
      });
    }
    // permission === 'denied' or 'unsupported' → tab title + favicon are
    // the best we can do; nothing more to surface.
  }, [isStreaming, pathname, router]);

  // Prime the audio element on the first user gesture so /notify.mp3 can
  // play even when the tab is hidden later. Must mount once at app startup.
  useEffect(() => {
    attachPrimeListeners();
  }, []);

  // Reset badges when the user returns to the tab.
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const reset = () => {
      if (document.visibilityState === 'visible' && unreadRef.current > 0) {
        unreadRef.current = 0;
        document.title = BASE_TITLE;
        clearFaviconDot();
      }
    };
    document.addEventListener('visibilitychange', reset);
    window.addEventListener('focus', reset);
    return () => {
      document.removeEventListener('visibilitychange', reset);
      window.removeEventListener('focus', reset);
    };
  }, []);

  // Click-through from OS notifications. The Notification.onclick handler
  // dispatches this CustomEvent because it runs outside React; we hop back
  // in here to do the route push with startTransition.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    const onClick = (e: Event) => {
      const detail = (e as CustomEvent<{ threadId: string }>).detail;
      if (!detail?.threadId) return;
      startTransition(() => {
        router.push(`/chat/${detail.threadId}`);
      });
    };
    window.addEventListener('mti-brain:notification-click', onClick);
    return () => window.removeEventListener('mti-brain:notification-click', onClick);
  }, [router]);
}
