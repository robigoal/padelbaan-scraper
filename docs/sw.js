// Service worker: app-schil offline beschikbaar; beschikbaarheid altijd live ophalen.
const CACHE = "padel-v5";
const SHELL = ["./", "index.html", "manifest.webmanifest",
               "icons/icon-192.png", "icons/icon-512.png", "icons/apple-touch-icon.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // Alleen de eigen app-schil uit de cache; data (slots.json, GitHub API) gaat altijd naar het netwerk.
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.endsWith("slots.json")) return;
  // Netwerk eerst, zodat updates direct zichtbaar zijn; cache als je offline bent.
  e.respondWith(
    fetch(e.request)
      .then((r) => { const copy = r.clone(); caches.open(CACHE).then((c) => c.put(e.request, copy)); return r; })
      .catch(() => caches.match(e.request))
  );
});
