"""Weather-to-appliance decision engine.

Reads weather_appliance_rules.json (hot-reloaded when the file changes) and turns one day's weather into recommended
operating hours for every registered appliance:

  * cooling kinds (AC, cooler, fan)  -> cooling_bands by apparent high temperature, adjusted by humidity (coolers)
                                         and rainfall (multipliers, never above 1.0)
  * heating kinds (heater, geyser)   -> heating_bands by daily low temperature, adjusted by rainfall multipliers
  * lighting, laundry                -> baseline hours adjusted by rainfall multipliers
  * everything else (fridge, TV ...) -> typical hours, no weather change

Each result also carries a floor (the minimum that is never cut for safety) and the reasons, so recommendations
can be explained. Nothing here is a claim of exact appliance demand: these are configurable starting rules.
"""
import json
import os
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
RULES_PATH = Path(os.environ.get("WATTWISE_RULES", BASE_DIR / "weather_appliance_rules.json"))
COOLING_KINDS = ("ac", "cooler", "fan")
HEATING_KINDS = ("heater", "geyser")
MULTIPLIER_KINDS = ("ac", "cooler", "fan", "geyser")   # rain must never raise these
HUMIDITY_KINDS = ("ac", "cooler")                      # humidity changes these appliances' effectiveness
RAIN_KINDS = ("ac", "cooler", "fan", "heater", "geyser", "lighting", "laundry")
PROB_KINDS = ("lighting", "laundry")                   # rain probability matters for lighting and laundry only
_cache = {"mtime": None, "rules": None, "error": None}


class RuleError(ValueError):
    pass


def _check_table(name, table):
    if "range" not in table or "bands" not in table or not table["bands"]:
        raise RuleError(f"{name}: needs 'range' and at least one band")
    lo, hi = table["range"]
    cur, seen = lo, set()
    for b in table["bands"]:
        for k in ("id", "min", "max"):
            if k not in b:
                raise RuleError(f"{name}: band {b.get('id', '?')} is missing '{k}'")
        if b["id"] in seen:
            raise RuleError(f"{name}: duplicate band id '{b['id']}'")
        seen.add(b["id"])
        if b["min"] >= b["max"]:
            raise RuleError(f"{name}/{b['id']}: min must be below max")
        if abs(b["min"] - cur) > 1e-9:
            raise RuleError(f"{name}: gap or overlap before '{b['id']}' (starts at {b['min']}, expected {cur})")
        cur = b["max"]
    if abs(cur - hi) > 1e-9:
        raise RuleError(f"{name}: bands end at {cur} but the range ends at {hi}")


def validate_rules(r):
    """Raise RuleError with a precise message if the rules file is incomplete or unsafe."""
    for key in ("limits", "cooling_bands", "heating_bands", "humidity_bands", "rainfall_bands", "rain_probability_bands", "baseline"):
        if key not in r:
            raise RuleError(f"missing section '{key}'")
    _check_table("cooling_bands", r["cooling_bands"])
    _check_table("heating_bands", r["heating_bands"])
    _check_table("humidity_bands", r["humidity_bands"])
    _check_table("rainfall_bands", r["rainfall_bands"])
    _check_table("rain_probability_bands", r["rain_probability_bands"])
    for b in r["cooling_bands"]["bands"]:
        for k in COOLING_KINDS:
            if k not in b.get("kinds", {}):
                raise RuleError(f"cooling_bands/{b['id']}: missing rule for '{k}'")
            s = b["kinds"][k]
            if not (0 <= s.get("fraction", 0) <= 2) or not (0 <= s.get("floor_fraction", 0) <= s.get("fraction", 0) + 1e-9):
                raise RuleError(f"cooling_bands/{b['id']}/{k}: fraction must be 0–2 and floor_fraction must not exceed it")
    for b in r["heating_bands"]["bands"]:
        for k in HEATING_KINDS:
            if k not in b.get("kinds", {}):
                raise RuleError(f"heating_bands/{b['id']}: missing rule for '{k}'")
            s = b["kinds"][k]
            if "hours" in s and not (0 <= s["hours"] <= 24):
                raise RuleError(f"heating_bands/{b['id']}/{k}: hours must be 0–24")
            if "hours" in s and s.get("floor_hours", 0) > s["hours"] + 1e-9:
                raise RuleError(f"heating_bands/{b['id']}/{k}: floor_hours cannot exceed hours")
    for b in r["humidity_bands"]["bands"]:
        if not (0 <= b["cooler_effectiveness"] <= 1):
            raise RuleError(f"humidity_bands/{b['id']}: cooler_effectiveness must be between 0 and 1")
    for b in r["rainfall_bands"]["bands"]:
        for k in MULTIPLIER_KINDS:
            v = b.get(f"{k}_multiplier", 1.0)
            if not (0 <= v <= 1.0):
                raise RuleError(f"rainfall_bands/{b['id']}: {k}_multiplier must be between 0 and 1 (rain never increases {k} hours)")
    duty = r.get("duty_cycle", {})
    for k, v in duty.items():
        if k != "_about" and not (0 < float(v) <= 1):
            raise RuleError(f"duty_cycle/{k}: must be above 0 and at most 1")
    for k, v in r["limits"].items():
        if k == "_about":
            continue
        if not (0 < float(v) <= 24):
            raise RuleError(f"limits/{k}: must be between 0 and 24 hours")
    return True


def load_rules(force=False):
    """Return the validated rules. Reloads when the file changes. If the file is broken, keeps the last good rules
    and records the error (see rules_status)."""
    try:
        mtime = RULES_PATH.stat().st_mtime
    except OSError:
        if _cache["rules"] is None:
            _cache["error"] = f"rules file not found: {RULES_PATH}"
            raise RuleError(_cache["error"])
        return _cache["rules"]
    if force or _cache["mtime"] != mtime:
        try:
            r = json.loads(RULES_PATH.read_text(encoding="utf-8"))
            validate_rules(r)
            _cache.update(mtime=mtime, rules=r, error=None)
        except (ValueError, KeyError, TypeError) as e:
            _cache["mtime"] = mtime
            _cache["error"] = f"invalid rules file: {e}"
            if _cache["rules"] is None:
                raise RuleError(_cache["error"])
    return _cache["rules"]


def rules_status():
    try:
        load_rules()
        return {"ok": _cache["error"] is None, "error": _cache["error"], "path": str(RULES_PATH)}
    except RuleError as e:
        return {"ok": False, "error": str(e), "path": str(RULES_PATH)}


def band_for(table, value):
    """(band, in_range). Values outside the table are clamped to the nearest band and flagged."""
    lo, hi = table["range"]
    in_range = lo <= value <= hi
    v = min(max(value, lo), hi)
    bands = table["bands"]
    for i, b in enumerate(bands):
        last = i == len(bands) - 1
        if b["min"] <= v < b["max"] or (last and v == b["max"]):
            return b, in_range
    return bands[-1], in_range


def kind_of(a):
    """Classify an appliance into a rule kind from its name, category and weather setting."""
    n = (a.get("name") or "").lower()
    cat = a.get("category") or ""
    wt = a.get("weather_type") or "none"
    if "air con" in n or n.strip() in ("ac", "a/c") or re.search(r"\bac\b", n):
        return "ac"
    if "cooler" in n:
        return "cooler"
    if "fan" in n:
        return "fan"
    if "geyser" in n or "water heater" in n or cat == "Water Heating":
        return "geyser"
    if "heater" in n or cat == "Heating":
        return "heater"
    if "fridge" in n or "refrigerator" in n:
        return "fridge"
    if "light" in n or "bulb" in n or cat == "Lighting":
        return "lighting"
    if "washing" in n or "laundry" in n or cat == "Laundry":
        return "laundry"
    if wt == "cooling" or cat == "Cooling":
        return "ac"
    if wt == "heating":
        return "heater"
    return "baseline"


def apparent_high(day, humidity_band=None):
    """Feels-like daily high. Uses the provider's apparent temperature when present (it already includes humidity),
    otherwise the plain high plus the humidity band's bonus on hot days."""
    if day.get("apparent_max") is not None:
        return float(day["apparent_max"])
    t = day.get("temp_max")
    if t is None:
        return None
    if humidity_band is not None and t >= 28:
        t += humidity_band["apparent_bonus_c"]
    return float(t)


def evaluate(a, day, base_hours, rules=None):
    """Recommended hours for one appliance on one day.
    day: dict with temp_max, temp_min, apparent_max, humidity, rain_mm, rain_chance (any may be None).
    base_hours: the household's typical hours for the appliance (used by fraction-based rules)."""
    rules = rules or load_rules()
    kind = kind_of(a)
    cap = float(rules["limits"].get(kind, rules["limits"]["default_max_hours"]))
    base_hours = float(base_hours)
    humidity = day.get("humidity")
    rain = day.get("rain_mm")
    prob = day.get("rain_chance")
    hb = band_for(rules["humidity_bands"], humidity)[0] if humidity is not None else None
    rb = band_for(rules["rainfall_bands"], rain)[0] if rain is not None else None
    pb = band_for(rules["rain_probability_bands"], prob)[0] if prob is not None else None
    warnings, notes = [], []
    band_label = hum_label = rain_label = prob_label = None
    # labels and notes only for the appliances each factor actually affects (keeps the reasons relevant)
    if hb and kind in HUMIDITY_KINDS:
        hum_label = hb["label"]
        notes += hb["notes"]
    if rb and kind in RAIN_KINDS and rb["id"] != "no_rain":
        rain_label = rb["label"]
        notes += rb["notes"]
    if pb and kind in PROB_KINDS:
        prob_label = pb["label"]
        notes += pb["notes"]  # advice only; probability never changes hours

    hours, floor, band_id = base_hours, 0.0, None
    if kind in COOLING_KINDS:
        apparent = apparent_high(day, hb)
        if apparent is None:
            return _result(kind, base_hours, 0.0, cap, None, None, hum_label, rain_label, prob_label, notes,
                           ["No temperature forecast for this day; typical hours used."])
        band, in_range = band_for(rules["cooling_bands"], apparent)
        if not in_range:
            warnings.append(f"Feels-like {apparent:.0f}°C is outside the rule range; the nearest band is used.")
        spec = band["kinds"][kind]
        band_id, band_label = band["id"], band["label"]
        hours = spec["hours"] if "hours" in spec else base_hours * spec.get("fraction", 0.0)
        floor = spec.get("floor_hours", 0.0) if "floor_hours" in spec else base_hours * spec.get("floor_fraction", 0.0)
        if kind == "cooler" and hb:
            hours *= hb["cooler_effectiveness"]
        if rb:
            hours *= rb.get(f"{kind}_multiplier", 1.0)
        notes.append(spec.get("note", ""))
        notes += band.get("notes", [])
        hours = max(hours, floor)  # a safety floor is never cut by humidity or rain
    elif kind in HEATING_KINDS:
        low = day.get("temp_min")
        if low is None:
            return _result(kind, base_hours, 0.0, cap, None, None, hum_label, rain_label, prob_label, notes,
                           ["No temperature forecast for this day; typical hours used."])
        band, in_range = band_for(rules["heating_bands"], low)
        if not in_range:
            warnings.append(f"Daily low {low:.0f}°C is outside the rule range; the nearest band is used.")
        spec = band["kinds"][kind]
        band_id, band_label = band["id"], band["label"]
        hours = spec["hours"] if "hours" in spec else base_hours * spec.get("fraction", 0.0)
        floor = spec.get("floor_hours", 0.0) if "floor_hours" in spec else base_hours * spec.get("floor_fraction", 0.0)
        if rb:
            hours *= rb.get(f"{kind}_multiplier", 1.0)
        notes.append(spec.get("note", ""))
        notes += band.get("notes", [])
        hours = max(hours, floor)
    else:
        hours = base_hours * rules["baseline"]["fraction"]
        if kind == "lighting" and rb:
            hours *= rb["lighting_multiplier"]
        if kind == "laundry" and rb:
            hours *= rb["laundry_multiplier"]
        if kind in ("lighting", "laundry") and rb and rb["id"] != "no_rain":
            notes.append(f"Adjusted for {rb['label'].lower()}.")

    return _result(kind, hours, floor, cap, band_id, band_label, hum_label, rain_label, prob_label,
                   [n for n in notes if n], warnings)


def _result(kind, hours, floor, cap, band_id, band_label, hum_label, rain_label, prob_label, notes, warnings):
    hours = min(max(hours, 0.0), cap)
    floor = min(max(floor, 0.0), cap, hours)
    parts = [p for p in (band_label, hum_label, rain_label) if p]
    summary = "; ".join(parts) if parts else "No weather rule applies"
    if notes:
        summary += ". " + " ".join(dict.fromkeys(notes))
    return {"kind": kind, "hours": round(hours, 3), "floor_hours": round(floor, 3), "cap_hours": cap,
            "band_id": band_id, "band_label": band_label, "humidity_label": hum_label,
            "rain_label": rain_label, "probability_label": prob_label,
            "notes": list(dict.fromkeys(notes)), "warnings": warnings, "summary": summary}


def coverage_report(rules=None):
    """Check that every temperature (-10…60°C), humidity (0–100%) and rainfall value maps to a band."""
    rules = rules or load_rules()
    missing = []
    for name, table, lo, hi, step in (("cooling", rules["cooling_bands"], -10, 60, 0.5),
                                      ("heating", rules["heating_bands"], -10, 60, 0.5),
                                      ("humidity", rules["humidity_bands"], 0, 100, 0.5),
                                      ("rainfall", rules["rainfall_bands"], 0, 500, 0.1)):
        v = lo
        while v <= hi + 1e-9:
            _, inr = band_for(table, v)
            if not inr:
                missing.append(f"{name}:{v}")
            v = round(v + step, 6)
    return {"complete": not missing, "missing": missing[:20]}
