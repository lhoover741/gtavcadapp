const CACHE_NAME = "gtavcad-mobile-v1";
const APP_ASSETS = [
  "/",
  "/index.html",
  "/rules.html",
  "/police.html",
  "/dmv.html",
  "/businesses.html",
  "/applications.html",
  "/donations.html",
  "/complaints.html",
  "/assets/css/style.css",
  "/assets/css/gtavcad-branding.css",
  "/assets/css/mobile.css",
  "/assets/css/push-notifications.css",
  "/assets/js/main.js",
  "/assets/js/mobile.js",
  "/assets/js/push-notifications.js",
  "/assets/images/gtavcad-logo.png",
  "/assets/icons/icon-192.png",
  "/assets/icons/icon-512.png",
  "/manifest.json"
];

self.addEventListener("install", function(event) {
  event.waitUntil(
    caches.open(CACHE_NAME).then(function(cache) {
      return Promise.allSettled(
        APP_ASSETS.map(function(url) {
          return cache.add(url).catch(function() {});
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
        keys.filter(function(key) {
          return key !== CACHE_NAME;
        }).map(function(key) {
          return caches.delete(key);
        })
      );
    })
  );
  self.clients.claim();
});

self.addEventListener("fetch", function(event) {
  if (event.request.method !== "GET") return;
  if (event.request.url.includes("/api/")) return;
  event.respondWith(
    caches.match(event.request).then(function(cached) {
      if (cached) return cached;
      return fetch(event.request).then(function(response) {
        if (!response || response.status !== 200 || response.type === "opaque") {
          return response;
        }
        var responseToCache = response.clone();
        caches.open(CACHE_NAME).then(function(cache) {
          cache.put(event.request, responseToCache);
        });
        return response;
      }).catch(function() {
        return caches.match("/index.html");
      });
    })
  );
});
