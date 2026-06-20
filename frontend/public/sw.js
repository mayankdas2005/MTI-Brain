// MTI Brain service worker.
//
// Lightweight by design: just enough to (1) make the PWA installable with
// a working offline shell and (2) stand ready for push notifications when
// backend wiring lands. No precaching of API responses - Turbopack rebuilds
// chunk hashes on every deploy, so aggressive precaching would just stale.
//
// Edit me freely; this file is shipped as-is (not bundled).

/* eslint-disable no-restricted-globals */

const CACHE_VERSION = 'mti-brain-v5';
// Derive the base path from the SW's own URL so we don't hardcode it.
// e.g. /mti-brain/sw.js → "/mti-brain", /sw.js → ""
const BASE_PATH = self.location.pathname.replace(/\/sw\.js$/, '');
const APP_SHELL = [`${BASE_PATH}/`, `${BASE_PATH}/manifest.webmanifest`, `${BASE_PATH}/icon-192.png`, `${BASE_PATH}/favicon.ico`];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_VERSION).then((cache) => cache.addAll(APP_SHELL)).catch(() => undefined),
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_VERSION).map((k) => caches.delete(k))),
    ),
  );
  self.clients.claim();
});

// Network-first for top-level navigations only. Static assets, dev-server
// Turbopack chunks, HMR websocket upgrades, API calls, and SSE streams all
// pass through untouched - we never want a SW caching layer between the
// browser and a streaming endpoint.
self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET') return;
  if (req.mode !== 'navigate') return;

  const url = new URL(req.url);
  // Defense in depth - even if a navigation lands on these paths, never
  // intercept. Covers Next.js dev (Turbopack `_next/`), API routes, and
  // SSE streams that Next 16 occasionally classifies as navigations.
  if (url.pathname.startsWith(`${BASE_PATH}/_next/`)) return;
  if (url.pathname.startsWith(`${BASE_PATH}/api/`)) return;

  event.respondWith(
    fetch(req).catch(() =>
      caches.match(req).then((cached) => cached || caches.match(`${BASE_PATH}/`)),
    ),
  );
});

// Push-notification scaffolding. Wiring deferred to backend; this listener
// is here so the SW is ready the day VAPID + endpoint registration ship.
self.addEventListener('push', (event) => {
  if (!event.data) return;
  let payload = {};
  try {
    payload = event.data.json();
  } catch (e) {
    payload = { body: event.data.text() };
  }
  const title = payload.title || 'MTI Brain';
  const options = {
    body: payload.body,
    icon: `${BASE_PATH}/icon-192.png`,
    badge: `${BASE_PATH}/icon-192.png`,
    data: { url: payload.url || `${BASE_PATH}/` },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || '/';
  event.waitUntil(
    self.clients.matchAll({ type: 'window' }).then((clients) => {
      for (const client of clients) {
        if (client.url.includes(url) && 'focus' in client) return client.focus();
      }
      return self.clients.openWindow(url);
    }),
  );
});
