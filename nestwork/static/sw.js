// Nestwork's service worker: lets the installed app open without a connection to the Mac (showing what it
// last saw, with a "can't reach your Mac" banner) and makes repeat launches fast. Network first, always:
// the cache is only a fallback, so updates show up straight away. API calls are never cached.
const CACHE = 'nestwork-shell-v4';
const SHELL = ['/', '/manifest.webmanifest', '/static/vendor/tailwindcss-3.4.17.js', '/static/app.js', '/static/board.js',
  '/static/editor.js', '/static/settings.js', '/static/awaken.js', '/static/chat.js', '/static/hud.js', '/static/focusview.js',
  '/static/review.js', '/static/agentwork.js', '/static/sounds.js', '/static/notesview.js', '/static/attachments.js', '/static/gallery.js', '/static/spotlight.js', '/static/calendar.js',
  '/static/contextmenu.js', '/static/mobile.js', '/static/icons/icon-180.png', '/static/icons/icon-192.png'];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(CACHE).then(c => c.addAll(SHELL).catch(() => {})).then(() => self.skipWaiting()));
});
self.addEventListener('activate', e => {
  e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim()));
});
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  e.respondWith((async () => {
    try {
      const res = await fetch(e.request);
      if (res.ok && (url.pathname === '/' || url.pathname.startsWith('/static/'))) {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(e.request.mode === 'navigate' ? '/' : e.request, copy));
      }
      return res;
    } catch {
      const hit = await caches.match(e.request.mode === 'navigate' ? '/' : e.request, { ignoreSearch: true });
      return hit || new Response('Offline', { status: 503, headers: { 'Content-Type': 'text/plain' } });
    }
  })());
});
