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


def user_for_token(token):
    if not token:
        return None
    return query_one(
        "SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id WHERE s.token = ?", (token,)
    )
