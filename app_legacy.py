"""WattWise - weather-aware smart electricity management (Flask + SQLite)."""
import calendar
import json
import random
import sqlite3
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

BASE = Path(__file__).parent
DB_PATH = BASE / "wattwise.db"
app = Flask(__name__, static_folder="static", static_url_path="/static")

# Slab tariff (per unit, incremental). Editable defaults; estimate only.
SLABS = [(100, 3.5), (200, 5.0), (None, 7.0)]
CATEGORIES = ["ac", "cooler", "fan", "heater", "geyser", "fridge", "light", "tv", "washing_machine", "other"]
FLEXIBLE = {"ac", "cooler", "heater", "geyser"}   # hours we can recommend/curtail
MIN_SHARE = 0.3                                    # never recommend < 30% of comfort hours


# ---------------------------------------------------------------- database
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS appliances (
                id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL,
                watts REAL NOT NULL, quantity INTEGER NOT NULL DEFAULT 1, hours REAL NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS usage_log (day TEXT PRIMARY KEY, units REAL NOT NULL);
            """
        )
        defaults = {"budget": "300", "city": "Delhi", "lat": "28.61", "lon": "77.21"}
        for k, v in defaults.items():
            c.execute("INSERT OR IGNORE INTO settings VALUES (?,?)", (k, v))


def get_settings():
    with db() as c:
        s = {r["key"]: r["value"] for r in c.execute("SELECT * FROM settings")}
    return {"budget": float(s["budget"]), "city": s["city"], "lat": float(s["lat"]), "lon": float(s["lon"])}


def get_appliances():
    with db() as c:
        return [dict(r) for r in c.execute("SELECT * FROM appliances ORDER BY id")]


def get_usage(month_start, month_end):
    with db() as c:
        rows = c.execute("SELECT day, units FROM usage_log WHERE day BETWEEN ? AND ? ORDER BY day",
                         (month_start.isoformat(), month_end.isoformat()))
        return {r["day"]: r["units"] for r in rows}


# ---------------------------------------------------------------- weather
def http_json(url, timeout=6):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def fetch_weather(lat, lon):
    """Open-Meteo 16-day daily forecast. Falls back to a synthetic forecast if offline."""
    url = ("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode({
        "latitude": lat, "longitude": lon, "daily": "temperature_2m_max,temperature_2m_min",
        "forecast_days": 16, "timezone": "auto"}))
    try:
        d = http_json(url)["daily"]
        return {"source": "open-meteo", "days": [
            {"date": t, "tmax": mx, "tmin": mn}
            for t, mx, mn in zip(d["time"], d["temperature_2m_max"], d["temperature_2m_min"])
            if mx is not None and mn is not None]}
    except Exception:
        rnd = random.Random(date.today().toordinal())
        base = 34 + 6 * rnd.random()
        days = []
        for i in range(16):
            mx = base + rnd.uniform(-3, 3) + (3 if i % 5 == 0 else 0)
            days.append({"date": (date.today() + timedelta(days=i)).isoformat(),
                         "tmax": round(mx, 1), "tmin": round(mx - 10 - rnd.random() * 2, 1)})
        return {"source": "fallback (offline demo data)", "days": days}


def classify(tmax, tmin):
    if tmax >= 38:
        return "very hot"
    if tmax >= 32:
        return "hot"
    if tmin <= 8:
        return "very cold"
    if tmin <= 14:
        return "cold"
    return "mild"


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def weather_factor(cat, tmax, tmin):
    """Share of the user's 'comfort hours' needed on a day with this weather."""
    if cat == "ac":
        return clamp((tmax - 22) / 16, 0, 1.3)
    if cat == "cooler":
        return clamp((tmax - 24) / 16, 0, 1.3)
    if cat == "fan":
        return clamp((tmax - 20) / 15, 0.2, 1.3)
    if cat == "heater":
        return clamp((18 - tmin) / 12, 0, 1.3)
    if cat == "geyser":
        return clamp((24 - tmin) / 14, 0.3, 1.2)
    return 1.0


# ---------------------------------------------------------------- modelling
def kw(a):
    return a["watts"] * a["quantity"] / 1000.0


def day_demand(apps, tmax, tmin):
    """Unconstrained (comfort) consumption in units for one day."""
    return sum(kw(a) * a["hours"] * weather_factor(a["category"], tmax, tmin) for a in apps)


def tariff_bill(units):
    bill, left, prev = 0.0, units, 0
    for cap, rate in SLABS:
        width = (cap if cap is not None else float("inf"))
        take = min(left, width)
        bill += take * rate
        left -= take
        if left <= 0:
            break
    return round(bill, 2)


def advise_day(apps, tmax, tmin, limit):
    """Pick appliance hours for one day so total fits within `limit` units."""
    rows, fixed, flex = [], 0.0, 0.0
    for a in apps:
        f = weather_factor(a["category"], tmax, tmin)
        want = a["hours"] * f
        e = kw(a) * want
        if a["category"] in FLEXIBLE:
            flex += e
        else:
            fixed += e
        rows.append((a, want, e))
    avail = max(limit - fixed, 0)
    share = 1.0 if flex <= 0 else clamp(avail / flex, MIN_SHARE, 1.0)
    out, total = [], 0.0
    for a, want, e in rows:
        if a["category"] in FLEXIBLE:
            hrs = round(want * share * 2) / 2      # round to 0.5h
            hrs = min(hrs, 24)
        else:
            hrs = round(want, 1)
        units = kw(a) * hrs
        total += units
        out.append({"id": a["id"], "name": a["name"], "category": a["category"], "flexible": a["category"] in FLEXIBLE,
                    "comfort_hours": round(want, 1), "recommended_hours": hrs, "units": round(units, 2)})
    return {"appliances": out, "total_units": round(total, 2), "share": round(share, 2),
            "tight": flex > 0 and avail / flex < MIN_SHARE}


def build_state():
    today = date.today()
    st = get_settings()
    apps = get_appliances()
    first = today.replace(day=1)
    last = today.replace(day=calendar.monthrange(today.year, today.month)[1])
    usage = get_usage(first, last)
    weather = fetch_weather(st["lat"], st["lon"])
    wmap = {d["date"]: d for d in weather["days"]}
    avg_max = sum(d["tmax"] for d in weather["days"]) / max(len(weather["days"]), 1)
    avg_min = sum(d["tmin"] for d in weather["days"]) / max(len(weather["days"]), 1)

    used = sum(u for d, u in usage.items() if d < today.isoformat())
    today_used = usage.get(today.isoformat())
    remaining = st["budget"] - used

    # future plan days: today .. month end
    plan_days = []
    d = today
    while d <= last:
        w = wmap.get(d.isoformat(), {"date": d.isoformat(), "tmax": avg_max, "tmin": avg_min})
        plan_days.append({**w, "forecast": d.isoformat() in wmap})
        d += timedelta(days=1)
    for p in plan_days:
        p["demand"] = day_demand(apps, p["tmax"], p["tmin"])
        p["label"] = classify(p["tmax"], p["tmin"])
    total_w = sum(max(p["demand"], 0.01) for p in plan_days)
    for p in plan_days:
        p["limit"] = round(max(remaining, 0) * max(p["demand"], 0.01) / total_w, 2)
        p["demand"] = round(p["demand"], 2)

    predicted_future = sum(p["demand"] for p in plan_days)
    predicted_total = used + predicted_future
    avg_demand = predicted_future / max(len(plan_days), 1)
    days_left = len(plan_days)

    # Advisor for the next few days
    advice = []
    for p in plan_days[:7]:
        adv = advise_day(apps, p["tmax"], p["tmin"], p["limit"])
        advice.append({"date": p["date"], "tmax": p["tmax"], "tmin": p["tmin"], "label": p["label"],
                       "limit": p["limit"], "comfort_demand": p["demand"], **adv,
                       "message": message_for(adv, p)})

    # Alerts
    alerts = []
    if predicted_total > st["budget"]:
        over = predicted_total - st["budget"]
        alerts.append({"level": "danger", "text": f"At your normal comfort usage you will exceed the budget by about {over:.0f} units "
                       f"({predicted_total:.0f} vs {st['budget']:.0f}). Follow the advisor schedule to stay within budget."})
    elif predicted_total > 0.9 * st["budget"]:
        alerts.append({"level": "warn", "text": f"Projected usage ({predicted_total:.0f} units) is above 90% of your budget."})
    else:
        alerts.append({"level": "ok", "text": f"On track: projected {predicted_total:.0f} of {st['budget']:.0f} units."})
    if today_used is not None and plan_days and today_used > plan_days[0]["limit"] * 1.05:
        alerts.append({"level": "danger", "text": f"Excessive usage today: {today_used:.1f} units vs recommended limit {plan_days[0]['limit']:.1f}."})
    for p in plan_days[1:8]:
        if avg_demand and p["demand"] > 1.2 * avg_demand and p["label"] in ("very hot", "hot", "very cold", "cold"):
            alerts.append({"level": "warn", "text": f"High-consumption day ahead: {p['date']} ({p['label']}, {p['tmax']:.0f}°C max)."})
    if remaining <= 0:
        alerts.append({"level": "danger", "text": "Monthly budget is already used up."})

    per_app = sorted(({"name": a["name"], "units_per_day": round(kw(a) * a["hours"], 2),
                       "units_per_month": round(kw(a) * a["hours"] * 30, 1)} for a in apps),
                     key=lambda x: -x["units_per_day"])

    # chart series
    month_days = [(first + timedelta(days=i)).isoformat() for i in range(last.day)]
    cumulative, run = [], 0.0
    plan_map = {p["date"]: p for p in plan_days}
    pred_cum, prun = [], 0.0
    for dd in month_days:
        if dd < today.isoformat():
            run += usage.get(dd, 0)
            prun = run
            cumulative.append(round(run, 1))
            pred_cum.append(None)
        else:
            cumulative.append(None)
            prun += plan_map[dd]["demand"]
            pred_cum.append(round(prun, 1))
    if today.day > 1:
        pred_cum[today.day - 2] = cumulative[today.day - 2]

    return {
        "settings": st, "today": today.isoformat(), "weather": {"source": weather["source"], "days": plan_days[:16]},
        "appliances": apps, "top_appliances": per_app,
        "summary": {
            "budget": st["budget"], "used": round(used, 1), "today_used": today_used, "remaining": round(remaining, 1),
            "days_left": days_left, "daily_limit": plan_days[0]["limit"] if plan_days else 0,
            "predicted_units": round(predicted_total, 1), "predicted_bill": tariff_bill(predicted_total),
            "budget_bill": tariff_bill(st["budget"]),
            "will_exceed": predicted_total > st["budget"],
            "percent_used": round(100 * used / st["budget"], 1) if st["budget"] else 0,
        },
        "plan": plan_days, "advice": advice, "alerts": alerts,
        "charts": {"month_days": month_days, "cumulative": cumulative, "predicted_cumulative": pred_cum,
                   "daily_actual": [usage.get(d) for d in month_days],
                   "daily_limit": [plan_map[d]["limit"] if d in plan_map else None for d in month_days],
                   "budget": st["budget"]},
        "categories": CATEGORIES,
    }


def message_for(adv, p):
    flex = [a for a in adv["appliances"] if a["flexible"] and a["comfort_hours"] > 0]
    if not flex:
        return f"Mild weather ({p['tmax']:.0f}°C): no heavy cooling or heating needed."
    parts = [f"{a['name']} ~{a['recommended_hours']:g} h" for a in flex if a["recommended_hours"] > 0]
    msg = f"Expect up to {p['tmax']:.0f}°C. Recommended: " + ", ".join(parts) + "." if parts else "No flexible appliances needed."
    if adv["tight"]:
        msg += " Budget is very tight — consider cutting other loads."
    elif adv["share"] < 1:
        msg += " (Reduced from comfort hours to fit your budget.)"
    return msg


# ---------------------------------------------------------------- routes
@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/api/state")
def api_state():
    return jsonify(build_state())


@app.post("/api/settings")
def api_settings():
    data = request.get_json(force=True)
    with db() as c:
        if "budget" in data:
            b = float(data["budget"])
            if b <= 0:
                return jsonify(error="Budget must be positive"), 400
            c.execute("REPLACE INTO settings VALUES ('budget', ?)", (str(b),))
        city = (data.get("city") or "").strip()
        if city and city != get_settings()["city"]:
            try:
                g = http_json("https://geocoding-api.open-meteo.com/v1/search?" +
                              urllib.parse.urlencode({"name": city, "count": 1}))
                r = g["results"][0]
                c.execute("REPLACE INTO settings VALUES ('city', ?)", (r["name"],))
                c.execute("REPLACE INTO settings VALUES ('lat', ?)", (str(r["latitude"]),))
                c.execute("REPLACE INTO settings VALUES ('lon', ?)", (str(r["longitude"]),))
            except Exception:
                return jsonify(error=f"Could not find city '{city}'"), 400
    return jsonify(ok=True)


@app.post("/api/appliances")
def api_add_appliance():
    d = request.get_json(force=True)
    try:
        name, cat = d["name"].strip(), d.get("category", "other")
        watts, qty, hrs = float(d["watts"]), int(d.get("quantity", 1)), float(d.get("hours", 1))
        if not name or watts <= 0 or qty < 1 or not 0 <= hrs <= 24:
            raise ValueError
        if cat not in CATEGORIES:
            cat = "other"
    except (KeyError, ValueError, TypeError):
        return jsonify(error="Invalid appliance data"), 400
    with db() as c:
        c.execute("INSERT INTO appliances (name,category,watts,quantity,hours) VALUES (?,?,?,?,?)",
                  (name, cat, watts, qty, hrs))
    return jsonify(ok=True)


@app.put("/api/appliances/<int:aid>")
def api_update_appliance(aid):
    d = request.get_json(force=True)
    try:
        hrs = float(d["hours"])
        if not 0 <= hrs <= 24:
            raise ValueError
    except (KeyError, ValueError, TypeError):
        return jsonify(error="Hours must be 0-24"), 400
    with db() as c:
        c.execute("UPDATE appliances SET hours=? WHERE id=?", (hrs, aid))
    return jsonify(ok=True)


@app.delete("/api/appliances/<int:aid>")
def api_delete_appliance(aid):
    with db() as c:
        c.execute("DELETE FROM appliances WHERE id=?", (aid,))
    return jsonify(ok=True)


@app.post("/api/usage")
def api_usage():
    d = request.get_json(force=True)
    try:
        day = datetime.strptime(d.get("day") or date.today().isoformat(), "%Y-%m-%d").date().isoformat()
        units = float(d["units"])
        if units < 0:
            raise ValueError
    except (KeyError, ValueError, TypeError):
        return jsonify(error="Invalid date or units"), 400
    with db() as c:
        c.execute("REPLACE INTO usage_log VALUES (?,?)", (day, units))
    return jsonify(ok=True)


@app.post("/api/demo")
def api_demo():
    """Load a sample household and usage history for this month."""
    items = [("Air Conditioner", "ac", 1500, 1, 6), ("Ceiling Fan", "fan", 75, 4, 10),
             ("Geyser", "geyser", 2000, 1, 1), ("Refrigerator", "fridge", 150, 1, 24),
             ("LED Lights", "light", 10, 8, 6), ("Television", "tv", 100, 1, 4),
             ("Washing Machine", "washing_machine", 500, 1, 0.5), ("Air Cooler", "cooler", 200, 1, 4)]
    today = date.today()
    with db() as c:
        c.execute("DELETE FROM appliances")
        c.execute("DELETE FROM usage_log")
        c.executemany("INSERT INTO appliances (name,category,watts,quantity,hours) VALUES (?,?,?,?,?)", items)
        rnd = random.Random(7)
        apps = [dict(zip(("name", "category", "watts", "quantity", "hours"), i)) for i in items]
        for i in range(1, today.day):
            day = today.replace(day=i)
            tmax = 33 + rnd.uniform(-4, 6)
            u = day_demand(apps, tmax, tmax - 10) * rnd.uniform(0.75, 0.95)
            c.execute("REPLACE INTO usage_log VALUES (?,?)", (day.isoformat(), round(u, 1)))
    return jsonify(ok=True)


@app.post("/api/reset")
def api_reset():
    with db() as c:
        c.execute("DELETE FROM appliances")
        c.execute("DELETE FROM usage_log")
    return jsonify(ok=True)


init_db()
if __name__ == "__main__":
    app.run(debug=True, port=5000)
