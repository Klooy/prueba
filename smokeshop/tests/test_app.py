import io
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_tmp = tempfile.mkdtemp()
os.environ.setdefault("DATABASE", os.path.join(_tmp, "import.db"))
os.environ.setdefault("UPLOAD_FOLDER", os.path.join(_tmp, "uploads"))

from app import create_app  # noqa: E402


@pytest.fixture
def app(tmp_path):
    return create_app({
        "DATABASE": str(tmp_path / "test.db"),
        "UPLOAD_FOLDER": str(tmp_path / "uploads"),
        "SECRET_KEY": "test",
    })


@pytest.fixture
def client(app):
    return app.test_client()


def csrf(client, path="/"):
    html = client.get(path).get_data(as_text=True)
    m = re.search(r'name="csrf" value="([^"]+)"', html)
    if m:
        return m.group(1)
    with client.session_transaction() as s:
        return s["csrf"]


def post(client, path, data=None, **kw):
    data = dict(data or {})
    data["csrf"] = csrf(client)
    return client.post(path, data=data, **kw)


def login(client):
    return post(client, "/admin/login", {"password": "secreto1", "confirm": "secreto1"})


def new_customer(client, name, phone, ref="", **amounts):
    data = {"name": name, "phone": phone, "ref": ref, **amounts}
    resp = post(client, "/admin/clientes/nuevo", data)
    assert resp.status_code == 302, resp.get_data(as_text=True)
    return int(resp.headers["Location"].rstrip("/").split("/")[-1])


def code_of(app, cid):
    from app import get_db
    with app.app_context():
        return get_db().execute("SELECT code FROM customers WHERE id = ?", (cid,)).fetchone()[0]


def rewards_of(app, cid):
    from app import get_db
    with app.app_context():
        return get_db().execute(
            "SELECT kind, amount, note, redeemed_at FROM rewards WHERE customer_id = ? ORDER BY id", (cid,)
        ).fetchall()


def test_public_menu_renders(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Menú del día" in resp.get_data(as_text=True)


def test_admin_requires_login(client):
    resp = client.get("/admin")
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_post_without_csrf_rejected(client):
    assert client.post("/admin/login", data={"password": "x"}).status_code == 400


def test_first_run_then_login(client):
    assert login(client).status_code == 302
    assert client.get("/admin").status_code == 200
    post(client, "/admin/logout")
    assert post(client, "/admin/login", {"password": "malo"}).status_code == 200
    assert client.get("/admin").status_code == 302
    assert post(client, "/admin/login", {"password": "secreto1"}).status_code == 302


def test_referral_discounts_in_pesos_set_by_admin(app, client):
    login(client)
    post(client, "/admin/ajustes", {"store_name": "X", "tagline": "Y", "code_prefix": "ank", "whatsapp": ""})
    ana = new_customer(client, "Ana", "555 111 2222")
    code = code_of(app, ana)
    assert code.startswith("ANK-")
    assert rewards_of(app, ana) == []  # nadie recibe nada automáticamente

    b = new_customer(client, "Beto", "5553334444", ref=code.lower(),
                     ref_amount="10.000", welcome_amount="$5,000")
    assert [tuple(r)[:2] for r in rewards_of(app, ana)] == [("referral", 10000)]
    assert [tuple(r)[:2] for r in rewards_of(app, b)] == [("welcome", 5000)]
    html = client.get(f"/admin/clientes/{ana}").get_data(as_text=True)
    assert "$10.000" in html and "%" not in html.split("Descuentos", 1)[1].split("Sus referidos")[0]

    # referido sin valores: queda registrado, pero sin descuentos; no hay premios automáticos
    caro = new_customer(client, "Caro", "5556667777", ref=code)
    assert len(rewards_of(app, ana)) == 1 and rewards_of(app, caro) == []
    from app import get_db
    with app.app_context():
        assert get_db().execute("SELECT referred_by FROM customers WHERE id = ?", (caro,)).fetchone()[0] == ana


def test_new_customer_discount_validation(app, client):
    login(client)
    ana = new_customer(client, "Ana", "5551112222")
    base = {"name": "Z", "phone": "5559990000"}
    html = post(client, "/admin/clientes/nuevo", {**base, "ref_amount": "10000"}).get_data(as_text=True)
    assert "escribe su código" in html
    html = post(client, "/admin/clientes/nuevo",
                {**base, "ref": code_of(app, ana), "ref_amount": "diez mil"}).get_data(as_text=True)
    assert "valor en pesos" in html and 'value="diez mil"' in html
    html = post(client, "/admin/clientes/nuevo", {**base, "welcome_amount": "99000000"}).get_data(as_text=True)
    assert "máximo" in html
    # bienvenida sin referido sí se permite
    z = new_customer(client, "Z", "5559990000", welcome_amount="3000")
    assert [tuple(r)[:2] for r in rewards_of(app, z)] == [("welcome", 3000)]


def test_assign_discount_by_code(app, client):
    login(client)
    ana = new_customer(client, "Ana", "5551112222")
    code = code_of(app, ana)
    resp = post(client, "/admin/canjear/asignar", {"code": code.lower(), "amount": "15.000", "note": "Por referir a Juan"})
    assert resp.status_code == 302 and resp.headers["Location"].endswith(f"/admin/clientes/{ana}")
    assert [tuple(r)[:3] for r in rewards_of(app, ana)] == [("manual", 15000, "Por referir a Juan")]
    assert "$15.000" in client.get(resp.headers["Location"]).get_data(as_text=True)

    html = post(client, "/admin/canjear/asignar", {"code": "ANK-ZZZZZ", "amount": "1000"}).get_data(as_text=True)
    assert "Ningún cliente".lower() in html.lower()
    html = post(client, "/admin/canjear/asignar", {"code": code, "amount": "abc"}).get_data(as_text=True)
    assert "valor en pesos" in html
    html = post(client, "/admin/canjear/asignar", {"code": code, "amount": ""}).get_data(as_text=True)
    assert "Escribe el valor" in html
    assert len(rewards_of(app, ana)) == 1

    # el cliente ve su saldo en pesos
    html = post(client, "/mi-codigo", {"code": code}).get_data(as_text=True)
    assert "$15.000" in html


def test_assign_discount_from_customer_page(app, client):
    login(client)
    ana = new_customer(client, "Ana", "5551112222")
    post(client, f"/admin/clientes/{ana}/descuento", {"amount": "8000", "note": "Cumpleaños"})
    assert [tuple(r)[:3] for r in rewards_of(app, ana)] == [("manual", 8000, "Cumpleaños")]
    assert post(client, "/admin/clientes/999/descuento", {"amount": "8000"}).status_code == 404


def test_duplicate_phone_and_bad_ref(client):
    login(client)
    new_customer(client, "Ana", "5551112222")
    resp = post(client, "/admin/clientes/nuevo", {"name": "Otra", "phone": "555-111-2222"})
    assert "Ya existe" in resp.get_data(as_text=True)
    resp = post(client, "/admin/clientes/nuevo", {"name": "Z", "phone": "5559990000", "ref": "NOPE"})
    assert "no existe" in resp.get_data(as_text=True)


def test_redeem_once(app, client):
    login(client)
    ana = new_customer(client, "Ana", "5551112222")
    new_customer(client, "Beto", "5553334444", ref=code_of(app, ana), ref_amount="10000")
    from app import get_db
    with app.app_context():
        rid = get_db().execute("SELECT id FROM rewards WHERE customer_id = ?", (ana,)).fetchone()[0]
    post(client, f"/admin/recompensas/{rid}/canjear")
    assert rewards_of(app, ana)[0]["redeemed_at"] is not None
    resp = post(client, f"/admin/recompensas/{rid}/canjear", follow_redirects=True)
    assert "ya estaba canjeado" in resp.get_data(as_text=True)


def test_customer_lookup_by_code(app, client):
    login(client)
    ana = new_customer(client, "Ana López", "5551112222")
    code = code_of(app, ana)
    html = post(client, "/mi-codigo", {"code": code}).get_data(as_text=True)
    assert code in html and "Hola, Ana" in html and "5551112222" not in html
    assert client.get(f"/r/{code}").status_code == 200
    assert client.get("/r/ANK-ZZZZZ").status_code == 404


def test_product_lifecycle(client):
    login(client)
    img = (io.BytesIO(b"\x89PNG\r\n\x1a\nfake"), "foto.png")
    resp = post(client, "/admin/menu/nuevo", {
        "title": "Pipa Lapislázuli", "price": "$250", "category": "Pipas",
        "description": "Vidrio azul", "active": "1", "featured": "1", "image": img,
    }, content_type="multipart/form-data")
    assert resp.status_code == 302
    assert "Pipa Lapislázuli" in client.get("/").get_data(as_text=True)

    bad = (io.BytesIO(b"x"), "virus.exe")
    resp = post(client, "/admin/menu/nuevo", {"title": "Malo", "image": bad},
                content_type="multipart/form-data")
    assert "no permitido" in resp.get_data(as_text=True)

    post(client, "/admin/menu/vaciar")
    assert "Pipa Lapislázuli" not in client.get("/").get_data(as_text=True)


def test_open_redirect_blocked(client):
    login(client)
    post(client, "/admin/logout")
    resp = post(client, "/admin/login?next=//evil.com", {"password": "secreto1"})
    assert resp.headers["Location"].endswith("/admin")


def test_demo_seed_and_admin_password_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ADMIN_PASSWORD", "desdeenv")
    app = create_app({"DATABASE": str(tmp_path / "d.db"), "UPLOAD_FOLDER": str(tmp_path / "u"),
                      "SECRET_KEY": "t", "DEMO_DATA": True})
    from app import seed_demo
    with app.app_context():
        seed_demo()  # una segunda vez no duplica
    client = app.test_client()
    html = client.get("/").get_data(as_text=True)
    from app import DEMO_PRODUCTS
    assert html.count('<article class="product') == len(DEMO_PRODUCTS)
    assert post(client, "/admin/login", {"password": "desdeenv"}).status_code == 302
    assert "Ana Ramírez" in client.get("/admin").get_data(as_text=True)


def test_uploaded_image_served(client):
    login(client)
    img = (io.BytesIO(b"\x89PNG\r\n\x1a\nfake"), "foto.png")
    post(client, "/admin/menu/nuevo", {"title": "Con foto", "active": "1", "image": img},
         content_type="multipart/form-data")
    src = re.search(r'src="(/uploads/[^"]+)"', client.get("/").get_data(as_text=True)).group(1)
    assert client.get(src).data.startswith(b"\x89PNG")


def test_prices_in_cop(client):
    from app import format_cop, normalize_price
    assert normalize_price("$ 45.000") == "45000"
    assert normalize_price("45,000 COP") == "45000"
    assert normalize_price("Desde $20.000") == "Desde $20.000"
    assert format_cop("1250000") == "$1.250.000"
    assert format_cop("Consultar") is None
    login(client)
    post(client, "/admin/menu/nuevo", {"title": "Bong", "price": "240.000", "active": "1"})
    html = client.get("/").get_data(as_text=True)
    assert "$240.000<small>COP</small>" in html


def test_admin_entry_visible_on_public_pages(client):
    html = client.get("/").get_data(as_text=True)
    assert html.count('href="/admin"') >= 2


def test_redeem_all(app, client):
    login(client)
    ana = new_customer(client, "Ana", "5551112222")
    for amount in ("10000", "5000"):
        post(client, f"/admin/clientes/{ana}/descuento", {"amount": amount})
    html = client.get(f"/admin/clientes/{ana}").get_data(as_text=True)
    assert "Canjear todo" in html and "$15.000" in html
    resp = post(client, f"/admin/clientes/{ana}/canjear-todo", follow_redirects=True)
    assert "Canjeaste 2 descuentos por $15.000" in resp.get_data(as_text=True)
    assert all(r["redeemed_at"] for r in rewards_of(app, ana))
    resp = post(client, f"/admin/clientes/{ana}/canjear-todo", follow_redirects=True)
    assert "no tiene descuentos" in resp.get_data(as_text=True)


def test_money_helpers():
    from app import format_cop, parse_cop
    assert parse_cop("10.000") == 10000
    assert parse_cop("$ 25,000 COP") == 25000
    assert parse_cop("") == 0
    assert parse_cop("diez") is None
    assert format_cop(15000) == "$15.000"
    assert format_cop(None) is None


def test_legacy_percent_rewards_are_migrated(tmp_path):
    import sqlite3
    db_path = tmp_path / "old.db"
    con = sqlite3.connect(db_path)
    con.executescript("""
        CREATE TABLE customers (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            phone TEXT NOT NULL UNIQUE, code TEXT NOT NULL UNIQUE, referred_by INTEGER,
            notes TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
        CREATE TABLE rewards (id INTEGER PRIMARY KEY AUTOINCREMENT, customer_id INTEGER NOT NULL,
            kind TEXT NOT NULL, percent INTEGER NOT NULL, source_id INTEGER,
            note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, redeemed_at TEXT);
        INSERT INTO customers VALUES (1, 'Ana', '5551112222', 'ANK-AAAAA', NULL, '', '2026-10-01T10:00:00');
        INSERT INTO rewards (customer_id, kind, percent, created_at) VALUES (1, 'referral', 10, '2026-10-01T10:00:00');
    """)
    con.commit()
    con.close()
    app = create_app({"DATABASE": str(db_path), "UPLOAD_FOLDER": str(tmp_path / "u"), "SECRET_KEY": "t"})
    client = app.test_client()
    login(client)
    post(client, "/admin/clientes/1/descuento", {"amount": "7000"})  # inserta con la columna nueva
    html = client.get("/admin/clientes/1").get_data(as_text=True)
    assert "10 %" in html and "$7.000" in html


def test_settings_without_percentages(client):
    login(client)
    html = client.get("/admin/ajustes").get_data(as_text=True)
    form = html.split('<form method="post" class="narrow">', 1)[1].split("</form>", 1)[0]
    assert "referrer_discount" not in form and "%" not in form
    resp = post(client, "/admin/ajustes", {"store_name": "Templo", "tagline": "T", "code_prefix": "ank", "whatsapp": ""},
                follow_redirects=True)
    assert "Ajustes guardados" in resp.get_data(as_text=True)


def add_product(client, title, price="", **extra):
    resp = post(client, "/admin/menu/nuevo", {"title": title, "price": price, "active": "1", **extra})
    assert resp.status_code == 302, resp.get_data(as_text=True)


def save_settings(client, whatsapp, **extra):
    data = {"store_name": "Templo", "tagline": "T", "code_prefix": "ANK", "whatsapp": whatsapp, **extra}
    return post(client, "/admin/ajustes", data, follow_redirects=True)


def menu_data(html):
    import json
    raw = re.search(r'<script type="application/json" id="menu-data">(.*?)</script>', html, re.S).group(1)
    return {item["title"]: item for item in json.loads(raw)}


def test_whatsapp_helpers():
    from app import normalize_whatsapp, phone_display
    assert normalize_whatsapp("300 123 4567") == "573001234567"
    assert normalize_whatsapp("+57 300-123-4567") == "573001234567"
    assert normalize_whatsapp("0057 3001234567") == "573001234567"
    assert normalize_whatsapp("(604) 444 1234") == "576044441234"
    assert normalize_whatsapp("+1 (555) 010-9999") == "15550109999"
    assert normalize_whatsapp("") == ""
    assert phone_display("573001234567") == "+57 300 123 4567"
    assert phone_display("15550109999") == "+15550109999"
    assert phone_display("") == ""


def test_cart_on_menu_posts_order_to_server(client):
    login(client)
    add_product(client, "Bong Osiris", "240.000")
    add_product(client, "Kit sorpresa", "Consultar")
    add_product(client, "Sin precio")
    assert "Ajustes guardados" in save_settings(client, "300 123 4567").get_data(as_text=True)

    html = client.get("/").get_data(as_text=True)
    items = menu_data(html)
    assert items["Bong Osiris"]["price"] == 240000 and items["Bong Osiris"]["label"] == "$240.000"
    assert items["Kit sorpresa"]["price"] is None and items["Kit sorpresa"]["label"] == "Consultar"
    assert items["Sin precio"]["price"] is None and items["Sin precio"]["label"] == ""
    assert html.count("data-add>") == 3
    assert 'action="/pedido"' in html and 'target="_blank"' in html and "Pedir por WhatsApp" in html
    for field in ('name="name" form="cart-form"', 'name="code" form="cart-form"', 'name="note" form="cart-form"'):
        assert field in html
    assert "js/cart.js" in html


def test_no_cart_without_whatsapp_or_products(client):
    login(client)
    save_settings(client, "3001234567")
    assert 'id="cart"' not in client.get("/").get_data(as_text=True)  # menú vacío: sin carrito
    add_product(client, "Bong Osiris", "240.000")
    assert 'id="cart"' in client.get("/").get_data(as_text=True)
    save_settings(client, "")                                         # sin número: el menú queda sin carrito
    html = client.get("/").get_data(as_text=True)
    assert "Bong Osiris" in html
    assert 'id="cart"' not in html and "data-add" not in html and "js/cart.js" not in html


def test_menu_data_cannot_break_out_of_script(client):
    login(client)
    save_settings(client, "3001234567")
    add_product(client, "</script><script>alert(1)</script>", "1000")
    html = client.get("/").get_data(as_text=True)
    assert "<script>alert(1)" not in html
    assert "</script><script>alert(1)</script>" in menu_data(html)


def test_whatsapp_setting_validation(client):
    login(client)
    assert client.get("/admin/ajustes").get_data(as_text=True).count('id="whatsapp"') == 1  # la etiqueta apunta al campo
    html = save_settings(client, "12345").get_data(as_text=True)
    assert "indicativo del país" in html
    assert 'value="12345"' in html                    # lo que escribió sigue en el campo
    assert "Sin número todavía" in html               # y no se guardó
    html = save_settings(client, "letras").get_data(as_text=True)
    assert "indicativo del país" in html

    save_settings(client, "+57 300 123 4567")
    html = client.get("/admin/ajustes").get_data(as_text=True)
    assert 'value="+57 300 123 4567"' in html
    assert 'Los pedidos llegan a <strong class="tel">+57 300 123 4567</strong>' in html
    assert 'href="https://wa.me/573001234567"' in html

    assert "Ajustes guardados" in save_settings(client, "").get_data(as_text=True)  # vacío = desactivado
    assert "Sin número todavía" in client.get("/admin/ajustes").get_data(as_text=True)


def test_dashboard_warns_until_whatsapp_is_set(client):
    login(client)
    html = client.get("/admin").get_data(as_text=True)
    assert "Configura el WhatsApp" in html and 'href="/admin/ajustes#pedidos"' in html
    assert 'id="pedidos"' in client.get("/admin/ajustes").get_data(as_text=True)
    save_settings(client, "3001234567")
    assert "Configura el WhatsApp" not in client.get("/admin").get_data(as_text=True)


def test_store_whatsapp_env_only_fills_empty_value(tmp_path, monkeypatch):
    from app import get_settings, set_setting, get_db
    monkeypatch.setenv("STORE_WHATSAPP", "300 765 4321")
    config = {"DATABASE": str(tmp_path / "w.db"), "UPLOAD_FOLDER": str(tmp_path / "u"), "SECRET_KEY": "t"}
    app = create_app(config)
    with app.app_context():
        assert get_settings()["whatsapp"] == "573007654321"
        set_setting("whatsapp", "573001112222")
        get_db().commit()
    app = create_app(config)                          # reinicio: no pisa lo que guardó el administrador
    with app.app_context():
        assert get_settings()["whatsapp"] == "573001112222"


def test_my_code_remembers_code_for_cart(app, client):
    login(client)
    cid = new_customer(client, "Ana", "5551112222")
    code = code_of(app, cid)
    html = post(client, "/mi-codigo", {"code": code}).get_data(as_text=True)
    assert f'perfil.code = "{code}"' in html


def place_order(client, items, **form):
    return post(client, "/pedido", {"items": items, **form})


def wa_text(resp):
    """Mensaje que la redirección le pasa a WhatsApp."""
    return parse_qs(urlsplit(resp.headers["Location"]).query)["text"][0]


def orders_of(app):
    from app import get_db
    with app.app_context():
        return get_db().execute("SELECT * FROM orders ORDER BY id").fetchall()


def test_order_is_saved_and_opens_store_whatsapp(app, client):
    login(client)
    save_settings(client, "3001234567")
    add_product(client, "Bong Osiris", "240.000")
    add_product(client, "Kit sorpresa", "Consultar")
    add_product(client, "Oculto", "5000", active="")        # no está en el menú de hoy
    ana = new_customer(client, "Ana", "3005551111")
    code = code_of(app, ana)

    resp = place_order(client, "1:2,2:1,3:4,99:1,basura", token="t" * 32,
                       name=" Juan ", code=code.lower(), note="A las 6")
    assert resp.status_code == 303
    assert resp.headers["Location"].startswith("https://wa.me/573001234567?text=")
    text = wa_text(resp)
    assert text.startswith("Hola, Templo. Quiero hacer el pedido #0001:")
    assert "• 2 × Bong Osiris — $480.000" in text and "• 1 × Kit sorpresa — Consultar" in text
    assert "Oculto" not in text
    assert "Total: $480.000 COP + productos con precio a confirmar" in text
    assert "Nombre: Juan" in text and f"Mi código de cliente: {code} (quiero" in text and "Nota: A las 6" in text

    (order,) = orders_of(app)
    assert order["status"] == "nuevo" and order["total"] == 480000 and order["pending"] == 1
    assert order["customer_id"] == ana and order["code"] == code and order["name"] == "Juan"
    lines = json.loads(order["items"])
    assert [(line["title"], line["qty"], line["price"]) for line in lines] == [
        ("Kit sorpresa", 1, None), ("Bong Osiris", 2, 240000)]       # mismo orden que el menú


def test_resending_same_cart_updates_its_order(app, client):
    login(client)
    save_settings(client, "3001234567")
    add_product(client, "Bong", "240000")
    place_order(client, "1:1", token="a" * 32)
    text = wa_text(place_order(client, "1:3", token="a" * 32))
    assert "pedido #0001" in text and "3 × Bong" in text
    rows = orders_of(app)
    assert len(rows) == 1 and rows[0]["total"] == 720000

    post(client, "/admin/pedidos/1/estado", {"status": "confirmado"})
    assert "pedido #0002" in wa_text(place_order(client, "1:1", token="a" * 32))  # ya confirmado: uno nuevo
    place_order(client, "1:1", token="corto")                                    # token inválido: uno nuevo
    assert len(orders_of(app)) == 3


def test_order_rejected_without_whatsapp_or_items(app, client):
    login(client)
    add_product(client, "Bong", "240000")
    resp = post(client, "/pedido", {"items": "1:1"}, follow_redirects=True)
    assert "todavía no recibe pedidos" in resp.get_data(as_text=True)
    save_settings(client, "3001234567")
    for items in ("", "99:1", "1:0", "x:y", "1:" + "9" * 5000):
        resp = post(client, "/pedido", {"items": items}, follow_redirects=True)
        assert "Tu pedido está vacío" in resp.get_data(as_text=True)
    assert client.post("/pedido", data={"items": "1:1"}).status_code == 400   # sin token CSRF
    assert orders_of(app) == []


def test_order_flood_is_limited(app, client, monkeypatch):
    import app as app_module
    monkeypatch.setattr(app_module, "ORDER_LIMIT", (2, 600))
    login(client)
    save_settings(client, "3001234567")
    add_product(client, "Bong", "240000")
    assert place_order(client, "1:1", token="b" * 32).status_code == 303
    assert place_order(client, "1:2").status_code == 303
    blocked = post(client, "/pedido", {"items": "1:1"}, follow_redirects=True)
    assert "muchos pedidos seguidos" in blocked.get_data(as_text=True)
    assert place_order(client, "1:5", token="b" * 32).status_code == 303     # actualizar sí se puede
    assert len(orders_of(app)) == 2


def test_admin_order_history(app, client, monkeypatch):
    assert client.get("/admin/pedidos").status_code == 302                   # solo con sesión
    login(client)
    save_settings(client, "3001234567")
    add_product(client, "Bong Osiris", "240.000")
    ana = new_customer(client, "Ana Ramírez", "3005551111")
    post(client, f"/admin/clientes/{ana}/descuento", {"amount": "15000"})
    place_order(client, "1:1", name="Juan", code=code_of(app, ana))
    place_order(client, "1:2", name="Pedro")

    html = client.get("/admin/pedidos").get_data(as_text=True)
    assert "#0001 · Juan" in html and "#0002 · Pedro" in html and "$480.000" in html
    assert 'class="nav-badge" aria-hidden="true">2<' in html
    assert "#0002" in client.get("/admin/pedidos?q=%230002").get_data(as_text=True)
    found = client.get("/admin/pedidos?q=juan").get_data(as_text=True)
    assert "#0001" in found and "#0002" not in found

    import app as app_module
    monkeypatch.setattr(app_module, "ORDERS_PER_PAGE", 1)
    first = client.get("/admin/pedidos").get_data(as_text=True)
    assert "#0002" in first and "#0001 ·" not in first and "pagina=2" in first
    assert "#0001 · Juan" in client.get("/admin/pedidos?pagina=2").get_data(as_text=True)
    monkeypatch.undo()

    detail = client.get("/admin/pedidos/1").get_data(as_text=True)
    assert "1 × Bong Osiris" in detail and "Ana Ramírez" in detail
    assert "$15.000" in detail and "Descuentos para usar" in detail
    assert 'href="https://wa.me/573005551111"' in detail
    assert client.get("/admin/pedidos/99").status_code == 404

    resp = post(client, "/admin/pedidos/1/estado", {"status": "entregado"}, follow_redirects=True)
    assert "Pedido #0001 marcado como entregado" in resp.get_data(as_text=True)
    assert post(client, "/admin/pedidos/1/estado", {"status": "otro"}).status_code == 400
    nuevos = client.get("/admin/pedidos?estado=nuevo").get_data(as_text=True)
    assert "#0002" in nuevos and "#0001 ·" not in nuevos

    dashboard = client.get("/admin").get_data(as_text=True)
    assert "Pedidos nuevos" in dashboard and "#0002" in dashboard
    ficha = client.get(f"/admin/clientes/{ana}").get_data(as_text=True)
    assert "Sus pedidos" in ficha and "#0001" in ficha
    assert 'href="https://wa.me/573005551111?text=' in ficha              # el código va al número con 57

    resp = post(client, "/admin/pedidos/2/borrar", follow_redirects=True)
    assert "Pedido #0002 eliminado" in resp.get_data(as_text=True)
    assert client.get("/admin/pedidos/2").status_code == 404
    assert "Pedidos nuevos" not in client.get("/admin").get_data(as_text=True)


def test_order_date_filter(app):
    from app import local_now
    utc = datetime.now(timezone.utc).replace(tzinfo=None)
    assert abs(local_now() - (utc - timedelta(hours=5))) < timedelta(seconds=5)   # hora de Colombia
    fecha_hora = app.jinja_env.filters["fecha_hora"]
    today = local_now().replace(hour=15, minute=42, second=0, microsecond=0)
    assert fecha_hora(today.isoformat()) == "Hoy, 3:42 p. m."
    assert fecha_hora((today - timedelta(days=1)).replace(hour=0, minute=5).isoformat()) == "Ayer, 12:05 a. m."
    assert fecha_hora("2025-01-09T12:30:00") == "9 ene 2025, 12:30 p. m."
