// Klangkiste – Service Worker. Zeigt eine freundliche Seite, wenn die Box nicht
// erreichbar ist. Alles andere kommt immer frisch von der Box (kein Zwischenspeicher).
const OFFLINE = "/static/offline.html";
self.addEventListener("install", (e) => {
  e.waitUntil(caches.open("klangkiste-v1").then((c) => c.addAll([OFFLINE, "/static/icon.svg"])));
  self.skipWaiting();
});
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));
self.addEventListener("fetch", (e) => {
  if (e.request.mode === "navigate") {
    e.respondWith(fetch(e.request).catch(() => caches.match(OFFLINE)));
  }
});
