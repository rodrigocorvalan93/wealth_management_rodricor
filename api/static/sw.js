/* Service Worker simple: cache-first para shell, network-first para API.
   IMPORTANTE: bumpear VERSION cada vez que se modifica app.js o style.css
   para forzar a clientes existentes a re-descargar. */
const VERSION = "wm-v22";
const SHELL = [
  "/",
  "/static/style.css",
  "/static/app.js",
  "/static/icon.svg",
  "/static/manifest.json",
  "https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(VERSION).then((c) => c.addAll(SHELL)));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== VERSION).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);

  // Llamadas al API: network-first, sin cache (datos siempre frescos)
  if (url.pathname.startsWith("/api/")) {
    return; // dejar que el navegador haga la request normal
  }

  // Shell estático: cache-first
  e.respondWith(
    caches.match(e.request).then((hit) =>
      hit || fetch(e.request).then((res) => {
        if (res.ok && (url.origin === self.location.origin ||
                        url.host.includes("jsdelivr"))) {
          const copy = res.clone();
          caches.open(VERSION).then((c) => c.put(e.request, copy));
        }
        return res;
      }).catch(() => caches.match("/"))
    )
  );
});
