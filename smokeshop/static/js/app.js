/* App instalable (Anubis Shop y Anubis Panel): registra el service worker y muestra cómo instalarla.
   - Android y otros navegadores que lo permiten: botón "Instalar" (evento beforeinstallprompt).
   - iPhone: la guía de Safari (Compartir → Agregar a pantalla de inicio), porque Apple no deja botón.
   - Ya instalada (abierta desde la pantalla de inicio): no se ofrece nada. */
(function () {
  "use strict";
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () { /* sin service worker: igual funciona */ });
    });
  }

  var boxes = Array.prototype.slice.call(document.querySelectorAll("[data-install]"));
  var installed = window.matchMedia("(display-mode: standalone)").matches || navigator.standalone === true;
  if (!boxes.length || installed) return;

  var ios = /iphone|ipad|ipod/i.test(navigator.userAgent) ||
            (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);   // iPad con iPadOS
  var KEY = "anubis-app-ahora-no-";
  var DAYS_30 = 30 * 24 * 3600 * 1000;
  var deferred = null;

  function dismissed(box) {
    if (!box.hasAttribute("data-install-dismissible")) return false;
    try { return Date.now() - Number(localStorage.getItem(KEY + box.getAttribute("data-install")) || 0) < DAYS_30; }
    catch (e) { return false; }
  }

  // mode: "button" (instalar con un toque), "ios" (guía de Safari) u "other" (guía general).
  function show(mode) {
    boxes.forEach(function (box) {
      if (dismissed(box) || (mode === "other" && box.hasAttribute("data-install-dismissible"))) return;
      box.querySelectorAll("[data-install-mode]").forEach(function (el) {
        el.hidden = el.getAttribute("data-install-mode") !== mode;
      });
      box.hidden = false;
    });
  }

  function hideAll() { boxes.forEach(function (box) { box.hidden = true; }); }

  window.addEventListener("beforeinstallprompt", function (event) {
    event.preventDefault();          // sin la barra automática del navegador: la abre el botón
    deferred = event;
    show("button");
  });
  window.addEventListener("appinstalled", hideAll);

  boxes.forEach(function (box) {
    var install = box.querySelector("[data-install-btn]");
    if (install) install.addEventListener("click", function () {
      if (!deferred) return;
      var event = deferred;
      deferred = null;
      event.prompt();
      event.userChoice.then(function (choice) {
        if (choice && choice.outcome === "accepted") return hideAll();
        // Dijo que no: el aviso se va y la tarjeta fija explica cómo instalarla después.
        boxes.forEach(function (b) { if (b.hasAttribute("data-install-dismissible")) b.hidden = true; });
        show("other");
      });
    });
    var close = box.querySelector("[data-install-close]");
    if (close) close.addEventListener("click", function () {
      box.hidden = true;
      try { localStorage.setItem(KEY + box.getAttribute("data-install"), String(Date.now())); } catch (e) { /* modo privado */ }
    });
  });

  if (ios) {
    show("ios");
  } else {
    // Si el navegador no ofrece instalar (otro navegador, o la app ya está instalada), la tarjeta fija
    // explica cómo hacerlo desde su menú; el aviso del menú no sale.
    window.addEventListener("load", function () {
      setTimeout(function () { if (!deferred) show("other"); }, 2500);
    });
  }
})();
