"""Templo de Anubis — sistema de referidos y menú del día para smoke shop.

Aplicación Flask + SQLite. Una sola persona administra (el dueño de la tienda):
registra clientes, controla quién refirió a quién, entrega y canjea
descuentos, y publica las novedades del día. Los clientes solo ven el menú
del día y pueden consultar su código de referido.
"""
import hashlib
import os
import secrets
import sqlite3
import uuid
from datetime import datetime
from functools import wraps

from flask import (
    Flask, abort, flash, g, redirect, render_template, request, send_from_directory, session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# En Vercel el código es de solo lectura: lo único escribible es /tmp, que se
# borra cuando la función se recicla. Sirve para ver el diseño, no para datos reales.
ON_VERCEL = bool(os.environ.get("VERCEL"))
ALLOWED_IMAGES = {"png", "jpg", "jpeg", "webp", "gif"}
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin 0/O ni 1/I

DEFAULT_SETTINGS = {
    "store_name": "Templo de Anubis",
    "tagline": "Humo sagrado · Novedades frescas cada día",
    "code_prefix": "ANK",
    "whatsapp": "",              # WhatsApp de la tienda para pedidos: dígitos con indicativo (57...)
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS customers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    phone       TEXT NOT NULL UNIQUE,
    code        TEXT NOT NULL UNIQUE,
    referred_by INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    notes       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS rewards (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,          -- referral | welcome | manual (milestone: histórico)
    amount      INTEGER NOT NULL DEFAULT 0,  -- valor del descuento en pesos colombianos
    percent     INTEGER NOT NULL DEFAULT 0,  -- histórico: descuentos antiguos en porcentaje
    source_id   INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    redeemed_at TEXT
);
CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    price       TEXT NOT NULL DEFAULT '',
    category    TEXT NOT NULL DEFAULT '',
    image       TEXT,
    featured    INTEGER NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_customers_referred_by ON customers(referred_by);
CREATE INDEX IF NOT EXISTS idx_rewards_customer ON rewards(customer_id);
"""

REWARD_LABELS = {
    "referral": "Por referido",
    "welcome": "Bienvenida",
    "milestone": "Premio de meta",
    "manual": "Descuento asignado",
}

MAX_DISCOUNT = 10_000_000  # tope de seguridad contra ceros de más al escribir el valor


def create_app(test_config=None):
    app = Flask(
        __name__, instance_relative_config=True,
        instance_path="/tmp/smokeshop" if ON_VERCEL else None,
    )
    default_uploads = (os.path.join(app.instance_path, "uploads") if ON_VERCEL
                       else os.path.join(BASE_DIR, "static", "uploads"))
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or _fallback_secret(app),
        DATABASE=os.environ.get("DATABASE") or os.path.join(app.instance_path, "smokeshop.db"),
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER") or default_uploads,
        DEMO_DATA=os.environ.get("DEMO_DATA", "1" if ON_VERCEL else "0") == "1",
        MAX_CONTENT_LENGTH=6 * 1024 * 1024,
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.instance_path, exist_ok=True)
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

    app.teardown_appcontext(close_db)
    with app.app_context():
        init_db()
        if app.config["DEMO_DATA"]:
            seed_demo()

    register_hooks(app)
    register_public_routes(app)
    register_admin_routes(app)
    return app


def _fallback_secret(app):
    # En Vercel cada instancia tiene su propio /tmp; una clave aleatoria por
    # instancia rompería las sesiones, así que se deriva de ADMIN_PASSWORD.
    if ON_VERCEL and os.environ.get("ADMIN_PASSWORD"):
        return hashlib.sha256(("anubis:" + os.environ["ADMIN_PASSWORD"]).encode()).hexdigest()
    return _persistent_secret(app)


def _persistent_secret(app):
    """Genera una SECRET_KEY una vez y la guarda en instance/ para que las sesiones sobrevivan reinicios."""
    os.makedirs(app.instance_path, exist_ok=True)
    path = os.path.join(app.instance_path, "secret_key")
    if not os.path.exists(path):
        with open(path, "w") as fh:
            fh.write(secrets.token_hex(32))
    with open(path) as fh:
        return fh.read().strip()


# --------------------------------------------------------------------------- db

def get_db():
    if "db" not in g:
        from flask import current_app
        g.db = sqlite3.connect(current_app.config["DATABASE"])
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = get_db()
    db.executescript(SCHEMA)
    # Bases creadas cuando los descuentos eran en %: agregar la columna de pesos.
    columns = {row["name"] for row in db.execute("PRAGMA table_info(rewards)")}
    if "amount" not in columns:
        db.execute("ALTER TABLE rewards ADD COLUMN amount INTEGER NOT NULL DEFAULT 0")
    for key, value in DEFAULT_SETTINGS.items():
        db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    # STORE_WHATSAPP llena el número si el administrador aún no lo ha configurado.
    env_whatsapp = normalize_whatsapp(os.environ.get("STORE_WHATSAPP", ""))
    if 10 <= len(env_whatsapp) <= 15:
        db.execute("UPDATE settings SET value = ? WHERE key = 'whatsapp' AND value = ''", (env_whatsapp,))
    if os.environ.get("ADMIN_PASSWORD"):
        db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES ('admin_password', ?)",
            (generate_password_hash(os.environ["ADMIN_PASSWORD"]),),
        )
    db.commit()


DEMO_PRODUCTS = [
    ("Pipa Lapislázuli", "Vidrio soplado azul profundo con filo dorado.", "85000", "Pipas", 1),
    ("Bong Obelisco", "Vidrio borosilicato de 30 cm con percolador.", "240000", "Bongs", 1),
    ("Papel Oro 24k", "Edición limitada, combustión lenta.", "12000", "Papeles", 0),
    ("Grinder Escarabajo", "Aluminio negro mate, 4 piezas.", "65000", "Accesorios", 0),
    ("Encendedor Ankh", "Recargable, grabado egipcio.", "18000", "Accesorios", 0),
    ("Blunt Wraps Faraón", "Sabor mango, paquete x2.", "9000", "Papeles", 0),
]


def seed_demo():
    """Llena una base vacía con datos de ejemplo para enseñar el diseño."""
    db = get_db()
    if db.execute("SELECT 1 FROM products UNION ALL SELECT 1 FROM customers LIMIT 1").fetchone():
        return
    for title, desc, price, cat, featured in DEMO_PRODUCTS:
        db.execute(
            "INSERT INTO products (title, description, price, category, featured, active, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?)",
            (title, desc, price, cat, featured, now()),
        )
    db.commit()
    ana = register_customer("Ana Ramírez", "5551112222")
    referrer = db.execute("SELECT * FROM customers WHERE id = ?", (ana,)).fetchone()
    for name, phone in (("Beto Cruz", "5553334444"), ("Caro Méndez", "5556667777")):
        register_customer(name, phone, referrer, referrer_amount=10000, welcome_amount=5000)


def get_settings():
    if "settings" not in g:
        rows = get_db().execute("SELECT key, value FROM settings").fetchall()
        g.settings = {r["key"]: r["value"] for r in rows}
    return g.settings


def set_setting(key, value):
    get_db().execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    g.pop("settings", None)


def now():
    return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------- helpers

def normalize_phone(raw):
    return "".join(ch for ch in (raw or "") if ch.isdigit())


def normalize_whatsapp(raw):
    """Número para wa.me: solo dígitos, con indicativo del país y sin ceros al inicio.

    Un número colombiano escrito sin indicativo (10 dígitos: celular 3…, fijo 60…) recibe el 57.
    """
    digits = normalize_phone(raw).lstrip("0")
    if len(digits) == 10 and digits.startswith(("3", "60")):
        digits = "57" + digits
    return digits


def phone_display(value):
    """'573001234567' -> '+57 300 123 4567'; otros países: '+' y los dígitos."""
    digits = normalize_phone(value)
    if len(digits) == 12 and digits.startswith("57"):
        return f"+57 {digits[2:5]} {digits[5:8]} {digits[8:]}"
    return f"+{digits}" if digits else ""


def normalize_code(raw):
    return (raw or "").strip().upper().replace(" ", "")


def _money_digits(raw):
    """Quita '$', 'COP', espacios y separadores de miles: '$ 45.000 COP' -> '45000'."""
    return (raw or "").upper().replace("COP", "").replace("$", "").replace(".", "").replace(",", "").replace(" ", "")


def normalize_price(raw):
    """'45.000', '$ 45,000' o '45000 COP' se guardan como '45000'; cualquier otro texto se deja igual."""
    raw = (raw or "").strip()
    compact = _money_digits(raw)
    return compact if compact.isdecimal() else raw


def parse_cop(raw):
    """Valor en pesos escrito por el administrador: '10.000', '$10,000' o '10000 COP' -> 10000.

    Vacío -> 0. Devuelve None si no es un valor válido.
    """
    compact = _money_digits(raw)
    if not compact:
        return 0
    return int(compact) if compact.isdecimal() else None


def format_cop(value):
    """45000 o '45000' -> '$45.000' (formato colombiano). Devuelve None si no es un número."""
    value = str(value if value is not None else "").strip()
    if not value.isdecimal():
        return None
    return "$" + f"{int(value):,}".replace(",", ".")


def reward_value(reward):
    """Texto del valor de un descuento: pesos, o porcentaje en los descuentos antiguos."""
    if reward["amount"]:
        return format_cop(reward["amount"])
    return f"{reward['percent']} %"


def check_discount(amount):
    """Mensaje de error para un valor de descuento, o None si es válido."""
    if amount is None:
        return "Escribe el descuento como un valor en pesos, por ejemplo 10000."
    if amount > MAX_DISCOUNT:
        return f"El descuento máximo es {format_cop(MAX_DISCOUNT)}."
    return None


def generate_code():
    prefix = normalize_code(get_settings().get("code_prefix", "ANK"))[:6] or "ANK"
    db = get_db()
    while True:
        code = f"{prefix}-" + "".join(secrets.choice(CODE_ALPHABET) for _ in range(5))
        if not db.execute("SELECT 1 FROM customers WHERE code = ?", (code,)).fetchone():
            return code


def find_customer_by_code(code):
    code = normalize_code(code)
    if not code:
        return None
    return get_db().execute("SELECT * FROM customers WHERE code = ?", (code,)).fetchone()


def add_reward(customer_id, kind, amount, source_id=None, note=""):
    """Asigna un descuento de `amount` pesos. Un valor de 0 no crea nada."""
    if not amount or amount <= 0:
        return
    get_db().execute(
        "INSERT INTO rewards (customer_id, kind, amount, percent, source_id, note, created_at) "
        "VALUES (?, ?, ?, 0, ?, ?, ?)",
        (customer_id, kind, amount, source_id, note, now()),
    )


def register_customer(name, phone, referrer=None, notes="", referrer_amount=0, welcome_amount=0):
    """Crea el cliente y, si el administrador los indicó, los descuentos del referido.

    `referrer_amount` es para quien lo refirió y `welcome_amount` para el cliente nuevo,
    ambos en pesos. Devuelve el id nuevo.
    """
    db = get_db()
    cur = db.execute(
        "INSERT INTO customers (name, phone, code, referred_by, notes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (name, phone, generate_code(), referrer["id"] if referrer else None, notes, now()),
    )
    new_id = cur.lastrowid
    if referrer:
        add_reward(referrer["id"], "referral", referrer_amount, new_id)
    add_reward(new_id, "welcome", welcome_amount, referrer["id"] if referrer else None)
    db.commit()
    return new_id


def customer_stats(customer_id):
    db = get_db()
    referrals = db.execute(
        "SELECT COUNT(*) FROM customers WHERE referred_by = ?", (customer_id,)
    ).fetchone()[0]
    pending = db.execute(
        "SELECT * FROM rewards WHERE customer_id = ? AND redeemed_at IS NULL ORDER BY created_at",
        (customer_id,),
    ).fetchall()
    return {"referrals": referrals, "pending": pending, "total": sum(r["amount"] for r in pending)}


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_IMAGES


def save_image(file_storage):
    from flask import current_app
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_image(file_storage.filename):
        raise ValueError("Formato de imagen no permitido (usa png, jpg, webp o gif).")
    ext = secure_filename(file_storage.filename).rsplit(".", 1)[1].lower()
    name = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(current_app.config["UPLOAD_FOLDER"], name))
    return name


def delete_image(name):
    from flask import current_app
    if name:
        path = os.path.join(current_app.config["UPLOAD_FOLDER"], os.path.basename(name))
        if os.path.exists(path):
            os.remove(path)


def asset_version():
    """Huella corta de los estilos y el script del carrito: cambia cuando cambian, para invalidar la caché."""
    if "asset_v" not in ASSET_CACHE:
        digest = hashlib.md5()
        for name in ("css/anubis.css", "css/fonts.css", "js/cart.js"):
            with open(os.path.join(BASE_DIR, "static", name), "rb") as fh:
                digest.update(fh.read())
        ASSET_CACHE["asset_v"] = digest.hexdigest()[:10]
    return ASSET_CACHE["asset_v"]


ASSET_CACHE = {}


def is_admin():
    return session.get("admin") is True


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not is_admin():
            return redirect(url_for("admin_login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


# ------------------------------------------------------------------------ hooks

def register_hooks(app):
    @app.before_request
    def csrf_protect():
        if "csrf" not in session:
            session["csrf"] = secrets.token_urlsafe(32)
        if request.method == "POST" and not app.config.get("TESTING_SKIP_CSRF"):
            sent = request.form.get("csrf", "")
            if not secrets.compare_digest(sent, session["csrf"]):
                abort(400, "Token de seguridad inválido. Recarga la página e inténtalo de nuevo.")

    @app.context_processor
    def inject_globals():
        return {
            "asset_v": asset_version(),
            "settings": get_settings(),
            "csrf_token": session.get("csrf", ""),
            "is_admin": is_admin(),
            "reward_labels": REWARD_LABELS,
        }

    @app.template_filter("fecha")
    def fecha(value):
        if not value:
            return ""
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return value
        meses = ["ene", "feb", "mar", "abr", "may", "jun",
                 "jul", "ago", "sep", "oct", "nov", "dic"]
        return f"{dt.day} {meses[dt.month - 1]} {dt.year}"

    app.template_filter("cop")(format_cop)
    app.template_filter("reward_value")(reward_value)
    app.template_filter("phone")(phone_display)

    @app.template_filter("first_name")
    def first_name(value):
        return (value or "").split(" ")[0]

    @app.errorhandler(413)
    def too_large(_e):
        flash("La imagen es demasiado pesada (máximo 6 MB).", "error")
        return redirect(request.referrer or url_for("admin_products"))


# ----------------------------------------------------------------------- public

def register_public_routes(app):
    @app.route("/")
    def menu():
        products = get_db().execute(
            "SELECT * FROM products WHERE active = 1 ORDER BY featured DESC, created_at DESC"
        ).fetchall()
        categories = []
        for p in products:
            if p["category"] and p["category"] not in categories:
                categories.append(p["category"])
        # Lo que necesita el carrito (en el navegador) para calcular totales y armar el pedido.
        menu_items = [
            {
                "id": p["id"],
                "title": p["title"],
                "price": int(p["price"]) if p["price"].isdecimal() else None,
                "label": format_cop(p["price"]) or p["price"],
            }
            for p in products
        ]
        return render_template(
            "public/menu.html", products=products, categories=categories, menu_items=menu_items,
            today=datetime.now(),
        )

    @app.route("/uploads/<path:name>")
    def uploaded(name):
        return send_from_directory(app.config["UPLOAD_FOLDER"], name, max_age=86400)

    @app.route("/mi-codigo", methods=["GET", "POST"])
    def my_code():
        customer = stats = None
        code = ""
        if request.method == "POST":
            code = normalize_code(request.form.get("code"))
            customer = find_customer_by_code(code)
            if customer:
                stats = customer_stats(customer["id"])
            else:
                flash("No encontramos ese código. Revísalo o pregúntanos en tienda.", "error")
        return render_template("public/my_code.html", customer=customer, stats=stats, code=code)

    @app.route("/r/<code>")
    def referral_landing(code):
        referrer = find_customer_by_code(code)
        if not referrer:
            abort(404)
        return render_template("public/invite.html", referrer=referrer)


# ------------------------------------------------------------------------ admin

def register_admin_routes(app):
    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        settings = get_settings()
        first_run = "admin_password" not in settings
        if request.method == "POST":
            password = request.form.get("password", "")
            if first_run:
                if len(password) < 6:
                    flash("La contraseña debe tener al menos 6 caracteres.", "error")
                elif password != request.form.get("confirm", ""):
                    flash("Las contraseñas no coinciden.", "error")
                else:
                    set_setting("admin_password", generate_password_hash(password))
                    get_db().commit()
                    session["admin"] = True
                    flash("Contraseña creada. Te damos la bienvenida al templo.", "ok")
                    return redirect(url_for("admin_dashboard"))
            elif check_password_hash(settings["admin_password"], password):
                session.clear()
                session["admin"] = True
                nxt = request.args.get("next", "")
                if not nxt.startswith("/admin") or nxt.startswith("//"):
                    nxt = url_for("admin_dashboard")
                return redirect(nxt)
            else:
                flash("Contraseña incorrecta.", "error")
        return render_template("admin/login.html", first_run=first_run)

    @app.route("/admin/logout", methods=["POST"])
    def admin_logout():
        session.clear()
        return redirect(url_for("menu"))

    @app.route("/admin")
    @admin_required
    def admin_dashboard():
        db = get_db()
        month = datetime.now().strftime("%Y-%m")
        stats = {
            "customers": db.execute("SELECT COUNT(*) FROM customers").fetchone()[0],
            "referred": db.execute(
                "SELECT COUNT(*) FROM customers WHERE referred_by IS NOT NULL").fetchone()[0],
            "month": db.execute(
                "SELECT COUNT(*) FROM customers WHERE referred_by IS NOT NULL AND created_at LIKE ?",
                (month + "%",)).fetchone()[0],
            "pending": db.execute(
                "SELECT COUNT(*) FROM rewards WHERE redeemed_at IS NULL").fetchone()[0],
            "pending_total": db.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM rewards WHERE redeemed_at IS NULL").fetchone()[0],
            "products": db.execute(
                "SELECT COUNT(*) FROM products WHERE active = 1").fetchone()[0],
        }
        top = db.execute(
            "SELECT c.id, c.name, c.code, COUNT(r.id) AS total FROM customers c "
            "JOIN customers r ON r.referred_by = c.id GROUP BY c.id "
            "ORDER BY total DESC, c.name LIMIT 5"
        ).fetchall()
        recent = db.execute(
            "SELECT c.*, p.name AS referrer_name FROM customers c "
            "LEFT JOIN customers p ON p.id = c.referred_by "
            "ORDER BY c.created_at DESC, c.id DESC LIMIT 6"
        ).fetchall()
        return render_template("admin/dashboard.html", stats=stats, top=top, recent=recent)

    # ---- clientes

    @app.route("/admin/clientes")
    @admin_required
    def admin_customers():
        q = (request.args.get("q") or "").strip()
        sql = (
            "SELECT c.*, p.name AS referrer_name, "
            "(SELECT COUNT(*) FROM customers x WHERE x.referred_by = c.id) AS referrals, "
            "(SELECT COUNT(*) FROM rewards w WHERE w.customer_id = c.id AND w.redeemed_at IS NULL) AS pending, "
            "(SELECT COALESCE(SUM(w.amount), 0) FROM rewards w "
            " WHERE w.customer_id = c.id AND w.redeemed_at IS NULL) AS pending_total "
            "FROM customers c LEFT JOIN customers p ON p.id = c.referred_by"
        )
        params = ()
        if q:
            like = f"%{q}%"
            digits = normalize_phone(q)
            sql += " WHERE c.name LIKE ? OR c.code LIKE ? OR (? != '' AND c.phone LIKE ?)"
            params = (like, like.upper(), digits, f"%{digits}%")
        sql += " ORDER BY c.created_at DESC, c.id DESC"
        customers = get_db().execute(sql, params).fetchall()
        return render_template("admin/customers.html", customers=customers, q=q)

    @app.route("/admin/clientes/nuevo", methods=["GET", "POST"])
    @admin_required
    def admin_customer_new():
        form = {"name": "", "phone": "", "ref": request.args.get("ref", ""), "notes": "",
                "ref_amount": "", "welcome_amount": ""}
        if request.method == "POST":
            form = {k: (request.form.get(k) or "").strip() for k in form}
            phone = normalize_phone(form["phone"])
            referrer = find_customer_by_code(form["ref"]) if form["ref"] else None
            ref_amount = parse_cop(form["ref_amount"])
            welcome_amount = parse_cop(form["welcome_amount"])
            amount_error = check_discount(ref_amount) or check_discount(welcome_amount)
            db = get_db()
            if not form["name"]:
                flash("El nombre es obligatorio.", "error")
            elif len(phone) < 7:
                flash("Escribe un teléfono válido.", "error")
            elif db.execute("SELECT 1 FROM customers WHERE phone = ?", (phone,)).fetchone():
                flash("Ya existe un cliente con ese teléfono.", "error")
            elif form["ref"] and not referrer:
                flash("El código de referido no existe.", "error")
            elif amount_error:
                flash(amount_error, "error")
            elif ref_amount and not referrer:
                flash("Para darle un descuento a quien lo refirió, escribe su código.", "error")
            else:
                new_id = register_customer(form["name"], phone, referrer, form["notes"],
                                           referrer_amount=ref_amount, welcome_amount=welcome_amount)
                msg = "Cliente registrado."
                if referrer and ref_amount:
                    msg += f" {referrer['name']} recibió {format_cop(ref_amount)} por referirlo."
                elif referrer:
                    msg += f" Quedó como referido de {referrer['name']}."
                if welcome_amount:
                    msg += f" Descuento de bienvenida: {format_cop(welcome_amount)}."
                flash(msg, "ok")
                return redirect(url_for("admin_customer_detail", customer_id=new_id))
        return render_template("admin/customer_form.html", form=form)

    @app.route("/admin/clientes/<int:customer_id>")
    @admin_required
    def admin_customer_detail(customer_id):
        db = get_db()
        customer = db.execute(
            "SELECT c.*, p.name AS referrer_name, p.id AS referrer_id FROM customers c "
            "LEFT JOIN customers p ON p.id = c.referred_by WHERE c.id = ?",
            (customer_id,),
        ).fetchone()
        if not customer:
            abort(404)
        referrals = db.execute(
            "SELECT * FROM customers WHERE referred_by = ? ORDER BY created_at DESC", (customer_id,)
        ).fetchall()
        rewards = db.execute(
            "SELECT w.*, s.name AS source_name FROM rewards w "
            "LEFT JOIN customers s ON s.id = w.source_id WHERE w.customer_id = ? "
            "ORDER BY w.redeemed_at IS NOT NULL, w.created_at DESC",
            (customer_id,),
        ).fetchall()
        invite_url = url_for("referral_landing", code=customer["code"], _external=True)
        return render_template(
            "admin/customer_detail.html", customer=customer, referrals=referrals,
            rewards=rewards, invite_url=invite_url,
        )

    @app.route("/admin/clientes/<int:customer_id>/editar", methods=["POST"])
    @admin_required
    def admin_customer_edit(customer_id):
        name = (request.form.get("name") or "").strip()
        phone = normalize_phone(request.form.get("phone"))
        notes = (request.form.get("notes") or "").strip()
        db = get_db()
        clash = db.execute(
            "SELECT 1 FROM customers WHERE phone = ? AND id != ?", (phone, customer_id)
        ).fetchone()
        if not name or len(phone) < 7:
            flash("Nombre y teléfono son obligatorios.", "error")
        elif clash:
            flash("Otro cliente ya tiene ese teléfono.", "error")
        else:
            db.execute(
                "UPDATE customers SET name = ?, phone = ?, notes = ? WHERE id = ?",
                (name, phone, notes, customer_id),
            )
            db.commit()
            flash("Datos actualizados.", "ok")
        return redirect(url_for("admin_customer_detail", customer_id=customer_id))

    @app.route("/admin/clientes/<int:customer_id>/borrar", methods=["POST"])
    @admin_required
    def admin_customer_delete(customer_id):
        db = get_db()
        db.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
        db.commit()
        flash("Cliente eliminado.", "ok")
        return redirect(url_for("admin_customers"))

    def give_discount(customer):
        """Asigna el descuento en pesos del formulario. Devuelve True si se guardó."""
        amount = parse_cop(request.form.get("amount"))
        error = check_discount(amount) or (None if amount else "Escribe el valor del descuento en pesos.")
        if error:
            flash(error, "error")
            return False
        add_reward(customer["id"], "manual", amount, note=(request.form.get("note") or "").strip())
        get_db().commit()
        flash(f"Le asignaste {format_cop(amount)} de descuento a {customer['name']}.", "ok")
        return True

    @app.route("/admin/clientes/<int:customer_id>/descuento", methods=["POST"])
    @admin_required
    def admin_reward_give(customer_id):
        customer = get_db().execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not customer:
            abort(404)
        give_discount(customer)
        return redirect(url_for("admin_customer_detail", customer_id=customer_id))

    @app.route("/admin/recompensas/<int:reward_id>/canjear", methods=["POST"])
    @admin_required
    def admin_reward_redeem(reward_id):
        db = get_db()
        reward = db.execute("SELECT * FROM rewards WHERE id = ?", (reward_id,)).fetchone()
        if not reward:
            abort(404)
        if reward["redeemed_at"]:
            flash("Ese descuento ya estaba canjeado.", "error")
        else:
            db.execute("UPDATE rewards SET redeemed_at = ? WHERE id = ?", (now(), reward_id))
            db.commit()
            flash(f"Descuento de {reward_value(reward)} canjeado.", "ok")
        return redirect(url_for("admin_customer_detail", customer_id=reward["customer_id"]))

    @app.route("/admin/clientes/<int:customer_id>/canjear-todo", methods=["POST"])
    @admin_required
    def admin_reward_redeem_all(customer_id):
        db = get_db()
        if not db.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone():
            abort(404)
        total, count = db.execute(
            "SELECT COALESCE(SUM(amount), 0), COUNT(*) FROM rewards "
            "WHERE customer_id = ? AND redeemed_at IS NULL", (customer_id,)
        ).fetchone()
        if count:
            db.execute(
                "UPDATE rewards SET redeemed_at = ? WHERE customer_id = ? AND redeemed_at IS NULL",
                (now(), customer_id),
            )
            db.commit()
            what = "1 descuento" if count == 1 else f"{count} descuentos"
            flash(f"Canjeaste {what}" + (f" por {format_cop(total)}." if total else "."), "ok")
        else:
            flash("Ese cliente no tiene descuentos por canjear.", "error")
        return redirect(url_for("admin_customer_detail", customer_id=customer_id))

    @app.route("/admin/canjear", methods=["GET", "POST"])
    @admin_required
    def admin_quick_redeem():
        """Descuentos por código: buscar al cliente para canjear, o asignarle un descuento."""
        code = normalize_code(request.values.get("code"))
        if code:
            customer = find_customer_by_code(code)
            if customer:
                return redirect(url_for("admin_customer_detail", customer_id=customer["id"]))
            flash("Código no encontrado.", "error")
        return render_template("admin/quick_redeem.html", code=code, give={})

    @app.route("/admin/canjear/asignar", methods=["POST"])
    @admin_required
    def admin_give_by_code():
        give = {k: (request.form.get(k) or "").strip() for k in ("code", "amount", "note")}
        customer = find_customer_by_code(give["code"])
        if not customer:
            flash("No hay ningún cliente con ese código.", "error")
        elif give_discount(customer):
            return redirect(url_for("admin_customer_detail", customer_id=customer["id"]))
        return render_template("admin/quick_redeem.html", code="", give=give)

    # ---- menú del día

    @app.route("/admin/menu")
    @admin_required
    def admin_products():
        products = get_db().execute(
            "SELECT * FROM products ORDER BY active DESC, featured DESC, created_at DESC"
        ).fetchall()
        return render_template("admin/products.html", products=products)

    @app.route("/admin/menu/nuevo", methods=["GET", "POST"])
    @app.route("/admin/menu/<int:product_id>/editar", methods=["GET", "POST"])
    @admin_required
    def admin_product_form(product_id=None):
        db = get_db()
        product = None
        if product_id:
            product = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
            if not product:
                abort(404)
        if request.method == "POST":
            data = {
                "title": (request.form.get("title") or "").strip(),
                "description": (request.form.get("description") or "").strip(),
                "price": normalize_price(request.form.get("price")),
                "category": (request.form.get("category") or "").strip(),
                "featured": 1 if request.form.get("featured") else 0,
                "active": 1 if request.form.get("active") else 0,
            }
            if not data["title"]:
                flash("El nombre del producto es obligatorio.", "error")
                return render_template("admin/product_form.html", product=data, editing=bool(product))
            try:
                image = save_image(request.files.get("image"))
            except ValueError as exc:
                flash(str(exc), "error")
                return render_template("admin/product_form.html", product=data, editing=bool(product))
            if product:
                if image or request.form.get("remove_image"):
                    delete_image(product["image"])
                else:
                    image = product["image"]
                db.execute(
                    "UPDATE products SET title=?, description=?, price=?, category=?, "
                    "featured=?, active=?, image=? WHERE id=?",
                    (*data.values(), image, product_id),
                )
                flash("Producto actualizado.", "ok")
            else:
                db.execute(
                    "INSERT INTO products (title, description, price, category, featured, active, "
                    "image, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (*data.values(), image, now()),
                )
                flash("Novedad publicada en el menú del día.", "ok")
            db.commit()
            return redirect(url_for("admin_products"))
        categories = [r[0] for r in db.execute(
            "SELECT DISTINCT category FROM products WHERE category != '' ORDER BY category")]
        blank = {"title": "", "description": "", "price": "", "category": "",
                 "featured": 0, "active": 1, "image": None}
        return render_template(
            "admin/product_form.html", product=product or blank, editing=bool(product),
            categories=categories,
        )

    @app.route("/admin/menu/<int:product_id>/toggle", methods=["POST"])
    @admin_required
    def admin_product_toggle(product_id):
        db = get_db()
        db.execute("UPDATE products SET active = 1 - active WHERE id = ?", (product_id,))
        db.commit()
        return redirect(url_for("admin_products"))

    @app.route("/admin/menu/<int:product_id>/borrar", methods=["POST"])
    @admin_required
    def admin_product_delete(product_id):
        db = get_db()
        product = db.execute("SELECT * FROM products WHERE id = ?", (product_id,)).fetchone()
        if product:
            delete_image(product["image"])
            db.execute("DELETE FROM products WHERE id = ?", (product_id,))
            db.commit()
            flash("Producto eliminado.", "ok")
        return redirect(url_for("admin_products"))

    @app.route("/admin/menu/vaciar", methods=["POST"])
    @admin_required
    def admin_products_clear():
        db = get_db()
        db.execute("UPDATE products SET active = 0")
        db.commit()
        flash("Menú del día vaciado. Los productos quedan guardados para reactivarlos.", "ok")
        return redirect(url_for("admin_products"))

    # ---- ajustes

    @app.route("/admin/ajustes", methods=["GET", "POST"])
    @admin_required
    def admin_settings():
        if request.method == "POST":
            form = {k: (request.form.get(k) or "").strip()
                    for k in ("store_name", "tagline", "code_prefix", "whatsapp")}
            whatsapp = normalize_whatsapp(form["whatsapp"])
            new_pw = request.form.get("new_password", "")
            error = None
            if form["whatsapp"] and not 10 <= len(whatsapp) <= 15:
                error = "Escribe el WhatsApp con el indicativo del país, por ejemplo 57 300 123 4567."
            elif new_pw and len(new_pw) < 6:
                error = "La nueva contraseña debe tener al menos 6 caracteres."
            if error:
                flash(error, "error")
                return render_template("admin/settings.html", settings={**get_settings(), **form},
                                       wa_saved=get_settings()["whatsapp"])
            for key in ("store_name", "tagline"):
                set_setting(key, form[key] or DEFAULT_SETTINGS[key])
            set_setting("code_prefix", normalize_code(form["code_prefix"])[:6] or DEFAULT_SETTINGS["code_prefix"])
            set_setting("whatsapp", whatsapp)
            if new_pw:
                set_setting("admin_password", generate_password_hash(new_pw))
            get_db().commit()
            flash("Ajustes guardados.", "ok")
            return redirect(url_for("admin_settings"))
        return render_template("admin/settings.html", wa_saved=get_settings()["whatsapp"])


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=bool(os.environ.get("DEBUG")))
