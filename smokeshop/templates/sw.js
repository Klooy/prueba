/* Service worker de Anubis Shop y Anubis Panel (versión {{ version }}).
   Guarda solo el diseño (estilos, scripts, letras e íconos) y la página "Sin conexión": las páginas
   con datos (menú, pedidos, panel) siempre vienen de internet y nunca quedan en el celular. */
const CACHE = "anubis-{{ version }}";
const FILES = {{ files|tojson }};
const OFFLINE = {{ offline|tojson }};

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll([...FILES, OFFLINE])).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  // Borra lo que guardaron las versiones anteriores.
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((key) => key.startsWith("anubis-") && key !== CACHE)
        .map((key) => caches.delete(key))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (request.mode === "navigate") {
    // Páginas: de internet; sin conexión, el aviso.
    event.respondWith(fetch(request).catch(() => caches.match(OFFLINE)));
    return;
  }
  if (url.pathname.startsWith("/static/")) {
    // Diseño: lo guardado, si es esta misma versión; si no, de internet (y no se guarda).
    event.respondWith(caches.match(request).then((saved) => saved || fetch(request)));
  }
});
