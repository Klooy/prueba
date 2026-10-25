/* Compartir un producto del menú: abre el menú de compartir del celular (WhatsApp → "Mi estado" o
 * un chat) con el nombre, el precio y el enlace del producto. WhatsApp arma la vista previa con la
 * foto gracias a las etiquetas og: de la página del producto (/p/<id>).
 * Sin Web Share (la mayoría de los computadores) el botón sigue su enlace: WhatsApp con el texto.
 */
(function () {
  "use strict";
  if (!navigator.share) return;

  document.addEventListener("click", function (ev) {
    var btn = ev.target.closest ? ev.target.closest("[data-share]") : null;
    if (!btn) return;
    ev.preventDefault();
    // Precalienta la página y la foto: cuando WhatsApp las pida para la vista previa ya están listas.
    [btn.getAttribute("data-share-url"), btn.getAttribute("data-share-image")].forEach(function (url) {
      if (url) fetch(url, { credentials: "omit" }).catch(function () {});
    });
    navigator.share({
      title: btn.getAttribute("data-share-title"),
      text: btn.getAttribute("data-share-text"),
      url: btn.getAttribute("data-share-url"),
    }).catch(function () { /* cerró el menú de compartir */ });
  });
})();
