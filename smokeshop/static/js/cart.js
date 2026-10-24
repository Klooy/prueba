/* Carrito del menú del día.
 *
 * El pedido vive en el navegador del cliente (localStorage): { "<id de producto>": cantidad }.
 * Precios y nombres salen siempre del menú de hoy (#menu-data), así que si un producto ya no
 * está en el menú se quita del pedido. "Pedir por WhatsApp" abre el chat de la tienda
 * (número configurado en Ajustes) con el pedido ya escrito.
 */
(function () {
  "use strict";

  var dataEl = document.getElementById("menu-data");
  var drawer = document.getElementById("cart");
  if (!dataEl || !drawer) return;

  var CART_KEY = "anubis-pedido";
  var PROFILE_KEY = "anubis-cliente";    // { name, code }; "Mi código" también guarda aquí el código
  var MAX_QTY = 99;

  var products = JSON.parse(dataEl.textContent || "[]");
  var byId = {};
  products.forEach(function (p) { byId[String(p.id)] = p; });

  var store = drawer.getAttribute("data-store") || "la tienda";
  var whatsapp = drawer.getAttribute("data-whatsapp") || "";

  var fab = document.getElementById("cart-open");
  var overlay = document.querySelector(".cart-overlay");
  var itemsEl = drawer.querySelector("[data-cart-items]");
  var sendEl = drawer.querySelector("[data-cart-send]");
  var liveEl = drawer.querySelector("[data-cart-live]");
  var fields = {};
  Array.prototype.forEach.call(drawer.querySelectorAll("[data-cart-field]"), function (el) {
    fields[el.getAttribute("data-cart-field")] = el;
  });

  // ------------------------------------------------------------ almacenamiento
  function read(key) {
    try { return JSON.parse(window.localStorage.getItem(key) || "null"); } catch (e) { return null; }
  }
  function write(key, value) {
    try { window.localStorage.setItem(key, JSON.stringify(value)); } catch (e) { /* modo privado */ }
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
  if (dropped) write(CART_KEY, cart);

  var profile = read(PROFILE_KEY) || {};
  if (fields.name) fields.name.value = profile.name || "";
  if (fields.code) fields.code.value = profile.code || "";

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

  function message(s) {
    var out = ["Hola, " + store + ". Quiero hacer este pedido:", ""];
    s.lines.forEach(function (line) {
      var price;
      if (line.total !== null) price = cop(line.total);
      else if (line.p.label) price = line.p.label + (line.qty > 1 ? " c/u" : "");
      else price = "precio a confirmar";
      out.push("• " + line.qty + " × " + line.p.title + " — " + price);
    });
    out.push("");
    if (s.sum && s.pending) out.push("Total: " + cop(s.sum) + " COP + productos con precio a confirmar");
    else if (s.sum) out.push("Total: " + cop(s.sum) + " COP");
    else out.push("Total: por confirmar");
    var name = fields.name ? fields.name.value.trim() : "";
    var code = fields.code ? fields.code.value.trim().toUpperCase() : "";
    var notes = fields.notes ? fields.notes.value.trim() : "";
    if (name) out.push("Nombre: " + name);
    if (code) out.push("Mi código de cliente: " + code + " (quiero usar mis descuentos)");
    if (notes) out.push("Nota: " + notes);
    return out.join("\n");
  }

  // ------------------------------------------------------------ cambios
  function setQty(id, qty, announce) {
    id = String(id);
    var p = byId[id];
    if (!p) return;
    qty = Math.max(0, Math.min(MAX_QTY, qty));
    var before = cart[id] || 0;
    if (qty) cart[id] = qty; else delete cart[id];
    write(CART_KEY, cart);
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

    if (sendEl) {
      sendEl.classList.toggle("is-disabled", empty);
      sendEl.setAttribute("aria-disabled", empty ? "true" : "false");
      sendEl.href = "https://wa.me/" + whatsapp + (empty ? "" : "?text=" + encodeURIComponent(message(s)));
    }
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
      function (node) { return !node.hidden && node.offsetParent !== null; }
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
        write(CART_KEY, cart);
        render();
        var sent = drawer.querySelector("[data-cart-sent]");
        if (sent) sent.hidden = true;
        close();
      }
    } else if (t.hasAttribute("data-cart-send")) {
      if (!summary().count) { ev.preventDefault(); return; }
      var hint = drawer.querySelector("[data-cart-sent]");
      if (hint) hint.hidden = false;
    }
  });

  drawer.addEventListener("input", function (ev) {
    var key = ev.target.getAttribute("data-cart-field");
    if (!key) return;
    if (key === "name" || key === "code") {
      profile[key] = key === "code" ? ev.target.value.trim().toUpperCase() : ev.target.value.trim();
      write(PROFILE_KEY, profile);
    }
    render();
  });

  document.addEventListener("keydown", function (ev) {
    if (!isOpen()) return;
    if (ev.key === "Escape") { ev.preventDefault(); close(); return; }
    if (ev.key !== "Tab") return;
    var list = focusables();
    if (!list.length) return;
    var first = list[0], last = list[list.length - 1];
    if (ev.shiftKey && document.activeElement === first) { ev.preventDefault(); last.focus(); }
    else if (!ev.shiftKey && document.activeElement === last) { ev.preventDefault(); first.focus(); }
  });

  render();
})();
