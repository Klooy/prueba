/* Reduce una foto en el navegador antes de subirla (lado mayor de `maxSide` px, JPEG).
 * Las fotos del celular pesan varios MB y Vercel no recibe envíos de más de 4,5 MB.
 * shrinkPhoto(file, maxSide) -> Promise con el archivo reducido, o null si no hace falta
 * o el navegador no puede (entonces se sube el original). replaceFile(input, file) lo
 * deja en el campo de archivo.
 */
(function () {
  "use strict";

  window.shrinkPhoto = function (file, maxSide) {
    return new Promise(function (resolve) {
      if (!file || !/^image\/(jpeg|png|webp)$/.test(file.type) ||
          !window.DataTransfer || !HTMLCanvasElement.prototype.toBlob) return resolve(null);
      var img = new Image();
      var url = URL.createObjectURL(file);
      img.onload = function () {
        URL.revokeObjectURL(url);
        var scale = Math.min(1, maxSide / Math.max(img.naturalWidth, img.naturalHeight));
        if (scale === 1 && file.size < 1.5 * 1024 * 1024) return resolve(null);
        var canvas = document.createElement("canvas");
        canvas.width = Math.round(img.naturalWidth * scale);
        canvas.height = Math.round(img.naturalHeight * scale);
        var ctx = canvas.getContext("2d");
        ctx.fillStyle = "#040604";             // fondo para PNG con transparencia
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        canvas.toBlob(function (blob) {
          if (!blob || blob.size >= file.size) return resolve(null);
          resolve(new File([blob], file.name.replace(/\.[^.]+$/, "") + ".jpg", { type: "image/jpeg" }));
        }, "image/jpeg", 0.85);
      };
      img.onerror = function () { URL.revokeObjectURL(url); resolve(null); };
      img.src = url;
    });
  };

  window.replaceFile = function (input, file) {
    var dt = new DataTransfer();
    dt.items.add(file);
    input.files = dt.files;
  };
})();
