"""Templo de Anubis — sistema de referidos y menú del día para smoke shop.

Aplicación Flask con SQLite en local y Postgres (Supabase) en producción, si hay
DATABASE_URL. Una sola persona administra (el dueño de la tienda):
registra clientes, controla quién refirió a quién, entrega y canjea
descuentos, y publica las novedades del día. Los clientes ven el menú del día,
consultan su código de referido y envían su pedido por WhatsApp; cada pedido
queda en el historial del panel.
"""
import hashlib
import io
import json
import os
import secrets
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from itertools import zip_longest
from urllib.parse import quote, urlsplit

from flask import (
    Flask, Response, abort, flash, g, redirect, render_template, request, send_from_directory, session,
    url_for,
)
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# En Vercel el código es de solo lectura: lo único escribible es /tmp, que se
# borra cuando la función se recicla. Para datos reales: DATABASE_URL y Supabase Storage.
ON_VERCEL = bool(os.environ.get("VERCEL"))
IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg", "webp": "image/webp", "gif": "image/gif"}
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sin 0/O ni 1/I

DEFAULT_SETTINGS = {
    "store_name": "Templo de Anubis",
    "tagline": "Humo sagrado · Novedades frescas cada día",
    "code_prefix": "ANK",
    "whatsapp": "",              # WhatsApp de la tienda para pedidos: dígitos con indicativo (57...)
    "referral_value": "0",       # pesos que gana un cliente por cada persona que se registra con su enlace
    "self_signup": "1",          # los clientes pueden registrarse solos desde la web
    "payment_methods": "Efectivo, Nequi, Daviplata, Transferencia",   # el cliente elige uno al pedir
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
    origin      TEXT NOT NULL DEFAULT 'tienda',  -- tienda (lo registró el administrador) | web (se registró solo)
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
CREATE TABLE IF NOT EXISTS product_options (     -- medidas de un producto, cada una con su precio
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,               -- "10 g", "Caja x 3"
    price       INTEGER NOT NULL,            -- en pesos
    position    INTEGER NOT NULL DEFAULT 0   -- orden en que se muestran
);
CREATE TABLE IF NOT EXISTS orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token       TEXT NOT NULL,               -- id aleatorio del carrito: reenviarlo no duplica el pedido
    status      TEXT NOT NULL DEFAULT 'nuevo',  -- nuevo | confirmado | entregado | cancelado
    items       TEXT NOT NULL,               -- JSON: [{product_id, title, qty, price, label, option_id?, option?}]
    total       INTEGER NOT NULL DEFAULT 0,  -- suma de los productos con precio, en pesos
    pending     INTEGER NOT NULL DEFAULT 0,  -- 1 si hay productos con precio a confirmar
    name        TEXT NOT NULL DEFAULT '',
    code        TEXT NOT NULL DEFAULT '',    -- código de cliente tal como lo escribió
    customer_id INTEGER REFERENCES customers(id) ON DELETE SET NULL,
    note        TEXT NOT NULL DEFAULT '',
    payment     TEXT NOT NULL DEFAULT '',    -- método de pago que eligió
    address     TEXT NOT NULL DEFAULT '',    -- dirección y referencias para el domicilio
    location    TEXT NOT NULL DEFAULT '',    -- "lat,lng" del GPS del cliente
    photo       TEXT,                        -- foto de la calle o la fachada (opcional)
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL                -- última vez que el cliente lo envió
);
CREATE INDEX IF NOT EXISTS idx_customers_referred_by ON customers(referred_by);
CREATE INDEX IF NOT EXISTS idx_rewards_customer ON rewards(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_token ON orders(token);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_product_options_product ON product_options(product_id);
"""

# El mismo esquema en Postgres: ids con IDENTITY y enteros de 64 bits, como los de SQLite.
PG_SCHEMA = (SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")
             .replace("INTEGER", "BIGINT"))
TABLES = ("settings", "customers", "rewards", "products", "product_options", "orders")

REWARD_LABELS = {
    "referral": "Por referido",
    "welcome": "Bienvenida",
    "milestone": "Premio de meta",
    "manual": "Descuento asignado",
}

MAX_DISCOUNT = 10_000_000  # tope de seguridad contra ceros de más al escribir el valor
MAX_PRICE = 100_000_000    # tope del precio de una medida (mismo motivo)
MAX_PRODUCT_OPTIONS = 12   # medidas por producto
# Foto de la vista previa al compartir un producto: WhatsApp no la muestra si pesa más de ~300 KB.
SHARE_IMAGE_SIDE = 1080
SHARE_IMAGE_MAX_BYTES = 250_000

ORDER_STATUSES = {         # en este orden salen los filtros del historial
    "nuevo": "Nuevo",
    "confirmado": "Confirmado",
    "entregado": "Entregado",
    "cancelado": "Cancelado",
}
MAX_ORDER_LINES = 40       # líneas por pedido (cada producto o cada medida de un producto)
MAX_ORDER_QTY = 99         # unidades por línea (igual que el carrito)
ORDER_LIMIT = (10, 600)    # pedidos nuevos por dirección IP en 10 minutos
SIGNUP_LIMIT = (20, 3600)  # registros por dirección IP en una hora (holgado: en la tienda comparten wifi)
ORDERS_PER_PAGE = 30

MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
# Hora de la tienda: Colombia, UTC-5 todo el año. Los servidores (Vercel incluido) van en UTC.
STORE_TZ = timezone(timedelta(hours=-5))


def create_app(test_config=None):
    app = Flask(
        __name__, instance_relative_config=True,
        instance_path="/tmp/smokeshop" if ON_VERCEL else None,
    )
    default_uploads = (os.path.join(app.instance_path, "uploads") if ON_VERCEL
                       else os.path.join(BASE_DIR, "static", "uploads"))
    database_url = env("DATABASE_URL")
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or _fallback_secret(app),
        DATABASE=os.environ.get("DATABASE") or os.path.join(app.instance_path, "smokeshop.db"),
        DATABASE_URL=database_url,            # Postgres (Supabase); vacío = SQLite en DATABASE
        UPLOAD_FOLDER=os.environ.get("UPLOAD_FOLDER") or default_uploads,
        # Fotos en Supabase Storage; sin estas variables van a UPLOAD_FOLDER.
        SUPABASE_URL=env("SUPABASE_URL"),
        SUPABASE_SECRET_KEY=env("SUPABASE_SECRET_KEY") or env("SUPABASE_SERVICE_ROLE_KEY"),
        SUPABASE_BUCKET=env("SUPABASE_BUCKET") or "productos",
        # Datos de ejemplo solo en la vista previa de Vercel sin base de datos propia.
        DEMO_DATA=os.environ.get("DEMO_DATA", "1" if ON_VERCEL and not database_url else "0") == "1",
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

    if ON_VERCEL:
        # Detrás del proxy de Vercel: las direcciones completas (enlaces para compartir, og:image)
        # salen con https y el dominio público.
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    register_hooks(app)
    register_public_routes(app)
    register_admin_routes(app)
    return app


def env(name):
    """Variable de entorno sin los espacios o comillas que a veces se pegan al copiarla."""
    return os.environ.get(name, "").strip().strip("\"'")


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
#
# SQLite (archivo local) o Postgres (Supabase, con DATABASE_URL). Las dos conexiones se
# usan igual: SQL con marcadores `?`, filas que se leen por nombre o por posición, e
# insert() que devuelve el id nuevo.

class SqliteDB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql, params=()):
        return self.conn.execute(sql, params)

    def script(self, sql):
        self.conn.executescript(sql)

    def insert(self, sql, params=()):
        return self.conn.execute(sql, params).lastrowid

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


class PgRow:
    """Fila de Postgres que se lee como sqlite3.Row: row[0], row["nombre"], dict(row) y
    desempaquetar. No es una tupla para que `fila.count` en una plantilla sea la columna."""

    __slots__ = ("_values", "_columns")

    def __init__(self, values, columns):
        self._values, self._columns = tuple(values), columns

    def __getitem__(self, key):
        return self._values[self._columns[key] if isinstance(key, str) else key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return self._columns.keys()


def _pg_rows(cursor):
    columns = {col.name: i for i, col in enumerate(cursor.description or ())}
    return lambda values: PgRow(values, columns)


class PgDB:
    def __init__(self, url):
        import psycopg
        # El pooler de Supabase (modo transacción) no admite sentencias preparadas.
        self.conn = psycopg.connect(url, prepare_threshold=None, row_factory=_pg_rows, connect_timeout=10)

    @staticmethod
    def _sql(sql):
        # Marcadores de SQLite (?) a los de psycopg (%s). LIKE en SQLite no distingue
        # mayúsculas; en Postgres eso es ILIKE.
        return sql.replace("%", "%%").replace("?", "%s").replace(" LIKE ", " ILIKE ")

    def execute(self, sql, params=()):
        return self.conn.execute(self._sql(sql), params)

    def script(self, sql):
        self.conn.execute(sql)                # sin parámetros: admite varias sentencias

    def insert(self, sql, params=()):
        return self.conn.execute(self._sql(sql) + " RETURNING id", params).fetchone()[0]

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


def get_db():
    if "db" not in g:
        from flask import current_app
        url = current_app.config["DATABASE_URL"]
        g.db = PgDB(url) if url else SqliteDB(current_app.config["DATABASE"])
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def ensure_column(db, table, column, definition):
    """Agrega `column` a `table` si todavía no existe (en SQLite o en Postgres)."""
    if isinstance(db, PgDB):
        exists = db.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = 'public' "
            "AND table_name = ? AND column_name = ?", (table, column),
        ).fetchone()
    else:
        exists = any(row["name"] == column for row in db.execute(f"PRAGMA table_info({table})"))
    if not exists:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    db = get_db()
    if isinstance(db, PgDB):
        db.script(PG_SCHEMA)
        # Sin políticas de RLS nadie puede leer las tablas por la API pública de Supabase;
        # la app entra con el usuario dueño de las tablas, que no está sujeto a RLS.
        for (table,) in db.execute(
            "SELECT relname FROM pg_class WHERE relnamespace = 'public'::regnamespace "
            "AND relkind = 'r' AND NOT relrowsecurity"
        ).fetchall():
            if table in TABLES:
                db.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    else:
        db.script(SCHEMA)
    # Columnas que llegaron después: las bases creadas antes no las tienen.
    ensure_column(db, "rewards", "amount", "BIGINT NOT NULL DEFAULT 0")      # descuentos en pesos
    ensure_column(db, "customers", "origin", "TEXT NOT NULL DEFAULT 'tienda'")
    for column, definition in (("payment", "TEXT NOT NULL DEFAULT ''"), ("address", "TEXT NOT NULL DEFAULT ''"),
                               ("location", "TEXT NOT NULL DEFAULT ''"), ("photo", "TEXT")):
        ensure_column(db, "orders", column, definition)                    # datos de entrega
    for key, value in DEFAULT_SETTINGS.items():
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING", (key, value))
    # El WhatsApp de los pedidos sale solo de Ajustes. Antes una variable STORE_WHATSAPP llenaba las
    # bases nuevas, y así una copia de la página podía mandar los pedidos a un número viejo.
    if os.environ.get("ADMIN_PASSWORD"):
        db.execute(
            "INSERT INTO settings (key, value) VALUES ('admin_password', ?) ON CONFLICT (key) DO NOTHING",
            (generate_password_hash(os.environ["ADMIN_PASSWORD"]),),
        )
    db.commit()


DEMO_PRODUCTS = [
    ("Pipa Lapislázuli", "Vidrio soplado azul profundo con filo dorado.", "85000", "Pipas", 1),
    ("Bong Obelisco", "Vidrio borosilicato de 30 cm con percolador.", "240000", "Bongs", 1),
    ("Papel Oro 24k", "Edición limitada, combustión lenta.", "12000", "Papeles", 0),
    ("Grinder Escarabajo", "Aluminio negro mate, 4 piezas.", "65000", "Accesorios", 0),
    ("Encendedor Ankh", "Recargable, grabado egipcio.", "18000", "Accesorios", 0),
    ("Blunt Wraps Faraón", "Sabor mango.", "9000", "Papeles", 0),
]
DEMO_OPTIONS = {"Blunt Wraps Faraón": [("Paquete x2", 9000), ("Paquete x6", 24000), ("Caja x24", 85000)]}


def seed_demo():
    """Llena una base vacía con datos de ejemplo para enseñar el diseño."""
    db = get_db()
    if db.execute("SELECT 1 FROM products UNION ALL SELECT 1 FROM customers LIMIT 1").fetchone():
        return
    for title, desc, price, cat, featured in DEMO_PRODUCTS:
        product_id = db.insert(
            "INSERT INTO products (title, description, price, category, featured, active, created_at) "
            "VALUES (?, ?, ?, ?, ?, 1, ?)",
            (title, desc, price, cat, featured, now()),
        )
        options = [{"id": None, "label": label, "price": value} for label, value in DEMO_OPTIONS.get(title, [])]
        save_product_options(db, product_id, options)
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


def local_now():
    return datetime.now(STORE_TZ).replace(tzinfo=None)


def now():
    return local_now().isoformat(timespec="seconds")


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
    """Quita '$', 'COP', espacios y separadores de miles: '$ 45.000 COP' -> '45000'.

    También entiende los miles abreviados: '50k' o '50 mil' -> '50000'.
    """
    compact = (raw or "").upper().replace("COP", "").replace("$", "").replace(" ", "")
    for suffix in ("MIL", "K"):
        if compact.endswith(suffix) and compact[:-len(suffix)].isdecimal():
            return compact[:-len(suffix)] + "000"
    return compact.replace(".", "").replace(",", "")


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


def register_customer(name, phone, referrer=None, notes="", referrer_amount=0, welcome_amount=0, origin="tienda"):
    """Crea el cliente y, si los hay, los descuentos del referido.

    `referrer_amount` es para quien lo refirió y `welcome_amount` para el cliente nuevo,
    ambos en pesos. `origin` es "tienda" (lo registró el administrador) o "web" (se
    registró solo). Devuelve el id nuevo.
    """
    db = get_db()
    new_id = db.insert(
        "INSERT INTO customers (name, phone, code, referred_by, notes, origin, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (name, phone, generate_code(), referrer["id"] if referrer else None, notes, origin, now()),
    )
    if referrer:
        add_reward(referrer["id"], "referral", referrer_amount, new_id)
    add_reward(new_id, "welcome", welcome_amount, referrer["id"] if referrer else None)
    db.commit()
    return new_id


def referral_value():
    """Pesos que gana un cliente por cada persona que se registra con su enlace o su código."""
    value = get_settings().get("referral_value", "0")
    return int(value) if value.isdecimal() else 0


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


# ---------------------------------------------------------------------- pedidos

def order_number(order_id):
    return f"#{order_id:04d}"


def options_by_product(product_ids):
    """Medidas de esos productos, en su orden: {product_id: [{id, label, price}, …]}."""
    ids = tuple(product_ids)
    if not ids:
        return {}
    options = {}
    for row in get_db().execute(
        f"SELECT id, product_id, label, price FROM product_options WHERE product_id IN ({','.join('?' * len(ids))}) "
        "ORDER BY position, id",
        ids,
    ).fetchall():
        options.setdefault(row["product_id"], []).append({"id": row["id"], "label": row["label"], "price": row["price"]})
    return options


def parse_option_rows(form):
    """Filas de medida y precio del formulario del producto -> (medidas, error o None).

    Las filas vacías no cuentan. Cada medida lleva nombre ("10 g") y precio en pesos; si una
    fila no sirve, su precio queda como lo escribió para volver a mostrarlo en el formulario.
    """
    rows = zip_longest(form.getlist("option_id"), form.getlist("option_label"), form.getlist("option_price"),
                       fillvalue="")
    options, errors, seen = [], [], set()
    for option_id, label, price in rows:
        label, price = label.strip()[:40], price.strip()
        if not label and not price:
            continue
        value = parse_cop(price)
        if not label:
            errors.append(f"Escribe la medida que cuesta {price} (por ejemplo 10 g).")
        elif not value:
            errors.append(f"Ponle a «{label}» un precio en pesos, por ejemplo 50000.")
        elif value > MAX_PRICE:
            errors.append(f"Revisa el precio de «{label}»: parece que tiene ceros de más.")
        elif label.lower() in seen:
            errors.append(f"La medida «{label}» está repetida.")
        seen.add(label.lower())
        ok = bool(label) and bool(value) and value <= MAX_PRICE
        options.append({"id": int(option_id) if option_id.isdecimal() and len(option_id) < 10 else None,
                        "label": label, "price": value if ok else price})
    if len(options) > MAX_PRODUCT_OPTIONS:
        errors.append(f"Puedes poner hasta {MAX_PRODUCT_OPTIONS} medidas por producto.")
    return options, (errors[0] if errors else None)


def save_product_options(db, product_id, options):
    """Guarda las medidas en el orden del formulario. Las que ya existían conservan su id, así un
    carrito abierto que las tiene sigue sirviendo; las que se quitaron se borran."""
    existing = {row["id"] for row in db.execute(
        "SELECT id FROM product_options WHERE product_id = ?", (product_id,)).fetchall()}
    kept = set()
    for position, option in enumerate(options):
        if option["id"] in existing and option["id"] not in kept:
            db.execute("UPDATE product_options SET label = ?, price = ?, position = ? WHERE id = ?",
                       (option["label"], option["price"], position, option["id"]))
            kept.add(option["id"])
        else:
            db.execute("INSERT INTO product_options (product_id, label, price, position) VALUES (?, ?, ?, ?)",
                       (product_id, option["label"], option["price"], position))
    for option_id in existing - kept:
        db.execute("DELETE FROM product_options WHERE id = ?", (option_id,))


def parse_order_items(raw):
    """'3:2,5-8:1' (producto[-medida]:cantidad, lo arma el carrito) -> {(3, None): 2, (5, 8): 1}.

    Ignora lo que no encaje.
    """
    wanted = {}
    for part in (raw or "").split(",")[:MAX_ORDER_LINES]:
        key, _, qty = part.strip().partition(":")
        product_id, dash, option_id = key.partition("-")
        if not (product_id.isdecimal() and len(product_id) < 10 and qty.isdecimal() and len(qty) < 4 and int(qty)):
            continue
        if dash and not (option_id.isdecimal() and len(option_id) < 10):
            continue
        wanted[(int(product_id), int(option_id) if dash else None)] = min(int(qty), MAX_ORDER_QTY)
    return wanted


def order_lines(wanted):
    """Líneas del pedido con nombre, medida y precio del menú de hoy: del navegador solo se toman
    ids y cantidades. Un producto con medidas solo se pide con una de sus medidas."""
    product_ids = {product_id for product_id, _ in wanted}
    if not product_ids:
        return []
    rows = get_db().execute(
        f"SELECT id, title, price FROM products WHERE active = 1 AND id IN ({','.join('?' * len(product_ids))}) "
        "ORDER BY featured DESC, created_at DESC, id DESC",
        tuple(product_ids),
    ).fetchall()
    options = options_by_product(p["id"] for p in rows)
    lines = []
    for p in rows:
        if p["id"] in options:
            for option in options[p["id"]]:
                qty = wanted.get((p["id"], option["id"]))
                if qty:
                    lines.append({"product_id": p["id"], "option_id": option["id"], "title": p["title"],
                                  "option": option["label"], "qty": qty, "price": option["price"], "label": ""})
        elif (p["id"], None) in wanted:
            price = int(p["price"]) if p["price"].isdecimal() else None
            lines.append({"product_id": p["id"], "title": p["title"], "qty": wanted[(p["id"], None)],
                          "price": price, "label": "" if price is not None else p["price"]})
    return lines


def line_title(line):
    """Nombre de una línea del pedido: el producto y, si la tiene, la medida ("Flor (10 g)")."""
    return f"{line['title']} ({line['option']})" if line.get("option") else line["title"]


def order_totals(lines):
    """(suma en pesos de los productos con precio, si hay alguno con precio a confirmar)."""
    total = sum(line["price"] * line["qty"] for line in lines if line["price"] is not None)
    return total, any(line["price"] is None for line in lines)


def payment_methods():
    """Métodos de pago que acepta la tienda (Ajustes), en orden."""
    raw = get_settings().get("payment_methods") or DEFAULT_SETTINGS["payment_methods"]
    return [m.strip() for m in raw.split(",") if m.strip()]


def parse_location(lat, lng):
    """Coordenadas del GPS del navegador -> "4.609700,-74.081700", o "" si no son válidas."""
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return ""
    if not (-90 <= lat <= 90 and -180 <= lng <= 180) or (lat == 0 and lng == 0):
        return ""
    return f"{lat:.6f},{lng:.6f}"


def maps_url(location):
    """Enlace de Google Maps que el domiciliario abre para llegar."""
    return f"https://www.google.com/maps/search/?api=1&query={location}"


def order_message(order_id, lines, name="", code="", note="", payment="", location="", accuracy=0,
                  address="", photo_url=""):
    """Texto del pedido que llega al WhatsApp de la tienda."""
    out = [f"Hola, {get_settings()['store_name']}. Quiero hacer el pedido {order_number(order_id)}:", ""]
    for line in lines:
        if line["price"] is not None:
            price = format_cop(line["price"] * line["qty"])
        elif line["label"]:
            price = line["label"] + (" c/u" if line["qty"] > 1 else "")
        else:
            price = "precio a confirmar"
        out.append(f"• {line['qty']} × {line_title(line)} — {price}")
    total, pending = order_totals(lines)
    out.append("")
    if total and pending:
        out.append(f"Total: {format_cop(total)} COP + productos con precio a confirmar")
    elif total:
        out.append(f"Total: {format_cop(total)} COP")
    else:
        out.append("Total: por confirmar")
    if payment:
        out.append(f"Pago: {payment}")
    if location:
        out.append(f"Ubicación: {maps_url(location)}" + (f" (aprox. ±{accuracy} m)" if accuracy > 60 else ""))
    if address:
        out.append(f"Dirección: {address}")
    if photo_url:
        out.append(f"Foto de la entrada: {photo_url}")
    if name:
        out.append(f"Nombre: {name}")
    if code:
        out.append(f"Mi código de cliente: {code} (quiero usar mis descuentos)")
    if note:
        out.append(f"Nota: {note}")
    return "\n".join(out)


def order_view(row):
    """Fila de `orders` lista para las plantillas: con sus líneas y un resumen corto."""
    order = dict(row)
    order["lines"] = json.loads(order["items"])
    order["summary"] = ", ".join(f"{line['qty']} × {line_title(line)}" for line in order["lines"])
    return order


def rate_limited(kind, limit_window):
    """Freno básico contra pedidos o registros falsos en masa: como mucho `limit` por IP cada
    `window` segundos. Cuenta la acción si no está frenada.

    La cuenta vive en memoria (cada proceso lleva la suya): no es infalible, pero evita que un
    script llene el historial o reparta puntos de referido.
    """
    from flask import current_app
    limit, window = limit_window
    log = current_app.extensions.setdefault("rate_log", {})
    key = (kind, request.access_route[0] if request.access_route else "")
    moment = time.time()
    if len(log) > 1000:                       # olvida direcciones sin actividad reciente
        for old in [k for k, times in log.items() if not times or moment - times[-1] >= window]:
            del log[old]
    recent = [t for t in log.get(key, ()) if moment - t < window]
    blocked = len(recent) >= limit
    if not blocked:
        recent.append(moment)
    log[key] = recent
    return blocked


def allowed_image(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in IMAGE_TYPES


# Fotos: con SUPABASE_URL y SUPABASE_SECRET_KEY van al bucket público de Supabase Storage
# (en Vercel el disco se borra); si no, a UPLOAD_FOLDER.

def storage_target():
    """(url, clave, bucket) de Supabase Storage, o None si las fotos van al disco."""
    from flask import current_app
    cfg = current_app.config
    if cfg["SUPABASE_URL"] and cfg["SUPABASE_SECRET_KEY"]:
        return cfg["SUPABASE_URL"].rstrip("/"), cfg["SUPABASE_SECRET_KEY"], cfg["SUPABASE_BUCKET"]
    return None


def storage_request(method, name, data=None, content_type=None):
    url, key, bucket = storage_target()
    req = urllib.request.Request(f"{url}/storage/v1/object/{bucket}/{quote(name)}", data=data, method=method)
    # Las claves nuevas (sb_secret_…) solo valen en Authorization si es igual a apikey.
    req.add_header("apikey", key)
    req.add_header("Authorization", f"Bearer {key}")
    if content_type:
        req.add_header("Content-Type", content_type)
        req.add_header("Cache-Control", "max-age=31536000")   # cada foto tiene un nombre único
    with urllib.request.urlopen(req, timeout=20):
        pass


def storage_error(exc):
    """Detalle de un error de Supabase Storage para el registro del servidor."""
    if isinstance(exc, urllib.error.HTTPError):
        return f"{exc.code} {exc.read()[:300].decode('utf-8', 'replace')}"
    return str(exc)


def save_image(file_storage, prefix=""):
    from flask import current_app
    if not file_storage or not file_storage.filename:
        return None
    if not allowed_image(file_storage.filename):
        raise ValueError("Formato de imagen no permitido (usa png, jpg, webp o gif).")
    ext = file_storage.filename.rsplit(".", 1)[1].lower()
    name = f"{prefix}{uuid.uuid4().hex}.{ext}"
    if storage_target():
        try:
            storage_request("POST", name, file_storage.read(), IMAGE_TYPES[ext])
        except OSError as exc:                # HTTPError, URLError y tiempos de espera
            current_app.logger.error("Supabase Storage no guardó la foto: %s", storage_error(exc))
            raise ValueError(
                "No se pudo guardar la foto en Supabase. Revisa SUPABASE_URL, SUPABASE_SECRET_KEY "
                f"y que exista el bucket público “{current_app.config['SUPABASE_BUCKET']}”."
            ) from exc
    else:
        file_storage.save(os.path.join(current_app.config["UPLOAD_FOLDER"], name))
    return name


def delete_image(name):
    from flask import current_app
    if not name:
        return
    name = os.path.basename(name)
    if storage_target():
        try:
            storage_request("DELETE", name)
        except OSError as exc:                # una foto huérfana en el bucket no rompe nada
            current_app.logger.warning("No se pudo borrar %s de Supabase Storage: %s", name, storage_error(exc))
        return
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], name)
    if os.path.exists(path):
        os.remove(path)


def storage_status():
    """Dónde quedan los datos y las fotos: (nombre, detalle, permanente) de cada uno, y qué copia
    de la página es esta (en Vercel: la publicada o una vista previa, con la dirección de la publicada)."""
    from flask import current_app
    cfg = current_app.config
    if cfg["DATABASE_URL"]:
        parts = urlsplit(cfg["DATABASE_URL"])
        user, host = parts.username or "", parts.hostname or ""
        # id del proyecto: usuario "postgres.<id>" (pooler) o servidor "db.<id>.supabase.co"
        ref = user.split(".", 1)[1] if "." in user else (host.split(".")[1] if host.startswith("db.") else "")
        db = ("Supabase" if "supabase" in host else "Postgres", f"Proyecto {ref}" if ref else host, True)
    elif ON_VERCEL:
        db = ("Temporal", "Se borra cada vez que Vercel reinicia la app", False)
    else:
        db = ("Archivo en este equipo", os.path.basename(cfg["DATABASE"]), True)
    target = storage_target()
    if target:
        ref = (urlsplit(target[0]).hostname or "").split(".")[0]
        photos = ("Supabase Storage", f"Bucket {target[2]} · proyecto {ref}", True)
    elif ON_VERCEL:
        photos = ("Temporales", "Se borran cada vez que Vercel reinicia la app", False)
    else:
        photos = ("Disco de este equipo", "Carpeta de subidas", True)
    return {"db": db, "photos": photos, "temporary": not (db[2] and photos[2]),
            "vercel_env": env("VERCEL_ENV"), "public_url": env("VERCEL_PROJECT_PRODUCTION_URL")}


def image_url(name, external=False):
    """Dirección pública de una foto (del menú o de un pedido)."""
    target = storage_target()
    if target:
        url, _key, bucket = target
        return f"{url}/storage/v1/object/public/{bucket}/{quote(name)}"
    return url_for("uploaded", name=name, _external=external)


def read_image(name):
    """Bytes de una foto guardada (en Supabase Storage o en la carpeta de subidas), o None."""
    from flask import current_app
    name = os.path.basename(name or "")
    if not name:
        return None
    if storage_target():
        try:
            with urllib.request.urlopen(image_url(name), timeout=10) as resp:
                return resp.read(current_app.config["MAX_CONTENT_LENGTH"] + 1)
        except OSError:
            return None
    path = os.path.join(current_app.config["UPLOAD_FOLDER"], name)
    if not os.path.isfile(path):
        return None
    with open(path, "rb") as fh:
        return fh.read()


def share_image_name(image):
    """Nombre de la foto para compartir: cambia cuando cambia la foto (y así se renueva la caché)."""
    return os.path.splitext(os.path.basename(image))[0]


def share_image_bytes(data):
    """Foto de un producto lista para la vista previa de WhatsApp: JPEG de hasta SHARE_IMAGE_SIDE px
    por lado y menos de SHARE_IMAGE_MAX_BYTES (baja la calidad y, si hace falta, el tamaño)."""
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data)))
    if img.mode in ("RGBA", "LA", "P"):                  # transparencia sobre el fondo de la tienda
        rgba = img.convert("RGBA")
        img = Image.new("RGB", rgba.size, (4, 6, 4))
        img.paste(rgba, mask=rgba.getchannel("A"))
    img = img.convert("RGB")
    side = SHARE_IMAGE_SIDE
    while True:
        small = img.copy()
        small.thumbnail((side, side), Image.LANCZOS)
        for quality in (85, 78, 70, 62):
            out = io.BytesIO()
            small.save(out, "JPEG", quality=quality, optimize=True)
            if out.tell() <= SHARE_IMAGE_MAX_BYTES or side <= 480:
                return out.getvalue()
        side = side * 3 // 4


def price_text(product, sizes):
    """Precio corto de un producto: "$240.000", "desde $50.000" (con medidas) o el texto que
    tenga ("Consultar")."""
    if sizes:
        low = format_cop(min(option["price"] for option in sizes))
        return low if len(sizes) == 1 else f"desde {low}"
    return format_cop(product["price"]) or product["price"]


def product_share(product, sizes):
    """Lo que se comparte de un producto: el enlace a su página (con la foto que WhatsApp muestra en
    la vista previa), el título, la descripción y el texto que acompaña al enlace."""
    settings = get_settings()
    price = price_text(product, sizes)
    title = f"{product['title']} — {price}" if price else product["title"]
    detail = " · ".join(f"{option['label']} {format_cop(option['price'])}" for option in sizes)
    description = " · ".join(part for part in (detail, product["description"]) if part) or settings["tagline"]
    if product["image"]:
        image = url_for("product_share_image", product_id=product["id"],
                        name=share_image_name(product["image"]), _external=True)
    else:
        image = url_for("static", filename="img/anubis.jpg", _external=True)
    return {
        "url": url_for("product_page", product_id=product["id"], _external=True),
        "title": title,
        "description": description[:200],
        "image": image,
        "text": f"{title} en {settings['store_name']}. Pídelo aquí:",
    }


def asset_version():
    """Huella corta de los estilos y los scripts: cambia cuando cambian, para invalidar la caché."""
    if "asset_v" not in ASSET_CACHE:
        digest = hashlib.md5()
        for name in ("css/anubis.css", "css/fonts.css", "js/cart.js", "js/photo.js", "js/share.js"):
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
        admin = is_admin()
        return {
            "asset_v": asset_version(),
            "settings": get_settings(),
            "csrf_token": session.get("csrf", ""),
            "is_admin": admin,
            "reward_labels": REWARD_LABELS,
            "order_statuses": ORDER_STATUSES,
            # contador de la barra del panel
            "new_orders": get_db().execute(
                "SELECT COUNT(*) FROM orders WHERE status = 'nuevo'").fetchone()[0] if admin else 0,
            "storage": storage_status() if admin else None,
            "referral_value": referral_value(),
            "self_signup": get_settings().get("self_signup") == "1",
        }

    @app.template_filter("fecha")
    def fecha(value):
        if not value:
            return ""
        try:
            dt = datetime.fromisoformat(value)
        except ValueError:
            return value
        return f"{dt.day} {MESES[dt.month - 1]} {dt.year}"

    @app.template_filter("fecha_hora")
    def fecha_hora(value):
        """'2026-10-24T15:42:00' -> 'Hoy, 3:42 p. m.', 'Ayer, …' o '22 oct 2026, …'."""
        try:
            dt = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return value or ""
        hour = f"{dt.hour % 12 or 12}:{dt.minute:02d} {'a. m.' if dt.hour < 12 else 'p. m.'}"
        days = (local_now().date() - dt.date()).days
        day = {0: "Hoy", 1: "Ayer"}.get(days) or fecha(value)
        return f"{day}, {hour}"

    app.template_filter("cop")(format_cop)
    app.template_filter("reward_value")(reward_value)
    app.template_filter("phone")(phone_display)
    app.template_filter("wa")(normalize_whatsapp)
    app.template_filter("pedido")(order_number)
    app.template_filter("producto")(line_title)
    app.jinja_env.globals["image_url"] = image_url
    app.jinja_env.globals["maps_url"] = maps_url

    @app.template_filter("first_name")
    def first_name(value):
        return (value or "").split(" ")[0]

    @app.errorhandler(413)
    def too_large(_e):
        flash("La imagen es demasiado pesada (máximo 6 MB).", "error")
        return redirect(request.referrer or url_for("admin_products"))


# ----------------------------------------------------------------------- public

def render_menu(shared=None):
    """El menú del día. Con `shared` (un producto que alguien compartió) la página lleva la vista
    previa de ese producto para WhatsApp y baja hasta él."""
    products = get_db().execute(
        "SELECT * FROM products WHERE active = 1 ORDER BY featured DESC, created_at DESC, id DESC"
    ).fetchall()
    categories = []
    for p in products:
        if p["category"] and p["category"] not in categories:
            categories.append(p["category"])
    options = options_by_product(p["id"] for p in products)
    # Lo que necesita el carrito (en el navegador) para calcular totales y armar el pedido.
    menu_items = [
        {
            "id": p["id"],
            "title": p["title"],
            "price": int(p["price"]) if p["price"].isdecimal() else None,
            "label": format_cop(p["price"]) or p["price"],
            "options": options.get(p["id"], []),
        }
        for p in products
    ]
    shares = {p["id"]: product_share(p, options.get(p["id"], [])) for p in products}
    return render_template(
        "public/menu.html", products=products, categories=categories, menu_items=menu_items,
        options=options, shares=shares, shared=shared, payment_methods=payment_methods(), today=local_now(),
    )


def register_public_routes(app):
    @app.route("/")
    def menu():
        return render_menu()

    @app.route("/p/<int:product_id>")
    def product_page(product_id):
        """Enlace para compartir un producto (en un estado de WhatsApp, por ejemplo)."""
        product = get_db().execute(
            "SELECT * FROM products WHERE id = ? AND active = 1", (product_id,)).fetchone()
        if not product:
            flash("Ese producto ya no está en el menú de hoy. Mira lo que tenemos.", "error")
            return redirect(url_for("menu"))
        return render_menu(shared=product)

    @app.route("/p/<int:product_id>/<name>.jpg")
    def product_share_image(product_id, name):
        """Foto liviana del producto para la vista previa del enlace. Vercel la guarda en caché."""
        product = get_db().execute("SELECT image FROM products WHERE id = ?", (product_id,)).fetchone()
        if not product or not product["image"]:
            abort(404)
        if name != share_image_name(product["image"]):          # foto cambiada: va a la nueva
            return redirect(url_for("product_share_image", product_id=product_id,
                                    name=share_image_name(product["image"])))
        data = read_image(product["image"])
        if not data:
            abort(404)
        try:
            jpeg = share_image_bytes(data)
        except (OSError, ValueError):                        # el archivo no es una imagen
            abort(404)
        resp = Response(jpeg, mimetype="image/jpeg")
        resp.headers["Cache-Control"] = "public, max-age=604800, s-maxage=31536000, immutable"
        return resp

    @app.route("/uploads/<path:name>")
    def uploaded(name):
        return send_from_directory(app.config["UPLOAD_FOLDER"], name, max_age=86400)

    @app.route("/mi-codigo", methods=["GET", "POST"])
    def my_code():
        """Consulta por código. El código queda recordado en este navegador (sesión)."""
        code = ""
        if request.method == "POST":
            code = normalize_code(request.form.get("code"))
            customer = find_customer_by_code(code)
            if customer:
                session["customer_code"] = customer["code"]
            else:
                flash("No encontramos ese código. Revísalo o pregúntanos en tienda.", "error")
        else:
            customer = find_customer_by_code(session.get("customer_code", ""))
        stats = customer_stats(customer["id"]) if customer else None
        return render_template("public/my_code.html", customer=customer, stats=stats,
                               code=code or (customer["code"] if customer else ""))

    @app.route("/mi-codigo/salir", methods=["POST"])
    def my_code_forget():
        session.pop("customer_code", None)
        return redirect(url_for("my_code"))

    @app.route("/r/<code>")
    def referral_landing(code):
        referrer = find_customer_by_code(code)
        if not referrer:
            abort(404)
        if get_settings()["self_signup"] != "1":   # registro cerrado: la invitación de siempre
            return render_template("public/invite.html", referrer=referrer)
        return render_template("public/register.html", referrer=referrer, form={"name": "", "phone": ""})

    @app.route("/registro", methods=["GET", "POST"])
    def register():
        """Registro de clientes desde la web. Si llegan con el código de alguien (enlace o
        escrito), quien los invitó gana el valor por referido de Ajustes."""
        if get_settings()["self_signup"] != "1":
            abort(404)
        form = {k: (request.values.get(k) or "").strip() for k in ("name", "phone", "ref")}
        referrer = find_customer_by_code(form["ref"]) if form["ref"] else None
        if request.method == "POST":
            phone = normalize_phone(form["phone"])
            if not form["name"]:
                error = "Escribe tu nombre."
            elif not 7 <= len(phone) <= 15:
                error = "Escribe un celular válido."
            elif form["ref"] and not referrer:
                error = "Ese código de invitación no existe. Revísalo o déjalo vacío."
            elif not (request.form.get("adult") and request.form.get("consent")):
                error = "Para registrarte confirma que eres mayor de edad y autoriza el uso de tus datos."
            elif get_db().execute("SELECT 1 FROM customers WHERE phone = ?", (phone,)).fetchone():
                error = "Ese celular ya tiene un código. Si no lo recuerdas, pídelo en la tienda."
            elif rate_limited("registro", SIGNUP_LIMIT):
                error = "Hubo muchos registros seguidos desde tu conexión. Intenta más tarde."
            else:
                new_id = register_customer(form["name"][:60], phone, referrer,
                                           referrer_amount=referral_value(), origin="web")
                session["customer_code"] = get_db().execute(
                    "SELECT code FROM customers WHERE id = ?", (new_id,)).fetchone()[0]
                flash("¡Listo! Este es tu código. Comparte tu enlace y gana descuentos por cada amigo que se registre.", "ok")
                return redirect(url_for("my_code"))
            flash(error, "error")
        return render_template("public/register.html", referrer=referrer, form=form)

    @app.route("/pedido", methods=["POST"])
    def place_order():
        """Guarda el pedido del carrito y abre el WhatsApp de la tienda con el pedido escrito.

        Mientras el pedido siga "nuevo", el mismo carrito (mismo `token`) lo actualiza: volver
        a tocar el botón no lo duplica en el historial.
        """
        whatsapp = get_settings()["whatsapp"]
        if not whatsapp:
            flash("La tienda todavía no recibe pedidos por WhatsApp.", "error")
            return redirect(url_for("menu"))
        lines = order_lines(parse_order_items(request.form.get("items")))
        if not lines:
            flash("Tu pedido está vacío o sus productos ya no están en el menú de hoy.", "error")
            return redirect(url_for("menu"))
        token = request.form.get("token") or ""
        if not (16 <= len(token) <= 64 and token.isalnum() and token.isascii()):
            token = secrets.token_hex(16)
        # Datos mínimos para el domicilio: cómo paga y dónde entregar (GPS o dirección).
        payment = (request.form.get("payment") or "").strip()
        location = parse_location(request.form.get("lat"), request.form.get("lng"))
        address = (request.form.get("address") or "").strip()[:200]
        if payment not in payment_methods():
            flash("Elige cómo vas a pagar tu pedido.", "error")
            return redirect(url_for("menu"))
        if not location and len(address) < 6:
            flash("Comparte tu ubicación o escribe tu dirección para el domicilio.", "error")
            return redirect(url_for("menu"))
        accuracy = request.form.get("accuracy", "")
        accuracy = int(float(accuracy)) if location and accuracy.replace(".", "", 1).isdecimal() and len(accuracy) < 10 else 0
        name = (request.form.get("name") or "").strip()[:60]
        code = normalize_code(request.form.get("code"))[:16]
        note = (request.form.get("note") or "").strip()[:300]
        customer = find_customer_by_code(code)
        total, pending = order_totals(lines)
        db = get_db()
        order = db.execute(
            "SELECT id, photo FROM orders WHERE token = ? AND status = 'nuevo' ORDER BY id DESC LIMIT 1", (token,)
        ).fetchone()
        if not order and rate_limited("pedido", ORDER_LIMIT):
            flash("Recibimos muchos pedidos seguidos desde tu conexión. Espera unos minutos.", "error")
            return redirect(url_for("menu"))
        try:
            photo = save_image(request.files.get("photo"), prefix="pedido-")
        except ValueError as exc:
            flash(f"La foto de la entrada no se pudo guardar: {exc}", "error")
            return redirect(url_for("menu"))
        if order and photo and order["photo"]:
            delete_image(order["photo"])                     # la reemplaza la nueva
        photo = photo or (order["photo"] if order else None)
        values = (json.dumps(lines, ensure_ascii=False), total, int(pending), name, code,
                  customer["id"] if customer else None, note, payment, address, location, photo, now())
        if order:
            order_id = order["id"]
            db.execute(
                "UPDATE orders SET items = ?, total = ?, pending = ?, name = ?, code = ?, customer_id = ?, "
                "note = ?, payment = ?, address = ?, location = ?, photo = ?, updated_at = ? WHERE id = ?",
                (*values, order_id),
            )
        else:
            order_id = db.insert(
                "INSERT INTO orders (items, total, pending, name, code, customer_id, note, payment, address, "
                "location, photo, updated_at, token, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*values, token, values[-1]),
            )
        db.commit()
        text = order_message(order_id, lines, name, code, note, payment, location, accuracy, address,
                             image_url(photo, external=True) if photo else "")
        return redirect(f"https://wa.me/{whatsapp}?text={quote(text, safe='')}", code=303)


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
        month = local_now().strftime("%Y-%m")
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
        orders = db.execute(
            "SELECT o.*, c.name AS customer_name FROM orders o LEFT JOIN customers c ON c.id = o.customer_id "
            "WHERE o.status = 'nuevo' ORDER BY o.id DESC LIMIT 5"
        ).fetchall()
        return render_template("admin/dashboard.html", stats=stats, top=top, recent=recent,
                               orders=[order_view(o) for o in orders])

    # ---- pedidos

    @app.route("/admin/pedidos")
    @admin_required
    def admin_orders():
        status = request.args.get("estado", "")
        if status not in ORDER_STATUSES:
            status = ""
        q = (request.args.get("q") or "").strip()
        page = min(max(request.args.get("pagina", 1, type=int), 1), 10_000)
        sql = "SELECT o.*, c.name AS customer_name FROM orders o LEFT JOIN customers c ON c.id = o.customer_id"
        where, params = [], []
        if status:
            where.append("o.status = ?")
            params.append(status)
        if q:
            number = q.lstrip("#")
            like = f"%{q}%"
            where.append("(o.id = ? OR o.name LIKE ? OR o.code LIKE ? OR c.name LIKE ?)")
            params += [int(number) if number.isdecimal() and len(number) < 10 else 0, like, like.upper(), like]
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY o.id DESC LIMIT ? OFFSET ?"
        db = get_db()
        rows = db.execute(sql, (*params, ORDERS_PER_PAGE + 1, (page - 1) * ORDERS_PER_PAGE)).fetchall()
        counts = dict(db.execute("SELECT status, COUNT(*) FROM orders GROUP BY status").fetchall())
        return render_template(
            "admin/orders.html", orders=[order_view(r) for r in rows[:ORDERS_PER_PAGE]],
            has_more=len(rows) > ORDERS_PER_PAGE, page=page, status=status, q=q, counts=counts,
        )

    @app.route("/admin/pedidos/<int:order_id>")
    @admin_required
    def admin_order_detail(order_id):
        row = get_db().execute(
            "SELECT o.*, c.name AS customer_name, c.code AS customer_code, c.phone AS customer_phone "
            "FROM orders o LEFT JOIN customers c ON c.id = o.customer_id WHERE o.id = ?",
            (order_id,),
        ).fetchone()
        if not row:
            abort(404)
        order = order_view(row)
        balance = customer_stats(order["customer_id"])["total"] if order["customer_id"] else 0
        return render_template("admin/order_detail.html", order=order, balance=balance)

    @app.route("/admin/pedidos/<int:order_id>/estado", methods=["POST"])
    @admin_required
    def admin_order_status(order_id):
        status = request.form.get("status", "")
        if status not in ORDER_STATUSES:
            abort(400)
        db = get_db()
        if not db.execute("SELECT 1 FROM orders WHERE id = ?", (order_id,)).fetchone():
            abort(404)
        db.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
        db.commit()
        flash(f"Pedido {order_number(order_id)} marcado como {ORDER_STATUSES[status].lower()}.", "ok")
        return redirect(url_for("admin_order_detail", order_id=order_id))

    @app.route("/admin/pedidos/<int:order_id>/borrar", methods=["POST"])
    @admin_required
    def admin_order_delete(order_id):
        db = get_db()
        row = db.execute("SELECT photo FROM orders WHERE id = ?", (order_id,)).fetchone()
        if row and row["photo"]:
            delete_image(row["photo"])
        db.execute("DELETE FROM orders WHERE id = ?", (order_id,))
        db.commit()
        flash(f"Pedido {order_number(order_id)} eliminado.", "ok")
        return redirect(url_for("admin_orders"))

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
                "ref_amount": str(referral_value() or ""), "welcome_amount": ""}
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
        orders = db.execute(
            "SELECT o.*, c.name AS customer_name FROM orders o JOIN customers c ON c.id = o.customer_id "
            "WHERE o.customer_id = ? ORDER BY o.id DESC LIMIT 10",
            (customer_id,),
        ).fetchall()
        invite_url = url_for("referral_landing", code=customer["code"], _external=True)
        return render_template(
            "admin/customer_detail.html", customer=customer, referrals=referrals,
            rewards=rewards, invite_url=invite_url, orders=[order_view(o) for o in orders],
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
        # Si alguien ganó un descuento por referirlo y no lo ha usado, se le quita
        # (así un registro falso no deja puntos).
        db.execute("DELETE FROM rewards WHERE source_id = ? AND kind = 'referral' AND redeemed_at IS NULL",
                   (customer_id,))
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
        db = get_db()
        products = db.execute(
            "SELECT * FROM products ORDER BY active DESC, featured DESC, created_at DESC"
        ).fetchall()
        sizes = {row["product_id"]: (row["n"], row["low"]) for row in db.execute(
            "SELECT product_id, COUNT(*) AS n, MIN(price) AS low FROM product_options GROUP BY product_id").fetchall()}
        active = [p for p in products if p["active"]]       # solo se comparte lo que está en el menú
        options = options_by_product(p["id"] for p in active)
        shares = {p["id"]: product_share(p, options.get(p["id"], [])) for p in active}
        return render_template("admin/products.html", products=products, sizes=sizes, shares=shares)

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
        categories = [r[0] for r in db.execute(
            "SELECT DISTINCT category FROM products WHERE category != '' ORDER BY category")]
        if request.method == "POST":
            data = {
                "title": (request.form.get("title") or "").strip(),
                "description": (request.form.get("description") or "").strip(),
                "price": normalize_price(request.form.get("price")),
                "category": (request.form.get("category") or "").strip(),
                "featured": 1 if request.form.get("featured") else 0,
                "active": 1 if request.form.get("active") else 0,
            }
            options, options_error = parse_option_rows(request.form)
            # Para volver a mostrar el formulario con lo que escribió (el precio general llega vacío
            # si estaba desactivado por las medidas).
            again = {"product": dict(data, price=data["price"] or (product["price"] if product else ""),
                                     image=product["image"] if product else None),
                     "options": options, "editing": bool(product), "categories": categories}
            if not data["title"]:
                flash("El nombre del producto es obligatorio.", "error")
                return render_template("admin/product_form.html", **again)
            if options_error:
                flash(options_error, "error")
                return render_template("admin/product_form.html", **again)
            if options:                              # con medidas, el precio general es el más barato
                data["price"] = str(min(option["price"] for option in options))
            try:
                image = save_image(request.files.get("image"))
            except ValueError as exc:
                flash(str(exc), "error")
                return render_template("admin/product_form.html", **again)
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
                product_id = db.insert(
                    "INSERT INTO products (title, description, price, category, featured, active, "
                    "image, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (*data.values(), image, now()),
                )
                flash("Novedad publicada en el menú del día.", "ok")
            save_product_options(db, product_id, options)
            db.commit()
            return redirect(url_for("admin_products"))
        blank = {"title": "", "description": "", "price": "", "category": "",
                 "featured": 0, "active": 1, "image": None}
        return render_template(
            "admin/product_form.html", product=product or blank, editing=bool(product),
            options=options_by_product([product_id]).get(product_id, []) if product else [],
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
                    for k in ("store_name", "tagline", "code_prefix", "whatsapp", "referral_value",
                              "payment_methods")}
            form["self_signup"] = "1" if request.form.get("self_signup") else "0"
            whatsapp = normalize_whatsapp(form["whatsapp"])
            referral = parse_cop(form["referral_value"])
            new_pw = request.form.get("new_password", "")
            error = None
            if form["whatsapp"] and not 10 <= len(whatsapp) <= 15:
                error = "Escribe el WhatsApp con el indicativo del país, por ejemplo 57 300 123 4567."
            elif check_discount(referral):
                error = "Valor de cada referido: " + check_discount(referral)
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
            set_setting("referral_value", str(referral))
            set_setting("self_signup", form["self_signup"])
            methods = [m.strip()[:30] for m in form["payment_methods"].split(",") if m.strip()][:10]
            set_setting("payment_methods", ", ".join(methods) or DEFAULT_SETTINGS["payment_methods"])
            if new_pw:
                set_setting("admin_password", generate_password_hash(new_pw))
            get_db().commit()
            flash("Ajustes guardados.", "ok")
            return redirect(url_for("admin_settings"))
        return render_template("admin/settings.html", wa_saved=get_settings()["whatsapp"])


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=bool(os.environ.get("DEBUG")))
