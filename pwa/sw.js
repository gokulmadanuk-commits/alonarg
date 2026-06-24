/* Alonarg PWA service worker.
 * Precaches the app shell for offline launch + installability.
 * Cache-first for same-origin static assets; cross-origin (the engine API
 * and audio) is ALWAYS network — never cached.
 */
'use strict';

const CACHE = 'alonarg-shell-v4';

const SHELL = [
  './',
  './index.html',
  './app.js',
  './style.css',
  './manifest.webmanifest',
  './icons/icon-192.png',
  './icons/icon-512.png',
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE).then((cache) =>
      // Add resiliently so one missing optional asset (e.g. an icon during
      // first deploy) doesn't abort the whole install.
      Promise.allSettled(SHELL.map((url) => cache.add(url)))
    )
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
      )
      .then(() => self.clients.claim())
  );
});

/* Meeting nudge: show the push notification. */
self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (_) {
    payload = { title: 'Alonarg', body: event.data ? event.data.text() : '' };
  }
  const title = payload.title || 'Alonarg';
  const options = {
    body: payload.body || '',
    icon: './icons/icon-192.png',
    badge: './icons/icon-192.png',
    data: { url: payload.url || './' },
    tag: 'alonarg-nudge',
    renotify: true,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

/* Tapping the nudge focuses (or opens) the app. */
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = (event.notification.data && event.notification.data.url) || './';
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clients) => {
      for (const c of clients) {
        if ('focus' in c) return c.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow(target);
    })
  );
});

self.addEventListener('fetch', (event) => {
  const req = event.request;

  // Only handle GET.
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  // NEVER cache cross-origin requests (engine API + audio). Always network.
  if (url.origin !== self.location.origin) {
    return; // let the browser handle it normally
  }

  // Navigation requests: serve the cached shell so the app launches offline.
  if (req.mode === 'navigate') {
    event.respondWith(
      caches.match('./index.html').then((cached) => cached || fetch(req))
    );
    return;
  }

  // Same-origin static assets: cache-first, then network (and cache the result).
  event.respondWith(
    caches.match(req).then((cached) => {
      if (cached) return cached;
      return fetch(req)
        .then((resp) => {
          if (resp && resp.ok && resp.type === 'basic') {
            const copy = resp.clone();
            caches.open(CACHE).then((cache) => cache.put(req, copy));
          }
          return resp;
        })
        .catch(() => cached);
    })
  );
});
