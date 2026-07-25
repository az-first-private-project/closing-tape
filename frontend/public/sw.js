// GFNA Service Worker — 离线缓存
const CACHE_NAME = 'gfna-v2-cache-v1'
const urlsToCache = [
  '/',
  '/index.html',
]

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(urlsToCache))
  )
})

self.addEventListener('fetch', (event) => {
  event.respondWith(
    caches.match(event.request).then((response) => {
      // 缓存命中，返回缓存
      if (response) return response
      // 否则走网络请求
      return fetch(event.request)
    })
  )
})
