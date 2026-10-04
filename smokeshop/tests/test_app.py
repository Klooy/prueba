import io
import os
import re
import sys
import tempfile

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


def new_customer(client, name, phone, ref=""):
    resp = post(client, "/admin/clientes/nuevo", {"name": name, "phone": phone, "ref": ref})
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
            "SELECT kind, percent, redeemed_at FROM rewards WHERE customer_id = ? ORDER BY id", (cid,)
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


def test_referral_rewards_and_milestone(app, client):
    login(client)
    post(client, "/admin/ajustes", {
        "store_name": "X", "tagline": "Y", "code_prefix": "ank", "whatsapp": "",
        "referrer_discount": "10", "welcome_discount": "5",
        "milestone_every": "2", "milestone_discount": "25",
    })
    ana = new_customer(client, "Ana", "555 111 2222")
    code = code_of(app, ana)
    assert code.startswith("ANK-")

    b = new_customer(client, "Beto", "5553334444", ref=code.lower())
    assert [tuple(r)[:2] for r in rewards_of(app, b)] == [("welcome", 5)]
    assert [tuple(r)[:2] for r in rewards_of(app, ana)] == [("referral", 10)]

    new_customer(client, "Caro", "5556667777", ref=code)
    kinds = [r["kind"] for r in rewards_of(app, ana)]
    assert kinds == ["referral", "referral", "milestone"]


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
    new_customer(client, "Beto", "5553334444", ref=code_of(app, ana))
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
