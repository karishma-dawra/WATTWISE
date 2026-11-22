"""SQLite connection helpers and schema for WATTWISE."""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("WATTWISE_DB", os.path.join(BASE_DIR, "instance", "wattwise.db"))

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


def get_db():
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
    conn.executescript(SCHEMA)
    for table, col, ddl in MIGRATIONS:
        if col not in [r["name"] for r in conn.execute(f"PRAGMA table_info({table})")]:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
    from services import appliance_catalog
    appliance_catalog.seed(conn)
    conn.execute("UPDATE appliances SET need_hours = daily_hours WHERE need_hours IS NULL")  # typical full-use hours
    conn.execute("UPDATE household SET meter_base = start_meter_reading WHERE meter_base IS NULL AND start_meter_reading IS NOT NULL")
    conn.commit()
    conn.close()
