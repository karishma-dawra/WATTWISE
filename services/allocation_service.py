"""Dynamic weather-based unit allocation.

For every remaining billing day (the monthly budget's horizon):
  * weather        forecast (first 16 days) or climate average (up to 30 days ahead), from the weather rules
  * demand         each appliance's predicted kWh = rated kW x quantity x duty cycle x hours the weather rules set
  * weather score  the day's total demand relative to the average day (1.0 = average)
  * allowance      the day's share of the units still available, weighted by that day's weather-driven demand
  * totals         daily = sum of appliance kWh; monthly = units already used + sum of the daily totals

Nothing is split equally: a hotter day gets more AC and cooling units, a colder day more heating and hot water.
The monthly total is never forced to equal the budget; if it exceeds the budget the recommendations say so and
suggest where to cut.
"""
from datetime import date

from services.prediction_service import cycle_info
from services.weather_energy_engine import allocate_units, build_daily_demand, restore_unscaled, usage_windows
from services.weather_rules_engine import apparent_high, band_for, kind_of, load_rules

GROUPS = {"ac": "ac", "cooler": "cooling", "fan": "cooling", "heater": "heating", "geyser": "heating"}
HOT_DAY, COLD_DAY = 38.0, 10.0


def _source_label(src):
    return {"forecast": "forecast", "climatology": "climate average", "estimate": "estimate"}.get(src, src)


def build_allocation(h, apps, weather):
    cyc = cycle_info(h)
    days = build_daily_demand(h, apps, weather, cyc)  # scheduled hours: the same basis as the monthly prediction
    consumed, budget = h["units_consumed"], h["monthly_budget"]
    available = max(0.0, budget - consumed)
    allocate_units(available, days)
    restore_unscaled(days)  # predicted units are the expected use; the allowances above are the plan
    rules = load_rules()
    mean_demand = (sum(d["demand_kwh"] for d in days) / len(days)) if days else 0.0

    rows = []
    for d in days:
        groups = {"ac": 0.0, "cooling": 0.0, "heating": 0.0, "other": 0.0}
        for it in d["items"]:
            groups[GROUPS.get(it["kind"], "other")] += it["kwh"]
        hb = band_for(rules["humidity_bands"], d["humidity"])[0] if d.get("humidity") is not None else None
        apparent = apparent_high(d, hb) if d.get("forecasted") else None
        rows.append({
            "date": d["date"], "source": d.get("source"), "source_label": _source_label(d.get("source")),
            "forecasted": d["forecasted"], "condition": d.get("condition"),
            "temp_max": d["temp_max"], "temp_min": d["temp_min"], "feels_like": round(apparent, 1) if apparent is not None else None,
            "humidity": d.get("humidity"), "rain_mm": d.get("rain_mm"), "rain_chance": d.get("rain_chance"),
            "weather_score": round(d["demand_kwh"] / mean_demand, 2) if mean_demand > 0 else 1.0,
            "predicted_units": round(d["demand_kwh"], 3),
            "allowance": round(d["allowance"], 3),
            "over_allowance": d["demand_kwh"] > d["allowance"] + 1e-9,
            "essential_units": round(d["essential_kwh"], 3), "floor_units": round(d.get("floor_kwh", 0.0), 3),
            "groups": {k: round(v, 3) for k, v in groups.items()},
            "appliances": [{"id": it["id"], "name": it["name"], "kind": it["kind"], "hours": round(it["hours"], 2),
                            "kwh": round(it["kwh"], 3), "rule": it.get("rule", ""),
                            "windows": usage_windows(it["kind"], it["hours"], it.get("hourly", []), d)} for it in d["items"]],
        })

    predicted_total = round(sum(r["predicted_units"] for r in rows), 3)
    predicted_month = round(consumed + predicted_total, 2)
    headroom = round(budget - predicted_month, 2)
    peak = max(rows, key=lambda r: r["predicted_units"]) if rows else None
    recs = _recommendations(h, rows, budget, consumed, predicted_month, headroom, weather)
    return {
        "budget": budget, "consumed": round(consumed, 2), "remaining_now": round(budget - consumed, 2),
        "available_for_allocation": round(available, 2), "remaining_days": len(rows),
        "outlook_days": len(rows),
        "predicted_units_remaining": predicted_total, "predicted_month_units": predicted_month,
        "headroom_after_prediction": headroom, "over_budget": predicted_month > budget,
        "units_over_budget": round(max(0.0, predicted_month - budget), 2),
        "peak_day": ({"date": peak["date"], "predicted_units": peak["predicted_units"], "temp_max": peak["temp_max"],
                      "feels_like": peak["feels_like"], "source_label": peak["source_label"]} if peak else None),
        "weather_available": bool((weather or {}).get("available")),
        "days": rows, "recommendations": recs,
        "basis": "Predicted units use each appliance's scheduled hours, adjusted for the forecast weather.",
    }


def _recommendations(h, rows, budget, consumed, predicted_month, headroom, weather):
    recs = []
    if not (weather or {}).get("available"):
        recs.append({"severity": "info", "text": "Weather is unavailable, so the allocation uses typical conditions. "
                                                 "Set your location in Setup to get forecast-based allocation."})
    hot = [r for r in rows if r["temp_max"] is not None and (r["feels_like"] or r["temp_max"]) >= HOT_DAY]
    for r in sorted(hot, key=lambda r: -(r["feels_like"] or r["temp_max"]))[:3]:
        ac = next((a for a in r["appliances"] if a["kind"] == "ac"), None)
        ac_txt = f"AC {ac['hours']:g} h ({ac['kwh']:.1f} units)" if ac and ac["hours"] > 0 else "AC allocation"
        recs.append({"severity": "warning", "date": r["date"],
                     "text": f"{r['date']} feels like {r['feels_like']:.0f}°C: {ac_txt}. Pre-cool before 1 PM, keep AC at 24–26°C, "
                             f"close blinds, and use fans with the AC rather than a cooler (humid heat reduces coolers)."})
    humid_rain = [r for r in rows if (r.get("rain_mm") or 0) >= 2.5 and (r.get("humidity") or 0) >= 60]
    for r in humid_rain[:2]:
        recs.append({"severity": "info", "date": r["date"],
                     "text": f"{r['date']}: rain with {r['humidity']:.0f}% humidity. Coolers will be weak, so keep AC/fan for cooling."})
    cold = [r for r in rows if r["temp_min"] is not None and r["temp_min"] <= COLD_DAY]
    for r in sorted(cold, key=lambda r: r["temp_min"])[:2]:
        recs.append({"severity": "info", "date": r["date"],
                     "text": f"{r['date']} low of {r['temp_min']:.0f}°C: heating and geyser get {r['groups']['heating']:.1f} units. "
                             f"Use the geyser in one morning slot and keep heater sessions short."})
    for r in [r for r in rows if r["over_allowance"]][:3]:
        recs.append({"severity": "warning", "date": r["date"],
                     "text": f"{r['date']} is predicted at {r['predicted_units']:.1f} units against an allowance of "
                             f"{r['allowance']:.1f}. Cut back flexible use (cooler, TV) that day."})
    if predicted_month > budget:
        over = predicted_month - budget
        hot_ac = sorted([(r["groups"]["ac"], r["date"]) for r in rows], reverse=True)[:1]
        tip = (f" Cutting AC on the hottest day ({hot_ac[0][1]}) would save the most." if hot_ac and hot_ac[0][0] > 0 else "")
        recs.insert(0, {"severity": "critical",
                        "text": f"Predicted month-end usage is {predicted_month:.0f} units, {over:.0f} over your {budget:.0f}-unit "
                                f"budget. Reduce scheduled hours of flexible appliances, or raise the budget.{tip}"})
    elif predicted_month > 0.9 * budget:
        recs.insert(0, {"severity": "warning",
                        "text": f"Predicted month-end usage {predicted_month:.0f} units leaves only {headroom:.0f} units of headroom."})
    else:
        recs.insert(0, {"severity": "info",
                        "text": f"Predicted month-end usage {predicted_month:.0f} units is within budget, with {headroom:.0f} units to spare."})
    return recs


# ------------------------------------------------ Module 4: distribute remaining billing-period units
# alloc_d = R * p_d / sum(p), where p_d = Module 3 predicted kWh for the day and R = remaining units.
# Each day keeps its essential floor (fridge, lights, fans marked essential); if the floors alone exceed R the
# plan is infeasible and the shortfall is reported. Recomputed on every call, so actual usage self-corrects.
def daily_allocation(h, apps, weather, include_today=False):
    """Per remaining day: predicted kWh (Module 3), essential floor, allocation and the 24-hour split (Module 4).
    include_today adds today (when it is inside the billing cycle) so today's allocation can be tracked (Module 6)."""
    from services.prediction_service import predict_daily
    from services.weather_energy_engine import build_daily_demand
    import models

    if not apps:  # nothing to schedule yet
        return {"days": [], "remaining_units": round(max(0.0, h["monthly_budget"] - h["units_consumed"]), 3), "feasible": True,
                "shortfall_units": 0.0, "warnings": ["Add appliances to get a daily allocation."], "method": "formula", "note": None}
    cyc = cycle_info(h)
    remaining_dates = list(cyc["remaining_dates"])
    t = date.fromisoformat(cyc["today"])
    if include_today and date.fromisoformat(cyc["start"]) <= t <= date.fromisoformat(cyc["end"]):
        remaining_dates = [t] + remaining_dates
    R = max(0.0, h["monthly_budget"] - h["units_consumed"])
    pred = predict_daily(h, apps, weather, remaining_dates)
    if not pred:
        return {"days": [], "remaining_units": round(R, 3), "feasible": True, "shortfall_units": 0.0, "warnings": []}
    # essential floor per day from the same demand model the rest of the app uses
    floors = {d["date"]: d["essential_kwh"] + d.get("floor_kwh", 0.0) for d in build_daily_demand(h, apps, weather, cyc)}
    p = [d["predicted_kwh"] for d in pred]
    fl = [floors.get(d["day"], 0.0) for d in pred]
    total_floor, total_p = sum(fl), sum(p)
    warnings, shortfall = [], 0.0
    if total_floor > R + 1e-9:
        shortfall = total_floor - R
        alloc = list(fl)
        warnings.append(f"Budget infeasible: essential loads need {total_floor:.1f} units over the remaining days but only "
                        f"{R:.1f} remain. Shortfall {shortfall:.1f} units. Raise the budget or cut optional use.")
    else:
        raw = [R * x / total_p if total_p > 0 else R / len(p) for x in p]
        alloc = [max(f, r) for f, r in zip(fl, raw)]
        excess = sum(alloc) - R
        if excess > 1e-9:  # floors lifted some days above their share; take it back from the days with room above floor
            slack = [a - f for a, f in zip(alloc, fl)]
            slack_total = sum(slack)
            alloc = [a - excess * s / slack_total if slack_total > 0 else a for a, s in zip(alloc, slack)]

    # hourly split inside each day: weights = that day's hourly kWh from the Module 2 table
    from services.weather_energy_engine import hourly_comfort_table
    rows = []
    for d, a in zip(pred, alloc):
        hourly = hourly_comfort_table(apps, d, d["hourly"])
        w = [0.0] * 24
        for r in hourly:
            w[r["hour"]] += r["kwh"]
        tot = sum(w)
        split = [a * x / tot for x in w] if tot > 0 else [a / 24.0] * 24
        rows.append({"day": d["day"], "predicted_kwh": d["predicted_kwh"], "method": d["method"],
                     "essential_floor_kwh": round(floors.get(d["day"], 0.0), 3),
                     "allocation_kwh": round(a, 3), "over_allocation": d["predicted_kwh"] > a + 1e-9,
                     "hourly_allocation_kwh": [round(x, 3) for x in split],
                     "ac_kwh": round(sum(r["kwh"] for r in hourly if r["kind"] == "ac"), 3),
                     "feels_like_max": round(max(r["temp"] for r in hourly), 1) if hourly else None,
                     "humidity": d.get("humidity")})
    feasible = shortfall <= 0
    return {"days": rows, "remaining_units": round(R, 3), "feasible": feasible,
            "shortfall_units": round(shortfall, 3), "warnings": warnings,
            "method": pred[0]["method"], "note": pred[0].get("note")}


# ------------------------------------------------ Module 5: fit the plan to the day's allocation
# Cuts are applied in the spec's order until the day fits; essentials are never cut.
#   1. flexible loads you operate yourself (washing machine, iron, pump, EV) are never given a time here
#   2. raise the AC setpoint: each +1 °C saves about 6 % of AC use (up to 3 °C)
#   3. replace AC with fan/cooler when feels-like <= 30 °C or the air is dry -> fan uses about 10 % of AC power
#   4. pre-cool/pre-heat in the cheapest hour -> moves units, saves none (reported, not counted)
#   5. essential loads are never reduced
SETPOINT_SAVING_PER_C = 0.06
SETPOINT_MAX_STEPS = 3
FAN_FRACTION_OF_AC = 0.10


def optimize_day(h, apps, planned_kwh, allocation_kwh, ac_kwh, feels_like, humidity, essential_kwh):
    """Return the cut plan for one day: actions taken, units and money saved, and whether it now fits."""
    from services.bill_calculator import bill_for
    actions, saved = [], 0.0
    gap = planned_kwh - allocation_kwh
    if gap <= 1e-9:
        return {"fits": True, "planned_kwh": round(planned_kwh, 3), "optimized_kwh": round(planned_kwh, 3),
                "saved_kwh": 0.0, "saved_money": 0.0, "actions": [], "unmet_kwh": 0.0}
    ac_left = ac_kwh
    steps = 0
    while gap - saved > 1e-9 and steps < SETPOINT_MAX_STEPS and ac_left > 0:
        steps += 1
        cut = ac_left * SETPOINT_SAVING_PER_C
        ac_left -= cut
        saved += cut
        actions.append({"step": 2, "action": f"Raise AC setpoint by {steps} °C", "saved_kwh": round(cut, 3)})
    if gap - saved > 1e-9 and ac_left > 0 and (feels_like <= 30 or (humidity is not None and humidity < 50)):
        cut = ac_left * (1 - FAN_FRACTION_OF_AC)
        ac_left -= cut
        saved += cut
        actions.append({"step": 3, "action": "Use fan/cooler instead of AC for the cooler hours", "saved_kwh": round(cut, 3)})
    actions.append({"step": 4, "action": "Pre-cool or pre-heat in the cheapest hour before the peak", "saved_kwh": 0.0})
    saved = min(saved, planned_kwh - essential_kwh)  # never cut below the essential load
    optimized = planned_kwh - saved
    unmet = max(0.0, optimized - allocation_kwh)
    return {"fits": unmet <= 1e-9, "planned_kwh": round(planned_kwh, 3), "optimized_kwh": round(optimized, 3),
            "saved_kwh": round(saved, 3), "unmet_kwh": round(unmet, 3), "actions": actions,
            "step5": "Essential loads (fridge, lights, medical devices) are never reduced."}
