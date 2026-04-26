// Service Worker dla Akces Hub PWA
const CACHE_NAME = 'akces-hub-v14';

// Zasoby do cache'owania
const CACHE_ASSETS = [
  '/',
  '/magazyn',
  '/paletomat',
  '/narzedzia',
  '/analytics/dashboard',
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
    .then(() => {
      // Force reload wszystkich otwartych kart żeby wyrzucic stary kod ze starym SW
      return self.clients.matchAll({type: 'window'}).then((clients) => {
        clients.forEach((client) => {
          if (client.url && 'navigate' in client) {
            client.navigate(client.url).catch(() => {});
          }
        });
      });
    })
  );
});

// Fetch - Network First z cache (bez modyfikacji headerow — Cloudflare nie potrzebuje)
self.addEventListener('fetch', (event) => {
  // Przepuść external URLs (Google Fonts, CDN, etc.) BEZ modyfikacji
  if (!event.request.url.startsWith(self.location.origin)) {
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
            // Brak w cache — serwuj offline.html (przekieruje na ngrok URL jeśli znany)
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
