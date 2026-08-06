var CACHE_NAME = "llm-bench-v1";
var STATIC_ASSETS = [
  "/help",
  "/vendor/tabulator/tabulator.min.js",
  "/vendor/tabulator/tabulator.min.css"
];

self.addEventListener("install", function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return Promise.all(
        STATIC_ASSETS.map(function(url) {
          return cache.add(url).catch(function() {
            console.warn("SW: failed to cache " + url);
          });
        })
      );
    })
  );
  self.skipWaiting();
});

self.addEventListener("activate", function(event) {
  event.waitUntil(
    caches.keys().then(function(keys) {
      return Promise.all(
        keys.filter(function(k) { return k !== CACHE_NAME; })
            .map(function(k) { return caches.delete(k); })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener("fetch", function(event) {
  if (event.request.method !== "GET") return;
  event.respondWith(
    caches.match(event.request).then(function(response) {
      return response || fetch(event.request).then(function(network) {
        if (network && network.status === 200) {
          var clone = network.clone();
          caches.open(CACHE_NAME).then(function(cache) {
            cache.put(event.request, clone);
          });
        }
        return network;
      }).catch(function() {
        return caches.match("/help").then(function(fallback) {
          return fallback || new Response("Offline — no cached data available", {
            status: 503,
            statusText: "Service Unavailable"
          });
        });
      });
    })
  );
});
