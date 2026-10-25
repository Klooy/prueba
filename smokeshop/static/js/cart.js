/* Carrito del menú del día.
 *
 * El pedido vive en el navegador del cliente (localStorage): { "<id de producto>": cantidad }.
 * Precios y nombres salen siempre del menú de hoy (#menu-data), así que si un producto ya no
 * está en el menú se quita del pedido. "Pedir por WhatsApp" envía el pedido al servidor, que lo
 * guarda en el historial del panel con un número y abre el chat de la tienda con el pedido escrito.
 * Para el domicilio se pide el método de pago y la ubicación (GPS o dirección escrita); la foto de
 * la entrada es opcional y se reduce antes de subirla.
 */
(function () {
  "use strict";

  var dataEl = document.getElementById("menu-data");
  var drawer = document.getElementById("cart");
  if (!dataEl || !drawer) return;

  var CART_KEY = "anubis-pedido";
  var TOKEN_KEY = "anubis-pedido-token"; // identifica este carrito: reenviarlo actualiza el mismo pedido
  var PROFILE_KEY = "anubis-cliente";    // { name, code, payment, address }; "Mi código" guarda el código
  var MAX_QTY = 99;

  var products = JSON.parse(dataEl.textContent || "[]");
  var byId = {};
  products.forEach(function (p) { byId[String(p.id)] = p; });

  var fab = document.getElementById("cart-open");
  var overlay = document.querySelector(".cart-overlay");
  var itemsEl = drawer.querySelector("[data-cart-items]");
  var formEl = drawer.querySelector("[data-cart-form]");
  var sendBtn = drawer.querySelector("[data-cart-send]");
  var liveEl = drawer.querySelector("[data-cart-live]");
  var fields = {};
  Array.prototype.forEach.call(drawer.querySelectorAll("[data-cart-field]"), function (el) {
    fields[el.getAttribute("data-cart-field")] = el;
  });
  var payments = drawer.querySelectorAll('input[name="payment"]');
  var geoBtn = drawer.querySelector("[data-geo]");
  var geoStatus = drawer.querySelector("[data-geo-status]");
  var geo = formEl && {
    lat: formEl.querySelector("[data-geo-lat]"),
    lng: formEl.querySelector("[data-geo-lng]"),
    acc: formEl.querySelector("[data-geo-acc]"),
  };
  var photoInput = drawer.querySelector("[data-photo]");
  var photoBox = drawer.querySelector("[data-photo-preview]");

  // ------------------------------------------------------------ almacenamiento
  function read(key) {
    try { return JSON.parse(window.localStorage.getItem(key) || "null"); } catch (e) { return null; }
  }
  function write(key, value) {
    try { window.localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* modo privado */ }
  }
  function forget(key) {
    try { window.localStorage.removeItem(key); } catch (e) { /* modo privado */ }
  }

  var cart = {};
  var dropped = 0;
  var saved = read(CART_KEY) || {};
  Object.keys(saved).forEach(function (id) {
    var qty = parseInt(saved[id], 10);
    if (!(qty > 0)) return;
    if (byId[id]) cart[id] = Math.min(qty, MAX_QTY);
    else dropped += 1;
  });
  if (dropped) save();

  // Un carrito vacío empieza un pedido nuevo la próxima vez.
  function save() {
    write(CART_KEY, cart);
    if (!Object.keys(cart).length) forget(TOKEN_KEY);
  }

  function cartToken() {
    var token = read(TOKEN_KEY);
    if (typeof token !== "string" || !/^[0-9a-f]{32}$/.test(token)) {
      var bytes = new Uint8Array(16);
      window.crypto.getRandomValues(bytes);
      token = Array.prototype.map.call(bytes, function (b) { return ("0" + b.toString(16)).slice(-2); }).join("");
      write(TOKEN_KEY, token);
    }
    return token;
  }

  var profile = read(PROFILE_KEY) || {};
  if (fields.name) fields.name.value = profile.name || "";
  if (fields.code) fields.code.value = profile.code || "";
  if (fields.address) fields.address.value = profile.address || "";
  Array.prototype.forEach.call(payments, function (r) { r.checked = r.value === profile.payment; });

  // ------------------------------------------------------------ cálculos
  function cop(n) {
    return "$" + String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
  }

  function summary() {
    var lines = [], count = 0, sum = 0, pending = false;
    products.forEach(function (p) {
      var qty = cart[String(p.id)];
      if (!qty) return;
      var total = p.price === null ? null : p.price * qty;
      lines.push({ p: p, qty: qty, total: total });
      count += qty;
      if (total === null) pending = true; else sum += total;
    });
    return { lines: lines, count: count, sum: sum, pending: pending };
  }

  function unitText(line) {
    if (line.p.price !== null) return cop(line.p.price) + " c/u";
    return line.p.label || "Precio a confirmar";
  }

  // ------------------------------------------------------------ cambios
  function setQty(id, qty, announce) {
    id = String(id);
    var p = byId[id];
    if (!p) return;
    qty = Math.max(0, Math.min(MAX_QTY, qty));
    var before = cart[id] || 0;
    if (qty) cart[id] = qty; else delete cart[id];
    save();
    render();
    if (announce && liveEl) {
      liveEl.textContent = qty > before
        ? "Agregado: " + p.title + ". Llevas " + qty + "."
        : (qty ? "Quitaste uno: " + p.title + ". Llevas " + qty + "." : "Quitaste " + p.title + " del pedido.");
    }
    if (qty > before && fab) {
      fab.classList.remove("bump");
      void fab.offsetWidth;            // reinicia la animación
      fab.classList.add("bump");
    }
  }

  // ------------------------------------------------------------ pintar
  function icon(name) {
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("aria-hidden", "true");
    var use = document.createElementNS("http://www.w3.org/2000/svg", "use");
    use.setAttribute("href", "#" + name);
    svg.appendChild(use);
    return svg;
  }

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function stepButton(kind, label) {
    var b = el("button");
    b.type = "button";
    b.setAttribute(kind === "inc" ? "data-inc" : "data-dec", "");
    b.setAttribute("aria-label", label);
    b.appendChild(icon(kind === "inc" ? "i-plus" : "i-minus"));
    return b;
  }

  function renderItems(s) {
    itemsEl.textContent = "";
    s.lines.forEach(function (line) {
      var li = el("li", "cart-item");
      li.setAttribute("data-id", String(line.p.id));
      var main = el("div", "ci-main");
      main.appendChild(el("strong", "", line.p.title));
      main.appendChild(el("small", "", unitText(line)));
      var step = el("div", "stepper compact");
      step.appendChild(stepButton("dec", line.qty > 1 ? "Quitar uno: " + line.p.title : "Quitar del pedido: " + line.p.title));
      step.appendChild(el("span", "qty", String(line.qty)));
      step.appendChild(stepButton("inc", "Agregar otro: " + line.p.title));
      li.appendChild(main);
      li.appendChild(step);
      li.appendChild(line.total === null
        ? el("span", "ci-total pending", "Por confirmar")
        : el("span", "ci-total", cop(line.total)));
      itemsEl.appendChild(li);
    });
  }

  function render() {
    var s = summary();
    var empty = s.count === 0;

    Array.prototype.forEach.call(document.querySelectorAll(".add[data-product]"), function (box) {
      var qty = cart[box.getAttribute("data-product")] || 0;
      box.querySelector("[data-add]").hidden = qty > 0;
      box.querySelector("[data-stepper]").hidden = qty === 0;
      box.querySelector("[data-qty]").textContent = String(qty);
      box.classList.toggle("in-cart", qty > 0);
    });

    if (fab) {
      fab.hidden = empty;
      fab.querySelector("[data-cart-count]").textContent = String(s.count);
      fab.querySelector("[data-cart-fab-total]").textContent = s.sum ? cop(s.sum) : "";
      fab.setAttribute("aria-label", "Ver pedido: " + s.count + (s.count === 1 ? " producto" : " productos") +
        (s.sum ? ", " + cop(s.sum) : ""));
    }
    document.documentElement.classList.toggle("has-cart", !empty);

    renderItems(s);
    drawer.querySelector("[data-cart-empty]").hidden = !empty;
    drawer.querySelector("[data-cart-sum-row]").hidden = empty;
    drawer.querySelector("[data-cart-sum]").textContent =
      s.sum ? cop(s.sum) + (s.pending ? " +" : "") : "Por confirmar";
    drawer.querySelector("[data-cart-pending]").hidden = !s.pending || empty;
    drawer.querySelector("[data-cart-fields]").hidden = empty;
    drawer.querySelector("[data-cart-clear]").hidden = empty;
    var note = drawer.querySelector("[data-cart-dropped]");
    note.hidden = !dropped;
    if (dropped) {
      note.textContent = dropped === 1
        ? "Un producto de tu pedido anterior ya no está en el menú de hoy y se quitó."
        : dropped + " productos de tu pedido anterior ya no están en el menú de hoy y se quitaron.";
    }
    if (sendBtn) sendBtn.disabled = empty;
  }

  // ------------------------------------------------------------ abrir / cerrar
  var lastFocus = null;

  function isOpen() { return drawer.classList.contains("open"); }

  function open() {
    lastFocus = document.activeElement;
    drawer.classList.add("open");
    overlay.classList.add("open");
    document.documentElement.classList.add("cart-lock");
    if (fab) fab.setAttribute("aria-expanded", "true");
    drawer.querySelector(".cart-x").focus();
  }

  function close() {
    drawer.classList.remove("open");
    overlay.classList.remove("open");
    document.documentElement.classList.remove("cart-lock");
    if (fab) fab.setAttribute("aria-expanded", "false");
    if (lastFocus && document.contains(lastFocus) && !lastFocus.hidden) lastFocus.focus();
    else if (fab && !fab.hidden) fab.focus();
  }

  function focusables() {
    return Array.prototype.filter.call(
      drawer.querySelectorAll("a[href], button, input, textarea"),
      function (node) { return !node.hidden && !node.disabled && node.type !== "hidden" && node.offsetParent !== null; }
    );
  }

  // Tras redibujar, el foco pasa al botón equivalente para no perderse (teclado / lector de pantalla).
  // Las filas del panel se rehacen en cada cambio, por eso se dice de antemano dónde fue el clic.
  function keepFocus(inDrawer, id, kind) {
    var next = null;
    if (inDrawer) {
      next = itemsEl.querySelector('[data-id="' + id + '"] [data-' + kind + "]") || drawer.querySelector(".cart-x");
    } else {
      var box = document.querySelector('.add[data-product="' + id + '"]');
      next = cart[id] ? box.querySelector("[data-stepper] [data-" + kind + "]") : box.querySelector("[data-add]");
    }
    if (next && next !== document.activeElement) next.focus();
  }

  // ------------------------------------------------------------ eventos
  document.addEventListener("click", function (ev) {
    var t = ev.target.closest ? ev.target.closest("button, a, .cart-overlay") : null;
    if (!t) return;
    var holder = t.closest("[data-product], [data-id]");
    var id = holder ? (holder.getAttribute("data-product") || holder.getAttribute("data-id")) : null;

    if ((t.hasAttribute("data-add") || t.hasAttribute("data-inc") || t.hasAttribute("data-dec")) && id) {
      var kind = t.hasAttribute("data-dec") ? "dec" : "inc";
      var inDrawer = drawer.contains(t);
      setQty(id, (cart[id] || 0) + (kind === "dec" ? -1 : 1), true);
      keepFocus(inDrawer, id, kind);
    } else if (t === fab) {
      open();
    } else if (t.hasAttribute("data-cart-close")) {
      close();
    } else if (t.hasAttribute("data-cart-clear")) {
      if (window.confirm("¿Vaciar todo el pedido?")) {
        cart = {};
        save();
        render();
        var sent = drawer.querySelector("[data-cart-sent]");
        if (sent) sent.hidden = true;
        close();
      }
    }
  });

  // Al enviar: los productos van como "id:cantidad,…"; nombre, código y nota son campos del formulario.
  if (formEl) {
    formEl.addEventListener("submit", function (ev) {
      var s = summary();
      if (!s.count) { ev.preventDefault(); return; }
      var missing = missingDelivery();
      showError("payment", missing.indexOf("payment") >= 0);
      showError("location", missing.indexOf("location") >= 0);
      if (missing.length) {
        ev.preventDefault();
        var group = drawer.querySelector('[data-group="' + missing[0] + '"]');
        group.scrollIntoView({ block: "center", behavior: "smooth" });
        var target = missing[0] === "payment" ? payments[0] : (geoBtn && !geoBtn.hidden ? geoBtn : fields.address);
        if (target) target.focus({ preventScroll: true });
        if (liveEl) liveEl.textContent = missing[0] === "payment" ? "Falta elegir cómo vas a pagar." : "Falta tu ubicación o tu dirección.";
        return;
      }
      formEl.querySelector("[data-cart-order]").value =
        s.lines.map(function (line) { return line.p.id + ":" + line.qty; }).join(",");
      formEl.querySelector("[data-cart-token]").value = cartToken();
      var hint = drawer.querySelector("[data-cart-sent]");
      if (hint) hint.hidden = false;
    });
  }

  drawer.addEventListener("input", function (ev) {
    var key = ev.target.getAttribute("data-cart-field");
    if (!key) return;
    if (key === "name" || key === "code" || key === "payment" || key === "address") {
      profile[key] = key === "code" ? ev.target.value.trim().toUpperCase() : ev.target.value.trim();
      write(PROFILE_KEY, profile);
    }
    if (key === "payment") showError("payment", false);
    if (key === "address" && !missingDelivery().length) showError("location", false);
  });

  // ------------------------------------------------------------ entrega
  function missingDelivery() {
    var missing = [];
    if (payments.length && !drawer.querySelector('input[name="payment"]:checked')) missing.push("payment");
    if (geo && !geo.lat.value && (!fields.address || fields.address.value.trim().length < 6)) missing.push("location");
    return missing;
  }

  function showError(kind, on) {
    var box = drawer.querySelector('[data-error="' + kind + '"]');
    if (box) box.hidden = !on;
  }

  function geoMessage(text, isError, mapUrl) {
    geoStatus.textContent = text;
    geoStatus.classList.toggle("is-error", isError);
    if (mapUrl) {
      var link = el("a", "", "Ver en el mapa");
      link.href = mapUrl;
      link.target = "_blank";
      link.rel = "noopener";
      geoStatus.appendChild(document.createTextNode(" · "));
      geoStatus.appendChild(link);
    }
    geoStatus.hidden = false;
  }

  if (geoBtn) {
    var geoLabel = geoBtn.querySelector("[data-geo-label]");
    if (!navigator.geolocation || !window.isSecureContext) geoBtn.hidden = true;   // queda la dirección
    geoBtn.addEventListener("click", function () {
      geoBtn.disabled = true;
      geoLabel.textContent = "Buscando tu ubicación…";
      navigator.geolocation.getCurrentPosition(function (pos) {
        geo.lat.value = pos.coords.latitude.toFixed(6);
        geo.lng.value = pos.coords.longitude.toFixed(6);
        geo.acc.value = String(Math.round(pos.coords.accuracy || 0));
        geoBtn.disabled = false;
        geoLabel.textContent = "Actualizar mi ubicación";
        geoMessage("✓ Ubicación lista (±" + geo.acc.value + " m).", false,
          "https://www.google.com/maps/search/?api=1&query=" + geo.lat.value + "," + geo.lng.value);
        showError("location", false);
      }, function (err) {
        geoBtn.disabled = false;
        geoLabel.textContent = "Intentar de nuevo";
        geoMessage(err.code === 1
          ? "No diste permiso para usar tu ubicación. Escribe tu dirección abajo o permítela en tu navegador."
          : err.code === 3
            ? "Tu celular tardó mucho en encontrar la ubicación. Intenta de nuevo o escribe tu dirección."
            : "No pudimos encontrar tu ubicación. Escribe tu dirección abajo.", true);
      }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
    });
  }

  var photoUrl = null;
  function showPhoto(file) {
    if (photoUrl) URL.revokeObjectURL(photoUrl);
    photoUrl = file ? URL.createObjectURL(file) : null;
    photoBox.querySelector("[data-photo-img]").src = photoUrl || "";
    photoBox.hidden = !file;
    drawer.querySelector("[data-photo-label]").textContent = file ? "Cambiar foto" : "Agregar foto de la calle o la fachada";
  }
  if (photoInput) {
    photoInput.addEventListener("change", function () {
      var file = photoInput.files[0];
      if (!file) return showPhoto(null);
      if (sendBtn) sendBtn.disabled = true;      // mientras se reduce
      (window.shrinkPhoto ? window.shrinkPhoto(file, 1600) : Promise.resolve(null)).then(function (small) {
        if (small) window.replaceFile(photoInput, small);
        showPhoto(photoInput.files[0]);
        render();
      });
    });
    drawer.querySelector("[data-photo-clear]").addEventListener("click", function () {
      photoInput.value = "";
      showPhoto(null);
    });
  }

  document.addEventListener("keydown", function (ev) {
    if (!isOpen()) return;
    if (ev.key === "Escape") { ev.preventDefault(); close(); return; }
    if (ev.key === "Enter" && ev.target.matches("input[data-cart-field]")) {
      ev.preventDefault();             // Enter no envía el pedido por accidente: pasa al siguiente campo
      var order = focusables();
      var next = order[order.indexOf(ev.target) + 1];
      if (next) next.focus();
      return;
    }
    if (ev.key !== "Tab") return;
    var list = focusables();
    if (!list.length) return;
    var first = list[0], last = list[list.length - 1];
    if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
  });

  render();
})();
