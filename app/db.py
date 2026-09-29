import sqlite3

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL UNIQUE,
    phone TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('customer', 'rider')),
    password_hash TEXT NOT NULL,
    vehicle TEXT,
    plate TEXT,
    is_online INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    token TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    customer_id INTEGER NOT NULL REFERENCES users(id),
    rider_id INTEGER REFERENCES users(id),
    status TEXT NOT NULL CHECK (status IN ('requested', 'accepted', 'picked_up', 'delivered', 'cancelled')),
    pickup_address TEXT NOT NULL,
    pickup_lat REAL NOT NULL,
    pickup_lng REAL NOT NULL,
    dropoff_address TEXT NOT NULL,
    dropoff_lat REAL NOT NULL,
    dropoff_lng REAL NOT NULL,
    stops_json TEXT NOT NULL DEFAULT '[]',
    tasks_json TEXT NOT NULL DEFAULT '[]',
    recipient_name TEXT,
    recipient_phone TEXT,
    notes TEXT,
    distance_km REAL NOT NULL,
    duration_min REAL NOT NULL,
    route_json TEXT NOT NULL DEFAULT '[]',
    route_source TEXT,
    price REAL NOT NULL,
    currency TEXT NOT NULL,
    breakdown_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    accepted_at TEXT,
    picked_up_at TEXT,
    delivered_at TEXT,
    cancelled_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_customer ON orders(customer_id);
CREATE INDEX IF NOT EXISTS idx_orders_rider ON orders(rider_id);

CREATE TABLE IF NOT EXISTS rider_locations (
    rider_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    lat REAL NOT NULL,
    lng REAL NOT NULL,
    updated_at TEXT NOT NULL
);
"""


# Schema changes, applied in order on startup. Each runs once (tracked by PRAGMA user_version).
MIGRATIONS = [
    # 1: admin role, rider approval, account suspension, payments, editable settings
    """
    PRAGMA foreign_keys = OFF;
    BEGIN;
    CREATE TABLE users_new (
        id INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        email TEXT NOT NULL UNIQUE,
        phone TEXT NOT NULL,
        role TEXT NOT NULL CHECK (role IN ('customer', 'rider', 'admin')),
        password_hash TEXT NOT NULL,
        vehicle TEXT,
        plate TEXT,
        is_online INTEGER NOT NULL DEFAULT 0,
        approved INTEGER NOT NULL DEFAULT 1,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    );
    INSERT INTO users_new (id, name, email, phone, role, password_hash, vehicle, plate, is_online, created_at)
        SELECT id, name, email, phone, role, password_hash, vehicle, plate, is_online, created_at FROM users;
    DROP TABLE users;
    ALTER TABLE users_new RENAME TO users;

    ALTER TABLE orders ADD COLUMN payment_method TEXT NOT NULL DEFAULT 'cash';
    ALTER TABLE orders ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'unpaid';
    ALTER TABLE orders ADD COLUMN mmg_number TEXT;
    ALTER TABLE orders ADD COLUMN cancelled_by TEXT;
    UPDATE orders SET payment_status = 'paid' WHERE status = 'delivered';

    CREATE TABLE payments (
        id INTEGER PRIMARY KEY,
        order_id INTEGER NOT NULL REFERENCES orders(id),
        provider TEXT NOT NULL,
        amount REAL NOT NULL,
        currency TEXT NOT NULL,
        status TEXT NOT NULL CHECK (status IN ('pending', 'paid', 'failed', 'refunded', 'void')),
        provider_ref TEXT,
        message TEXT,
        raw_json TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX idx_payments_order ON payments(order_id);
    CREATE UNIQUE INDEX idx_payments_ref ON payments(provider, provider_ref);

    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    PRAGMA user_version = 1;
    COMMIT;
    PRAGMA foreign_keys = ON;
    """,
    # 2: orders cancelled before payments existed owe nothing
    """
    BEGIN;
    UPDATE orders SET payment_status = 'void' WHERE status = 'cancelled' AND payment_status = 'unpaid';
    PRAGMA user_version = 2;
    COMMIT;
    """,
]


def _conn():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _conn()
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        conn.commit()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for script in MIGRATIONS[version:]:
            conn.executescript(script)
    finally:
        conn.close()


def query(sql, args=()):
    conn = _conn()
    try:
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    finally:
        conn.close()


def query_one(sql, args=()):
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql, args=()):
    """Run a write. Returns (lastrowid, rowcount)."""
    conn = _conn()
    try:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid, cur.rowcount
    finally:
        conn.close()


def execute_all(statements):
    """Run several writes in one transaction: [(sql, args), ...]."""
    conn = _conn()
    try:
        with conn:
            for sql, args in statements:
                conn.execute(sql, args)
    finally:
        conn.close()


def user_for_token(token):
    if not token:
        return None
    return query_one(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ? AND u.active = 1", (token,)
    )
