// Service Worker dla Akces Hub PWA
const CACHE_NAME = 'akces-hub-v1';
const OFFLINE_URL = '/offline';

// Zasoby do cache'owania
const CACHE_ASSETS = [
  '/',
  '/magazyn',
  '/paletomat',
  '/narzedzia',
  '/analytics/dashboard',
  '/static/manifest.json'
];

// Instalacja
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => {
        console.log('📦 Cache opened');
        return cache.addAll(CACHE_ASSETS);
      })
      .then(() => self.skipWaiting())
  );
});

// Aktywacja - czyszczenie starych cache'y
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((cacheNames) => {
      return Promise.all(
        cacheNames.map((cacheName) => {
          if (cacheName !== CACHE_NAME) {
            console.log('🗑️ Deleting old cache:', cacheName);
            return caches.delete(cacheName);
          }
        })
      );
    }).then(() => self.clients.claim())
  );
});

// Fetch - strategia Network First z fallback do cache
self.addEventListener('fetch', (event) => {
  // Pomijamy POST requesty i API calls
  if (event.request.method !== 'GET' || 
      event.request.url.includes('/api/')) {
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
        return caches.match(event.request)
          .then((cachedResponse) => {
            if (cachedResponse) {
              return cachedResponse;
            }
            // Brak w cache - pokaż stronę offline
            return caches.match(OFFLINE_URL);
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
