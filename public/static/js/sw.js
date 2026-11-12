// Service worker: makes the site installable as an app and shows an offline page when there's no connection.
// Served at /sw.js (by app.py) so it covers the whole site.
//
// Deliberately cautious: pages, quizzes and anything with player data always come from the network and are never
// cached. Only the app's own CSS, JS and icons are kept, and even those are fetched fresh first (their names carry
// no version, so serving a cached copy first would keep an old stylesheet after a deploy).
const CACHE = 'wff-v1';
const OFFLINE_URL = '/offline';
const PRECACHE = [OFFLINE_URL, '/static/css/app.css', '/static/js/app.js', '/static/img/icons/icon-192.png'];

self.addEventListener('install', event => {
    event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(PRECACHE)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', event => {
    event.waitUntil(
        caches.keys()
            .then(keys => Promise.all(keys.filter(key => key !== CACHE).map(key => caches.delete(key))))
            .then(() => self.clients.claim())
    );
});

self.addEventListener('fetch', event => {
    const request = event.request;
    const url = new URL(request.url);
    if (request.method !== 'GET' || url.origin !== self.location.origin) return;

    // Pages: always the network; the offline page only when there's none
    if (request.mode === 'navigate') {
        event.respondWith(fetch(request).catch(() => caches.match(OFFLINE_URL)));
        return;
    }
    // The app's own static files: network first, the saved copy when offline
    if (url.pathname.startsWith('/static/')) {
        event.respondWith(
            fetch(request)
                .then(response => {
                    if (response.ok) {
                        const copy = response.clone();
                        caches.open(CACHE).then(cache => cache.put(request, copy));
                    }
                    return response;
                })
                .catch(() => caches.match(request))
        );
    }
    // Everything else (quiz state, live matches, uploads …) is left to the browser as if there were no worker
});
