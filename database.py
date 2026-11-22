"""Database helpers and schema for WATTWISE.

Two backends share one code base:
  * PostgreSQL when the DATABASE_URL environment variable is set (used in production, e.g. a free Neon database,
    so accounts survive server restarts);
  * SQLite otherwise (local development and the test-suite).
The rest of the app talks to a connection with the sqlite3 API (`execute`, `?` placeholders, `.fetchone()`,
`.lastrowid`, `.rowcount`, rows readable by name or index). For PostgreSQL that API is provided by `_PgConn` below.
"""
import contextlib
import os
import re
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("WATTWISE_DB", os.path.join(BASE_DIR, "instance", "wattwise.db"))
DATABASE_URL = (os.environ.get("DATABASE_URL") or "").strip()
USE_PG = DATABASE_URL.startswith(("postgres://", "postgresql://"))

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name TEXT NOT NULL,
    mobile TEXT NOT NULL UNIQUE,
    email TEXT DEFAULT '',
    city TEXT DEFAULT '',
    language TEXT NOT NULL DEFAULT 'en',
    password_hash TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS household (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL DEFAULT 'My Household',
    location_name TEXT DEFAULT '',
    latitude REAL,
    longitude REAL,
    monthly_budget REAL NOT NULL DEFAULT 300,
    units_consumed REAL NOT NULL DEFAULT 0,
    start_meter_reading REAL,
    cycle_start TEXT NOT NULL,
    cycle_end TEXT NOT NULL,
    tariff REAL NOT NULL DEFAULT 6.5,
    fixed_charges REAL NOT NULL DEFAULT 0,
    other_charges REAL NOT NULL DEFAULT 0,
    cooling_ref_temp REAL NOT NULL DEFAULT 32,
    cooling_sensitivity REAL NOT NULL DEFAULT 0.05,
    heating_ref_temp REAL NOT NULL DEFAULT 12,
    heating_sensitivity REAL NOT NULL DEFAULT 0.08,
    comfort_bias INTEGER NOT NULL DEFAULT 50,
    is_demo INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS appliances (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES household(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    category TEXT NOT NULL DEFAULT 'Other',
    quantity INTEGER NOT NULL DEFAULT 1,
    wattage REAL NOT NULL,
    daily_hours REAL NOT NULL,
    priority TEXT NOT NULL DEFAULT 'flexible',      -- essential | flexible
    weather_type TEXT NOT NULL DEFAULT 'none',      -- cooling | heating | none
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS readings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES household(id) ON DELETE CASCADE,
    reading_date TEXT NOT NULL,
    units_consumed REAL NOT NULL,        -- cumulative units used in the billing cycle
    meter_reading REAL,
    source TEXT NOT NULL DEFAULT 'manual',  -- manual | demo
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(household_id, reading_date)
);

CREATE TABLE IF NOT EXISTS daily_consumption (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES household(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    units REAL NOT NULL,
    source TEXT NOT NULL DEFAULT 'readings',  -- readings | demo
    UNIQUE(household_id, day)
);

CREATE TABLE IF NOT EXISTS weather (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES household(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'forecast',   -- forecast | current
    temp_max REAL,
    temp_min REAL,
    current_temp REAL,
    weather_code INTEGER,
    condition TEXT,
    fetched_at TEXT,
    UNIQUE(household_id, day, kind)
);

CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES household(id) ON DELETE CASCADE,
    target_date TEXT NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES household(id) ON DELETE CASCADE,
    code TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    severity TEXT NOT NULL,          -- info | warning | critical
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    dismissed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(household_id, fingerprint)
);

-- Hourly forecast cache (Module 1). One row per local hour of each forecast day.
CREATE TABLE IF NOT EXISTS weather_hourly (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES household(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    hour INTEGER NOT NULL,               -- 0..23, local time
    temp REAL,
    apparent REAL,
    humidity REAL,
    cloud_cover REAL,
    shortwave REAL,
    precipitation REAL,
    fetched_at TEXT,
    UNIQUE(household_id, day, hour)
);

-- Module 2 output: 24 rows per day, one per hour (see services/weather_energy_engine.hourly_comfort_table).
CREATE TABLE IF NOT EXISTS hourly_plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES household(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    hour INTEGER NOT NULL,
    temp REAL,
    appliance_id INTEGER,
    appliance TEXT NOT NULL,
    on_off INTEGER NOT NULL,             -- 1 = ON this hour
    intensity REAL NOT NULL,             -- 0..1
    kwh REAL NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(household_id, day, hour, appliance)
);

-- Personal notifications: a history of alerts the user can read, filter and delete (separate from the live `alerts`).
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    type TEXT NOT NULL CHECK (type IN ('budget_warning','overuse','high_consumption','appliance_fault','info')),
    severity TEXT NOT NULL CHECK (severity IN ('info','warning','critical')),
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    appliance_id INTEGER REFERENCES appliances(id) ON DELETE SET NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    is_deleted INTEGER NOT NULL DEFAULT 0,   -- soft delete: keeps the 24-hour duplicate check working after the user deletes one
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP   -- UTC
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications (user_id, is_deleted, is_read, created_at);

-- Autofill catalog for the Add Appliance form (sample data, see services/appliance_catalog.py).
CREATE TABLE IF NOT EXISTS appliances_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    brand TEXT NOT NULL,
    model_number TEXT NOT NULL,
    model_key TEXT NOT NULL UNIQUE,          -- model number upper-cased with spaces, dashes and slashes removed
    type TEXT NOT NULL,
    wattage REAL NOT NULL,
    voltage INTEGER,
    star_rating INTEGER,                     -- BEE stars, NULL when the type has no label
    annual_kwh REAL
);

-- Module 4 output: one row per remaining day.
CREATE TABLE IF NOT EXISTS daily_plan (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    household_id INTEGER NOT NULL REFERENCES household(id) ON DELETE CASCADE,
    day TEXT NOT NULL,
    predicted_kwh REAL NOT NULL,
    allocation_kwh REAL NOT NULL,
    optimized_kwh REAL,
    feasible INTEGER NOT NULL DEFAULT 1,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(household_id, day)
);
"""


# ------------------------------------------------------------------ PostgreSQL adapter
_TS_NOW = "to_char(now() at time zone 'utc', 'YYYY-MM-DD HH24:MI:SS')"   # same text format SQLite's CURRENT_TIMESTAMP gives
_pool = None


class _PgRow(tuple):
    """A result row readable like sqlite3.Row: row[0], row["name"], row.keys(), dict(row)."""
    def __new__(cls, values, cols):
        r = super().__new__(cls, values)
        r._cols = cols
        return r

    def __getitem__(self, k):
        if isinstance(k, str):
            try:
                return tuple.__getitem__(self, self._cols.index(k))
            except ValueError:
                raise IndexError("No item with that key") from None
        return tuple.__getitem__(self, k)

    def keys(self):
        return list(self._cols)


class _PgCursor:
    def __init__(self, cur, want_id):
        self._cur = cur
        self.rowcount = cur.rowcount
        self.lastrowid = None
        self._cols = [d.name for d in cur.description] if cur.description else []
        if want_id and cur.description:
            r = cur.fetchone()
            self.lastrowid = r[0] if r else None
            self._cols = []

    def fetchone(self):
        if not self._cols:
            return None
        r = self._cur.fetchone()
        return None if r is None else _PgRow(r, self._cols)

    def fetchall(self):
        return [_PgRow(r, self._cols) for r in self._cur.fetchall()] if self._cols else []

    def __iter__(self):
        return iter(self.fetchall())


def _translate(sql):
    """sqlite-flavoured SQL -> PostgreSQL (placeholders, NULL-safe IS, timestamps)."""
    sql = sql.replace("%", "%%").replace("?", "%s")
    sql = re.sub(r"\bIS\s+%s", "IS NOT DISTINCT FROM %s", sql)
    return sql.replace("CURRENT_TIMESTAMP", _TS_NOW)


class _PgConn:
    """A pooled PostgreSQL connection that behaves like the sqlite3 connection the app was written for."""

    def __init__(self, raw):
        self._raw = raw

    def execute(self, sql, params=()):
        want_id = bool(re.match(r"\s*INSERT\s+INTO", sql, re.I)) and "RETURNING" not in sql.upper()
        q = _translate(sql)
        args = tuple(int(p) if isinstance(p, bool) else p for p in params)
        if want_id:
            q += " RETURNING id"
        cur = self._raw.cursor()
        cur.execute(q, args)
        return _PgCursor(cur, want_id)

    def executescript(self, script):
        self._raw.cursor().execute(script)

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        raw, self._raw = self._raw, None
        if raw is None:
            return
        try:
            raw.rollback()      # anything not committed is discarded, like closing an sqlite3 connection
        finally:
            _get_pool().putconn(raw)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def _configure(conn):
    from psycopg.adapt import Loader

    class _Float(Loader):          # AVG()/SUM() over integers come back as numeric -> plain float, as in SQLite
        def load(self, data):
            return float(bytes(data).decode())
    conn.adapters.register_loader("numeric", _Float)


def _get_pool():
    global _pool
    if _pool is None:
        from psycopg_pool import ConnectionPool
        _pool = ConnectionPool(DATABASE_URL, min_size=1, max_size=int(os.environ.get("DB_POOL_MAX", "8")), open=True,
                               kwargs={"prepare_threshold": None, "connect_timeout": 15},   # prepared statements off: safe behind a pgbouncer-style pooler
                               configure=_configure, check=ConnectionPool.check_connection,
                               max_idle=120, timeout=30)
    return _pool


def _pg_schema(script):
    s = re.sub(r"PRAGMA[^;]*;", "", script)
    s = s.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    s = re.sub(r"\bREAL\b", "DOUBLE PRECISION", s)
    return s.replace("DEFAULT CURRENT_TIMESTAMP", f"DEFAULT ({_TS_NOW})")


_SERIAL_TABLES = ("users", "appliances", "readings", "daily_consumption", "weather", "recommendations", "alerts",
                  "weather_hourly", "hourly_plan", "notifications", "appliances_catalog", "daily_plan")


def sync_sequences(conn):
    """After rows were inserted with an explicit id (the demo account), move each id counter past the largest id."""
    if USE_PG:
        for t in _SERIAL_TABLES:
            conn.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), COALESCE((SELECT MAX(id) FROM {t}), 0) + 1, false)")


@contextlib.contextmanager
def startup_lock():
    """Serialises first-time set-up when several server processes start together (PostgreSQL only).

    Uses a transaction-level advisory lock: it is released automatically when the transaction ends, so it can never
    be left behind (a session-level lock can get stuck behind a pgbouncer-style pooler such as Neon's). Waiting is
    capped at 30 seconds; after that start-up carries on, because the set-up statements are safe to repeat."""
    if not USE_PG:
        yield
        return
    pool = _get_pool()
    raw = pool.getconn()
    try:
        try:
            cur = raw.cursor()
            cur.execute("SET LOCAL lock_timeout = '30s'")
            cur.execute("SELECT pg_advisory_xact_lock(727002)")
        except Exception:
            raw.rollback()
        yield
    finally:
        try:
            raw.rollback()
        finally:
            pool.putconn(raw)


def get_db():
    if USE_PG:
        return _PgConn(_get_pool().getconn())
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


MIGRATIONS = [  # (table, column, DDL) — added to databases created by earlier versions
    ("household", "provider", "TEXT DEFAULT ''"),
    ("household", "consumer_number", "TEXT DEFAULT ''"),
    ("household", "meter_type", "TEXT DEFAULT 'Smart'"),
    ("household", "connection_type", "TEXT DEFAULT 'Domestic'"),
    ("household", "sanctioned_load", "REAL"),
    ("household", "bill_date", "TEXT DEFAULT ''"),
    ("household", "home_type", "TEXT DEFAULT 'Apartment'"),
    ("household", "rooms", "INTEGER"),
    ("household", "floors", "INTEGER"),
    ("household", "family_size", "INTEGER"),
    ("household", "cooling_on_temp", "REAL NOT NULL DEFAULT 24"),
    ("household", "heating_off_temp", "REAL NOT NULL DEFAULT 18"),
    ("household", "country_code", "TEXT DEFAULT ''"),
    ("household", "region_name", "TEXT DEFAULT ''"),
    ("household", "currency_symbol", "TEXT NOT NULL DEFAULT '₹'"),
    ("household", "tariff_slabs", "TEXT DEFAULT ''"),
    ("household", "duty_percent", "REAL NOT NULL DEFAULT 0"),
    ("household", "free_units", "REAL NOT NULL DEFAULT 0"),
    ("household", "tariff_source", "TEXT NOT NULL DEFAULT 'manual'"),
    ("household", "free_units_mode", "TEXT NOT NULL DEFAULT 'deduct'"),   # deduct = take units off before pricing; cover = the first N units are free in place
    ("household", "subsidy_scheme", "TEXT DEFAULT ''"),                     # name of the subsidy applied from tariff_data.json, if any
    ("household", "tariff_unverified", "TEXT DEFAULT ''"),   # JSON list of charges the applied region tariff does not know yet
    ("appliances", "need_hours", "REAL"),
    ("appliances", "user_hours", "REAL"),  # optional: hours the user entered = a daily minimum
    ("household", "meter_base", "REAL"),
    ("household", "units_offset", "REAL NOT NULL DEFAULT 0"),
    ("weather", "humidity", "REAL"),
    ("weather", "rain_mm", "REAL"),
    ("weather", "apparent_max", "REAL"),
    ("weather", "apparent_min", "REAL"),
    ("weather", "rain_chance", "REAL"),
    ("weather", "provider", "TEXT"),
    ("weather", "sunrise", "TEXT"),      # daily sunrise/sunset, local time (Module 1)
    ("weather", "sunset", "TEXT"),
]


def init_db():
    conn = get_db()
    if USE_PG:
        conn.executescript(_pg_schema(SCHEMA))
        for table, col, ddl in MIGRATIONS:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {_pg_schema(ddl)}")
    else:
        conn.executescript(SCHEMA)
        for table, col, ddl in MIGRATIONS:
            if col not in [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    from services import appliance_catalog
    appliance_catalog.seed(conn)
    conn.execute("UPDATE appliances SET need_hours = daily_hours WHERE need_hours IS NULL")  # typical full-use hours
    conn.execute("UPDATE household SET meter_base = start_meter_reading WHERE meter_base IS NULL AND start_meter_reading IS NOT NULL")
    conn.commit()
    sync_sequences(conn)
    conn.commit()
    conn.close()
