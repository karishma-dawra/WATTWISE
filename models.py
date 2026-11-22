"""Data access, validation and demo seeding for WATTWISE (single-household MVP)."""
import json
import os
import random
from datetime import date, datetime, timedelta

from database import get_db
from services import region_service

DEFAULT_HID = int(os.environ.get("WATTWISE_USER", "1"))  # used only outside a web request (scripts, tests)


def hid():
    """The household id of the logged-in user. Inside a request it never falls back to another account."""
    try:
        from flask import g, has_request_context
    except ImportError:  # pragma: no cover
        return DEFAULT_HID
    if has_request_context():
        uid = getattr(g, "user_id", None)
        if uid is None:
            raise PermissionError("Not logged in")
        return uid
    return DEFAULT_HID

PRESETS = [
    {"name": "Air Conditioner", "category": "Cooling", "wattage": 1500, "priority": "flexible", "weather_type": "cooling"},
    {"name": "Ceiling Fan", "category": "Cooling", "wattage": 75, "priority": "essential", "weather_type": "none"},
    {"name": "LED Bulb", "category": "Lighting", "wattage": 9, "priority": "essential", "weather_type": "none"},
    {"name": "Refrigerator", "category": "Kitchen", "wattage": 200, "priority": "essential", "weather_type": "none"},
    {"name": "Television", "category": "Entertainment", "wattage": 100, "priority": "flexible", "weather_type": "none"},
    {"name": "Washing Machine", "category": "Laundry", "wattage": 500, "priority": "flexible", "weather_type": "none"},
    {"name": "Geyser", "category": "Water Heating", "wattage": 2000, "priority": "flexible", "weather_type": "heating"},
    {"name": "Cooler", "category": "Cooling", "wattage": 200, "priority": "flexible", "weather_type": "cooling"},
    {"name": "Room Heater", "category": "Heating", "wattage": 1500, "priority": "flexible", "weather_type": "heating"},
    {"name": "Water Pump", "category": "Utility", "wattage": 750, "priority": "essential", "weather_type": "none"},
    {"name": "Laptop", "category": "Electronics", "wattage": 65, "priority": "flexible", "weather_type": "none"},
    {"name": "Microwave", "category": "Kitchen", "wattage": 1200, "priority": "flexible", "weather_type": "none"},
    {"name": "Iron", "category": "Laundry", "wattage": 1000, "priority": "flexible", "weather_type": "none"},
    {"name": "Other", "category": "Other", "wattage": 100, "priority": "flexible", "weather_type": "none"},
]

CATEGORIES = ["Cooling", "Heating", "Water Heating", "Lighting", "Kitchen", "Entertainment",
              "Laundry", "Utility", "Electronics", "Other"]


class ValidationError(ValueError):
    pass


def today():
    """Today's date (overridable with WATTWISE_TODAY=YYYY-MM-DD for testing)."""
    o = os.environ.get("WATTWISE_TODAY")
    return date.fromisoformat(o) if o else date.today()


def _row(r):
    return dict(r) if r else None


# ---------------------------------------------------------------- household
def get_household():
    conn = get_db()
    r = conn.execute("SELECT * FROM household WHERE id=?", (hid(),)).fetchone()
    conn.close()
    return _row(r)


def _num(data, key, lo=None, hi=None, required=True, default=None):
    v = data.get(key)
    if v in (None, ""):
        if required:
            raise ValidationError(f"'{key}' is required")
        return default
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise ValidationError(f"'{key}' must be a number")
    if v != v or v in (float("inf"), float("-inf")):
        raise ValidationError(f"'{key}' must be a finite number")
    if lo is not None and v < lo:
        raise ValidationError(f"'{key}' must be at least {lo}")
    if hi is not None and v > hi:
        raise ValidationError(f"'{key}' must be at most {hi}")
    return v


def _date(data, key):
    try:
        return date.fromisoformat(str(data.get(key)))
    except ValueError:
        raise ValidationError(f"'{key}' must be a valid date (YYYY-MM-DD)")


def validate_household(data, current):
    out = {}
    out["name"] = str(data.get("name", current["name"]) or "My Household").strip()[:80]
    out["location_name"] = str(data.get("location_name", current["location_name"]) or "").strip()[:120]
    for k in ("latitude", "longitude"):
        v = data.get(k, current[k])
        out[k] = None if v in (None, "") else _num({k: v}, k, -180 if k == "longitude" else -90, 180 if k == "longitude" else 90)
    merged = {**current, **data}
    # monthly budget, units and meter readings are managed in the meter section (save_meter_details)
    cs, ce = _date(merged, "cycle_start"), _date(merged, "cycle_end")
    if ce <= cs:
        raise ValidationError("Billing cycle end must be after the start")
    if (ce - cs).days > 70:
        raise ValidationError("Billing cycle cannot be longer than 70 days")
    out["cycle_start"], out["cycle_end"] = cs.isoformat(), ce.isoformat()
    out["tariff"] = _num(merged, "tariff", 0, 10000)
    out["duty_percent"] = _num(merged, "duty_percent", 0, 100, required=False, default=0)
    out["free_units"] = _num(merged, "free_units", 0, 100000, required=False, default=0)
    out["currency_symbol"] = str(merged.get("currency_symbol", "₹") or "").strip()[:6]
    out["country_code"] = str(merged.get("country_code") or "").strip().upper()[:2]
    out["region_name"] = str(merged.get("region_name") or "").strip()[:80]
    if "tariff_slabs_text" in data:
        try:
            slabs = region_service.parse_slabs(data["tariff_slabs_text"])
        except ValueError as e:
            raise ValidationError(f"'tariff_slabs' {e}")
    else:
        slabs = json.loads(current["tariff_slabs"]) if current.get("tariff_slabs") else []
    out["tariff_slabs"] = json.dumps(slabs) if slabs else ""
    out["tariff_source"] = current.get("tariff_source", "manual")
    out["fixed_charges"] = _num(merged, "fixed_charges", 0, 1000000)
    out["other_charges"] = _num(merged, "other_charges", 0, 1000000)
    out["cooling_ref_temp"] = _num(merged, "cooling_ref_temp", 10, 50)
    out["cooling_sensitivity"] = _num(merged, "cooling_sensitivity", 0, 0.3)
    out["cooling_on_temp"] = _num(merged, "cooling_on_temp", 5, 45)
    if out["cooling_on_temp"] >= out["cooling_ref_temp"]:
        raise ValidationError("'cooling_on_temp' must be below the cooling reference temperature")
    out["heating_ref_temp"] = _num(merged, "heating_ref_temp", -10, 30)
    out["heating_off_temp"] = _num(merged, "heating_off_temp", -5, 35)
    if out["heating_off_temp"] <= out["heating_ref_temp"]:
        raise ValidationError("'heating_off_temp' must be above the heating reference temperature")
    out["heating_sensitivity"] = _num(merged, "heating_sensitivity", 0, 0.5)
    out["comfort_bias"] = int(_num(merged, "comfort_bias", 0, 100))
    return out


def update_household(data):
    cur = get_household()
    v = validate_household(data, cur)
    if any(v[k] != cur[k] for k in ("tariff", "fixed_charges", "other_charges", "duty_percent", "free_units", "tariff_slabs")):
        v["tariff_source"] = "manual"  # the user edited pricing, so region detection must not silently overwrite it
        v["tariff_unverified"] = ""    # their own numbers are not "not verified yet"
        v["free_units_mode"] = "deduct"  # a free-units figure typed by hand is a plain deduction
        v["subsidy_scheme"] = ""
    conn = get_db()
    sets = ", ".join(f"{k}=?" for k in v)
    conn.execute(f"UPDATE household SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?", (*v.values(), hid()))
    conn.commit()
    conn.close()
    rebuild_daily()
    return get_household()


def set_location(lat, lon, label, country_code, region_name):
    conn = get_db()
    conn.execute("UPDATE household SET latitude=?, longitude=?, location_name=?, country_code=?, region_name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                 (lat, lon, label, country_code or "", region_name or "", hid()))
    conn.commit()
    conn.close()


def apply_region_profile(profile):
    """Write a region pricing profile into the household (currency, tariff, slabs, fixed charge, duty)."""
    conn = get_db()
    if profile.get("can_apply") is False:
        # the region's rates are not verified yet: take the currency only and leave the household's own tariff alone
        conn.execute("UPDATE household SET currency_symbol=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (profile.get("currency_symbol") or "", hid()))
        conn.commit()
        conn.close()
        return
    cur = conn.execute("SELECT tariff, fixed_charges, duty_percent, free_units, subsidy_scheme FROM household WHERE id=?", (hid(),)).fetchone()
    unverified = list(profile.get("unverified") or []) + (["free_units_rule"] if cur["subsidy_scheme"] else [])  # an applied subsidy stays flagged
    # a value the profile does not know (None) keeps what the household has; it is never replaced by a guess
    pick = lambda key, old: profile[key] if profile.get(key) is not None else old
    conn.execute("UPDATE household SET tariff=?, fixed_charges=?, duty_percent=?, free_units=?, tariff_slabs=?, currency_symbol=?, "
                 "tariff_source='region', tariff_unverified=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                 (pick("tariff", cur["tariff"]), pick("fixed_charges", cur["fixed_charges"]), pick("duty_percent", cur["duty_percent"]),
                  pick("free_units", cur["free_units"]), json.dumps(profile["slabs"]) if profile.get("slabs") else "",
                  profile.get("currency_symbol") or "", json.dumps(unverified), hid()))
    conn.commit()
    conn.close()


def apply_subsidy(scheme_name, free_units):
    """Apply a free-units scheme: the first `free_units` units each month are covered in place (units above them keep
    their slab rate). The scheme is not verified, so the household is flagged until the user removes it or edits
    pricing by hand."""
    conn = get_db()
    cur = conn.execute("SELECT tariff_unverified FROM household WHERE id=?", (hid(),)).fetchone()
    flags = [f for f in (json.loads(cur["tariff_unverified"]) if cur["tariff_unverified"] else []) if f != "free_units_rule"]
    conn.execute("UPDATE household SET free_units=?, free_units_mode='cover', subsidy_scheme=?, tariff_unverified=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                 (free_units, scheme_name, json.dumps(flags + ["free_units_rule"]), hid()))
    conn.commit()
    conn.close()


def remove_subsidy():
    """Undo apply_subsidy: no free units, back to the plain deduction mode."""
    conn = get_db()
    cur = conn.execute("SELECT tariff_unverified FROM household WHERE id=?", (hid(),)).fetchone()
    flags = [f for f in (json.loads(cur["tariff_unverified"]) if cur["tariff_unverified"] else []) if f != "free_units_rule"]
    conn.execute("UPDATE household SET free_units=0, free_units_mode='deduct', subsidy_scheme='', tariff_unverified=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                 (json.dumps(flags) if flags else "", hid()))
    conn.commit()
    conn.close()


def set_units_consumed(units):
    conn = get_db()
    conn.execute("UPDATE household SET units_consumed=? WHERE id=?", (units, hid()))
    conn.commit()
    conn.close()


# --------------------------------------------------------------- appliances
def _with_need(r):
    a = dict(r)
    if a.get("need_hours") is None:
        a["need_hours"] = a["daily_hours"]
    a["usage_hours_entered"] = a.get("user_hours") is not None
    return a


def list_appliances():
    conn = get_db()
    rows = conn.execute("SELECT * FROM appliances WHERE household_id=? ORDER BY id", (hid(),)).fetchall()
    conn.close()
    return [_with_need(r) for r in rows]


def get_appliance(aid):
    conn = get_db()
    r = conn.execute("SELECT * FROM appliances WHERE id=? AND household_id=?", (aid, hid())).fetchone()
    conn.close()
    return _with_need(r) if r else None


# Estimated typical daily use by appliance type. Not entered by the user; shown in the UI as an estimate.
TYPICAL_HOURS = {"Air Conditioner": 6, "Ceiling Fan": 10, "LED Bulb": 5, "Refrigerator": 8, "Television": 4, "Washing Machine": 1,
                 "Geyser": 0.5, "Cooler": 8, "Room Heater": 3, "Water Pump": 0.5, "Laptop": 6, "Microwave": 0.25, "Iron": 0.5, "Other": 1}
CATEGORY_TYPICAL_HOURS = {"Cooling": 6, "Heating": 3, "Water Heating": 0.5, "Lighting": 5, "Kitchen": 8, "Entertainment": 4,
                          "Laundry": 1, "Utility": 0.5, "Electronics": 4, "Other": 1}


def typical_hours(name, category):
    return TYPICAL_HOURS.get(name, CATEGORY_TYPICAL_HOURS.get(category, 1.0))


def validate_usage_hours(value):
    """Optional daily usage hours. Blank -> None (use the estimate). Otherwise 0.25 to 24 hours."""
    if value in (None, ""):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise ValidationError("Usage hours must be a number")
    if not 0.25 <= v <= 24:
        raise ValidationError("Usage hours must be between 0.25 and 24 per day (leave blank to use an estimate)")
    return round(v, 2)


def validate_appliance(data, current=None):
    m = {**(current or {}), **data}
    name = str(m.get("name") or "").strip()
    if not name:
        raise ValidationError("Appliance name is required")
    if len(name) > 60:
        raise ValidationError("Appliance name is too long (max 60 characters)")
    qty = _num(m, "quantity", 1, 500)
    if qty != int(qty):
        raise ValidationError("'quantity' must be a whole number")
    priority = m.get("priority", "flexible")
    wtype = m.get("weather_type", "none")
    if priority not in ("essential", "flexible"):
        raise ValidationError("priority must be 'essential' or 'flexible'")
    if wtype not in ("cooling", "heating", "none"):
        raise ValidationError("weather_type must be cooling, heating or none")
    return {
        "name": name,
        "category": str(m.get("category") or "Other")[:40],
        "quantity": int(qty),
        "wattage": _num(m, "wattage", 0.1, 50000),
        "priority": priority,
        "weather_type": wtype,
    }


def create_appliance(data):
    v = validate_appliance(data)
    user = validate_usage_hours(data.get("usage_hours"))
    hrs = user if user is not None else typical_hours(v["name"], v["category"])
    conn = get_db()
    cur = conn.execute(
        "INSERT INTO appliances (household_id,name,category,quantity,wattage,daily_hours,priority,weather_type,need_hours,user_hours) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (hid(), v["name"], v["category"], v["quantity"], v["wattage"], hrs, v["priority"], v["weather_type"], hrs, user))
    conn.commit()
    aid = cur.lastrowid
    conn.close()
    return get_appliance(aid)


def update_appliance(aid, data):
    cur = get_appliance(aid)
    if not cur:
        return None
    v = validate_appliance(data, cur)
    user = cur.get("user_hours")
    hrs_daily, hrs_need = cur["daily_hours"], cur["need_hours"]
    if "usage_hours" in data:  # the user changed (or cleared) their usage hours
        user = validate_usage_hours(data.get("usage_hours"))
        if user is not None:
            hrs_daily = hrs_need = user
        else:
            hrs_daily = hrs_need = typical_hours(v["name"], v["category"])
    conn = get_db()
    conn.execute("UPDATE appliances SET name=?,category=?,quantity=?,wattage=?,priority=?,weather_type=?,user_hours=?,daily_hours=?,need_hours=? WHERE id=?",
                 (v["name"], v["category"], v["quantity"], v["wattage"], v["priority"], v["weather_type"], user, hrs_daily, hrs_need, aid))
    conn.commit()
    conn.close()
    return get_appliance(aid)


def apply_schedule(hours_by_id):
    """Write recommended hours into the current schedule (daily_hours). Typical full-use hours are untouched."""
    conn = get_db()
    for aid, hrs in hours_by_id.items():
        conn.execute("UPDATE appliances SET daily_hours=? WHERE id=? AND household_id=?", (round(float(hrs), 2), int(aid), hid()))
    conn.commit()
    conn.close()


def delete_appliance(aid):
    conn = get_db()
    n = conn.execute("DELETE FROM appliances WHERE id=? AND household_id=?", (aid, hid())).rowcount
    conn.commit()
    conn.close()
    return n > 0


def _leave_demo_label():
    pass  # demo flag stays until the user edits the household or clears demo data


# ----------------------------------------------------------------- readings
def list_readings():
    conn = get_db()
    rows = conn.execute("SELECT * FROM readings WHERE household_id=? ORDER BY reading_date", (hid(),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def latest_reading():
    r = list_readings()
    return r[-1] if r else None


def add_reading(day, units=None, meter=None, source="manual", _from_household=False):
    h = get_household()
    try:
        d = date.fromisoformat(str(day))
    except ValueError:
        raise ValidationError("Reading date must be a valid date")
    cs, ce = date.fromisoformat(h["cycle_start"]), date.fromisoformat(h["cycle_end"])
    if d < cs or d > ce:
        raise ValidationError("Reading date must fall inside the billing cycle")
    if d > today():
        raise ValidationError("Reading date cannot be in the future")
    meter_v = _num({"m": meter}, "m", 0, required=False) if meter not in (None, "") else None
    if units in (None, ""):
        if meter_v is None:
            raise ValidationError("Provide either units consumed so far or a meter reading")
        base = h["meter_base"] if h.get("meter_base") is not None else h["start_meter_reading"]
        if base is None:
            raise ValidationError("Enter your previous bill meter reading in the Meter Details section first")
        units_v = (h.get("units_offset") or 0) + meter_v - base
        if units_v < 0:
            raise ValidationError("Meter reading is lower than the meter base. Use 'My meter was reset' in Meter Details if the meter was reset.")
    else:
        units_v = _num({"u": units}, "u", 0, 1000000)
    prev = [r for r in list_readings() if r["reading_date"] < d.isoformat()]
    if prev and units_v < prev[-1]["units_consumed"] - 1e-9:
        raise ValidationError("Cumulative units cannot be lower than an earlier reading")
    conn = get_db()
    conn.execute(
        "INSERT INTO readings (household_id,reading_date,units_consumed,meter_reading,source) VALUES (?,?,?,?,?) "
        "ON CONFLICT(household_id,reading_date) DO UPDATE SET units_consumed=excluded.units_consumed, "
        "meter_reading=excluded.meter_reading, source=excluded.source",
        (hid(), d.isoformat(), units_v, meter_v, source))
    conn.commit()
    conn.close()
    lr = latest_reading()
    set_units_consumed(lr["units_consumed"])
    rebuild_daily()
    return {"reading_date": d.isoformat(), "units_consumed": units_v}


def meter_state():
    h = get_household()
    latest_meter = next((r["meter_reading"] for r in reversed(list_readings()) if r["meter_reading"] is not None), None)
    budget, units = h["monthly_budget"], h["units_consumed"]
    util = 100 * units / budget if budget else 0.0
    warnings = []
    if units > budget:
        warnings.append(f"Consumption is {units - budget:.1f} units over your monthly budget.")
    elif util >= 80:
        warnings.append(f"{util:.0f}% of your budget is used.")
    return {"previous_reading": h["start_meter_reading"], "today_reading": latest_meter,
            "monthly_budget": budget, "units_consumed": round(units, 2), "remaining_units": round(budget - units, 2),
            "utilization_pct": round(util, 1), "reset_applied": bool((h.get("units_offset") or 0) > 0),
            "warnings": warnings, "cycle_start": h["cycle_start"], "cycle_end": h["cycle_end"]}


def save_meter_details(previous, today_reading, budget, meter_reset=False, reading_before_reset=None):
    """Previous bill meter reading + today's meter reading + monthly budget -> units consumed, remaining, utilisation."""
    prev = _num({"p": previous}, "p", 0, 1e9)
    try:
        bud = float(budget)
    except (TypeError, ValueError):
        raise ValidationError("Monthly electricity budget must be a number")
    if not bud > 0:
        raise ValidationError("Monthly electricity budget must be greater than zero")
    if bud > 1000000:
        raise ValidationError("Monthly electricity budget is too large")
    tr = prev if today_reading in (None, "") else _num({"t": today_reading}, "t", 0, 1e9)
    t = today()
    h = get_household()
    cs, ce = date.fromisoformat(h["cycle_start"]), date.fromisoformat(h["cycle_end"])
    if t < cs or t > ce:
        raise ValidationError("Today is outside your billing cycle. Update the cycle dates in Setup first.")
    if meter_reset:
        before = _num({"b": reading_before_reset}, "b", 0, 1e9) if reading_before_reset not in (None, "") else None
        if before is None:
            raise ValidationError("Enter the meter reading just before the reset")
        if before < prev:
            raise ValidationError("The reading before the reset cannot be lower than the previous bill reading")
        offset, base = before - prev, 0.0
        units = offset + tr
    else:
        if tr < prev:
            raise ValidationError(f"Today's reading ({tr:g}) is lower than the previous bill reading ({prev:g}). "
                                  "If your meter was reset or rolled over, tick 'My meter was reset' and enter the reading before the reset.")
        offset, base = 0.0, prev
        units = tr - prev
    conn = get_db()
    conn.execute("UPDATE household SET start_meter_reading=?, meter_base=?, units_offset=?, monthly_budget=?, units_consumed=?, "
                 "updated_at=CURRENT_TIMESTAMP WHERE id=?", (prev, base, offset, bud, units, hid()))
    # readings after today or above the new total no longer hold; today's reading is replaced
    conn.execute("DELETE FROM readings WHERE household_id=? AND (reading_date>=? OR units_consumed>?)", (hid(), t.isoformat(), units))
    conn.execute("INSERT INTO readings (household_id,reading_date,units_consumed,meter_reading,source) VALUES (?,?,?,?,'manual')",
                 (hid(), t.isoformat(), units, tr))
    conn.commit()
    conn.close()
    rebuild_daily()
    return meter_state()


def delete_reading(rid):
    conn = get_db()
    n = conn.execute("DELETE FROM readings WHERE id=? AND household_id=?", (rid, hid())).rowcount
    conn.commit()
    conn.close()
    lr = latest_reading()
    set_units_consumed(lr["units_consumed"] if lr else 0)
    rebuild_daily()
    return n > 0


def rebuild_daily():
    """Derive daily consumption from cumulative readings (spread evenly between readings)."""
    h = get_household()
    readings = list_readings()
    src = "demo" if readings and all(r["source"] == "demo" for r in readings) else "readings"
    conn = get_db()
    conn.execute("DELETE FROM daily_consumption WHERE household_id=?", (hid(),))
    prev_d = date.fromisoformat(h["cycle_start"]) - timedelta(days=1)
    prev_u = 0.0
    for r in readings:
        d = date.fromisoformat(r["reading_date"])
        gap = (d - prev_d).days
        if gap > 0:
            per = max(0.0, (r["units_consumed"] - prev_u) / gap)
            for i in range(1, gap + 1):
                conn.execute("INSERT INTO daily_consumption (household_id,day,units,source) VALUES (?,?,?,?) "
                             "ON CONFLICT(household_id,day) DO UPDATE SET units=excluded.units, source=excluded.source",
                             (hid(), (prev_d + timedelta(days=i)).isoformat(), round(per, 3), src))
        prev_d, prev_u = d, r["units_consumed"]
    conn.commit()
    conn.close()


def list_daily():
    conn = get_db()
    rows = conn.execute("SELECT * FROM daily_consumption WHERE household_id=? ORDER BY day", (hid(),)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------ weather
def save_climate(conn, rows, now):
    for c in rows:
        conn.execute("INSERT INTO weather (household_id,day,kind,temp_max,temp_min,condition,fetched_at,rain_mm,apparent_max,apparent_min,provider) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (hid(), c["day"], "climate", c["temp_max"], c["temp_min"], c["condition"], now, c.get("rain_mm"),
                      c.get("apparent_max"), c.get("apparent_min"), c.get("provider")))


def load_climate():
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM weather WHERE household_id=? AND kind='climate' ORDER BY day", (hid(),)).fetchall()]
    conn.close()
    return rows


def save_weather(current, forecast, climate=None, hourly=None):
    """Replace the cached weather for this household: current conditions, daily forecast (with sunrise/sunset),
    climate averages and the hourly forecast (Module 1)."""
    conn = get_db()
    now = datetime.now().isoformat(timespec="seconds")
    conn.execute("DELETE FROM weather WHERE household_id=?", (hid(),))
    conn.execute("DELETE FROM weather_hourly WHERE household_id=?", (hid(),))
    if current:
        conn.execute("INSERT INTO weather (household_id,day,kind,current_temp,weather_code,condition,fetched_at) VALUES (?,?,?,?,?,?,?)",
                     (hid(), current["day"], "current", current["temp"], current["code"], current["condition"], now))
    for f in forecast:
        conn.execute("INSERT INTO weather (household_id,day,kind,temp_max,temp_min,weather_code,condition,fetched_at,humidity,rain_chance,provider,rain_mm,apparent_max,apparent_min,sunrise,sunset) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (hid(), f["day"], "forecast", f["temp_max"], f["temp_min"], f["code"], f["condition"], now,
                      f.get("humidity"), f.get("rain_chance"), f.get("provider"),
                      f.get("rain_mm"), f.get("apparent_max"), f.get("apparent_min"), f.get("sunrise"), f.get("sunset")))
    for r in hourly or []:
        conn.execute("INSERT INTO weather_hourly (household_id,day,hour,temp,apparent,humidity,cloud_cover,shortwave,precipitation,fetched_at) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?)",
                     (hid(), r["day"], r["hour"], r.get("temp"), r.get("apparent"), r.get("humidity"), r.get("cloud_cover"),
                      r.get("shortwave"), r.get("precipitation"), now))
    save_climate(conn, climate or [], now)
    conn.commit()
    conn.close()


def load_weather():
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM weather WHERE household_id=? ORDER BY day", (hid(),)).fetchall()]
    conn.close()
    cur = next((r for r in rows if r["kind"] == "current"), None)
    fc = [r for r in rows if r["kind"] == "forecast"]
    return cur, fc


def load_hourly(day):
    """Cached hourly forecast rows for one day (empty list if not cached)."""
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM weather_hourly WHERE household_id=? AND day=? ORDER BY hour",
                                          (hid(), day)).fetchall()]
    conn.close()
    return rows


def load_hourly_all():
    """All cached hourly forecast rows, as {day: [rows ordered by hour]}."""
    conn = get_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM weather_hourly WHERE household_id=? ORDER BY day, hour", (hid(),)).fetchall()]
    conn.close()
    out = {}
    for r in rows:
        out.setdefault(r["day"], []).append(r)
    return out


def save_hourly_plan(day, rows):
    """Replace the stored 24-hour comfort plan for one day. rows: dicts from hourly_comfort_table()."""
    conn = get_db()
    conn.execute("DELETE FROM hourly_plan WHERE household_id=? AND day=?", (hid(), day))
    for r in rows:
        conn.execute("INSERT INTO hourly_plan (household_id,day,hour,temp,appliance_id,appliance,on_off,intensity,kwh) "
                     "VALUES (?,?,?,?,?,?,?,?,?)",
                     (hid(), day, r["hour"], r.get("temp"), r.get("appliance_id"), r["appliance"],
                      1 if r["on"] else 0, r["intensity"], r["kwh"]))
    conn.commit()
    conn.close()


def save_daily_plan(rows):
    """Replace the stored daily plan. rows: dicts with day, predicted_kwh, allocation_kwh, optimized_kwh, feasible."""
    conn = get_db()
    for r in rows:
        conn.execute("INSERT INTO daily_plan (household_id,day,predicted_kwh,allocation_kwh,optimized_kwh,feasible) "
                     "VALUES (?,?,?,?,?,?) ON CONFLICT(household_id,day) DO UPDATE SET predicted_kwh=excluded.predicted_kwh, "
                     "allocation_kwh=excluded.allocation_kwh, optimized_kwh=excluded.optimized_kwh, feasible=excluded.feasible",
                     (hid(), r["day"], r["predicted_kwh"], r["allocation_kwh"], r.get("optimized_kwh"),
                      1 if r.get("feasible", True) else 0))
    conn.commit()
    conn.close()


# ----------------------------------------------------- recommendations/alerts
def save_recommendation(target_date, payload):
    conn = get_db()
    conn.execute("DELETE FROM recommendations WHERE household_id=? AND target_date=?", (hid(), target_date))
    conn.execute("INSERT INTO recommendations (household_id,target_date,payload) VALUES (?,?,?)",
                 (hid(), target_date, json.dumps(payload)))
    conn.execute("DELETE FROM recommendations WHERE household_id=? AND id NOT IN "
                 "(SELECT id FROM recommendations WHERE household_id=? ORDER BY id DESC LIMIT 30)", (hid(), hid()))
    conn.commit()
    conn.close()


def sync_alerts(alerts):
    """Replace stale undismissed alerts with the currently computed set."""
    conn = get_db()
    fps = [a["fingerprint"] for a in alerts]
    existing = {r["fingerprint"]: r for r in conn.execute("SELECT * FROM alerts WHERE household_id=?", (hid(),))}
    for fp, r in existing.items():
        if fp not in fps and not r["dismissed"]:
            conn.execute("DELETE FROM alerts WHERE id=?", (r["id"],))
    for a in alerts:
        if a["fingerprint"] in existing:
            conn.execute("UPDATE alerts SET severity=?, title=?, message=? WHERE household_id=? AND fingerprint=?",
                         (a["severity"], a["title"], a["message"], hid(), a["fingerprint"]))
        else:
            conn.execute("INSERT INTO alerts (household_id,code,fingerprint,severity,title,message) VALUES (?,?,?,?,?,?)",
                         (hid(), a["code"], a["fingerprint"], a["severity"], a["title"], a["message"]))
    conn.commit()
    conn.close()


def list_alerts(include_dismissed=False):
    conn = get_db()
    q = "SELECT * FROM alerts WHERE household_id=?" + ("" if include_dismissed else " AND dismissed=0")
    rows = [dict(r) for r in conn.execute(q, (hid(),))]
    conn.close()
    order = {"critical": 0, "warning": 1, "info": 2}
    rows.sort(key=lambda r: (order.get(r["severity"], 3), -r["id"]))
    return rows


def dismiss_alert(aid):
    conn = get_db()
    n = conn.execute("UPDATE alerts SET dismissed=1 WHERE id=? AND household_id=?", (aid, hid())).rowcount
    conn.commit()
    conn.close()
    return n > 0


# --------------------------------------------------------------------- demo
def _wipe(conn):
    for t in ("alerts", "recommendations", "weather", "daily_consumption", "readings", "appliances"):
        conn.execute(f"DELETE FROM {t} WHERE household_id=?", (hid(),))
    conn.execute("DELETE FROM household WHERE id=?", (hid(),))


def _insert_household(conn, **kw):
    cols = ["id"] + list(kw)
    conn.execute(f"INSERT INTO household ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", (hid(), *kw.values()))


def ensure_household():
    if get_household() is None:
        reset_demo()


PROFILE_CHOICES = {
    "meter_type": ("Smart", "Digital", "Traditional"),
    "connection_type": ("Domestic", "Commercial"),
    "home_type": ("Apartment", "Independent House", "Rented House"),
}


def update_profile_extras(data):
    """Electricity-meter and household profile fields (the budget, tariff and cycle are saved by their own forms)."""
    cur = get_household()
    out = {}
    out["provider"] = str(data.get("provider", cur.get("provider")) or "").strip()[:80]
    out["consumer_number"] = str(data.get("consumer_number", cur.get("consumer_number")) or "").strip()[:40]
    out["bill_date"] = str(data.get("bill_date", cur.get("bill_date")) or "").strip()[:20]
    for key, choices in PROFILE_CHOICES.items():
        field = {"meter_type": "meter_type", "connection_type": "connection_type", "home_type": "home_type"}[key]
        v = data.get(field, cur.get(field)) or choices[0]
        if v not in choices:
            raise ValidationError(f"'{field}' must be one of: {', '.join(choices)}")
        out[field] = v
    sl = data.get("sanctioned_load", cur.get("sanctioned_load"))
    out["sanctioned_load"] = None if sl in (None, "") else _num({"s": sl}, "s", 0, 1000000)
    for key, lo, hi in (("rooms", 1, 50), ("floors", 1, 30), ("family_size", 1, 30)):
        v = data.get(key, cur.get(key))
        if v in (None, ""):
            out[key] = None
        else:
            n = _num({"v": v}, "v", lo, hi)
            if n != int(n):
                raise ValidationError(f"'{key}' must be a whole number")
            out[key] = int(n)
    sets = ", ".join(f"{k}=?" for k in out)
    conn = get_db()
    conn.execute(f"UPDATE household SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?", (*out.values(), hid()))
    conn.commit()
    conn.close()
    return get_household()


def create_household_row(uid, city=""):
    """A new, empty household owned by user `uid` (same id as the user)."""
    t = today()
    conn = get_db()
    conn.execute("DELETE FROM household WHERE id=?", (uid,))
    conn.execute("INSERT INTO household (id, name, location_name, monthly_budget, units_consumed, cycle_start, cycle_end, is_demo, tariff_source) "
                 "VALUES (?,?,?,?,?,?,?,0,'default')",
                 (uid, "My Household", city or "", 300, 0,
                  t.replace(day=1).isoformat(),
                  ((t.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)).isoformat()))
    conn.commit()
    conn.close()


def clear_household():
    """Start fresh: empty household with no appliances and no readings (not demo)."""
    t = today()
    conn = get_db()
    _wipe(conn)
    _insert_household(conn, name="My Household", location_name="", monthly_budget=300, units_consumed=0,
                      cycle_start=t.replace(day=1).isoformat(),
                      cycle_end=((t.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)).isoformat(),
                      is_demo=0, tariff_source="default")
    conn.commit()
    conn.close()


def reset_demo():
    """Reset to the labelled demo household (Ludhiana). Readings are simulated sample data."""
    t = today()
    start = t - timedelta(days=13)  # today is day 14 of a 30-day cycle
    end = start + timedelta(days=29)
    conn = get_db()
    _wipe(conn)
    _insert_household(conn, name="Sharma Family (Demo)", location_name="Ludhiana, Punjab, India", country_code="IN", region_name="Punjab",
                      latitude=30.91204, longitude=75.85379, monthly_budget=300, units_consumed=0,
                      cycle_start=start.isoformat(), cycle_end=end.isoformat(), tariff=6.5, fixed_charges=120,
                      other_charges=0, is_demo=1, tariff_source="default")
    demo_apps = [
        ("Air Conditioner", "Cooling", 1, 1500, 6, "flexible", "cooling"),
        ("Ceiling Fan", "Cooling", 2, 75, 10, "essential", "none"),
        ("Refrigerator", "Kitchen", 1, 200, 8, "essential", "none"),
        ("LED Bulb", "Lighting", 8, 9, 5, "essential", "none"),
        ("Geyser", "Water Heating", 1, 2000, 0.5, "flexible", "heating"),
        ("Television", "Entertainment", 1, 100, 4, "flexible", "none"),
    ]
    for a in demo_apps:
        conn.execute("INSERT INTO appliances (household_id,name,category,quantity,wattage,daily_hours,priority,weather_type,need_hours) VALUES (?,?,?,?,?,?,?,?,?)",
                     (hid(), *a, a[4]))
    # Simulated sample readings (clearly labelled demo data, deterministic)
    rnd = random.Random(42)
    cum = 0.0
    for i in range(14):
        d = start + timedelta(days=i)
        cum += 11.4 + rnd.uniform(-1.8, 1.8) + (0.8 if d.weekday() >= 5 else 0)
        conn.execute("INSERT INTO readings (household_id,reading_date,units_consumed,source) VALUES (?,?,?,'demo')",
                     (hid(), d.isoformat(), round(cum, 2)))
    conn.execute("UPDATE household SET units_consumed=? WHERE id=?", (round(cum, 2), hid()))
    conn.commit()
    conn.close()
    rebuild_daily()
