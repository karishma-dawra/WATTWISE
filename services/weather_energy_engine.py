# services/weather_energy_engine.py - weather-based hourly split (Feature 1) and unit distribution (Feature 2).
import math
from services.consumption import effective_kw
from services.weather_rules_engine import apparent_high, band_for, kind_of, load_rules

WEATHER_KINDS = ("ac", "cooler", "fan", "geyser", "heater")   # weather-dependent
COOLING_KINDS = ("ac", "cooler", "fan")

# Time-of-day weight per hour 0..23 (how likely the appliance is used at that hour).
TIME_WEIGHT = {
    "ac":     [0.6] * 9 + [1.0] * 13 + [0.6] * 2,           # 9:00-21:59 high
    "cooler": [0.5] * 9 + [1.0] * 13 + [0.5] * 2,
    "fan":    [0.3] * 9 + [1.0] * 13 + [0.3] * 2,
    "geyser": [0] * 5 + [1] * 5 + [0] * 8 + [1] * 4 + [0] * 2,   # morning 5-9, evening 18-21
    "heater": [1] * 9 + [0.4] * 9 + [1] * 6,                     # night and morning high
}
# Feels-like steps (upper bound inclusive). Above the last bound the weight is the last value.
COOLING_STEPS = [(24.0, 0.0), (28.0, 0.3), (32.0, 0.6), (36.0, 0.85)]   # above 36 -> 1.0
HEATING_STEPS = [(10.0, 1.0), (15.0, 0.85), (20.0, 0.6), (24.0, 0.3)]   # above 24 -> 0.0
FAN_STEPS = [(22.0, 0.0), (25.0, 0.3), (28.0, 0.6), (32.0, 0.85)]       # above 32 -> 1.0
appliance_kind = kind_of  # kept for existing callers (recommendation_engine)
DEFAULT_DAY = {"temp_max": 30.0, "temp_min": 20.0, "humidity": 60.0, "apparent_max": None, "apparent_min": None}


def _step(value, steps, above):
    for bound, w in steps:
        if value <= bound:
            return w
    return above


def weather_weight(kind, feels_like, humidity=None):
    """0..1 how much weather pushes this appliance. Cooling kinds use the feels-like steps; heating kinds reverse them."""
    if kind == "fan":
        return _step(feels_like, FAN_STEPS, 1.0)
    if kind in COOLING_KINDS:  # ac and cooler share the cooling steps
        w = _step(feels_like, COOLING_STEPS, 1.0)
        if kind == "cooler" and humidity is not None and humidity > 40:  # humid air weakens evaporative coolers
            w *= max(0.2, 1.0 - 0.8 * (humidity - 40.0) / 60.0)
        return w
    return _step(feels_like, HEATING_STEPS, 0.0)  # geyser and heater: cold = high weight


def hourly_temperatures(tmin, tmax):
    """24 hourly temperatures: a daily curve with its minimum at 05:00 and its maximum at 17:00."""
    mean, amp = (tmax + tmin) / 2.0, (tmax - tmin) / 2.0
    return [mean - amp * math.cos(2 * math.pi * (h - 5) / 24.0) for h in range(24)]


def hourly_feels_like(day, kind):
    """Feels-like per hour. The cached hourly forecast (day["hourly_apparent"], 24 values) is used when present;
    otherwise a daily curve between the low and high, shifted by the provider's apparent-temperature offset."""
    real = day.get("hourly_apparent")
    if real and len(real) == 24 and all(v is not None for v in real):
        return [float(v) for v in real]
    temps = hourly_temperatures(day["temp_min"], day["temp_max"])
    if kind in COOLING_KINDS:
        if day.get("apparent_max") is not None and day.get("temp_max") is not None:
            off = day["apparent_max"] - day["temp_max"]
        else:
            off = max(0.0, (day.get("humidity") or 50.0) - 50.0) * 0.05 if day["temp_max"] >= 27 else 0.0
    else:
        off = (day["apparent_min"] - day["temp_min"]) if day.get("apparent_min") is not None else 0.0
    return [t + off for t in temps]


def split_hours(weights, target):
    """Spread `target` hours over 24 hours in proportion to `weights`, at most 1.0 hour per hour.
    Excess from capped hours goes to the next-highest-weight hours (water-filling). No division by zero."""
    hours = [0.0] * 24
    active = [h for h in range(24) if weights[h] > 0]
    remaining = min(target, sum(1.0 for h in active))  # cannot exceed one hour per active hour
    while remaining > 1e-9 and active:
        wsum = sum(weights[h] for h in active)
        capped = [h for h in active if remaining * weights[h] / wsum >= 1.0 - hours[h] - 1e-12]
        if capped:
            for h in capped:
                remaining -= 1.0 - hours[h]
                hours[h] = 1.0
            active = [h for h in active if h not in capped]
            continue
        for h in active:
            hours[h] += remaining * weights[h] / wsum
        remaining = 0.0
    return hours


def day_hours(kind, day, max_hours, user_min=0.0, start_hour=0):
    """Feature 1 for one appliance on one day.
    raw(h) = weather_weight(h) * time_weight(h); total_raw = sum(raw). hours_total = min(max, total_raw).
    If total_raw == 0 -> 0 hours (unless the user entered a minimum). Returns (hours per hour, total_raw)."""
    feels = hourly_feels_like(day, kind)
    humidity = day.get("humidity")
    raw = [0.0 if h < start_hour else weather_weight(kind, feels[h], humidity) * TIME_WEIGHT[kind][h] for h in range(24)]
    total_raw = sum(raw)
    target = min(max_hours, max(total_raw, user_min))
    if total_raw <= 0:
        if user_min <= 0 or target <= 0:
            return [0.0] * 24, 0.0          # e.g. AC on a 24 degree day: explicitly zero
        valid = [1.0 if h >= start_hour else 0.0 for h in range(24)]
        return split_hours(valid, target), 0.0  # only the user's own minimum: spread it over the valid hours
    return split_hours(raw, target), total_raw


def schedule_windows(hourly, min_hours=0.05):
    """Contiguous time ranges when the appliance runs (at least min_hours of use in that hour, 0.05 h = 3 minutes)."""
    runs, start = [], None
    for h in range(25):
        on = h < 24 and hourly[h] >= min_hours
        if on and start is None:
            start = h
        if not on and start is not None:
            runs.append((start, h))
            start = None
    return [f"{s:02d}:00-{e:02d}:00" for s, e in runs]


def _average_day(days):
    keys = ("temp_max", "temp_min", "humidity", "apparent_max", "apparent_min")
    out = dict(DEFAULT_DAY)
    for k in keys:
        vals = [d[k] for d in days if d.get(k) is not None]
        out[k] = sum(vals) / len(vals) if vals else DEFAULT_DAY[k]
    return out


def build_daily_demand(h, apps, weather, cyc, hours_key="daily_hours", today=None, now_hour=0, overrides=None):
    """Expected demand for each remaining day (Feature 1 applied to each day's forecast).
    Fixed appliances keep their daily kWh. Weather-dependent appliances use day_hours().
    Days without a forecast use the average of the available forecast days, or DEFAULT_DAY if none exist.
    overrides: optional {appliance_id: hours} what-if (planner slider). A fixed appliance uses those hours; a
    weather-driven one scales its weather-driven hours by override / usual hours, so mild days stay unaffected."""
    limits = load_rules()["limits"]
    wx = weather or {}
    forecast = (wx.get("forecast", []) + wx.get("climatology", [])) if wx.get("available") else []
    fmap = {f["day"]: f for f in forecast}
    dates = [d.isoformat() for d in cyc["remaining_dates"]]
    covered = [fmap[ds] for ds in dates if ds in fmap]
    fallback = _average_day(covered) if covered else dict(DEFAULT_DAY)
    out = []
    for d in cyc["remaining_dates"]:
        ds = d.isoformat()
        f = fmap.get(ds)
        day = f if f is not None else fallback
        start = now_hour if (today is not None and d == today) else 0
        items, fixed, weather_kwh, essential, floor, cool, heat = [], 0.0, 0.0, 0.0, 0.0, 0.0, 0.0
        for a in apps:
            kind = kind_of(a)
            kw = effective_kw(a)
            base = float(a[hours_key] if a.get(hours_key) is not None else a["daily_hours"])
            umin = float(a.get("user_hours") or 0.0)
            ov = None
            if overrides and (a["id"] in overrides or str(a["id"]) in overrides):
                ov = float(overrides.get(a["id"], overrides.get(str(a["id"]))))
            if kind in WEATHER_KINDS:
                cap = max(float(limits.get(kind, limits["default_max_hours"])), umin)
                hrs, total_raw = day_hours(kind, day, cap, umin, start)
                if kind == "geyser" and ov is None and base > 0 and total_raw > 0:
                    # hot water is a daily routine, not a weather-hours sum: start from the household's usual hours
                    # and move them with the cold (0.4x on a mild morning up to 1.6x on a freezing one)
                    active = sum(1 for w in TIME_WEIGHT["geyser"] if w > 0)
                    target = min(cap, max(umin, base * (0.4 + 1.2 * total_raw / active)))
                    hrs, total_raw = day_hours(kind, day, target, target, start)
                if ov is not None and base > 0:
                    target = total_raw * (ov / base)
                    if target >= total_raw:  # more hours than the weather alone asks for
                        hrs, total_raw = day_hours(kind, day, max(cap, target), max(umin, target), start)
                    else:                    # fewer hours: cap the day's total
                        hrs, total_raw = day_hours(kind, day, max(target, umin), umin, start)
                hours_total = sum(hrs)
                hourly = list(hrs)
                kwh = kw * hours_total
                weather_kwh += kwh
                if kind in COOLING_KINDS:
                    cool += kwh
                else:
                    heat += kwh
            else:
                hours_total = max(base if ov is None else ov, umin)
                hourly = [hours_total / 24.0] * 24  # fixed use: spread across the day
                kwh = kw * hours_total
                fixed += kwh
            if a["priority"] == "essential":
                essential += kwh
            floor += kw * umin
            items.append({"id": a["id"], "name": a["name"], "kind": kind, "weather": kind in WEATHER_KINDS,
                          "essential": a["priority"] == "essential", "hours": hours_total,
                          "kwh": kwh, "floor_kwh": kw * umin, "factor": (hours_total / base) if base > 0 else 1.0,
                          "hourly": hourly,
                          "rule": "weather-based hourly split" if kind in WEATHER_KINDS else "fixed daily use"})
        eff = None
        if f is not None:  # feels-like high shown on the pages (provider value, else temperature + humidity bonus)
            hb = band_for(load_rules()["humidity_bands"], day["humidity"])[0] if day.get("humidity") is not None else None
            eff = apparent_high(day, hb)
        out.append({"date": ds, "forecasted": f is not None, "source": "forecast" if f is not None else "estimate",
                    "temp_max": day["temp_max"], "temp_min": day["temp_min"], "humidity": day.get("humidity"),
                    "apparent_max": day.get("apparent_max"),
                    "sunrise": day.get("sunrise"), "sunset": day.get("sunset"),
                    "effective_temp": round(eff, 1) if eff is not None else None,
                    "rain_chance": f.get("rain_chance") if f else None, "rain_mm": f.get("rain_mm") if f else None,
                    "condition": f.get("condition") if f else None,
                    "fixed_kwh": fixed, "weather_kwh": weather_kwh,
                    "demand_kwh": fixed + weather_kwh, "cooling_kwh": cool, "heating_kwh": heat,
                    "essential_kwh": essential, "flexible_kwh": fixed + weather_kwh - essential,
                    "floor_kwh": floor, "items": items})
    return out


def restore_unscaled(days):
    """Put back the expected (unscaled) use that allocate_units snapshotted. allocate_units scales weather use down to fit
    the units left, which is the plan; predictions must show what will actually be used, so they call this afterwards.
    The allowances computed by allocate_units are kept."""
    for d in days:
        snap = d.get("_unscaled")
        if not snap:
            continue
        d.update(snap)
        for it in d["items"]:
            if "_unscaled" in it:
                it.update(it["_unscaled"])
    return days


def allocate_units(remaining_units, days):
    """Feature 2. leftover = remaining - sum(fixed_d). limit_d = fixed_d + leftover * expected_d / sum(expected),
    where expected_d = weather-dependent kWh of day d (before any scaling).
    If leftover < sum(expected), weather use is scaled down (AC first), never below the user's minimum hours.
    Sets d['allowance'] and d['warnings']."""
    if not days:
        return []
    for d in days:  # snapshot the expected use before any budget scaling (see restore_unscaled)
        d["_unscaled"] = {k: d[k] for k in ("demand_kwh", "weather_kwh", "cooling_kwh", "heating_kwh", "essential_kwh", "flexible_kwh")}
        for it in d["items"]:
            it["_unscaled"] = {"kwh": it["kwh"], "hours": it["hours"], "hourly": list(it["hourly"])}
    fixed = [d["fixed_kwh"] for d in days]
    expected = [d["weather_kwh"] for d in days]
    leftover = remaining_units - sum(fixed)
    expected_total = sum(expected)
    for d in days:
        d["warnings"] = []
    # step 4: limits from the unscaled expectation
    if leftover < 0:
        limits = list(fixed)                                  # fixed load stays; weather use gets nothing
    elif expected_total <= 1e-9:
        limits = [f + leftover / len(days) for f in fixed]    # no weather-dependent appliances: nothing to weight by
    else:
        limits = [f + leftover * e / expected_total for f, e in zip(fixed, expected)]
    # step 6: shortfall handling
    if leftover < 0:
        for d in days:
            d["warnings"].append("Budget does not cover the fixed load (fridge, lights, TV ...). Weather-dependent use set to 0.")
        cut_needed = expected_total
    elif leftover < expected_total - 1e-9:
        for d in days:
            d["warnings"].append("Budget is below the expected weather-dependent demand. Weather use scaled down, AC first.")
        cut_needed = expected_total - leftover
    else:
        cut_needed = 0.0
    if cut_needed > 0:
        for group in ("ac", "other"):  # AC first, then the rest of the weather-dependent appliances
            items = [it for d in days for it in d["items"] if it["weather"] and ((it["kind"] == "ac") == (group == "ac"))]
            adjustable = sum(max(0.0, it["kwh"] - it["floor_kwh"]) for it in items)
            take = min(cut_needed, adjustable)
            if take > 0:
                keep = (adjustable - take) / adjustable
                for it in items:
                    new_kwh = it["floor_kwh"] + max(0.0, it["kwh"] - it["floor_kwh"]) * keep
                    if it["kwh"] > 0:
                        ratio = new_kwh / it["kwh"]
                        it["hours"] *= ratio
                        it["hourly"] = [x * ratio for x in it["hourly"]]
                    it["kwh"] = new_kwh
                cut_needed -= take
        for d in days:
            wi = [it for it in d["items"] if it["weather"]]
            d["weather_kwh"] = sum(it["kwh"] for it in wi)
            d["cooling_kwh"] = sum(it["kwh"] for it in wi if it["kind"] in COOLING_KINDS)
            d["heating_kwh"] = sum(it["kwh"] for it in wi if it["kind"] not in COOLING_KINDS)
            d["essential_kwh"] = sum(it["kwh"] for it in d["items"] if it["essential"])
            d["demand_kwh"] = d["fixed_kwh"] + d["weather_kwh"]
            d["flexible_kwh"] = d["demand_kwh"] - d["essential_kwh"]
    for d, lim in zip(days, limits):
        d["allowance"] = lim
    return limits


# ---------------------------------------------------------------- Module 2: comfort-based hourly need
# Spec constants. Feels-like (apparent) temperature drives the comfort rules; no ML is involved.
COOL_BASE, COOL_FULL = 24.0, 36.0     # cool_need ramps 0 -> 1 between these
HEAT_BASE, HEAT_COLD = 18.0, 5.0      # heat_need ramps 0 -> 1 going down from HEAT_BASE to HEAT_COLD
AC_ON_NEED = 0.1                      # AC / heater run only when need >= 0.1
COOLER_MIN_T, COOLER_MAX_HUMIDITY = 30.0, 50.0   # cooler preferred above 30 °C when the air is dry
DEFAULT_WAKE_HOUR = 6                 # geyser runs in the 2 hours before wake/bath time (assumed 06:00)
DEFAULT_SUNRISE, DEFAULT_SUNSET = 6.5, 18.0       # fallback when the forecast has no sun times (decimal hours)
CLOUDY_LIGHT_NEED = 0.3               # daytime lighting need when cloud cover is above 80 %


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def cool_need(t_eff):
    """0..1 cooling need from the feels-like temperature."""
    return clamp((t_eff - COOL_BASE) / (COOL_FULL - COOL_BASE))


def heat_need(t_eff):
    """0..1 heating need from the feels-like temperature."""
    return clamp((HEAT_BASE - t_eff) / (HEAT_BASE - HEAT_COLD))


def _clock_hours(iso_time, default):
    """'2026-11-22T06:45' -> 6.75 (decimal local hours); default when missing or unreadable."""
    try:
        hh, mm = iso_time[11:16].split(":")
        return int(hh) + int(mm) / 60.0
    except (TypeError, ValueError, AttributeError):
        return default


def light_need(hour, sunrise_h, sunset_h, cloud_pct):
    """1.0 after sunset or before sunrise. In daylight, 0.3 when cloud cover is above 80 %, otherwise 0."""
    mid = hour + 0.5
    if mid < sunrise_h or mid >= sunset_h:
        return 1.0
    if cloud_pct is not None and cloud_pct > 80:
        return CLOUDY_LIGHT_NEED
    return 0.0


def hourly_comfort_table(apps, day, hourly_rows=None, occupied=None, wake_hour=DEFAULT_WAKE_HOUR):
    """Module 2: hour-by-hour ON/OFF, intensity and kWh for every appliance on one day.

    apps:        appliance dicts (see models.list_appliances)
    day:         forecast dict for the day (temp_max, temp_min, humidity, sunrise, sunset, ...)
    hourly_rows: 24 cached hourly rows (temp, apparent, humidity, cloud_cover) or None to use the daily curve
    occupied:    24 booleans, True when someone is home (default: home all day)
    Returns one dict per (hour, appliance): hour, temp (feels-like), humidity, cloud, appliance_id, appliance,
    kind, on, intensity, kwh. kWh = effective kW (rated x quantity x duty cycle) x intensity x occupancy.
    """
    occupied = occupied or [True] * 24
    if hourly_rows and len(hourly_rows) == 24 and all(r.get("apparent") is not None for r in hourly_rows):
        feels = [float(r["apparent"]) for r in hourly_rows]
        hums = [r.get("humidity") for r in hourly_rows]
        clouds = [r.get("cloud_cover") for r in hourly_rows]
        source = "hourly"
    else:
        feels = hourly_feels_like(day, "ac")
        hums = [day.get("humidity")] * 24
        clouds = [None] * 24
        source = "daily-curve"
    sunrise = _clock_hours(day.get("sunrise"), DEFAULT_SUNRISE)
    sunset = _clock_hours(day.get("sunset"), DEFAULT_SUNSET)
    geyser_hours = {(wake_hour - 2) % 24, (wake_hour - 1) % 24}

    out = []
    for h in range(24):
        t, hum, cloud = feels[h], hums[h], clouds[h]
        cool, heat = cool_need(t), heat_need(t)
        # per-appliance intensity for this hour; cooler replaces AC when dry and hot
        cooler_on = t > COOLER_MIN_T and hum is not None and hum < COOLER_MAX_HUMIDITY
        need = {}
        for a in apps:
            kind = kind_of(a)
            if kind == "ac":
                need[a["id"]] = cool if (cool >= AC_ON_NEED and not cooler_on) else 0.0
            elif kind == "cooler":
                need[a["id"]] = 1.0 if cooler_on else 0.0
            elif kind == "fan":
                need[a["id"]] = 1.0 if 22 < t <= 28 else 0.0       # fan as AC substitute in the 24-28 band
            elif kind == "heater":
                need[a["id"]] = heat if heat >= AC_ON_NEED else 0.0
            elif kind == "geyser":
                need[a["id"]] = heat if h in geyser_hours else 0.0  # colder = longer; only before wake/bath
            elif kind == "lighting":
                need[a["id"]] = light_need(h, sunrise, sunset, cloud)
            else:
                need[a["id"]] = 0.0                                  # flexible loads are scheduled by the optimizer
        for a in apps:
            kind = kind_of(a)
            occ = 1.0 if occupied[h] else 0.0          # occupancy scales every appliance
            intensity = clamp(need[a["id"]]) * occ
            kwh = effective_kw(a) * intensity
            out.append({"hour": h, "temp": round(t, 2), "humidity": hum, "cloud": cloud,
                        "appliance_id": a["id"], "appliance": a["name"], "kind": kind,
                        "on": intensity > 0, "intensity": round(intensity, 3), "kwh": round(kwh, 4),
                        "source": source})
    return out


def degree_hours(temps):
    """Module 3 features from a list of hourly feels-like temperatures.
    CDH = sum of max(0, T - 24); HDH = sum of max(0, 18 - T)."""
    cdh = sum(max(0.0, t - COOL_BASE) for t in temps)
    hdh = sum(max(0.0, HEAT_BASE - t) for t in temps)
    return round(cdh, 3), round(hdh, 3)


# ---------------------------------------------------- usage times shown to the user (per appliance, per day)
# Only appliances whose timing is decided by the weather or the clock get a time. Appliances the user operates
# (washing machine, iron, TV, lights, pump, charger ...) get none: when to run them is the user's choice, so no time is invented.


def _best_hours_windows(weights, hours):
    """Clock ranges for the `hours` best hours (highest weight first, earlier hour on ties), one slot per started hour.
    A 0.5 h appliance gets one slot; the duration shown next to it says how long it actually runs in that slot."""
    n = max(1, int(hours + 0.5))
    ranked = sorted((h for h in range(24) if weights[h] > 0), key=lambda h: (-weights[h], h))[:n]
    chosen = set(ranked)
    return [w.replace("-", "–") for w in schedule_windows([1.0 if h in chosen else 0.0 for h in range(24)], 0.5)]


def usage_windows(kind, hours, hourly, day=None):
    """Clock-time ranges when an appliance runs, as a list like ["13:00–17:00"] ([] when it is off), or None when the
    user decides when to use it (washing machine, iron, TV, ...).
    Weather-driven appliances (AC, cooler, fan, heater, geyser) run in the hours their weather schedule ranks highest
    (the hottest hours for cooling, the coldest for heating). The fridge runs all day. Lights are switched on whenever the
    user wants, so they get no predicted time either."""
    if kind in WEATHER_KINDS:
        return _best_hours_windows(hourly, hours) if hours > 0.01 and sum(hourly) > 0 else []
    if kind == "fridge":
        return ["00:00–24:00"] if hours > 0.01 else []
    return None  # user-operated: no predicted time


def clock12(hhmm):
    """"13:00" -> "1:00 PM", "00:00" or "24:00" -> "12:00 AM"."""
    h, m = (int(x) for x in hhmm.split(":"))
    h %= 24
    return f"{h % 12 or 12}:{m:02d} {'AM' if h < 12 else 'PM'}"


def windows12(windows):
    """["13:00–14:00", "00:00–24:00"] -> ["1:00 PM – 2:00 PM", "All day"]."""
    return ["All day" if w == "00:00–24:00" else " – ".join(clock12(x) for x in w.split("–")) for w in windows]
