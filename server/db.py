"""SQLite access: schema, per-thread connections, data-version counter for cache invalidation."""
import os
import sqlite3
import threading
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.environ.get("BIZPILOT_DB", ROOT / "data" / "bizpilot.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY, email TEXT UNIQUE, name TEXT, role TEXT, approval_limit REAL,
  salt TEXT, pw_hash TEXT);
CREATE TABLE IF NOT EXISTS warehouses(id INTEGER PRIMARY KEY, name TEXT, city TEXT, region TEXT);
CREATE TABLE IF NOT EXISTS suppliers(
  id INTEGER PRIMARY KEY, name TEXT, country TEXT, category TEXT, rating REAL,
  on_time_pct REAL, lead_time_days INTEGER, payment_terms TEXT, status TEXT, contact_email TEXT,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS products(
  id INTEGER PRIMARY KEY, sku TEXT UNIQUE, name TEXT, category TEXT, unit TEXT,
  unit_cost REAL, unit_price REAL, supplier_id INTEGER REFERENCES suppliers(id));
CREATE TABLE IF NOT EXISTS supplier_prices(
  supplier_id INTEGER, product_id INTEGER, unit_price REAL, min_qty INTEGER, lead_time_days INTEGER,
  PRIMARY KEY(supplier_id, product_id));
CREATE TABLE IF NOT EXISTS inventory(
  product_id INTEGER, warehouse_id INTEGER, qty INTEGER, reorder_level INTEGER,
  PRIMARY KEY(product_id, warehouse_id));
CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, name TEXT, segment TEXT, region TEXT, country TEXT);
CREATE TABLE IF NOT EXISTS sales_orders(
  id INTEGER PRIMARY KEY, so_number TEXT UNIQUE, customer_id INTEGER, region TEXT, warehouse_id INTEGER,
  status TEXT, order_date TEXT, promised_date TEXT, delivered_date TEXT, total REAL);
CREATE TABLE IF NOT EXISTS so_items(
  id INTEGER PRIMARY KEY, order_id INTEGER, product_id INTEGER, qty INTEGER, unit_price REAL);
CREATE TABLE IF NOT EXISTS shipments(
  id INTEGER PRIMARY KEY, order_id INTEGER, carrier TEXT, status TEXT, shipped_at TEXT, eta TEXT,
  delivered_at TEXT, delay_days INTEGER, origin TEXT, destination TEXT);
CREATE TABLE IF NOT EXISTS purchase_orders(
  id INTEGER PRIMARY KEY, po_number TEXT UNIQUE, supplier_id INTEGER, status TEXT, created_at TEXT,
  expected_at TEXT, received_at TEXT, total REAL, created_by TEXT, notes TEXT);
CREATE TABLE IF NOT EXISTS po_items(
  id INTEGER PRIMARY KEY, po_id INTEGER, product_id INTEGER, qty INTEGER, unit_price REAL);
CREATE TABLE IF NOT EXISTS expenses(
  id INTEGER PRIMARY KEY, date TEXT, category TEXT, amount REAL);
CREATE TABLE IF NOT EXISTS invoices(
  id INTEGER PRIMARY KEY, order_id INTEGER, customer_id INTEGER, issued_at TEXT, due_at TEXT,
  paid_at TEXT, amount REAL);
CREATE TABLE IF NOT EXISTS approvals(
  id INTEGER PRIMARY KEY, type TEXT, ref_id INTEGER, ref_label TEXT, amount REAL, requested_by TEXT,
  status TEXT, reason TEXT, created_at TEXT, decided_by TEXT, decided_at TEXT);
CREATE TABLE IF NOT EXISTS agent_audit(
  id INTEGER PRIMARY KEY, ts TEXT, user_email TEXT, utterance TEXT, tier TEXT, intent TEXT,
  confidence REAL, page TEXT, tools TEXT, actions TEXT, status TEXT, route_ms REAL, total_ms REAL, detail TEXT);
CREATE INDEX IF NOT EXISTS ix_so_date ON sales_orders(order_date);
CREATE INDEX IF NOT EXISTS ix_so_status ON sales_orders(status);
CREATE INDEX IF NOT EXISTS ix_soi_order ON so_items(order_id);
CREATE INDEX IF NOT EXISTS ix_soi_prod ON so_items(product_id);
CREATE INDEX IF NOT EXISTS ix_po_created ON purchase_orders(created_at);
CREATE INDEX IF NOT EXISTS ix_poi_po ON po_items(po_id);
CREATE INDEX IF NOT EXISTS ix_exp_date ON expenses(date);
CREATE INDEX IF NOT EXISTS ix_ship_order ON shipments(order_id);
CREATE INDEX IF NOT EXISTS ix_inv_prod ON inventory(product_id);
"""

_local = threading.local()
_version = {"n": 0}
_meta_cache: dict = {}


def get_db() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_schema(conn: sqlite3.Connection):
    conn.executescript(SCHEMA)
    conn.commit()


def data_version() -> int:
    return _version["n"]


def bump():
    """Called after every write so cached read results are discarded."""
    _version["n"] += 1


def meta(key: str, default=None):
    if key not in _meta_cache:
        row = get_db().execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        _meta_cache[key] = row["value"] if row else default
    return _meta_cache[key]


def as_of() -> date:
    """The 'today' of the dataset (the date the demo data was seeded)."""
    return date.fromisoformat(meta("as_of"))


def q(sql: str, params=()):
    return [dict(r) for r in get_db().execute(sql, params).fetchall()]


def q1(sql: str, params=()):
    r = get_db().execute(sql, params).fetchone()
    return dict(r) if r else None


def scalar(sql: str, params=(), default=0):
    r = get_db().execute(sql, params).fetchone()
    return default if r is None or r[0] is None else r[0]
