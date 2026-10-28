// Mandarake Web 控制台 PWA Service Worker
const CACHE_NAME = 'mandarake-pwa-v1';

self.addEventListener('install', (event) => {
    // 强制跳过等待，立即激活
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    // 立即接管所有客户端页面
    event.waitUntil(self.clients.claim());
});

self.addEventListener('fetch', (event) => {
    // 动态接口与后台事件流始终直通网络，保证数据实时性
    if (event.request.method !== 'GET') return;
    
    // 网络优先策略，同时满足 PWA 安装性校验
    event.respondWith(
        fetch(event.request).catch(() => caches.match(event.request))
    );
});
