"""Templo de Anubis — sistema de referidos y menú del día para smoke shop.

Aplicación Flask con SQLite en local y Postgres (Supabase) en producción, si hay
DATABASE_URL. Una sola persona administra (el dueño de la tienda):
registra clientes, controla quién refirió a quién, entrega y canjea
descuentos, y publica las novedades del día. Los clientes ven el menú del día,
consultan su código de referido y envían su pedido por WhatsApp; cada pedido
queda en el historial del panel.
"""
import base64
import csv
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import struct
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
    "self_signup": "1",          # los clientes pueden registrarse solos desde la web (no con el menú privado)
    "private_menu": "1",         # el menú y los pedidos son solo para clientes registrados: entran con su código
    "payment_methods": "Efectivo, Nequi, Daviplata, Transferencia",   # el cliente elige uno al pedir
    "orders_paused": "0",        # pausa rápida (Inicio del panel): el menú no recibe pedidos nuevos
    "hours_open": "",            # horario de pedidos "HH:MM" (24 h); vacío = a cualquier hora
    "hours_close": "",
    "max_orders_day": "0",       # pedidos nuevos por día como máximo; 0 = sin límite
    "delivery_fee": "0",         # domicilio en pesos (con barrios: el de "Otro barrio"); 0 = no se cobra
    "delivery_zones": "",        # un barrio por línea con su domicilio: "Chapinero = 5000"
    "free_delivery_from": "0",   # domicilio gratis desde este valor en productos; 0 = nunca
    # Seguridad del panel (nunca llegan a las plantillas: ver template_settings).
    "admin_totp": "",            # clave de la verificación en dos pasos; vacía = apagada
    "admin_totp_pending": "",    # clave nueva mientras se activa (aún sin confirmar con un código)
    "admin_totp_step": "0",      # último código aceptado (cada código sirve una sola vez)
    "admin_recovery": "[]",      # huellas de los códigos de respaldo que no se han usado
    "admin_password_short": "0", # 1: la contraseña tiene menos de PASSWORD_MIN caracteres
    "admin_alerts_seen": "",     # hasta cuándo ya vio la tienda los intentos fallidos de entrar
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
    paused      INTEGER NOT NULL DEFAULT 0,      -- 1: acceso en pausa (no entra al menú privado ni a Mi código)
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
    sold_out    INTEGER NOT NULL DEFAULT 0,  -- se ve en el menú, pero no se puede pedir
    created_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS product_options (     -- medidas de un producto, cada una con su precio
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id  INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    label       TEXT NOT NULL,               -- "10 g", "Caja x 3"
    price       INTEGER NOT NULL,            -- en pesos
    position    INTEGER NOT NULL DEFAULT 0,  -- orden en que se muestran
    sold_out    INTEGER NOT NULL DEFAULT 0   -- esa medida se acabó
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
    discount    INTEGER NOT NULL DEFAULT 0,  -- pesos de su saldo de descuentos que el cliente pidió usar
    discount_used INTEGER NOT NULL DEFAULT 0,  -- pesos que ya se descontaron de su saldo (al confirmar)
    delivery    INTEGER NOT NULL DEFAULT 0,  -- domicilio en pesos
    delivery_note TEXT NOT NULL DEFAULT '',  -- '' | gratis | por confirmar
    zone        TEXT NOT NULL DEFAULT '',    -- barrio que eligió (si la tienda cobra por barrio)
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL                -- última vez que el cliente lo envió
);
CREATE INDEX IF NOT EXISTS idx_customers_referred_by ON customers(referred_by);
CREATE INDEX IF NOT EXISTS idx_rewards_customer ON rewards(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_token ON orders(token);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_product_options_product ON product_options(product_id);
CREATE TABLE IF NOT EXISTS admin_logins (       -- ingresos al panel: los que entraron y los intentos fallidos
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    ip          TEXT NOT NULL DEFAULT '',
    device      TEXT NOT NULL DEFAULT '',        -- "Android · Chrome", "iPhone · Safari"...
    result      TEXT NOT NULL                    -- ok | password (contraseña incorrecta) | code (código incorrecto)
);
CREATE INDEX IF NOT EXISTS idx_admin_logins_ip ON admin_logins(ip, created_at);
"""

# El mismo esquema en Postgres: ids con IDENTITY y enteros de 64 bits, como los de SQLite.
PG_SCHEMA = (SCHEMA.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY")
             .replace("INTEGER", "BIGINT"))
TABLES = ("settings", "customers", "rewards", "products", "product_options", "orders", "admin_logins")

REWARD_LABELS = {
    "referral": "Por referido",
    "welcome": "Bienvenida",
    "milestone": "Premio de meta",
    "manual": "Descuento asignado",
    "remainder": "Saldo que quedó",          # lo que sobró de un descuento usado en un pedido
    "refund": "Devuelto de un pedido",       # descuento de un pedido que se canceló
}

MAX_DISCOUNT = 10_000_000  # tope de seguridad contra ceros de más al escribir el valor
MAX_PRICE = 100_000_000    # tope del precio de una medida (mismo motivo)
MAX_PRODUCT_OPTIONS = 12   # medidas por producto
# Foto de la vista previa al compartir un producto: WhatsApp no la muestra si pesa más de ~300 KB.
SHARE_IMAGE_SIDE = 1080
SHARE_IMAGE_MAX_BYTES = 250_000

CLIENT_STATUSES = {        # cómo ve el cliente el estado de su pedido (Mi código)
    "nuevo": "Recibido",
    "confirmado": "Confirmado",
    "entregado": "Entregado",
    "cancelado": "Cancelado",
}
OTHER_ZONE = "Otro barrio"  # en el domicilio por barrios: el que no está en la lista
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
LOGIN_LIMIT = (10, 900)    # intentos fallidos de entrar al panel por dirección IP en 15 minutos
PASSWORD_MIN = 10          # largo mínimo de la contraseña del panel
CODE_TRIES = 5             # códigos de 6 dígitos equivocados antes de volver a pedir la contraseña
CODE_WAIT = 300            # segundos para escribir el código después de la contraseña
RECOVERY_CODES = 8         # códigos de respaldo de la verificación en dos pasos
LOGIN_LOG_KEEP = 500       # ingresos al panel que se guardan (los más viejos se borran)
CODE_LIMIT = (20, 600)     # códigos de cliente escritos por dirección IP en 10 minutos (contra adivinarlos)
ORDERS_PER_PAGE = 30

# Apps instalables (se agregan a la pantalla de inicio desde la página): la de los clientes y la del
# panel, con nombres e íconos discretos (la silueta de Anubis, ver tools/app_icons.py).
APP_NAMES = {"shop": "Anubis Shop", "panel": "Anubis Panel"}
APP_BG = "#050705"
# Lo que el celular guarda para abrir la app rápido y mostrar "Sin conexión": solo el diseño, nunca
# páginas con datos (el menú, los pedidos o el panel siempre se cargan de internet).
OFFLINE_FILES = (
    "css/fonts.css", "css/anubis.css", "js/app.js", "js/cart.js", "js/photo.js", "js/share.js",
    "fonts/cinzel-600.woff2", "fonts/cinzel-decorative-700.woff2", "fonts/cinzel-decorative-900.woff2",
    "fonts/cormorant-garamond-italic.woff2", "fonts/sora.woff2", "img/anubis-logo.jpg",
    "img/app/shop-32.png", "img/app/shop-192.png", "img/app/panel-32.png", "img/app/panel-192.png",
)
VERSIONED_FILES = ("css/fonts.css", "css/anubis.css", "js/app.js", "js/cart.js", "js/photo.js", "js/share.js")

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
        SESSION_COOKIE_SECURE=ON_VERCEL,      # en Vercel todo va por https
        PERMANENT_SESSION_LIFETIME=timedelta(days=60),   # el celular recuerda el código del cliente
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
    ensure_column(db, "customers", "paused", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(db, "products", "sold_out", "INTEGER NOT NULL DEFAULT 0")
    ensure_column(db, "product_options", "sold_out", "INTEGER NOT NULL DEFAULT 0")
    for column, definition in (("payment", "TEXT NOT NULL DEFAULT ''"), ("address", "TEXT NOT NULL DEFAULT ''"),
                               ("location", "TEXT NOT NULL DEFAULT ''"), ("photo", "TEXT")):
        ensure_column(db, "orders", column, definition)                    # datos de entrega
    for column, definition in (("discount", "BIGINT NOT NULL DEFAULT 0"), ("discount_used", "BIGINT NOT NULL DEFAULT 0"),
                               ("delivery", "BIGINT NOT NULL DEFAULT 0"), ("delivery_note", "TEXT NOT NULL DEFAULT ''"),
                               ("zone", "TEXT NOT NULL DEFAULT ''")):
        ensure_column(db, "orders", column, definition)                    # descuento y domicilio
    for key, value in DEFAULT_SETTINGS.items():
        db.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING", (key, value))
    # Versión de las sesiones del panel: al cambiarla se cierran las abiertas en otros equipos.
    db.execute("INSERT INTO settings (key, value) VALUES ('admin_session', ?) ON CONFLICT (key) DO NOTHING",
               (secrets.token_urlsafe(16),))
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


def current_client():
    """El cliente que entró con su código en este navegador, o None: si lo borraron o le pusieron
    su acceso en pausa, ya no entra."""
    customer = find_customer_by_code(session.get("customer_code", ""))
    return customer if customer and not customer["paused"] else None


def private_mode():
    """Menú privado (Ajustes): solo los clientes registrados por la tienda ven el menú y piden."""
    return get_settings().get("private_menu") == "1"


def signup_open():
    """Los clientes pueden registrarse solos desde la web (Ajustes), nunca con el menú privado."""
    return get_settings().get("self_signup") == "1" and not private_mode()


def safe_next(target):
    """Página de esta misma tienda a la que volver después de entrar, o ""."""
    target = target or ""
    return target if target.startswith("/") and not target.startswith(("//", "/\\")) else ""


# ------------------------------------------------------------- seguridad del panel

def template_settings():
    """Ajustes para las plantillas, sin los de seguridad (contraseña, clave de dos pasos...)."""
    return {k: v for k, v in get_settings().items() if not k.startswith("admin_")}


def client_ip():
    """Dirección IP de quien hace la petición. En Vercel la pone ProxyFix desde el último salto (el
    proxy de Vercel); no se lee el primer X-Forwarded-For, que cualquiera puede inventar."""
    return request.remote_addr or ""


def device_name(user_agent):
    """'Android · Chrome', 'iPhone · Safari'... para reconocer en qué equipo se entró al panel."""
    ua = user_agent or ""
    system = next((name for key, name in (("iPhone", "iPhone"), ("iPad", "iPad"), ("Android", "Android"),
                                          ("Windows", "Windows"), ("Mac OS X", "Mac"), ("CrOS", "Chromebook"),
                                          ("Linux", "Linux")) if key in ua), "Otro equipo")
    browser = next((name for key, name in (("EdgA", "Edge"), ("Edg/", "Edge"), ("SamsungBrowser", "Samsung Internet"),
                                           ("OPR/", "Opera"), ("FxiOS", "Firefox"), ("Firefox", "Firefox"),
                                           ("CriOS", "Chrome"), ("Chrome", "Chrome"), ("Safari", "Safari")) if key in ua), "")
    return f"{system} · {browser}" if browser else system


def log_login(result):
    """Anota un ingreso al panel ("ok") o un intento fallido ("password" o "code")."""
    db = get_db()
    db.insert("INSERT INTO admin_logins (created_at, ip, device, result) VALUES (?, ?, ?, ?)",
              (now(), client_ip()[:64], device_name(request.headers.get("User-Agent"))[:60], result))
    db.execute("DELETE FROM admin_logins WHERE id <= (SELECT MAX(id) FROM admin_logins) - ?", (LOGIN_LOG_KEEP,))
    db.commit()


def login_blocked():
    """Freno contra adivinar la contraseña o el código: como mucho LOGIN_LIMIT intentos fallidos por
    dirección IP. Se cuentan en la base de datos, así que también frena en Vercel (donde cada
    instancia tiene su propia memoria)."""
    limit, window = LOGIN_LIMIT
    since = (local_now() - timedelta(seconds=window)).isoformat(timespec="seconds")
    failed = get_db().execute(
        "SELECT COUNT(*) FROM admin_logins WHERE ip = ? AND result <> 'ok' AND created_at >= ?",
        (client_ip()[:64], since)).fetchone()[0]
    return failed >= limit


def suspicious_logins():
    """Intentos fallidos de entrar al panel que la tienda aún no ha visto. Si a los pocos minutos
    se entró bien desde la misma conexión, se toman como un error de la tienda al escribir."""
    rows = get_db().execute("SELECT * FROM admin_logins WHERE created_at > ? ORDER BY id",
                            (get_settings().get("admin_alerts_seen") or "",)).fetchall()
    oks = [(r["ip"], datetime.fromisoformat(r["created_at"])) for r in rows if r["result"] == "ok"]
    out = []
    for r in rows:
        if r["result"] == "ok":
            continue
        at = datetime.fromisoformat(r["created_at"])
        if not any(ip == r["ip"] and timedelta(0) <= when - at <= timedelta(minutes=15) for ip, when in oks):
            out.append(r)
    return out


def renew_admin_sessions():
    """Nueva versión de las sesiones del panel: se cierran las de los demás equipos y esta sigue."""
    token = secrets.token_urlsafe(16)
    set_setting("admin_session", token)
    session["admin_v"] = token


def start_admin_session():
    """Entra al panel en este equipo (sesión de 60 días, como la del código de los clientes)."""
    session.clear()
    session["admin"] = True
    session["admin_v"] = get_settings().get("admin_session")
    session.permanent = True


def new_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_code(secret, step):
    """Código de 6 dígitos (RFC 6238, como Google Authenticator) para el intervalo `step` de 30 s."""
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    return f"{(struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF) % 1_000_000:06d}"


def unix_now():
    """Segundos desde 1970 (en su propia función para que las pruebas puedan fijar el reloj)."""
    return time.time()


def check_totp(secret, code, after_step=-1):
    """El intervalo del código si es válido (el actual o uno al lado, por si el reloj del celular
    va un poco corrido) y no se usó antes; None si no."""
    code = re.sub(r"\D", "", code or "")
    if not secret or len(code) != 6:
        return None
    current = int(unix_now() // 30)
    for step in (current, current - 1, current + 1):
        if step > after_step and hmac.compare_digest(totp_code(secret, step), code):
            return step
    return None


def otpauth_uri(secret):
    """Enlace para la app de códigos (Google Authenticator, Contraseñas del iPhone...)."""
    name = quote(APP_NAMES["panel"])
    return f"otpauth://totp/{name}:admin?secret={secret}&issuer={name}&algorithm=SHA1&digits=6&period=30"


def qr_svg(text):
    """Código QR en SVG (para escanear la clave de dos pasos con el celular)."""
    import qrcode
    import qrcode.image.svg
    svg = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    svg = svg.to_string(encoding="unicode")
    return re.sub(r'<svg width="[^"]*" height="[^"]*"', '<svg class="qr" role="img" aria-label="Código QR"', svg, count=1)


def recovery_hash(code):
    return hashlib.sha256(re.sub(r"[^A-Z0-9]", "", (code or "").upper()).encode()).hexdigest()


def new_recovery_codes():
    """Códigos de respaldo nuevos (se muestran una sola vez); guarda solo sus huellas."""
    codes = ["{}-{}".format(*(("".join(secrets.choice(CODE_ALPHABET) for _ in range(4))) for _ in range(2)))
             for _ in range(RECOVERY_CODES)]
    set_setting("admin_recovery", json.dumps([recovery_hash(c) for c in codes]))
    return codes


def recovery_left():
    return len(json.loads(get_settings().get("admin_recovery") or "[]"))


def use_recovery_code(code):
    """Gasta un código de respaldo si es uno de los que quedan."""
    if len(re.sub(r"[^A-Za-z0-9]", "", code or "")) != 8:
        return False
    hashes = json.loads(get_settings().get("admin_recovery") or "[]")
    digest = recovery_hash(code)
    if digest not in hashes:
        return False
    hashes.remove(digest)
    set_setting("admin_recovery", json.dumps(hashes))
    return True


def check_second_step(code):
    """Código de 6 dígitos o código de respaldo: "totp", "recovery" o None. Guarda lo gastado."""
    settings = get_settings()
    step = check_totp(settings.get("admin_totp"), code, int(settings.get("admin_totp_step") or 0))
    if step is not None:
        set_setting("admin_totp_step", str(step))
        return "totp"
    return "recovery" if use_recovery_code(code) else None


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


def client_balance(customer_id):
    """Pesos que un cliente puede usar: la suma de sus descuentos sin canjear."""
    return get_db().execute(
        "SELECT COALESCE(SUM(amount), 0) FROM rewards WHERE customer_id = ? AND redeemed_at IS NULL",
        (customer_id,),
    ).fetchone()[0]


def redeem_amount(customer_id, amount, note):
    """Canjea `amount` pesos del saldo del cliente, de los descuentos más viejos a los más nuevos.

    Si un descuento vale más de lo que falta, se canjea entero y lo que sobra queda como un descuento
    nuevo ("Saldo que quedó"). Devuelve los pesos canjeados (menos, si no alcanzaba el saldo).
    """
    db = get_db()
    left = amount
    for reward in db.execute(
        "SELECT id, amount FROM rewards WHERE customer_id = ? AND redeemed_at IS NULL AND amount > 0 "
        "ORDER BY created_at, id", (customer_id,),
    ).fetchall():
        if left <= 0:
            break
        db.execute("UPDATE rewards SET redeemed_at = ? WHERE id = ?", (now(), reward["id"]))
        if reward["amount"] > left:
            add_reward(customer_id, "remainder", reward["amount"] - left, note=note)
            left = 0
        else:
            left -= reward["amount"]
    return amount - left


# ---------------------------------------------------------------------- pedidos

def order_number(order_id):
    return f"#{order_id:04d}"


def options_by_product(product_ids):
    """Medidas de esos productos, en su orden: {product_id: [{id, label, price, sold_out}, …]}."""
    ids = tuple(product_ids)
    if not ids:
        return {}
    options = {}
    for row in get_db().execute(
        f"SELECT id, product_id, label, price, sold_out FROM product_options WHERE product_id IN ({','.join('?' * len(ids))}) "
        "ORDER BY position, id",
        ids,
    ).fetchall():
        options.setdefault(row["product_id"], []).append(
            {"id": row["id"], "label": row["label"], "price": row["price"], "sold_out": bool(row["sold_out"])})
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
    ids y cantidades. Un producto con medidas solo se pide con una de sus medidas, y lo agotado no
    entra."""
    product_ids = {product_id for product_id, _ in wanted}
    if not product_ids:
        return []
    rows = get_db().execute(
        f"SELECT id, title, price FROM products WHERE active = 1 AND sold_out = 0 "
        f"AND id IN ({','.join('?' * len(product_ids))}) "
        "ORDER BY featured DESC, created_at DESC, id DESC",
        tuple(product_ids),
    ).fetchall()
    options = options_by_product(p["id"] for p in rows)
    lines = []
    for p in rows:
        if p["id"] in options:
            for option in options[p["id"]]:
                qty = wanted.get((p["id"], option["id"]))
                if qty and not option["sold_out"]:
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


def setting_int(key):
    """Un ajuste en número (pesos, cantidades): 0 si está vacío o no es un número."""
    value = get_settings().get(key, "")
    return int(value) if value.isdecimal() else 0


def parse_hhmm(raw):
    """'21:30' -> minutos desde la medianoche (1290); None si no es una hora válida."""
    hours, _, minutes = (raw or "").strip().partition(":")
    if hours.isdecimal() and minutes.isdecimal() and len(minutes) == 2 and int(hours) < 24 and int(minutes) < 60:
        return int(hours) * 60 + int(minutes)
    return None


def hour_text(minutes):
    """1290 -> '9:30 p. m.'"""
    hours, minutes = divmod(minutes, 60)
    return f"{hours % 12 or 12}:{minutes:02d} {'a. m.' if hours < 12 else 'p. m.'}"


def orders_today():
    """Pedidos de hoy (hora de Colombia) que cuentan para el máximo diario: todos menos los cancelados."""
    start = local_now().strftime("%Y-%m-%dT00:00:00")
    return get_db().execute(
        "SELECT COUNT(*) FROM orders WHERE created_at >= ? AND status != 'cancelado'", (start,)
    ).fetchone()[0]


def ordering_status():
    """¿El menú recibe pedidos nuevos ahora? -> (sí o no, el aviso para el cliente).

    La tienda puede pausarlos, poner un horario (también uno que pasa la medianoche, como 6 p. m. a
    2 a. m.) y un máximo de pedidos por día.
    """
    settings = get_settings()
    if settings.get("orders_paused") == "1":
        return False, "Por ahora no estamos recibiendo pedidos. Vuelve a intentarlo más tarde."
    start, end = parse_hhmm(settings.get("hours_open")), parse_hhmm(settings.get("hours_close"))
    if start is not None and end is not None and start != end:
        moment = local_now().hour * 60 + local_now().minute
        inside = start <= moment < end if start < end else (moment >= start or moment < end)
        if not inside:
            return False, f"Recibimos pedidos de {hour_text(start)} a {hour_text(end)}."
    limit = setting_int("max_orders_day")
    if limit and orders_today() >= limit:
        return False, "Ya completamos los pedidos de hoy. ¡Gracias! Vuelve mañana."
    return True, ""


def parse_zone_line(line):
    """'Chapinero = 5.000' (o 'Chapinero: 5k') -> ('Chapinero', 5000); ('', None) si no sirve."""
    for sep in ("=", ":"):
        if sep in line:
            name, _, value = line.rpartition(sep)
            break
    else:
        return "", None
    name, value = name.strip()[:40], value.strip()
    amount = parse_cop(value) if value else None
    if not name or amount is None or amount > MAX_PRICE:
        return "", None
    return name, amount


def delivery_zones():
    """Barrios con su valor de domicilio, de Ajustes: [("Chapinero", 5000), …]."""
    zones = []
    for line in (get_settings().get("delivery_zones") or "").splitlines():
        name, amount = parse_zone_line(line)
        if name and name not in dict(zones):
            zones.append((name, amount))
    return zones


def delivery_cost(zone, subtotal):
    """Domicilio de un pedido -> (pesos, nota) con nota "", "gratis" o "por confirmar"; None si
    hay barrios y el que llegó no es ninguno. `subtotal` es lo que suman los productos."""
    zones = dict(delivery_zones())
    general = setting_int("delivery_fee")
    if zones:
        if zone in zones:
            fee = zones[zone]
        elif zone == OTHER_ZONE:
            fee = general or None              # sin valor general lo confirma la tienda
        else:
            return None
    elif general:
        fee = general
    else:
        return 0, ""                           # la tienda no cobra domicilio por aquí
    free_from = setting_int("free_delivery_from")
    if fee == 0 or (free_from and subtotal >= free_from):
        return 0, "gratis"
    if fee is None:
        return 0, "por confirmar"
    return fee, ""


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


def order_message(order_id, lines, *, name="", code="", note="", payment="", location="", accuracy=0,
                  address="", photo_url="", discount=0, delivery=0, delivery_note="", zone=""):
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
    if discount or delivery or delivery_note:                # cuentas claras: productos, descuento, domicilio
        out.append(f"Productos: {format_cop(total) if total or not pending else 'por confirmar'}")
        if discount:
            out.append(f"Descuento de mi saldo: -{format_cop(discount)}")
        if delivery or delivery_note:
            out.append(f"Domicilio{f' ({zone})' if zone else ''}: {format_cop(delivery) if delivery else delivery_note}")
    missing = [what for what, flag in (("productos con precio a confirmar", pending),
                                       ("domicilio por confirmar", delivery_note == "por confirmar")) if flag]
    if not total and pending:
        out.append("Total: por confirmar")
    else:
        out.append(f"Total: {format_cop(max(total - discount + delivery, 0))} COP"
                   + (" + " + " y ".join(missing) if missing else ""))
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
        # El código escrito en el carrito del menú abierto es para pedir sus descuentos.
        wants = not discount and not private_mode()
        out.append(f"Mi código de cliente: {code}" + (" (quiero usar mis descuentos)" if wants else ""))
    if note:
        out.append(f"Nota: {note}")
    return "\n".join(out)


def order_view(row):
    """Fila de `orders` lista para las plantillas: con sus líneas y un resumen corto."""
    order = dict(row)
    order["lines"] = json.loads(order["items"])
    order["summary"] = ", ".join(f"{line['qty']} × {line_title(line)}" for line in order["lines"])
    order["to_pay"] = max(order["total"] - order["discount"] + order["delivery"], 0)   # lo que paga el cliente
    order["open_amount"] = bool(order["pending"]) or order["delivery_note"] == "por confirmar"
    return order


def csv_cell(value):
    """Valor para el respaldo en CSV. Un texto que empieza con =, +, - o @ va con un apóstrofo para
    que Excel no lo tome como fórmula (un nombre así no debe ejecutar nada al abrir el archivo)."""
    if value is None:
        return ""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def csv_date(value):
    """'2026-11-04T10:30:00' -> '2026-11-04 10:30' (Excel lo reconoce como fecha)."""
    return (value or "")[:16].replace("T", " ")


def rate_limited(kind, limit_window):
    """Freno básico contra pedidos o registros falsos en masa: como mucho `limit` por IP cada
    `window` segundos. Cuenta la acción si no está frenada.

    La cuenta vive en memoria (cada proceso lleva la suya): no es infalible, pero evita que un
    script llene el historial o reparta puntos de referido.
    """
    from flask import current_app
    limit, window = limit_window
    log = current_app.extensions.setdefault("rate_log", {})
    key = (kind, client_ip())
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
        "name": product["title"],
        "title": title,
        "description": description[:200],
        "image": image,
        "text": f"{title} en {settings['store_name']}. Pídelo aquí:",
    }


def asset_version():
    """Huella corta del diseño (estilos, scripts, letras e íconos): cambia cuando cambian, para
    invalidar la caché del navegador y la de la app instalada."""
    if "asset_v" not in ASSET_CACHE:
        digest = hashlib.md5()
        for name in OFFLINE_FILES:
            with open(os.path.join(BASE_DIR, "static", name), "rb") as fh:
                digest.update(fh.read())
        ASSET_CACHE["asset_v"] = digest.hexdigest()[:10]
    return ASSET_CACHE["asset_v"]


ASSET_CACHE = {}


def is_admin():
    """Sesión del panel vigente: si la tienda cerró las sesiones de los demás equipos (o cambió la
    contraseña), las que tenían la versión anterior ya no sirven."""
    return session.get("admin") is True and session.get("admin_v") == get_settings().get("admin_session")


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
            "settings": template_settings(),
            "csrf_token": session.get("csrf", ""),
            "is_admin": admin,
            "reward_labels": REWARD_LABELS,
            "order_statuses": ORDER_STATUSES,
            # contador de la barra del panel
            "new_orders": get_db().execute(
                "SELECT COUNT(*) FROM orders WHERE status = 'nuevo'").fetchone()[0] if admin else 0,
            "storage": storage_status() if admin else None,
            "referral_value": referral_value(),
            "self_signup": signup_open(),
            "private_mode": private_mode(),
            # qué app se instala desde esta página: la del panel o la de los clientes
            "app_kind": "panel" if request.path.startswith("/admin") else "shop",
            "app_names": APP_NAMES,
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
        hour = hour_text(dt.hour * 60 + dt.minute)
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

    @app.after_request
    def security_headers(resp):
        # La tienda no quiere aparecer en Google ni en otros buscadores: sus clientes llegan por ella.
        resp.headers.setdefault("X-Robots-Tag", "noindex, nofollow")
        # Nadie puede meter la página dentro de otra (para engañar con clics), el navegador no adivina
        # tipos de archivo y los enlaces a otras páginas no dicen de dónde vienen.
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("Referrer-Policy", "same-origin")
        if request.path.startswith("/admin"):
            # El panel no queda guardado en el navegador (ni con el botón Atrás después de salir).
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(413)
    def too_large(_e):
        flash("La imagen es demasiado pesada (máximo 6 MB).", "error")
        return redirect(request.referrer or url_for("admin_products"))


# ----------------------------------------------------------------------- public

def manifest_response(kind):
    """La ficha de una app instalable: la de los clientes ("shop") o la del panel ("panel")."""
    start = url_for("admin_dashboard") if kind == "panel" else url_for("menu")
    icons = [{"src": url_for("static", filename=f"img/app/{kind}{extra}-{size}.png"), "sizes": f"{size}x{size}",
              "type": "image/png", "purpose": purpose}
             for extra, purpose in (("", "any"), ("-maskable", "maskable")) for size in (192, 512)]
    data = {
        "id": start, "name": APP_NAMES[kind], "short_name": APP_NAMES[kind],
        "description": "Panel de la tienda" if kind == "panel" else "Menú del día y pedidos",
        "lang": "es-CO", "dir": "ltr", "start_url": start, "scope": start, "display": "standalone",
        "background_color": APP_BG, "theme_color": APP_BG, "icons": icons,
    }
    resp = Response(json.dumps(data, ensure_ascii=False, indent=1), mimetype="application/manifest+json")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


def render_menu(shared=None):
    """El menú del día. Con `shared` (un producto que alguien compartió) la página lleva la vista
    previa de ese producto para WhatsApp y baja hasta él.

    Con el menú privado, quien no ha entrado con su código ve la puerta (y nada del menú). En un
    enlace de producto la puerta conserva su vista previa, para los estados que comparte la tienda.
    """
    client = current_client()
    if private_mode() and not client and not is_admin():
        og = product_share(shared, options_by_product([shared["id"]]).get(shared["id"], [])) if shared else None
        return render_template("public/private.html", og=og, next=request.path)
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
            "sold_out": bool(p["sold_out"]),
        }
        for p in products
    ]
    shares = {p["id"]: product_share(p, options.get(p["id"], [])) for p in products}
    is_open, closed_reason = ordering_status()
    return render_template(
        "public/menu.html", products=products, categories=categories, menu_items=menu_items,
        options=options, shares=shares, shared=shared, og=shares[shared["id"]] if shared else None,
        client=client, balance=client_balance(client["id"]) if client else 0,
        ordering_open=is_open, closed_reason=closed_reason, zones=delivery_zones(),
        delivery_fee=setting_int("delivery_fee"), free_from=setting_int("free_delivery_from"), other_zone=OTHER_ZONE,
        payment_methods=payment_methods(), today=local_now(),
    )


def register_public_routes(app):
    @app.route("/")
    def menu():
        return render_menu()

    @app.route("/robots.txt")
    def robots():
        """Fuera de los buscadores. Las vistas previas de WhatsApp y Facebook sí pueden leer los
        enlaces de productos y la imagen de la tienda (para los estados que comparte la tienda)."""
        body = ("User-agent: WhatsApp\nUser-agent: facebookexternalhit\n"
                "Allow: /p/\nAllow: /static/img/\nDisallow: /\n\n"
                "User-agent: *\nDisallow: /\n")
        return Response(body, mimetype="text/plain")

    @app.route("/manifest.webmanifest")
    def app_manifest():
        return manifest_response("shop")

    @app.route("/admin/manifest.webmanifest")          # sin sesión: el navegador la pide sin cookies
    def panel_manifest():
        return manifest_response("panel")

    @app.route("/sw.js")
    def service_worker():
        """El service worker de las apps: guarda el diseño (OFFLINE_FILES) y la página "Sin conexión".
        Cambia con cada versión del diseño, y así el celular actualiza lo que guardó."""
        version = asset_version()
        files = [url_for("static", filename=name, v=version) if name in VERSIONED_FILES
                 else url_for("static", filename=name) for name in OFFLINE_FILES]
        body = render_template("sw.js", version=version, files=files, offline=url_for("offline"))
        resp = Response(body, mimetype="text/javascript")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.route("/sin-conexion")
    def offline():
        return render_template("public/offline.html")

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
        """Consulta por código. El código queda recordado en este navegador (60 días) y, con el menú
        privado, es lo que abre el menú: `next` vuelve a la página desde donde entró."""
        code = ""
        nxt = safe_next(request.values.get("next"))
        if request.method == "POST":
            code = normalize_code(request.form.get("code"))
            customer = None
            if rate_limited("codigo", CODE_LIMIT):
                flash("Demasiados intentos seguidos desde tu conexión. Espera unos minutos.", "error")
            else:
                customer = find_customer_by_code(code)
                if customer and customer["paused"]:
                    customer = None
                    flash("Tu acceso está en pausa. Escríbele a la tienda.", "error")
                elif customer:
                    session["customer_code"] = customer["code"]
                    session.permanent = True
                else:
                    flash("No encontramos ese código. Revísalo o pregúntanos en tienda.", "error")
            if nxt:
                return redirect(nxt)
        else:
            customer = current_client()
        stats = customer_stats(customer["id"]) if customer else None
        orders = []
        if customer:                               # sus pedidos: estado y "Repetir"
            for row in get_db().execute(
                "SELECT * FROM orders WHERE customer_id = ? ORDER BY id DESC LIMIT 10", (customer["id"],)
            ).fetchall():
                order = order_view(row)
                order["repeat"] = json.dumps({
                    f"{line['product_id']}-{line['option_id']}" if line.get("option_id") else str(line["product_id"]): line["qty"]
                    for line in order["lines"]})
                orders.append(order)
        return render_template("public/my_code.html", customer=customer, stats=stats, orders=orders,
                               client_statuses=CLIENT_STATUSES,
                               code=code or (customer["code"] if customer else ""))

    @app.route("/mi-codigo/salir", methods=["POST"])
    def my_code_forget():
        session.pop("customer_code", None)
        return redirect(url_for("my_code"))

    @app.route("/r/<code>")
    def referral_landing(code):
        if private_mode():                         # menú privado: el enlace no abre la puerta
            return redirect(url_for("menu"))
        referrer = find_customer_by_code(code)
        if not referrer:
            abort(404)
        if not signup_open():                      # registro cerrado: la invitación de siempre
            return render_template("public/invite.html", referrer=referrer)
        return render_template("public/register.html", referrer=referrer, form={"name": "", "phone": ""})

    @app.route("/registro", methods=["GET", "POST"])
    def register():
        """Registro de clientes desde la web. Si llegan con el código de alguien (enlace o
        escrito), quien los invitó gana el valor por referido de Ajustes."""
        if not signup_open():
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
        a tocar el botón no lo duplica en el historial. Con los pedidos en pausa, fuera de horario
        o con el máximo del día completo no entran pedidos nuevos, pero sí se actualiza uno abierto.
        """
        whatsapp = get_settings()["whatsapp"]
        if not whatsapp:
            flash("La tienda todavía no recibe pedidos por WhatsApp.", "error")
            return redirect(url_for("menu"))
        client = current_client()
        if private_mode() and not client and not is_admin():
            flash("Para pedir, entra con tu código de cliente.", "error")
            return redirect(url_for("menu"))
        lines = order_lines(parse_order_items(request.form.get("items")))
        if not lines:
            flash("Tu pedido está vacío o sus productos ya no están en el menú de hoy.", "error")
            return redirect(url_for("menu"))
        token = request.form.get("token") or ""
        if not (16 <= len(token) <= 64 and token.isalnum() and token.isascii()):
            token = secrets.token_hex(16)
        db = get_db()
        order = db.execute(
            "SELECT id, photo FROM orders WHERE token = ? AND status = 'nuevo' ORDER BY id DESC LIMIT 1", (token,)
        ).fetchone()
        if not order:
            is_open, reason = ordering_status()
            if not is_open:
                flash(reason, "error")
                return redirect(url_for("menu"))
        # Datos mínimos para el domicilio: cómo paga y dónde entregar (GPS o dirección), y su barrio
        # si la tienda cobra el domicilio por barrio.
        payment = (request.form.get("payment") or "").strip()
        location = parse_location(request.form.get("lat"), request.form.get("lng"))
        address = (request.form.get("address") or "").strip()[:200]
        if payment not in payment_methods():
            flash("Elige cómo vas a pagar tu pedido.", "error")
            return redirect(url_for("menu"))
        if not location and len(address) < 6:
            flash("Comparte tu ubicación o escribe tu dirección para el domicilio.", "error")
            return redirect(url_for("menu"))
        total, pending = order_totals(lines)
        zone = (request.form.get("zone") or "").strip() if delivery_zones() else ""
        cost = delivery_cost(zone, total)
        if cost is None:
            flash("Elige tu barrio para calcular el domicilio.", "error")
            return redirect(url_for("menu"))
        delivery, delivery_note = cost
        accuracy = request.form.get("accuracy", "")
        accuracy = int(float(accuracy)) if location and accuracy.replace(".", "", 1).isdecimal() and len(accuracy) < 10 else 0
        name = (request.form.get("name") or "").strip()[:60]
        code = normalize_code(request.form.get("code"))[:16]
        if private_mode() and client:               # el menú privado ya sabe quién pide
            name, code = name or client["name"], client["code"]
        # Su saldo de descuentos solo lo usa quien entró con su código; se descuenta al confirmar el pedido.
        discount = min(client_balance(client["id"]), total) if client and request.form.get("use_discount") else 0
        if discount:
            code = client["code"]
        note = (request.form.get("note") or "").strip()[:300]
        customer = find_customer_by_code(code)
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
                  customer["id"] if customer else None, note, payment, address, location, photo,
                  discount, delivery, delivery_note, zone, now())
        if order:
            order_id = order["id"]
            db.execute(
                "UPDATE orders SET items = ?, total = ?, pending = ?, name = ?, code = ?, customer_id = ?, "
                "note = ?, payment = ?, address = ?, location = ?, photo = ?, discount = ?, delivery = ?, "
                "delivery_note = ?, zone = ?, updated_at = ? WHERE id = ?",
                (*values, order_id),
            )
        else:
            order_id = db.insert(
                "INSERT INTO orders (items, total, pending, name, code, customer_id, note, payment, address, "
                "location, photo, discount, delivery, delivery_note, zone, updated_at, token, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (*values, token, values[-1]),
            )
        db.commit()
        text = order_message(
            order_id, lines, name=name, code=code, note=note, payment=payment, location=location,
            accuracy=accuracy, address=address, photo_url=image_url(photo, external=True) if photo else "",
            discount=discount, delivery=delivery, delivery_note=delivery_note, zone=zone,
        )
        return redirect(f"https://wa.me/{whatsapp}?text={quote(text, safe='')}", code=303)


# ------------------------------------------------------------------------ admin

def register_admin_routes(app):
    def admin_next(target):
        """Página del panel a la que volver después de entrar (nunca otra página ni otro sitio)."""
        target = safe_next(target)
        return target if target.startswith("/admin") else url_for("admin_dashboard")

    @app.route("/admin/login", methods=["GET", "POST"])
    def admin_login():
        settings = get_settings()
        first_run = "admin_password" not in settings
        if request.method == "GET" and is_admin():
            return redirect(url_for("admin_dashboard"))           # ya entró (por ejemplo, desde la app)
        if request.method == "POST":
            password = request.form.get("password", "")
            if not first_run and login_blocked():
                flash("Demasiados intentos seguidos desde tu conexión. Espera unos minutos.", "error")
            elif first_run:
                if len(password) < PASSWORD_MIN:
                    flash(f"La contraseña debe tener al menos {PASSWORD_MIN} caracteres.", "error")
                elif password != request.form.get("confirm", ""):
                    flash("Las contraseñas no coinciden.", "error")
                else:
                    set_setting("admin_password", generate_password_hash(password))
                    get_db().commit()
                    log_login("ok")
                    start_admin_session()
                    flash("Contraseña creada. Te damos la bienvenida al templo.", "ok")
                    return redirect(url_for("admin_dashboard"))
            elif check_password_hash(settings["admin_password"], password):
                set_setting("admin_password_short", "1" if len(password) < PASSWORD_MIN else "0")
                get_db().commit()
                nxt = admin_next(request.args.get("next"))
                if settings.get("admin_totp"):
                    # Segundo paso: el código de la app del celular (o uno de respaldo).
                    session.clear()
                    session["admin_pending"] = {"at": time.time(), "next": nxt, "tries": 0}
                    return redirect(url_for("admin_login_code"))
                log_login("ok")
                start_admin_session()
                return redirect(nxt)
            else:
                log_login("password")
                flash("Contraseña incorrecta.", "error")
        return render_template("admin/login.html", first_run=first_run, password_min=PASSWORD_MIN)

    @app.route("/admin/login/codigo", methods=["GET", "POST"])
    def admin_login_code():
        pending = session.get("admin_pending")
        if not pending or time.time() - pending.get("at", 0) > CODE_WAIT:
            session.pop("admin_pending", None)
            flash("Escribe otra vez tu contraseña.", "error")
            return redirect(url_for("admin_login"))
        if request.method == "POST":
            if login_blocked():
                flash("Demasiados intentos seguidos desde tu conexión. Espera unos minutos.", "error")
            else:
                how = check_second_step(request.form.get("code", ""))
                if how:
                    get_db().commit()
                    log_login("ok")
                    start_admin_session()
                    if how == "recovery":
                        left = recovery_left()
                        flash(f"Entraste con un código de respaldo: te quedan {left}."
                              + (" Crea códigos nuevos en Ajustes → Seguridad." if left <= 2 else ""), "ok")
                    return redirect(pending["next"])
                log_login("code")
                pending["tries"] = pending.get("tries", 0) + 1
                if pending["tries"] >= CODE_TRIES:
                    session.pop("admin_pending", None)
                    flash("Demasiados códigos incorrectos. Escribe otra vez tu contraseña.", "error")
                    return redirect(url_for("admin_login"))
                session["admin_pending"] = pending
                flash("Código incorrecto. Escribe el que muestra ahora tu app de códigos para Anubis Panel.", "error")
        return render_template("admin/login_code.html")

    @app.route("/admin/logout", methods=["POST"])
    def admin_logout():
        session.clear()
        flash("Saliste del panel.", "ok")
        return redirect(url_for("admin_login"))

    @app.route("/admin/seguridad")
    @admin_required
    def admin_security():
        settings = get_settings()
        logins = get_db().execute("SELECT * FROM admin_logins ORDER BY created_at DESC, id DESC LIMIT 15").fetchall()
        return render_template("admin/security.html", logins=logins, totp_on=bool(settings.get("admin_totp")),
                               recovery=recovery_left(), password_short=settings.get("admin_password_short") == "1",
                               password_min=PASSWORD_MIN)

    @app.route("/admin/seguridad/dos-pasos", methods=["GET", "POST"])
    @admin_required
    def admin_totp_setup():
        settings = get_settings()
        if settings.get("admin_totp"):
            flash("La verificación en dos pasos ya está activa.", "ok")
            return redirect(url_for("admin_security"))
        secret = settings.get("admin_totp_pending")
        if request.method == "POST" and secret:
            step = check_totp(secret, request.form.get("code", ""))
            if step is None:
                flash("Ese código no coincide. Escribe el que muestra ahora la app (cambia cada 30 segundos).", "error")
            else:
                set_setting("admin_totp", secret)
                set_setting("admin_totp_pending", "")
                set_setting("admin_totp_step", str(step))
                codes = new_recovery_codes()
                renew_admin_sessions()                    # si alguien más tenía el panel abierto, se le cierra
                get_db().commit()
                return render_template("admin/recovery_codes.html", codes=codes, first=True)
        if not secret:
            secret = new_totp_secret()
            set_setting("admin_totp_pending", secret)
            get_db().commit()
        uri = otpauth_uri(secret)
        return render_template("admin/totp_setup.html", secret=secret, uri=uri, qr=qr_svg(uri))

    def second_step_ok():
        """Para apagar la verificación o crear códigos nuevos: un código de la app o uno de respaldo."""
        if login_blocked():
            flash("Demasiados intentos seguidos desde tu conexión. Espera unos minutos.", "error")
            return False
        if check_second_step(request.form.get("code", "")):
            return True
        log_login("code")
        flash("Código incorrecto. Escribe el que muestra ahora tu app de códigos (o uno de respaldo).", "error")
        return False

    @app.route("/admin/seguridad/dos-pasos/apagar", methods=["POST"])
    @admin_required
    def admin_totp_disable():
        if get_settings().get("admin_totp") and second_step_ok():
            set_setting("admin_totp", "")
            set_setting("admin_totp_step", "0")
            set_setting("admin_recovery", "[]")
            get_db().commit()
            flash("Apagaste la verificación en dos pasos: el panel solo pide la contraseña.", "ok")
        return redirect(url_for("admin_security"))

    @app.route("/admin/seguridad/codigos", methods=["POST"])
    @admin_required
    def admin_new_recovery_codes():
        if get_settings().get("admin_totp") and second_step_ok():
            codes = new_recovery_codes()
            get_db().commit()
            return render_template("admin/recovery_codes.html", codes=codes, first=False)
        return redirect(url_for("admin_security"))

    @app.route("/admin/seguridad/contrasena", methods=["POST"])
    @admin_required
    def admin_change_password():
        current = request.form.get("current", "")
        new_password = request.form.get("new_password", "")
        if login_blocked():
            flash("Demasiados intentos seguidos desde tu conexión. Espera unos minutos.", "error")
        elif not check_password_hash(get_settings()["admin_password"], current):
            log_login("password")
            flash("La contraseña actual no es esa.", "error")
        elif len(new_password) < PASSWORD_MIN:
            flash(f"La nueva contraseña debe tener al menos {PASSWORD_MIN} caracteres.", "error")
        elif new_password != request.form.get("confirm", ""):
            flash("Las dos contraseñas nuevas no coinciden.", "error")
        elif new_password == current:
            flash("La nueva contraseña debe ser distinta de la actual.", "error")
        else:
            set_setting("admin_password", generate_password_hash(new_password))
            set_setting("admin_password_short", "0")
            renew_admin_sessions()
            get_db().commit()
            flash("Contraseña cambiada. El panel se cerró en los demás equipos.", "ok")
        return redirect(url_for("admin_security"))

    @app.route("/admin/seguridad/cerrar-sesiones", methods=["POST"])
    @admin_required
    def admin_close_sessions():
        renew_admin_sessions()
        get_db().commit()
        flash("Listo: el panel se cerró en todos los demás equipos. En este sigues dentro.", "ok")
        return redirect(url_for("admin_security"))

    @app.route("/admin/seguridad/visto", methods=["POST"])
    @admin_required
    def admin_alerts_seen():
        set_setting("admin_alerts_seen", now())
        get_db().commit()
        return redirect(admin_next(request.form.get("next")))

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
        is_open, reason = ordering_status()
        start, end = parse_hhmm(get_settings()["hours_open"]), parse_hhmm(get_settings()["hours_close"])
        ordering = {
            "open": is_open, "reason": reason, "paused": get_settings()["orders_paused"] == "1",
            "today": orders_today(), "limit": setting_int("max_orders_day"),
            "hours": f"{hour_text(start)} a {hour_text(end)}" if start is not None and end is not None and start != end else "",
        }
        settings = get_settings()
        security = {"totp": bool(settings.get("admin_totp")), "short": settings.get("admin_password_short") == "1",
                    "failed": suspicious_logins()}
        return render_template("admin/dashboard.html", stats=stats, top=top, recent=recent,
                               orders=[order_view(o) for o in orders], ordering=ordering, security=security,
                               password_min=PASSWORD_MIN)

    @app.route("/admin/pedidos/pausa", methods=["POST"])
    @admin_required
    def admin_orders_pause():
        """Pausa rápida: cuando no das abasto, el menú deja de recibir pedidos nuevos."""
        paused = get_settings()["orders_paused"] != "1"
        set_setting("orders_paused", "1" if paused else "0")
        get_db().commit()
        flash("Pedidos en pausa: el menú no recibe pedidos nuevos hasta que los reanudes." if paused
              else "Pedidos reanudados.", "ok")
        return redirect(url_for("admin_dashboard"))

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
        order = db.execute(
            "SELECT customer_id, discount, discount_used FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not order:
            abort(404)
        db.execute("UPDATE orders SET status = ? WHERE id = ?", (status, order_id))
        message, kind = f"Pedido {order_number(order_id)} marcado como {ORDER_STATUSES[status].lower()}.", "ok"
        # El saldo de descuentos que el cliente pidió usar se descuenta al confirmar (o entregar) y
        # se le devuelve si el pedido se cancela.
        if order["customer_id"] and order["discount"]:
            if status in ("confirmado", "entregado") and not order["discount_used"]:
                used = redeem_amount(order["customer_id"], order["discount"], f"Lo que sobró en el pedido {order_number(order_id)}")
                db.execute("UPDATE orders SET discount_used = ? WHERE id = ?", (used, order_id))
                message += f" Se descontaron {format_cop(used)} de su saldo."
                if used < order["discount"]:
                    message += f" Ojo: su saldo no alcanzaba; cóbrale {format_cop(order['discount'] - used)} más."
                    kind = "error"
            elif status == "cancelado" and order["discount_used"]:
                add_reward(order["customer_id"], "refund", order["discount_used"], note=f"Pedido {order_number(order_id)}")
                db.execute("UPDATE orders SET discount_used = 0 WHERE id = ?", (order_id,))
                message += f" Le devolvimos {format_cop(order['discount_used'])} a su saldo."
        db.commit()
        flash(message, kind)
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

    # ---- resumen y respaldo

    @app.route("/admin/resumen")
    @admin_required
    def admin_summary():
        """Cómo va la tienda en un período: ventas, pedidos, productos, clientes, referidos y descuentos.

        Ventas = lo que pagan los pedidos confirmados o entregados (con descuento y domicilio).
        """
        days = request.args.get("dias", 30, type=int)
        if days not in (7, 30, 90, 365):
            days = 30
        db = get_db()
        today = local_now().date()
        since = f"{today - timedelta(days=days - 1)}T00:00:00"
        orders = [order_view(r) for r in db.execute(
            "SELECT o.*, c.name AS customer_name FROM orders o LEFT JOIN customers c ON c.id = o.customer_id "
            "WHERE o.created_at >= ? ORDER BY o.id", (since,)).fetchall()]
        live = [o for o in orders if o["status"] != "cancelado"]
        sold = [o for o in live if o["status"] in ("confirmado", "entregado")]
        sales = sum(o["to_pay"] for o in sold)
        products, clients = {}, {}
        for o in live:
            for line in o["lines"]:
                item = products.setdefault(line_title(line), {"qty": 0, "amount": 0})
                item["qty"] += line["qty"]
                item["amount"] += (line["price"] or 0) * line["qty"]
            if o["customer_id"]:
                client = clients.setdefault(o["customer_id"], {"id": o["customer_id"], "name": o["customer_name"],
                                                               "orders": 0, "amount": 0})
                client["orders"] += 1
                client["amount"] += o["to_pay"] if o["status"] in ("confirmado", "entregado") else 0
        # Las últimas 8 semanas, de lunes a domingo.
        monday = today - timedelta(days=today.weekday())
        weeks = [{"start": monday - timedelta(weeks=n), "orders": 0, "sales": 0} for n in range(7, -1, -1)]
        for o in map(order_view, db.execute(
            "SELECT * FROM orders WHERE created_at >= ? AND status != 'cancelado'",
            (f"{weeks[0]['start']}T00:00:00",)).fetchall()):
            week = weeks[min((datetime.fromisoformat(o["created_at"]).date() - weeks[0]["start"]).days // 7, 7)]
            week["orders"] += 1
            week["sales"] += o["to_pay"] if o["status"] in ("confirmado", "entregado") else 0
        best_week = max(w["sales"] for w in weeks) or 1
        for week in weeks:
            week["label"] = f"{week['start'].day} {MESES[week['start'].month - 1]}"
            week["pct"] = round(week["sales"] * 100 / best_week)
        referrers = db.execute(
            "SELECT p.id, p.name, COUNT(*) AS total FROM customers c JOIN customers p ON p.id = c.referred_by "
            "WHERE c.created_at >= ? GROUP BY p.id, p.name ORDER BY total DESC, p.name LIMIT 5", (since,)).fetchall()
        summary = {
            "orders": len(live), "sold": len(sold), "sales": sales,
            "average": sales // len(sold) if sold else 0,
            "statuses": {key: sum(o["status"] == key for o in orders) for key in ORDER_STATUSES},
            "new_customers": db.execute(
                "SELECT COUNT(*) FROM customers WHERE created_at >= ?", (since,)).fetchone()[0],
            "referred": db.execute(
                "SELECT COUNT(*) FROM customers WHERE created_at >= ? AND referred_by IS NOT NULL",
                (since,)).fetchone()[0],
            "given": db.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM rewards WHERE created_at >= ? "
                "AND kind NOT IN ('remainder', 'refund')", (since,)).fetchone()[0],
            "used": sum(o["discount_used"] for o in live),
            "pending": db.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM rewards WHERE redeemed_at IS NULL").fetchone()[0],
        }
        top_products = sorted(products.items(), key=lambda item: (-item[1]["qty"], item[0]))[:8]
        top_clients = sorted(clients.values(), key=lambda c: (-c["orders"], -c["amount"]))[:5]
        return render_template("admin/summary.html", days=days, summary=summary, weeks=weeks,
                               top_products=top_products, top_clients=top_clients, referrers=referrers)

    @app.route("/admin/respaldo/<what>.csv")
    @admin_required
    def admin_backup(what):
        """Copia de seguridad para abrir en Excel: clientes, pedidos o descuentos. CSV separado por
        punto y coma, con la marca UTF-8 para que Excel lea bien las tildes."""
        db = get_db()
        if what == "clientes":
            header = ["Código", "Nombre", "Teléfono", "Referido por", "Código de quien lo refirió", "Origen",
                      "Acceso", "Registrado", "Referidos", "Saldo de descuentos", "Notas"]
            rows = [[c["code"], c["name"], c["phone"], c["referrer_name"] or "", c["referrer_code"] or "",
                     "Web" if c["origin"] == "web" else "Tienda", "En pausa" if c["paused"] else "Activo",
                     csv_date(c["created_at"]), c["referrals"], c["balance"], c["notes"]]
                    for c in db.execute(
                        "SELECT c.*, p.name AS referrer_name, p.code AS referrer_code, "
                        "(SELECT COUNT(*) FROM customers x WHERE x.referred_by = c.id) AS referrals, "
                        "(SELECT COALESCE(SUM(w.amount), 0) FROM rewards w "
                        " WHERE w.customer_id = c.id AND w.redeemed_at IS NULL) AS balance "
                        "FROM customers c LEFT JOIN customers p ON p.id = c.referred_by ORDER BY c.id").fetchall()]
        elif what == "pedidos":
            header = ["Pedido", "Fecha", "Estado", "Cliente", "Código", "Productos", "Valor de productos",
                      "Descuento", "Domicilio", "Total a pagar", "Pago", "Barrio", "Dirección", "Ubicación", "Nota"]
            rows = [[order_number(o["id"]), csv_date(o["created_at"]), ORDER_STATUSES[o["status"]],
                     o["name"] or o["customer_name"] or "", o["code"], o["summary"], o["total"], o["discount"],
                     o["delivery"] or o["delivery_note"], o["to_pay"], o["payment"], o["zone"], o["address"],
                     maps_url(o["location"]) if o["location"] else "", o["note"]]
                    for o in map(order_view, db.execute(
                        "SELECT o.*, c.name AS customer_name FROM orders o "
                        "LEFT JOIN customers c ON c.id = o.customer_id ORDER BY o.id").fetchall())]
        elif what == "descuentos":
            header = ["Cliente", "Código", "Tipo", "Valor", "Nota", "Fecha", "Canjeado"]
            rows = [[r["name"], r["code"], REWARD_LABELS.get(r["kind"], r["kind"]), r["amount"], r["note"],
                     csv_date(r["created_at"]), csv_date(r["redeemed_at"])]
                    for r in db.execute(
                        "SELECT w.*, c.name, c.code FROM rewards w JOIN customers c ON c.id = w.customer_id "
                        "ORDER BY w.id").fetchall()]
        else:
            abort(404)
        out = io.StringIO()
        writer = csv.writer(out, delimiter=";")
        writer.writerow(header)
        writer.writerows([csv_cell(value) for value in row] for row in rows)
        resp = Response("\ufeff" + out.getvalue(), mimetype="text/csv")
        resp.headers["Content-Disposition"] = f'attachment; filename="{what}-{local_now():%Y-%m-%d}.csv"'
        resp.headers["Cache-Control"] = "no-store"
        return resp

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
        if private_mode():                   # el enlace que se le manda es el del menú: entra con su código
            invite_url = url_for("menu", _external=True)
        else:
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

    @app.route("/admin/clientes/<int:customer_id>/pausa", methods=["POST"])
    @admin_required
    def admin_customer_pause(customer_id):
        """Pausar el acceso de un cliente sin borrarlo: no entra al menú privado ni a Mi código."""
        db = get_db()
        customer = db.execute("SELECT name, paused FROM customers WHERE id = ?", (customer_id,)).fetchone()
        if not customer:
            abort(404)
        db.execute("UPDATE customers SET paused = ? WHERE id = ?", (0 if customer["paused"] else 1, customer_id))
        db.commit()
        flash(f"Reactivaste el acceso de {customer['name']}." if customer["paused"] else
              f"Acceso de {customer['name']} en pausa: ya no entra al menú ni a Mi código.", "ok")
        return redirect(url_for("admin_customer_detail", customer_id=customer_id))

    @app.route("/admin/clientes/<int:customer_id>/codigo", methods=["POST"])
    @admin_required
    def admin_customer_new_code(customer_id):
        """Código nuevo (si alguien regaló o publicó el suyo): el anterior deja de servir. Sus
        referidos, descuentos y pedidos se conservan."""
        db = get_db()
        if not db.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone():
            abort(404)
        code = generate_code()
        db.execute("UPDATE customers SET code = ? WHERE id = ?", (code, customer_id))
        db.commit()
        flash(f"Código nuevo: {code}. El anterior ya no sirve; mándale este por WhatsApp.", "ok")
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
        options = options_by_product(p["id"] for p in products)
        # solo se comparte lo que está en el menú
        shares = {p["id"]: product_share(p, options.get(p["id"], [])) for p in products if p["active"]}
        return render_template("admin/products.html", products=products, sizes=sizes, shares=shares,
                               options=options)

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

    @app.route("/admin/menu/<int:product_id>/agotado", methods=["POST"])
    @admin_required
    def admin_product_sold_out(product_id):
        """Marca agotado (o disponible otra vez) un producto o, con `option_id`, una de sus medidas."""
        db = get_db()
        option_id = request.form.get("option_id", type=int)
        if option_id:
            db.execute("UPDATE product_options SET sold_out = 1 - sold_out WHERE id = ? AND product_id = ?",
                       (option_id, product_id))
        else:
            db.execute("UPDATE products SET sold_out = 1 - sold_out WHERE id = ?", (product_id,))
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
                              "payment_methods", "hours_open", "hours_close", "max_orders_day",
                              "delivery_fee", "delivery_zones", "free_delivery_from")}
            form["self_signup"] = "1" if request.form.get("self_signup") else "0"
            form["private_menu"] = "1" if request.form.get("private_menu") else "0"
            whatsapp = normalize_whatsapp(form["whatsapp"])
            referral = parse_cop(form["referral_value"])
            fee, free_from = parse_cop(form["delivery_fee"]), parse_cop(form["free_delivery_from"])
            zone_lines = [line.strip() for line in form["delivery_zones"].splitlines() if line.strip()]
            bad_zone = next((line for line in zone_lines if not parse_zone_line(line)[0]), None)
            error = None
            if form["whatsapp"] and not 10 <= len(whatsapp) <= 15:
                error = "Escribe el WhatsApp con el indicativo del país, por ejemplo 57 300 123 4567."
            elif (form["hours_open"] or form["hours_close"]) and None in (parse_hhmm(form["hours_open"]),
                                                                          parse_hhmm(form["hours_close"])):
                error = "Horario de pedidos: pon la hora de abrir y la de cerrar (por ejemplo 10:00 y 21:00), o deja las dos vacías."
            elif form["max_orders_day"] and not (form["max_orders_day"].isdecimal() and int(form["max_orders_day"]) <= 1000):
                error = "Máximo de pedidos por día: escribe un número, o déjalo vacío para no tener límite."
            elif fee is None or fee > MAX_PRICE:
                error = "Valor del domicilio: escríbelo en pesos, por ejemplo 5000."
            elif free_from is None or free_from > MAX_PRICE:
                error = "Domicilio gratis desde: escríbelo en pesos, por ejemplo 100000."
            elif bad_zone:
                error = f"Revisa el barrio «{bad_zone[:40]}»: escríbelo así, Chapinero = 5000."
            elif len(zone_lines) > 40:
                error = "Puedes poner hasta 40 barrios."
            elif check_discount(referral):
                error = "Valor de cada referido: " + check_discount(referral)
            if error:
                flash(error, "error")
                return render_template("admin/settings.html", settings={**template_settings(), **form},
                                       wa_saved=get_settings()["whatsapp"])
            for key in ("store_name", "tagline"):
                set_setting(key, form[key] or DEFAULT_SETTINGS[key])
            set_setting("code_prefix", normalize_code(form["code_prefix"])[:6] or DEFAULT_SETTINGS["code_prefix"])
            set_setting("whatsapp", whatsapp)
            set_setting("referral_value", str(referral))
            set_setting("self_signup", form["self_signup"])
            set_setting("private_menu", form["private_menu"])
            set_setting("hours_open", form["hours_open"] if form["hours_close"] else "")
            set_setting("hours_close", form["hours_close"] if form["hours_open"] else "")
            set_setting("max_orders_day", str(int(form["max_orders_day"] or 0)))
            set_setting("delivery_fee", str(fee))
            set_setting("free_delivery_from", str(free_from))
            set_setting("delivery_zones", "\n".join("{} = {}".format(*parse_zone_line(line)) for line in zone_lines))
            methods = [m.strip()[:30] for m in form["payment_methods"].split(",") if m.strip()][:10]
            set_setting("payment_methods", ", ".join(methods) or DEFAULT_SETTINGS["payment_methods"])
            get_db().commit()
            flash("Ajustes guardados.", "ok")
            return redirect(url_for("admin_settings"))
        return render_template("admin/settings.html", wa_saved=get_settings()["whatsapp"])


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=bool(os.environ.get("DEBUG")))
