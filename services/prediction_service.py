"""Consumption maths, weather-adjusted demand model and month-end prediction.

Method (transparent, rule-based — no ML is claimed because a billing cycle has far too little data to train on):
  * Appliance model:  daily kWh = W x qty x hours / 1000
  * Weather factor:   cooling appliances scale with max temperature above a reference temperature,
                      heating appliances scale with min temperature below a reference temperature.
                      Both are user-configurable assumptions, never exact appliance demand.
  * Projection:       consumed + sum over remaining days of (observed avg daily kWh x relative demand of that day),
                      where relative demand = modelled demand on that day / modelled demand at reference conditions.
"""
import math
from datetime import date, timedelta

import models
from services.bill_calculator import bill_for
from services.consumption import predicted_kwh
from services.weather_energy_engine import allocate_units, build_daily_demand, restore_unscaled


from services.consumption import effective_kw, predicted_kwh


def appliance_daily_kwh(a, hours=None):
    h = a["daily_hours"] if hours is None else hours
    return predicted_kwh(a, h)


def appliance_hourly_kw(a):
    """Effective kW while operating (rated x quantity x duty cycle) - see services.consumption."""
    return effective_kw(a)


def appliance_summary(apps, days):
    rows = []
    for a in apps:
        d = appliance_daily_kwh(a)
        rows.append({**a, "daily_kwh": round(d, 3), "monthly_kwh": round(d * days, 2), "hourly_kw": round(appliance_hourly_kw(a), 3)})
    total = sum(r["daily_kwh"] for r in rows)
    for r in rows:
        r["percent"] = round(100 * r["daily_kwh"] / total, 1) if total > 0 else 0.0
    with_use = [r for r in rows if r["daily_kwh"] > 0]
    return {
        "appliances": rows, "billing_days": days,
        "total_daily_kwh": round(total, 3), "total_monthly_kwh": round(total * days, 2),
        "highest": max(with_use, key=lambda r: r["daily_kwh"]) if with_use else None,
        "lowest": min(with_use, key=lambda r: r["daily_kwh"]) if with_use else None,
    }


def cycle_info(h, t=None):
    t = t or models.today()
    cs, ce = date.fromisoformat(h["cycle_start"]), date.fromisoformat(h["cycle_end"])
    total = (ce - cs).days + 1
    elapsed = min(max((t - cs).days + 1, 0), total)
    first_future = max(t + timedelta(days=1), cs)
    remaining_dates = [first_future + timedelta(days=i) for i in range(max(0, (ce - first_future).days + 1))]
    return {"start": cs.isoformat(), "end": ce.isoformat(), "total_days": total, "elapsed_days": elapsed,
            "remaining_days": len(remaining_dates), "today": t.isoformat(), "remaining_dates": remaining_dates,
            "ended": t > ce, "not_started": t < cs}


def day_weather_map(weather):
    return {f["day"]: f for f in (weather or {}).get("forecast", [])} if (weather or {}).get("available") else {}


def demand_series(h, apps, weather, cyc, overrides=None):
    """Per-remaining-day modelled demand (see services.weather_energy_engine)."""
    return build_daily_demand(h, apps, weather, cyc, overrides=overrides)


def _phi(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def risk_level(consumed, budget, projected):
    if consumed >= budget:
        return "Budget Exceeded"
    if projected > budget:
        return "High Risk"
    if projected >= 0.9 * budget:
        return "Moderate"
    return "Safe"


def predict(h, apps, weather, t=None, overrides=None):
    cyc = cycle_info(h, t)
    budget, consumed = h["monthly_budget"], h["units_consumed"]
    remaining_units = budget - consumed
    elapsed, rem_days = cyc["elapsed_days"], cyc["remaining_days"]
    avg_daily = consumed / elapsed if elapsed > 0 else 0.0
    baseline_proj = consumed + avg_daily * rem_days

    # Appliance-based projection. Each remaining day is the sum of its appliances' predicted kWh for that day's
    # weather, the same numbers as the daily allocation. The meter average is only a check (below), never a multiplier.
    series = demand_series(h, apps, weather, cyc, overrides)
    allocate_units(remaining_units, series)  # Feature 2: limits and shortfall scaling first, so the projection matches the allocation
    # The projection is what the household is expected to use, before any budget scaling (the scaled numbers are the
    # plan, not a prediction): otherwise a month that will overshoot could never show as over budget.
    restore_unscaled(series)
    future = sum(s["demand_kwh"] for s in series)
    for s in series:
        s["projected_kwh"] = s["demand_kwh"]
    projected = consumed + future
    method = "Appliance-based projection: each day is the sum of appliance kWh for that day's weather"
    method_note = []
    base_model = sum(predicted_kwh(a, a["daily_hours"]) for a in apps)  # no-weather reference at scheduled hours
    have_weather = bool((weather or {}).get("available"))
    if not have_weather:
        method_note.append("No weather data available; the projection uses typical conditions.")
    weather_effect = (future - base_model * rem_days) if have_weather else 0.0
    meter_gap = None
    if elapsed >= 3 and consumed > 0 and base_model > 0:
        meter_gap = abs(base_model - avg_daily) / max(avg_daily, 0.01)
    if meter_gap is not None and meter_gap > 0.25:
        method_note.append(f"Check: your meter averages {avg_daily:.1f} units/day, but your appliance list at scheduled hours "
                           f"(no weather) implies {base_model:.1f}. The projection uses the appliance list; check appliance "
                           f"hours or readings if this looks wrong.")

    sigma = 0.08 * future + 0.5
    prob = 1.0 if consumed >= budget else (_phi((projected - budget) / sigma) if rem_days > 0 else (1.0 if projected > budget else 0.0))
    level = risk_level(consumed, budget, projected)

    why = []
    why.append(f"{consumed:.1f} of {budget:.0f} units used over {elapsed} of {cyc['total_days']} billing days "
               f"({avg_daily:.2f} units/day so far).")
    why.append(f"Straight-line projection: {consumed:.1f} + {avg_daily:.2f} × {rem_days} remaining days = {baseline_proj:.1f} units.")
    if have_weather and rem_days:
        hot = [s for s in series if s["forecasted"] and s["temp_max"] is not None]
        if hot:
            mx = max(hot, key=lambda s: s["temp_max"])
            why.append(f"Forecast peak is {mx['temp_max']:.0f}°C on {mx['date']}; weather changes the appliance demand by "
                       f"{weather_effect:+.1f} units.")
    why.extend(method_note)
    why.append(f"Risk is the chance of exceeding budget under a ±{sigma:.1f}-unit uncertainty on the remaining usage (heuristic estimate).")

    bill = bill_for(h, projected)
    return {
        "cycle": {k: v for k, v in cyc.items() if k != "remaining_dates"},
        "budget": budget, "consumed": round(consumed, 2), "remaining_units": round(remaining_units, 2),
        "avg_daily": round(avg_daily, 3), "baseline_projection": round(baseline_proj, 2),
        "projected": round(projected, 2), "weather_effect": round(weather_effect, 2),
        "projected_pct_of_budget": round(100 * projected / budget, 1) if budget else 0,
        "units_over_budget": round(max(0.0, projected - budget), 2),
        "risk": {"level": level, "probability": round(prob, 3)},
        "method": method, "why": why, "model_daily_kwh": round(base_model, 3),
        "days": [{**{k: v for k, v in s.items() if k != "items"}, "projected_kwh": round(s["projected_kwh"], 3), "allowance": round(s["allowance"], 3)} for s in series],
        "series": series,  # internal; stripped by API layer
        "bill": bill, "budget_bill": bill_for(h, budget),
    }


# ------------------------------------------------ Module 3: daily load prediction (formula + optional ML)
# predicted_kWh_d = base_load + k_cool * CDH_d + k_heat * HDH_d, times a weekend factor.
# The k values are measured from the rule-based hourly model (calibrate_k), not guessed.
# Once the household has ML_MIN_DAYS logged days, a LinearRegression is trained on those days and used instead,
# retrained every ML_RETRAIN_DAYS days. scikit-learn is optional: without it the formula is used and says so.
ML_MIN_DAYS = 14
ML_RETRAIN_DAYS = 7
WEEKEND_FACTOR = 1.05
FEATURES = ("mean_temp", "max_temp", "cdh", "hdh", "humidity", "dow", "is_weekend", "prev_kwh")
COOL_KINDS = ("ac", "cooler", "fan")
HEAT_KINDS = ("heater", "geyser")
_ml_cache = {}  # household id -> {"trained_on": date, "model": estimator or None, "rows": int, "note": str}


def _feels(hourly):
    return [float(r["apparent"] if r.get("apparent") is not None else r["temp"]) for r in hourly
            if r.get("apparent") is not None or r.get("temp") is not None]


def weather_features(hourly, day_iso, prev_kwh):
    """Module 3 features for one day from 24 hourly rows (feels-like, humidity)."""
    from services.weather_energy_engine import degree_hours
    feels = _feels(hourly)
    hums = [r["humidity"] for r in hourly if r.get("humidity") is not None]
    cdh, hdh = degree_hours(feels)
    d = date.fromisoformat(day_iso)
    return {"mean_temp": sum(feels) / len(feels), "max_temp": max(feels), "cdh": cdh, "hdh": hdh,
            "humidity": sum(hums) / len(hums) if hums else 50.0, "dow": d.weekday(),
            "is_weekend": int(d.weekday() >= 5), "prev_kwh": float(prev_kwh)}


def _vector(f):
    return [f[k] for k in FEATURES]


def _curve_hourly(tmin, tmax):
    """Synthetic hourly rows from a daily low/high (same daily curve the rule engine uses)."""
    from services.weather_energy_engine import hourly_temperatures
    return [{"apparent": t, "temp": t, "humidity": 50.0} for t in hourly_temperatures(tmin, tmax)]


def calibrate_k(apps):
    """k_cool and k_heat (kWh per degree-hour) and base_load (kWh/day of non-weather appliances), measured with the
    rule-based hourly model on a hot and a cold reference day."""
    from services.weather_energy_engine import hourly_comfort_table, degree_hours
    from services.weather_rules_engine import kind_of
    ref = {"sunrise": None, "sunset": None, "humidity": 50.0}
    hot, cold = _curve_hourly(30.0, 40.0), _curve_hourly(2.0, 12.0)
    cool_kwh = sum(r["kwh"] for r in hourly_comfort_table(apps, ref, hot) if r["kind"] in COOL_KINDS)
    heat_kwh = sum(r["kwh"] for r in hourly_comfort_table(apps, ref, cold) if r["kind"] in HEAT_KINDS)
    cdh_hot, _ = degree_hours([r["apparent"] for r in hot])
    _, hdh_cold = degree_hours([r["apparent"] for r in cold])
    base = sum(appliance_daily_kwh(a) for a in apps if kind_of(a) not in COOL_KINDS + HEAT_KINDS)
    return {"base_load": base, "k_cool": cool_kwh / cdh_hot if cdh_hot > 0 else 0.0,
            "k_heat": heat_kwh / hdh_cold if hdh_cold > 0 else 0.0}


def formula_kwh(f, k):
    """predicted_kWh_d = base_load + k_cool * CDH + k_heat * HDH, with the weekend factor."""
    v = k["base_load"] + k["k_cool"] * f["cdh"] + k["k_heat"] * f["hdh"]
    return v * (WEEKEND_FACTOR if f["is_weekend"] else 1.0)


def _training_rows(h):
    """(X, y) from logged days that have a previous logged day and archived hourly weather."""
    import models
    from services.weather_service import fetch_past_hourly
    days = sorted(models.list_daily(), key=lambda d: d["day"])
    if len(days) < ML_MIN_DAYS + 1:
        return [], []
    units = {d["day"]: d["units"] for d in days}
    ordered = [d["day"] for d in days]
    first, last = date.fromisoformat(ordered[0]), date.fromisoformat(ordered[-1])
    if h.get("latitude") is None or h.get("longitude") is None:
        return [], []
    hourly = fetch_past_hourly(h["latitude"], h["longitude"], first, last)
    X, y = [], []
    for prev, cur in zip(ordered, ordered[1:]):
        if cur in hourly:
            X.append(_vector(weather_features(hourly[cur], cur, units[prev])))
            y.append(units[cur])
    return X, y


def _ml_model(h, apps):
    """Return a fitted model, or None with a reason. Retrains when the cached model is older than ML_RETRAIN_DAYS."""
    key = h.get("id")
    cached = _ml_cache.get(key)
    if cached and (models.today() - cached["trained_on"]).days < ML_RETRAIN_DAYS:
        return cached
    try:
        from sklearn.linear_model import LinearRegression
    except ImportError:
        return {"trained_on": models.today(), "model": None, "rows": 0, "note": "scikit-learn is not installed; using the formula."}
    X, y = _training_rows(h)
    if len(y) < ML_MIN_DAYS:
        return {"trained_on": models.today(), "model": None, "rows": len(y),
                "note": f"ML needs {ML_MIN_DAYS} training days (the previous-day feature uses one extra logged day, "
                        f"so {ML_MIN_DAYS + 1} logged days) with archived weather; have {len(y)}. Using the formula."}
    model = LinearRegression().fit(X, y)
    _ml_cache[key] = {"trained_on": models.today(), "model": model, "rows": len(y), "note": f"ML trained on {len(y)} logged days."}
    return _ml_cache[key]


def predict_daily(h, apps, weather, remaining_dates):
    """Module 3 output for each remaining day: {day, predicted_kwh, method, temp_max, temp_min, cdh, hdh}.
    Uses the trained model when available, otherwise the formula."""
    import models
    from services.weather_energy_engine import hourly_temperatures
    k = calibrate_k(apps)
    ml = _ml_model(h, apps)
    logged = models.list_daily()
    prev = logged[-1]["units"] if logged else k["base_load"]
    fmap = {f["day"]: f for f in (weather or {}).get("forecast", []) + (weather or {}).get("climatology", [])} \
        if (weather or {}).get("available") else {}
    out = []
    for d in remaining_dates:
        ds = d.isoformat()
        src = fmap.get(ds)
        cached_hours = models.load_hourly(ds)
        if len(cached_hours) == 24:
            hourly = cached_hours
        elif src and src.get("temp_max") is not None and src.get("temp_min") is not None:
            hourly = _curve_hourly(src["temp_min"], src["temp_max"])
            for r in hourly:
                r["humidity"] = src.get("humidity") or 50.0
        else:
            hourly = _curve_hourly(20.0, 30.0)  # no forecast or climate average: typical day
        f = weather_features(hourly, ds, prev)
        if ml.get("model") is not None:
            kwh, method = float(ml["model"].predict([_vector(f)])[0]), "ml"
        else:
            kwh, method = formula_kwh(f, k), "formula"
        kwh = max(0.0, kwh)
        out.append({"day": ds, "predicted_kwh": round(kwh, 3), "method": method, "forecasted": bool(src),
                    "cdh": f["cdh"], "hdh": f["hdh"], "note": ml.get("note"),
                    "temp_max": src.get("temp_max") if src else None, "temp_min": src.get("temp_min") if src else None,
                    "humidity": src.get("humidity") if src else None,
                    "sunrise": src.get("sunrise") if src else None, "sunset": src.get("sunset") if src else None,
                    "hourly": hourly})  # 24 hourly rows; API responses strip this
        prev = kwh
    return out
