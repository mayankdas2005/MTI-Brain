'use client';

/**
 * Web Notifications API wrapper.
 *
 * Different from the Web Push API - this fires OS-native notifications from
 * the page, no service worker / VAPID / backend required. Works as long as
 * the browser process is alive (covers "user is in another tab" and "user
 * switched to VS Code"). Stops working when the browser is fully closed -
 * that's the long-tail Web Push case, deferred.
 *
 * All entry points are SSR-safe.
 */

const NOTIFICATION_TAG = 'mti-brain-completion';

export type NotificationPermissionState =
  | 'default'
  | 'granted'
  | 'denied'
  | 'unsupported';

export function notificationsSupported(): boolean {
  return typeof window !== 'undefined' && 'Notification' in window;
}

export function getPermission(): NotificationPermissionState {
  if (!notificationsSupported()) return 'unsupported';
  return Notification.permission as NotificationPermissionState;
}

/**
 * Wrap `Notification.requestPermission` so callers don't have to handle
 * both the legacy callback form and the modern Promise form. Resolves to
 * the final permission state.
 */
export async function requestPermission(): Promise<NotificationPermissionState> {
  if (!notificationsSupported()) return 'unsupported';
  // Some older browsers still use the callback signature.
  const result = await new Promise<NotificationPermission>((resolve) => {
    const ret = Notification.requestPermission((perm) => resolve(perm));
    if (ret && typeof (ret as Promise<NotificationPermission>).then === 'function') {
      (ret as Promise<NotificationPermission>).then(resolve);
    }
  });
  return result as NotificationPermissionState;
}

interface NotifyOptions {
  body?: string;
  /** Conversation/thread to navigate to when the notification is clicked. */
  threadId?: string;
  /** When true, suppresses the OS default notification sound. */
  silent?: boolean;
}

/**
 * Fire a desktop notification. No-ops cleanly when:
 *  - the browser doesn't support notifications,
 *  - permission is anything other than `granted`,
 *  - the page is in an SSR context.
 *
 * Click handler: focuses the window and dispatches a `mti-brain:notification-click`
 * CustomEvent so a React-side listener can route via `router.push()`.
 */
export function notify(title: string, opts: NotifyOptions = {}): void {
  if (getPermission() !== 'granted') return;
  try {
    // `silent` - when false (the default), Windows / macOS plays its built-in
    //   notification chime. This is the ONLY reliable way to get audio when
    //   the browser is minimized or in a background tab - `Audio.play()` is
    //   throttled in hidden tabs, so a page-level <audio> ping won't fire.
    // `requireInteraction: true` - Windows would otherwise auto-dismiss the
    //   toast in ~5s; this keeps it visible until clicked or expired by the
    //   OS notification center.
    // `renotify: true` - forces re-display when a previous notification
    //   with the same `tag` is still on screen (OS would silently coalesce
    //   otherwise, hiding follow-up completions).
    const n = new Notification(title, {
      body: opts.body,
      icon: '/icon-192.png',
      badge: '/icon-192.png',
      tag: NOTIFICATION_TAG,
      silent: opts.silent ?? false,
      requireInteraction: true,
      renotify: true,
    } as NotificationOptions);
    n.onclick = () => {
      try {
        window.focus();
      } catch {
        // some platforms refuse - best effort
      }
      if (opts.threadId) {
        window.dispatchEvent(
          new CustomEvent('mti-brain:notification-click', {
            detail: { threadId: opts.threadId },
          }),
        );
      }
      n.close();
    };
  } catch {
    // Construction can throw on Safari iOS when the page isn't a PWA.
    // Silent fail is correct - caller already has fallback channels.
  }
}
