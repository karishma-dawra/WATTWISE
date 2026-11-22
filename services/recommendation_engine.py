"""Smart Appliance Usage Plan — automatically calculated operating hours (no user-entered hours).

Inputs used for every recommendation:
  * remaining units  = monthly budget - units consumed
  * remaining days   = days left in the billing cycle
  * forecast per day = max/min temperature, humidity, rain chance (Open-Meteo), via weather_energy_engine
  * appliance profile = wattage, quantity, priority and typical full-use hours (`need_hours`)

Steps for tomorrow:
  1. Expected demand per appliance from the forecast (AC from effective high, heater/geyser from daily low).
  2. Allowance for tomorrow from weather_energy_engine.allocate_units (essentials first, remainder by weather demand).
     The flat average (remaining / days) is shown only for reference; it is NOT the final allocation.
  3. Essentials keep their weather-adjusted need. Flexible appliances share the rest of tomorrow's allowance,
     comfort-weighted, with a protected comfort floor for weather-driven appliances.
  4. Hours = allocated kWh / (kW). Each appliance is capped at a realistic daily maximum.
  5. If the budget cannot cover essentials and comfort floors, a warning is returned instead of a fake "safe" plan.

Recommendations never write to the appliance list by themselves; applying them is a separate, confirmed action.
"""
from services.bill_calculator import marginal_rate
from services.consumption import predicted_kwh
from services.prediction_service import appliance_hourly_kw, cycle_info
from services.weather_energy_engine import allocate_units, appliance_kind, build_daily_demand, usage_windows, windows12
from services.weather_rules_engine import load_rules

COMFORT_FLOOR = 0.35   # share of desired use always protected for weather-driven appliances
STEP = 0.25            # round hours down to 15 minutes
SMALL_STEP = 1 / 12    # 5 minutes for short sessions (geyser etc.)
DEFAULT_MAX_HOURS = 24
# Realistic daily ceilings per appliance kind (hours/day). Prevents "budget safe" plans with absurd runtimes.



def _round_hours(h):
    step = SMALL_STEP if h < 1 else STEP
    return int(h / step + 1e-9) * step


def fmt_duration(hours):
    if hours <= 0:
        return "Off"
    m = int(round(hours * 60))
    if m < 60:
        return f"{m} min"
    hh, mm = divmod(m, 60)
    return f"{hh} h" if mm == 0 else f"{hh} h {mm} min"




def _cap(a, hours):
    limits = load_rules()["limits"]
    cap = float(limits.get(appliance_kind(a), limits["default_max_hours"]))
    umin = float(a.get("user_hours") or 0.0)  # the user's own minimum is never capped below
    return min(hours, max(cap, umin))


def _waterfill(items, budget):
    """items: dicts with desired, floor, weight. Returns allocation list (kWh) in the same order."""
    floors = [min(i["floor"], i["desired"]) for i in items]
    fsum = sum(floors)
    if fsum >= budget:
        # minimums (the user's entered hours, safety cooling/heating, hot water) are never scaled down;
        # the shortfall is reported so the user can raise the budget
        return floors[:], True
    alloc = floors[:]
    left = budget - fsum
    active = [k for k, i in enumerate(items) if i["desired"] - alloc[k] > 1e-9]
    while left > 1e-9 and active:
        wsum = sum(items[k]["weight"] * (items[k]["desired"] - floors[k]) for k in active) or 1e-9
        capped = False
        for k in list(active):
            share = left * items[k]["weight"] * (items[k]["desired"] - floors[k]) / wsum
            room = items[k]["desired"] - alloc[k]
            if share >= room - 1e-9:
                alloc[k] += room
                capped = True
                active.remove(k)
        if capped:
            left = budget - sum(alloc)
            continue
        for k in active:
            alloc[k] += left * items[k]["weight"] * (items[k]["desired"] - floors[k]) / wsum
        left = 0
    return alloc, False


def _plan(apps, day, allowance, mode="balanced"):
    """Appliance plan for one day. mode: balanced | comfort (full desired use) | eco (comfort floors only)."""
    items = {i["id"]: i for i in day["items"]}
    ess = [a for a in apps if a["priority"] == "essential"]
    flex = [a for a in apps if a["priority"] != "essential"]
    plan = []
    ess_kwh = sum(items[a["id"]]["kwh"] for a in ess)
    flex_units = max(0.0, allowance - ess_kwh)
    rows = [{"desired": items[a["id"]]["kwh"],
             "floor": min(items[a["id"]].get("floor_kwh", 0.0), items[a["id"]]["kwh"]),
             "weight": 1.0} for a in flex]
    desired_total = sum(r["desired"] for r in rows)
    if mode == "comfort":
        alloc, short = [r["desired"] for r in rows], False
    elif mode == "eco":
        alloc, short = [r["floor"] for r in rows], False
    else:
        alloc, short = _waterfill(rows, flex_units) if rows else ([], False)

    for a in ess:
        it = items[a["id"]]
        kw = appliance_hourly_kw(a)
        hrs = round(_cap(a, it["hours"]), 2)
        plan.append({"id": a["id"], "name": a["name"], "priority": "essential", "weather_type": a["weather_type"],
                     "rule": it.get("rule", ""), "floor_kwh": round(it.get("floor_kwh", 0.0), 3),
                     "category": a["category"], "hours": hrs, "usual_hours": a["need_hours"],
                     "kwh": round(hrs * kw, 3), "desired_kwh": round(it["kwh"], 3), "desired_hours": round(it["hours"], 2),
                     "weather_factor": round(it["factor"], 2)})
    for a, r, k in zip(flex, rows, alloc):
        it = items[a["id"]]
        kw = appliance_hourly_kw(a)
        hrs = _cap(a, min(_round_hours(k / kw) if kw > 0 else 0.0, 24.0))
        if mode == "balanced" and a["weather_type"] != "none" and flex_units > 0 and it["hours"] >= SMALL_STEP and hrs < SMALL_STEP:
            hrs = SMALL_STEP  # never round a needed comfort appliance down to "Off" (tiny overshoot is acceptable)
        if mode == "comfort":
            hrs = round(_cap(a, it["hours"]), 2)
        plan.append({"id": a["id"], "name": a["name"], "priority": "flexible", "weather_type": a["weather_type"],
                     "rule": it.get("rule", ""), "floor_kwh": round(it.get("floor_kwh", 0.0), 3),
                     "category": a["category"], "hours": hrs, "usual_hours": a["need_hours"],
                     "kwh": round(hrs * kw, 3), "desired_kwh": round(r["desired"], 3),
                     "desired_hours": round(_cap(a, it["hours"]), 2), "weather_factor": round(it["factor"], 2)})
    total = sum(p["kwh"] for p in plan)
    return {"plan": plan, "total_kwh": round(total, 2), "essential_kwh": round(ess_kwh, 2),
            "flexible_units": round(flex_units, 2), "desired_flex_kwh": round(desired_total, 2),
            "shortfall": bool(short) or (flex_units + 1e-9 < sum(r["floor"] for r in rows))}


def _explain_explanation(tomorrow, plan_items):
    """Plain-language 'why' built from the calculated numbers (no hardcoded hours)."""
    if not tomorrow["forecasted"] or tomorrow["temp_max"] is None:
        return ("No forecast is available for tomorrow, so these hours are based on your appliance profile "
                "and remaining budget only (estimate).")
    parts = [f"Tomorrow's forecast indicates a high of {tomorrow['temp_max']:.0f}°C"
             + (f" (feels like about {tomorrow['effective_temp']:.0f}°C with humidity and rain)" if tomorrow.get("effective_temp") is not None else "")
             + f" and a low of {tomorrow['temp_min']:.0f}°C."]
    changed = [p["name"] for p in plan_items if p["weather_type"] != "none" and p["priority"] == "flexible"]
    if changed:
        parts.append(f"We have adjusted your {' and '.join(changed)} schedule based on the weather and your remaining electricity budget.")
    else:
        parts.append("We have set each appliance's hours from your remaining electricity budget.")
    return " ".join(parts)


def advise(h, apps, weather, pred=None):
    """Automatic usage plan for tomorrow. Hours come from the weather rules and the household's appliances only."""
    if not apps:
        return {"ready": False, "message": "Add at least one appliance to generate your smart usage plan."}
    cyc = cycle_info(h)
    if not cyc["remaining_dates"]:
        return {"ready": False, "message": "The billing cycle has no remaining days. Update your billing cycle in Household Setup."}

    consumed = h["units_consumed"]
    rem_units = h["monthly_budget"] - consumed
    days = build_daily_demand(h, apps, weather, cyc, hours_key="need_hours")
    R = max(0.0, rem_units)
    allocate_units(R, days)
    rem_days = len(days)
    flat = R / rem_days
    tomorrow = days[0]
    allowance = tomorrow["allowance"]
    exceeded = rem_units <= 0

    main = _plan(apps, tomorrow, allowance, "balanced")
    comfort_p = _plan(apps, tomorrow, allowance, "comfort")
    eco_p = _plan(apps, tomorrow, allowance, "eco")

    # Feasibility: can the remaining units cover essentials across all remaining days?
    essential_over_cycle = sum(d["essential_kwh"] for d in days)
    floor_over_cycle = sum(d.get("floor_kwh", 0.0) for d in days)  # essential + never-cut comfort/safety hours
    infeasible = max(essential_over_cycle, floor_over_cycle) > R + 1e-9
    warning = None
    if exceeded:
        warning = "Your monthly budget is already used. Only essential appliances and minimum comfort hours are shown."
    elif infeasible:
        warning = (f"Budget warning: essential and safety-critical use (cooling, heating, hot water) needs about "
                   f"{max(essential_over_cycle, floor_over_cycle):.1f} units over the remaining {rem_days} days, but only "
                   f"{R:.1f} units remain. These hours are never cut; consider raising the budget.")
    elif main["shortfall"]:
        warning = "Your remaining budget can't fully cover minimum comfort hours for weather-driven appliances on every day."

    day_items = {i["id"]: i for i in tomorrow["items"]}

    def windows_for(x):
        """Clock times when appliance x runs tomorrow, from tomorrow's hour-by-hour weather schedule."""
        it = day_items[x["id"]]
        return usage_windows(it["kind"], x["hours"], it.get("hourly", []), tomorrow)

    def scen(label, p, desc):
        proj = consumed + p["total_kwh"] * rem_days
        return {"label": label, "description": desc, "units_tomorrow": p["total_kwh"],
                "month_end_if_repeated": round(proj, 1), "within_budget": proj <= h["monthly_budget"],
                "plan": [{"name": x["name"], "hours": x["hours"], "duration": fmt_duration(x["hours"]),
                          "windows": windows_for(x)} for x in p["plan"]]}

    scenarios = [
        scen("Recommended", main, "Calculated from tomorrow's weather and your remaining budget"),
        scen("Comfort-first", comfort_p, "Full weather-driven use (budget ignored)"),
        scen("Eco saver", eco_p, "Only the minimum comfort hours for weather-driven appliances"),
    ]

    for p in main["plan"]:
        a = next(x for x in apps if x["id"] == p["id"])
        p["duration"] = fmt_duration(p["hours"])
        p["usual_duration"] = fmt_duration(p["usual_hours"])
        p["wattage"], p["quantity"] = a["wattage"], a["quantity"]
        p["min_hours"] = a.get("user_hours")  # usage hours the user entered: a minimum, shown on the card
        if a.get("user_hours"):
            p["reason"] = (p.get("reason", "") + f" Your entered usage of {a['user_hours']:g} h/day is kept as the minimum.").strip()
        p["monthly_kwh"] = round(p["kwh"] * rem_days, 2)  # estimate if tomorrow's schedule repeated
        p["monthly_share_pct"] = round(100 * p["monthly_kwh"] / R, 1) if R > 0 else 0.0
        p["windows"] = windows_for(p)  # when it runs tomorrow, e.g. ["13:00–17:00"]; None = the user decides
        p["window"] = None if p["windows"] is None else (("Run at " + ", ".join(windows12(p["windows"]))) if p["windows"] else "Off tomorrow")
        kw = appliance_hourly_kw(a)
        if p["priority"] == "essential":
            p["reason"] = (f"Essential appliance: {kw:.2f} kWh per hour, kept at its calculated need "
                           f"({fmt_duration(p['hours'])}) and reserved before anything else.")

        else:
            d = p["desired_hours"]
            bits = [f"{a['name']} draws {kw:.2f} kWh per hour ({a['quantity']} × {a['wattage']:.0f} W)."]
            if a["weather_type"] != "none" and tomorrow["forecasted"]:
                tmp = tomorrow["temp_max"] if a["weather_type"] == "cooling" else tomorrow["temp_min"]
                bits.append(f"Forecast {'high' if a['weather_type'] == 'cooling' else 'low'} of {tmp:.0f}°C gives a "
                            f"{p['weather_factor']:.2f}× weather factor, so the calculated need is {fmt_duration(d)}.")
            else:
                bits.append(f"Calculated need is {fmt_duration(d)}.")
            bits.append(f"The budget allows {p['kwh']:.2f} kWh = {fmt_duration(p['hours'])}."
                        if p["hours"] < d - 1e-6 else "The budget covers the full calculated need.")
            p["reason"] = " ".join(bits)

    hero = max([p for p in main["plan"] if p["weather_type"] in ("cooling", "heating") and p["priority"] == "flexible"],
               key=lambda p: p["kwh"], default=None)
    sentence = []
    if tomorrow["forecasted"] and tomorrow["temp_max"] is not None:
        t = tomorrow["temp_max"]
        sentence.append(("Tomorrow is expected to be extremely hot" if t >= 40 else "Tomorrow is expected to be hot" if t >= 34
                         else "Tomorrow looks mild" if t >= 20 else "Tomorrow is expected to be cold") + f" ({t:.0f}°C high).")
    else:
        sentence.append("No forecast is available for tomorrow, so the plan assumes your usual conditions.")
    if exceeded:
        sentence.append("Your monthly budget is already used.")
    elif hero:
        sentence.append(f"Based on {R:.1f} remaining units over {rem_days} days, we recommend about {fmt_duration(hero['hours'])} "
                        f"of {hero['name']} while keeping other appliances within your monthly target.")
    else:
        sentence.append(f"Based on {R:.1f} remaining units over {rem_days} days, your allowance for tomorrow is {allowance:.1f} units.")

    # Normal use = every appliance at its typical hours with no weather change (same kWh formula as everything else)
    baseline_units = round(sum(predicted_kwh(a, a["need_hours"]) for a in apps), 2)
    saved = baseline_units - main["total_kwh"]  # negative = using more than normal (hot or cold day)
    why = [
        f"Remaining budget: {h['monthly_budget']:.0f} − {consumed:.1f} = {R:.1f} units for {rem_days} remaining days "
        f"(flat average {flat:.2f} units/day, reference only).",
        f"Tomorrow's allowance: {allowance:.2f} units, from its essential load ({tomorrow['essential_kwh']:.2f}) plus its share of the "
        f"weather-driven demand.",
        f"Essential appliances reserve {main['essential_kwh']:.2f} units, leaving {main['flexible_units']:.2f} for flexible appliances.",
        "Hours are kept within realistic daily limits for each appliance.",
    ]
    if warning:
        why.append("⚠ " + warning)

    return {
        "ready": True, "target_date": tomorrow["date"], "forecast": {
            "temp_max": tomorrow["temp_max"], "temp_min": tomorrow["temp_min"], "condition": tomorrow["condition"],
            "humidity": tomorrow.get("humidity"), "rain_chance": tomorrow.get("rain_chance"),
            "effective_temp": tomorrow.get("effective_temp"), "forecasted": tomorrow["forecasted"]},
        "headline": " ".join(sentence), "explanation": _explain_explanation(tomorrow, main["plan"]),
        "source": "estimate", "remaining_units": round(rem_units, 2), "remaining_days": rem_days,
        "flat_allowance": round(flat, 2), "weighted_allowance": round(allowance, 2),
        "essential_kwh": main["essential_kwh"], "flexible_units": main["flexible_units"],
        "plan": main["plan"], "estimated_units": main["total_kwh"],
        "expected_savings_units": round(saved, 2),
        "expected_savings_amount": round(saved * marginal_rate(h, consumed), 2),
        "baseline_units": baseline_units, "household_total_units": main["total_kwh"],
        "allowance_tomorrow": round(allowance, 2),
        "appliance_units": [{"id": p["id"], "name": p["name"], "kwh": p["kwh"]} for p in main["plan"]],
        "currency_symbol": h.get("currency_symbol") or "",
        "unconstrained_units": comfort_p["total_kwh"], "shortfall": main["shortfall"] or infeasible,
        "within_budget": not (exceeded or infeasible), "warning": warning,
        "budget_exceeded": exceeded, "scenarios": scenarios, "why": why,
    }
