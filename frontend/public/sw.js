/**
 * Service worker for the AI Guide application shell.
 *
 * Strategy overview
 * ─────────────────
 * • Navigation (HTML)     → NetworkFirst with cache fallback
 *                           (guarantees fresh shell while surviving offline)
 * • Static assets         → CacheOnly
 *                           (everything the shell needs is pre-cached at install)
 * • API / map tiles       → NetworkOnly, never cached
 *                           (tourist must never see yesterday's route as today's)
 *
 * Versioning
 * ─────────
 * CACHE_NAME is set at build time by sw-vite-plugin (timestamp-based).
 * Version format: "sw-shell-v{unix-timestamp}".
 *
 * Update flow
 * ───────────
 * When a new SW activates, it deletes the OLD cache.
 * The page is NOT immediately reloaded — the tourist finishes their walk
 * on the old shell. On the NEXT visit the fresh shell is used.
 * This avoids interrupting a live tour mid-walk.
 *
 * Cache key format
 * ────────────────
 * Keys are prefixed with BASE_URL so the same browser can serve the app
 * from two different base paths (e.g. PR preview + production) without
 * cache collisions.
 */

// Injected by sw-vite-plugin at build time. The placeholders are BARE here — no
// surrounding quotes — because the plugin substitutes them with their JSON
// forms. Quoting them as well produced `baseUrl: '"/"'`: a string containing
// literal quotes, which broke every URL the worker built.
const SW_CONFIG = {
  baseUrl: __SW_BASE_URL__,
  cacheName: __SW_CACHE_NAME__,
  noCachePrefixes: [
    // Grodno agent — more-specific first so /routes matches before /route
    '/routes/generate',
    '/routes',
    // Valhalla endpoints
    '/route',
    '/isochrone',
    '/optimized_route',
    '/status',
    '/locate',
    '/height',
    '/tile/',
    '/tile',
    // Anonymous client entity
    '/clients',
  ],
};

// Precache list — injected by sw-vite-plugin at build time
const PRE_CACHE_URLS = __SW_PRECACHE_URLS__;

// ─── Helpers ─────────────────────────────────────────────────────────────────

/**
 * Whether a URL matches any no-cache prefix.
 * Returns the matching prefix (string) or null.
 */
function matchNoCache(url) {
  let pathname;
  try {
    pathname = url.startsWith('http')
      ? new URL(url).pathname
      : url.split('?')[0];
  } catch {
    pathname = url.split('?')[0];
  }

  for (const prefix of SW_CONFIG.noCachePrefixes) {
    if (pathname.startsWith(prefix) || pathname === prefix) return prefix;
  }
  return null;
}

/**
 * Resolves a URL relative to the configured base URL.
 */
function resolveUrl(url) {
  try {
    return new URL(url, self.location.origin + SW_CONFIG.baseUrl).href;
  } catch {
    return url;
  }
}

/**
 * Determines the cache strategy for a request.
 * Returns 'skip' | 'shell' | 'static'.
 */
function getStrategy(request) {
  const url = request.url;

  if (matchNoCache(url) !== null) return 'skip';

  if (request.mode === 'navigate' || request.destination === 'document') {
    return 'shell';
  }

  const dest = request.destination;
  if (
    dest === 'script' ||
    dest === 'style' ||
    dest === 'font' ||
    dest === 'image' ||
    dest === 'manifest'
  ) {
    return 'static';
  }

  return 'skip';
}

// ─── Cache strategies ───────────────────────────────────────────────────────

/**
 * CacheOnly — for static assets.
 * If not in cache, falls through to the network.
 */
async function strategyCacheOnly(request) {
  const cache = await caches.open(SW_CONFIG.cacheName);
  const cached = await cache.match(request.url);
  if (cached) return cached;
  return fetch(request);
}

/**
 * NetworkFirst with cache fallback — for navigation requests.
 */
async function strategyNetworkFirstWithFallback(request) {
  const cache = await caches.open(SW_CONFIG.cacheName);

  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request.url, response.clone());
    return response;
  } catch {
    const cached = await cache.match(request);
    if (cached) return cached;

    // Nothing cached — return a minimal offline page
    return new Response(
      `<!DOCTYPE html>
<html lang="ru">
<head><meta charset="utf-8" /><title>Нет соединения — AI-гид</title>
<style>
  body { font-family: system-ui, sans-serif; display: flex; flex-direction: column;
    align-items: center; justify-content: center; height: 100vh; margin: 0;
    background: #f9fafb; color: #374151; text-align: center; padding: 1rem; }
  h1 { color: #111827; margin-bottom: 0.5rem; }
  p  { color: #6b7280; max-width: 24rem; }
</style>
</head>
<body><h1>Нет соединения</h1>
<p>Проверьте интернет и попробуйте ещё раз.</p>
</body></html>`,
      { headers: { 'Content-Type': 'text/html; charset=utf-8' }, status: 503 }
    );
  }
}

// ─── Lifecycle ───────────────────────────────────────────────────────────────

/**
 * Install: pre-cache every shell asset.
 */
async function onInstall(event) {
  const cache = await caches.open(SW_CONFIG.cacheName);

  await Promise.allSettled(
    PRE_CACHE_URLS.map(async (url) => {
      try {
        const fullUrl = resolveUrl(url);
        const response = await fetch(fullUrl);
        if (response.ok) await cache.put(fullUrl, response);
      } catch {
        console.warn(`[SW] Could not pre-cache: ${url}`);
      }
    })
  );

  event.waitUntil(self.skipWaiting());
}

/**
 * Activate: claim all clients, delete old caches.
 */
async function onActivate(event) {
  const currentCacheNames = [SW_CONFIG.cacheName];

  event.waitUntil(
    (async () => {
      const allCacheNames = await caches.keys();
      await Promise.all(
        allCacheNames
          .filter((name) => !currentCacheNames.includes(name))
          .map((name) => caches.delete(name))
      );
      await self.clients.claim();
    })()
  );
}

/**
 * Fetch: apply the appropriate strategy based on request type.
 */
async function onFetch(event) {
  const request = event.request;
  const url = request.url;

  // Skip cross-origin requests (external CDN, tile servers, etc.)
  const isSameOrigin =
    url.startsWith(self.location.origin) || url.startsWith('/');
  if (!isSameOrigin) return;

  const strategy = getStrategy(request);

  switch (strategy) {
    case 'shell':
      event.respondWith(strategyNetworkFirstWithFallback(request));
      break;
    case 'static':
      event.respondWith(strategyCacheOnly(request));
      break;
    // 'skip' → do nothing, let the browser handle it (network only)
  }
}

// ─── Service Worker events ────────────────────────────────────────────────────

self.addEventListener('install', onInstall);
self.addEventListener('activate', onActivate);
self.addEventListener('fetch', onFetch);

// Handle SKIP_WAITING message (sent by the app when user accepts update)
self.addEventListener('message', (event) => {
  if (event.data?.type === 'SKIP_WAITING') {
    self.skipWaiting();
  }
});
