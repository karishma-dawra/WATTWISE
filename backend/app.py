"""WattWise backend: one Flask file. Run with: python app.py  (port 5000)."""
import calendar
import math
import os
import sqlite3
import sys
from datetime import date, timedelta

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "logic"))  # WattWise logic modules (unchanged)
import weather  # noqa: E402
from api import run_wattwise  # noqa: E402

DB_FILE = os.path.join(HERE, "wattwise.db")
DEFAULT_CITY = "Delhi"
DEFAULT_FAMILY_SIZE = 4  # not stored in the database
SCENARIOS = ("live", "hot", "cold")
DEMO_LEVELS = {"normal": 0.60, "tight": 0.85}  # share of the monthly budget used so far

app = Flask(__name__, static_folder=os.path.join(HERE, "frontend"), static_url_path="")
CORS(app)


# ---------- database ----------

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS households (
            id INTEGER PRIMARY KEY, name TEXT, city TEXT,
            budget_value REAL, budget_type TEXT, monthly_budget_kwh REAL, tariff_rate REAL);
        CREATE TABLE IF NOT EXISTS appliances (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, wattage REAL NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1, daily_hours REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS daily_consumption (
            date TEXT PRIMARY KEY, units REAL NOT NULL CHECK (units >= 0));
    """)
    conn.commit()
    conn.close()


def get_household(conn):
    return conn.execute("SELECT * FROM households WHERE id = 1").fetchone()


def error(message, status=400):
    return jsonify({"error": message}), status


NO_HOUSEHOLD = "No household saved yet. Save a budget first."


# ---------- household ----------

@app.get("/")
def home():
    """The website (frontend/index.html)."""
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/health")
def health():
    return jsonify({"status": "WattWise backend is running!"})


@app.post("/api/household")
def save_household():
    data = request.get_json(silent=True) or {}
    try:
        value = float(data["budget_value"])
        tariff = float(data.get("tariff_rate", 8.0))
    except (KeyError, TypeError, ValueError):
        return error("budget_value is required and must be a number")
    if value <= 0 or tariff <= 0:
        return error("budget_value and tariff_rate must be above 0")
    budget_type = str(data.get("budget_type", "kwh")).lower()
    kwh = round(value / tariff, 2) if budget_type == "rupees" else value

    conn = db()
    conn.execute("INSERT OR REPLACE INTO households VALUES (1, ?, ?, ?, ?, ?, ?)",
                 (data.get("name", "My Home"), data.get("city", DEFAULT_CITY), value, budget_type, kwh, tariff))
    conn.commit()
    conn.close()
    return jsonify({"message": "Household saved", "monthly_budget_kwh": kwh}), 201


@app.get("/api/household")
def read_household():
    conn = db()
    row = get_household(conn)
    conn.close()
    return jsonify(dict(row)) if row else error("No household saved yet", 404)


# ---------- appliances ----------

@app.get("/api/appliances")
def list_appliances():
    conn = db()
    rows = conn.execute("SELECT * FROM appliances").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.post("/api/appliances")
def add_appliance():
    data = request.get_json(silent=True) or {}
    try:
        name = str(data["name"]).strip()
        wattage, hours = float(data["wattage"]), float(data["daily_hours"])
        quantity = int(data.get("quantity", 1))
    except (KeyError, TypeError, ValueError):
        return error("name, wattage and daily_hours are required")
    if not name:
        return error("name is required")
    conn = db()
    if conn.execute("SELECT 1 FROM appliances WHERE lower(name) = lower(?)", (name,)).fetchone():
        conn.close()
        return error(f"An appliance named '{name}' already exists (names must be unique)")
    cur = conn.execute("INSERT INTO appliances (name, wattage, quantity, daily_hours) VALUES (?, ?, ?, ?)",
                       (name, wattage, quantity, hours))
    conn.commit()
    conn.close()
    return jsonify({"message": "Appliance added", "id": cur.lastrowid}), 201


@app.put("/api/appliances/<int:appliance_id>")
def edit_appliance(appliance_id):
    data = request.get_json(silent=True) or {}
    conn = db()
    row = conn.execute("SELECT * FROM appliances WHERE id = ?", (appliance_id,)).fetchone()
    if row is None:
        conn.close()
        return error("Appliance not found", 404)
    try:
        name = str(data.get("name", row["name"])).strip()
        wattage = float(data.get("wattage", row["wattage"]))
        hours = float(data.get("daily_hours", row["daily_hours"]))
        quantity = int(data.get("quantity", row["quantity"]))
    except (TypeError, ValueError):
        conn.close()
        return error("wattage, daily_hours and quantity must be numbers")
    if not name:
        conn.close()
        return error("name is required")
    if conn.execute("SELECT 1 FROM appliances WHERE lower(name) = lower(?) AND id != ?", (name, appliance_id)).fetchone():
        conn.close()
        return error(f"An appliance named '{name}' already exists (names must be unique)")
    conn.execute("UPDATE appliances SET name = ?, wattage = ?, quantity = ?, daily_hours = ? WHERE id = ?",
                 (name, wattage, quantity, hours, appliance_id))
    conn.commit()
    conn.close()
    return jsonify({"message": "Appliance updated"})


@app.delete("/api/appliances/<int:appliance_id>")
def delete_appliance(appliance_id):
    conn = db()
    cur = conn.execute("DELETE FROM appliances WHERE id = ?", (appliance_id,))
    conn.commit()
    conn.close()
    return jsonify({"message": "Appliance deleted"}) if cur.rowcount else error("Appliance not found", 404)


# ---------- consumption ----------

@app.post("/api/consumption")
def log_consumption():
    """Log units for a date (default today), replacing any existing entry for that date."""
    data = request.get_json(silent=True) or {}
    units = data.get("units")
    if isinstance(units, bool) or not isinstance(units, (int, float)) or units < 0:
        return error("units is required and must be a number of at least 0")
    try:
        day = date.fromisoformat(data.get("date") or date.today().isoformat())
    except ValueError:
        return error("date must look like YYYY-MM-DD")
    conn = db()
    if get_household(conn) is None:
        conn.close()
        return error(NO_HOUSEHOLD)
    conn.execute("INSERT OR REPLACE INTO daily_consumption VALUES (?, ?)", (day.isoformat(), float(units)))
    conn.commit()
    conn.close()
    return jsonify({"message": "Consumption saved", "date": day.isoformat(), "units": float(units)}), 201


@app.get("/api/consumption")
def list_consumption():
    conn = db()
    rows = conn.execute("SELECT date, units FROM daily_consumption ORDER BY date").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ---------- advice ----------

def household_dict(household, appliances):
    """The household in the format run_wattwise expects (category omitted: classified by name)."""
    return {
        "monthly_budget_units": household["monthly_budget_kwh"],
        "family_size": int(request.args.get("family_size") or DEFAULT_FAMILY_SIZE),
        "appliances": [{"name": a["name"], "wattage": a["wattage"], "quantity": a["quantity"],
                        "avg_hours_per_day": a["daily_hours"]} for a in appliances],
    }


def forecast_for(scenario, city, today):
    """(forecast, warnings, weather_source): dated to start today and filled to the end of the month."""
    if scenario == "live":
        forecast, warnings = weather.fetch_forecast_safe(city)
        source = "sample_fallback" if warnings else "live"
    elif scenario == "hot":
        forecast, warnings, source = weather.load_hot_sample_forecast(), [], "sample_hot"
    else:
        forecast, warnings, source = weather.load_cold_sample_forecast(), [], "sample_cold"

    shift = today - date.fromisoformat(forecast[0]["date"])  # run_wattwise treats forecast[0] as today
    if shift:
        forecast = [{**d, "date": (date.fromisoformat(d["date"]) + shift).isoformat()} for d in forecast]
    days_in_month = calendar.monthrange(today.year, today.month)[1]
    forecast, fill_warnings = weather.fill_month_forecast(forecast, days_in_month, today.isoformat())
    return forecast, warnings + fill_warnings, source


@app.get("/api/advice")
def advice():
    """Query: scenario = live (default) | hot | cold, city (optional), family_size (optional)."""
    scenario = request.args.get("scenario", "live").lower()
    if scenario not in SCENARIOS:
        return error(f"scenario must be one of: {', '.join(SCENARIOS)}")

    today = date.today()
    first = today.replace(day=1)
    conn = db()
    household = get_household(conn)
    appliances = conn.execute("SELECT * FROM appliances").fetchall()
    logged = {r["date"]: r["units"] for r in conn.execute(
        "SELECT date, units FROM daily_consumption WHERE date >= ? AND date <= ?",
        (first.isoformat(), today.isoformat()))}
    conn.close()
    if household is None:
        return error(NO_HOUSEHOLD)

    past_days = [(first + timedelta(days=i)).isoformat() for i in range(today.day - 1)]
    missing = [d for d in past_days if d not in logged]
    history = [logged.get(d, 0.0) for d in past_days]
    city = request.args.get("city") or household["city"] or DEFAULT_CITY

    try:
        forecast, weather_warnings, source = forecast_for(scenario, city, today)
        result = run_wattwise(household_dict(household, appliances), forecast, history,
                              logged.get(today.isoformat(), 0.0))
    except ValueError as e:
        return error(str(e))

    if missing:
        result["warnings"].append(
            f"only {len(past_days) - len(missing)} of {len(past_days)} past days are logged "
            f"(missing: {', '.join(missing)}); missing days are counted as 0 units, "
            f"so the budget may look better than it is")
    result.update(history_logged_days=len(past_days) - len(missing), history_expected_days=len(past_days),
                  weather_source=source, weather_warnings=weather_warnings, city=city)
    return jsonify(result)


# ---------- demo data ----------

@app.post("/api/demo/seed")
def seed_demo():
    """DEMO ONLY. Body: {"level": "normal"|"tight", "scenario": "live"|"hot"|"cold" (optional)}.

    Deletes ALL logged consumption, then logs the 1st..yesterday of this month adding up to ~60% (normal)
    or ~85% (tight) of the budget, with today's entry at about half of today's predicted usage.
    """
    data = request.get_json(silent=True) or {}
    level, scenario = data.get("level"), str(data.get("scenario", "live")).lower()
    if level not in DEMO_LEVELS:
        return error('level must be "normal" or "tight"')
    if scenario not in SCENARIOS:
        return error(f"scenario must be one of: {', '.join(SCENARIOS)}")

    conn = db()
    household = get_household(conn)
    appliances = conn.execute("SELECT * FROM appliances").fetchall()
    if household is None:
        conn.close()
        return error(NO_HOUSEHOLD)

    today = date.today()
    first = today.replace(day=1)
    budget = household["monthly_budget_kwh"]
    try:
        forecast, _, _ = forecast_for(scenario, household["city"] or DEFAULT_CITY, today)
        predicted = run_wattwise(household_dict(household, appliances), forecast, [], 0)["today_plan"]["total_units"]
    except ValueError as e:
        conn.close()
        return error(str(e))

    # slightly varied daily values that add up to the target
    n = today.day - 1
    target = round(budget * DEMO_LEVELS[level], 2)
    weights = [1 + 0.15 * math.sin(i * 1.7) + 0.08 * math.cos(i * 0.9) for i in range(n)]
    values = [round(target * w / sum(weights), 2) for w in weights]
    if values:
        values[-1] = round(values[-1] + target - sum(values), 2)  # absorb rounding drift
    today_units = round(predicted / 2, 2)

    conn.execute("DELETE FROM daily_consumption")
    rows = [((first + timedelta(days=i)).isoformat(), v) for i, v in enumerate(values)]
    conn.executemany("INSERT INTO daily_consumption VALUES (?, ?)", rows + [(today.isoformat(), today_units)])
    conn.commit()
    conn.close()

    total = round(sum(values), 2)
    return jsonify({"demo": True, "level": level, "days_seeded": n, "history_total_units": total,
                    "history_percent_of_budget": round(total / budget * 100, 1),
                    "today_units": today_units, "predicted_daily_units": predicted}), 201


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
