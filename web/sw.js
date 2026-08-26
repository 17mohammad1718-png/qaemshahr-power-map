/* sw.js — نقشه قطعی برق قائم‌شهر
   استراتژی:
   - صفحات (navigate): network-first + fallback به نسخه کش‌شده = داده همیشه تازه، آفلاین = آخرین صفحه
   - snapshot.json: stale-while-revalidate = نمایش فوری از کش + به‌روزرسانی بی‌صدا در پس‌زمینه
   - استاتیک‌های خودِ سایت و CDN (leaflet/فونت): cache-first
*/
const CACHE = "qaem-power-v2";
const PRECACHE = [
  "./",
  "index.html",
  "manifest.json",
  "icons/icon-144x144.png",
  "icons/icon-192x192.png",
  "icons/icon-512x512.png",
  "icons/icon-maskable-192x192.png",
  "icons/icon-maskable-512x512.png"
];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(CACHE).then((cache) =>
      Promise.all(PRECACHE.map((u) =>
        cache.add(new Request(u, { cache: "reload" })).catch(() => {})
      ))
    )
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((names) =>
      Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)))
    ).then(() => self.clients.claim())
  );
});

/* پاسخ کش‌شده را با نشان X-From-Cache برمی‌گرداند تا UI بتواند «آفلاین/کش» را تفکیک کند
   (body استریم یک‌بارمصرف است؛ متن را می‌خوانیم و Response نو می‌سازیم) */
async function stampCached(cached){
  if (!cached) return cached;
  const body = await cached.text();
  const h = new Headers(cached.headers);
  h.set("X-From-Cache", "1");
  return new Response(body, { status: cached.status, statusText: cached.statusText, headers: h });
}

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  let url;
  try { url = new URL(req.url); } catch { return; }
  if (!/^https?:$/.test(url.protocol)) return;
  if (url.pathname.includes("/admin") || url.pathname.endsWith(".php")) return;

  /* ---- snapshot.json: stale-while-revalidate ---- */
  if (url.pathname.endsWith("/snapshot.json")) {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE);
      const cached = await cache.match("snapshot.json");
      const netPromise = fetch(req).then((r) => {
        if (r && r.ok) cache.put("snapshot.json", r.clone());
        return r;
      }).catch(() => null);
      if (cached) {
        netPromise.catch(() => {});           /* به‌روزرسانی پس‌زمینه؛ خطایش بی‌صدا */
        return stampCached(cached);
      }
      const fresh = await netPromise;          /* اولین بار: فقط شبکه */
      return fresh || new Response(
        JSON.stringify({ offline: true, error: "no snapshot cached" }),
        { status: 503, headers: { "Content-Type": "application/json" } }
      );
    })());
    return;
  }

  /* ---- ناوبری صفحه: network-first + fallback کش ---- */
  if (req.mode === "navigate") {
    event.respondWith((async () => {
      const cache = await caches.open(CACHE);
      try {
        const fresh = await fetch(req, { cache: "no-store" });
        if (fresh && fresh.ok) cache.put("index.html", fresh.clone());
        return fresh;
      } catch {
        return (await cache.match("index.html")) ||
               (await cache.match("./")) ||
               new Response("<h1 dir=rtl>آفلاین</h1><p>این صفحه هنوز کش نشده است.</p>",
                 { status: 503, headers: { "Content-Type": "text/html; charset=utf-8" } });
      }
    })());
    return;
  }

  /* ---- استاتیک خودم + jsdelivr: cache-first ---- */
  const isStatic = url.origin === self.location.origin || url.hostname === "cdn.jsdelivr.net";
  if (isStatic) {
    event.respondWith((async () => {
      const hit = await caches.match(req, { ignoreSearch: true });
      if (hit) return hit;
      try {
        const r = await fetch(req);
        if (r && (r.ok || r.type === "opaque")) {
          const cache = await caches.open(CACHE);
          cache.put(req, r.clone());
        }
        return r;
      } catch {
        return new Response("offline", { status: 504, statusText: "Offline" });
      }
    })());
  }
});
