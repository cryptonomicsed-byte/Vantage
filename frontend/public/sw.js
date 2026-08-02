// Vantage service worker -- minimal by design. This is what makes the
// "Install App" prompt (beforeinstallprompt) eligible in the first place;
// browsers require a registered SW with a fetch handler before they'll
// ever fire that event, regardless of how good the manifest is.
//
// Strategy: cache only the static app shell (built JS/CSS/icons), never
// /api/* or /ws*/* (those are always live data, caching them would show
// stale prices/chat/trades) and never anything cross-origin (fonts,
// video.js CDN). Navigation requests are network-first with a cached
// index.html fallback so a reload while offline doesn't hard-fail.

const CACHE_NAME = "vantage-shell-v1";

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.add("/").catch(() => {}))
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

function isShellAsset(url) {
  return (
    url.origin === self.location.origin &&
    (url.pathname.startsWith("/assets/") ||
      url.pathname.startsWith("/icons/") ||
      url.pathname === "/manifest.webmanifest")
  );
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);
  if (url.origin !== self.location.origin) return; // fonts, video.js CDN -- leave to the network untouched
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/ws")) return; // always live

  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req).catch(() => caches.match("/").then((r) => r || fetch(req)))
    );
    return;
  }

  if (isShellAsset(url)) {
    event.respondWith(
      caches.match(req).then(
        (cached) =>
          cached ||
          fetch(req).then((res) => {
            const copy = res.clone();
            caches.open(CACHE_NAME).then((cache) => cache.put(req, copy));
            return res;
          })
      )
    );
  }
});
