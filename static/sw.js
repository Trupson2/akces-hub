// Service Worker dla Akces Hub PWA
// FIX 2026-05 (v15): NIE precache'owac HTML routes (/, /magazyn, /paletomat...)
// — Flask za proxy redirectuje je na http:// (Mixed Content blocked na HTTPS)
// ORAZ cache'owanie HTML powodowalo serwowanie STAREJ strony po deployu
// ("naprawiam, a na Pi widac stare"). Cache TYLKO prawdziwe statyczne assety.
const CACHE_NAME = 'akces-hub-v15';

// Zasoby do cache'owania — WYLACZNIE statyczne (zero HTML/nawigacji)
const CACHE_ASSETS = [
  '/static/manifest.json',
  '/static/offline.html'
];

// Odbierz wiadomość od strony (skipWaiting)
self.addEventListener('message', (event) => {
  if (event.data && event.data.action === 'skipWaiting') {
    self.skipWaiting();
  }
});

// Instalacja - cache assets individually (addAll fails if ANY request fails)
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        console.log('Cache opened');
        return Promise.allSettled(
          CACHE_ASSETS.map((url) =>
            cache.add(url).catch((e) => console.log('Cache skip:', url))
          )
        );
      })
      .then(() => self.skipWaiting())
  );
});

// Aktywacja - czyszczenie starych cache'y + wymuszenie reload otwartych kart
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            console.log('Deleting old cache:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => self.clients.claim())
    // v1.0.109 FIX: USUNIETO force client.navigate() reload wszystkich kart.
    // Powodowal PETLE odswiezania: activate -> navigate karty -> reg.update()
    // -> install -> activate -> navigate -> ... bez konca. Adrian: "caly czas
    // cos odswieza". Nowy SW przejmie kontrole przy nastepnej naturalnej
    // nawigacji (clients.claim wystarcza) - bez wymuszania reload.
  );
});

// Fetch - Network First z cache (bez modyfikacji headerow — Cloudflare nie potrzebuje)
self.addEventListener('fetch', (event) => {
  // Przepuść external URLs (Google Fonts, CDN, etc.) BEZ modyfikacji
  if (!event.request.url.startsWith(self.location.origin)) {
    return;
  }

  // FIX 2026-05 (v15): nawigacja HTML = ZAWSZE z sieci, NIGDY z cache.
  // Eliminuje: (1) serwowanie starej strony po deployu, (2) Mixed Content
  // gdy cache trzyma http:// redirect. Offline.html tylko gdy brak sieci.
  if (event.request.mode === 'navigate') {
    event.respondWith(
      fetch(event.request).catch(() =>
        caches.match('/static/offline.html').then(
          (p) => p || new Response('Offline', { status: 503 })
        )
      )
    );
    return;
  }

  // POST, PUT, DELETE i API — przepuść bez cache
  if (event.request.method !== 'GET' ||
      event.request.url.includes('/api/') ||
      event.request.url.includes('/sprzedaze/') ||
      event.request.url.includes('/produkt/') ||
      event.request.url.includes('/analityka/') ||
      event.request.url.includes('/winning/')) {
    return;
  }

  event.respondWith(
    fetch(event.request)
      .then((response) => {
        // Zapisz do cache jeśli sukces
        if (response.status === 200) {
          const responseClone = response.clone();
          caches.open(CACHE_NAME).then((cache) => {
            cache.put(event.request, responseClone);
          });
        }
        return response;
      })
      .catch(() => {
        // Offline - użyj cache
        return caches.match(event.request, {ignoreSearch: true})
          .then((cachedResponse) => {
            if (cachedResponse) {
              return cachedResponse;
            }
            // Brak w cache — serwuj offline.html (przekieruje na zapisany adres aplikacji)
            return caches.match('/static/offline.html')
              .then(function(offlinePage){
                return offlinePage || new Response('Offline', {status: 503});
              });
          });
      })
  );
});

// Push notifications
self.addEventListener('push', (event) => {
  const options = {
    body: event.data ? event.data.text() : 'Nowa sprzedaż!',
    icon: '/static/icon-192.png',
    badge: '/static/icon-192.png',
    vibrate: [200, 100, 200],
    tag: 'akces-notification'
  };
  
  event.waitUntil(
    self.registration.showNotification('🛒 Akces Hub', options)
  );
});

// Click na notyfikację
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    clients.openWindow('/')
  );
});
