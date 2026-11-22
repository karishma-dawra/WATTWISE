"""Smart alerts derived from real predictions, forecasts and recommendations."""

from services.bill_calculator import bill_for, marginal_rate

HEAT_WARN, HEAT_CRIT = 38, 42
COLD_WARN, COLD_CRIT = 5, 2


def generate_alerts(h, apps, weather, pred, advice):
    out = []

    def add(code, sev, title, msg, key=""):
        out.append({"code": code, "fingerprint": f"{code}:{key}", "severity": sev, "title": title, "message": msg})

    budget, consumed = h["monthly_budget"], pred["consumed"]
    cur = h.get("currency_symbol") or ""
    used_pct = 100 * consumed / budget if budget else 0
    cyc = pred["cycle"]
    if cyc["ended"]:
        add("cycle_ended", "info", "Billing cycle has ended", "Update your billing cycle dates and units in Household Setup to keep tracking.", cyc["end"])
    if consumed >= budget:
        add("budget_exceeded", "critical", "Monthly budget exceeded",
            f"You have used {consumed:.1f} of {budget:.0f} units ({used_pct:.0f}%). Every extra unit now costs about {cur}{marginal_rate(h, consumed):.2f}.")
    elif used_pct >= 80:
        add("budget_near", "warning", "Budget approaching its limit",
            f"{used_pct:.0f}% of your budget is used with {cyc['remaining_days']} days left. Only {pred['remaining_units']:.1f} units remain.")
    if consumed < budget and pred["projected"] > budget:
        over = pred["units_over_budget"]
        sev = "critical" if pred["projected"] > 1.1 * budget else "warning"
        add("projected_over", sev, "Projected to exceed budget",
            f"At the current pace you will reach about {pred['projected']:.0f} units, {over:.0f} over your {budget:.0f}-unit budget "
            f"(≈ {cur}{pred['bill']['total'] - bill_for(h, budget)['total']:.0f} extra on your bill). Exceed-risk: {pred['risk']['probability']*100:.0f}%.")
    elif consumed < budget and pred["risk"]["level"] == "Moderate":
        add("projected_moderate", "info", "Close to your budget",
            f"Projected {pred['projected']:.0f} units is within 10% of your {budget:.0f}-unit budget. Keep an eye on high-use appliances.")

    # daily pace vs recommended daily limit
    if cyc["remaining_days"] > 0 and consumed < budget and pred["avg_daily"] > 0:
        limit = pred["remaining_units"] / cyc["remaining_days"]
        if pred["avg_daily"] > 1.15 * limit:
            add("high_consumption", "warning", "High daily consumption",
                f"Your average of {pred['avg_daily']:.1f} units/day is {100*(pred['avg_daily']/limit-1):.0f}% above the "
                f"{limit:.1f} units/day you can afford for the rest of the cycle.")

    # weather
    if (weather or {}).get("available"):
        days = [d for d in pred["days"] if d["forecasted"]]
        hot = [d for d in days if d["temp_max"] >= HEAT_WARN]
        if hot:
            mx = max(hot, key=lambda d: d["temp_max"])
            sev = "critical" if mx["temp_max"] >= HEAT_CRIT else "warning"
            add("extreme_heat", sev, "Extreme heat ahead",
                f"{len(hot)} hot day(s) in the forecast, peaking at {mx['temp_max']:.0f}°C on {mx['date']}. "
                f"Expect higher cooling demand (~{mx['cooling_kwh']:.1f} units on that day by the model).", mx["date"])
        cold = [d for d in days if d["temp_min"] is not None and d["temp_min"] <= COLD_WARN]
        if cold:
            mn = min(cold, key=lambda d: d["temp_min"])
            sev = "critical" if mn["temp_min"] <= COLD_CRIT else "warning"
            add("cold_days", sev, "Cold days ahead",
                f"Lows down to {mn['temp_min']:.0f}°C on {mn['date']}. Heater and geyser use may rise "
                f"(~{mn['heating_kwh']:.1f} units on that day by the model).", mn["date"])
        if pred["weather_effect"] > max(1.0, 0.03 * budget):
            add("usage_increase", "warning", "Weather will raise your usage",
                f"The forecast adds about {pred['weather_effect']:.1f} units to your month-end projection versus a straight-line estimate.")

    # recommendation-driven
    if advice.get("ready"):
        cuts = [p for p in advice["plan"] if p["priority"] == "flexible" and p["usual_hours"] > 0
                and p["hours"] < 0.8 * p["usual_hours"] and p["kwh"] < p["desired_kwh"] - 0.05]
        if cuts:  # surface only the single biggest adjustment to avoid alert noise
            p = max(cuts, key=lambda p: p["desired_kwh"] - p["kwh"])
            add("adjust_appliance", "info", f"Adjust {p['name']} usage",
                f"For {advice['target_date']} we suggest {p['duration']} of {p['name']} instead of your usual {p['usual_duration']} "
                f"to stay within budget.", advice["target_date"])
    if not out:
        add("on_track", "info", "You're on track", f"Projected usage of {pred['projected']:.0f} units is comfortably within your {budget:.0f}-unit budget.")
    return out


# ------------------------------------------------ Module 6: live tracking against the day's allocation
DAILY_YELLOW, DAILY_RED = 0.8, 1.0


def daily_pace_alerts(allocation_kwh, used_kwh, day):
    """Yellow at 80 % of the day's allocation, red at 100 %. Uses the day's logged units (readings are daily;
    there is no hour-by-hour meter data, so the check is against the whole day)."""
    if not allocation_kwh or allocation_kwh <= 0 or used_kwh is None:
        return []
    share = used_kwh / allocation_kwh
    if share >= DAILY_RED:
        return [{"code": "daily_red", "fingerprint": f"daily_red:{day}", "severity": "critical",
                 "title": "Today's allocation used up",
                 "message": f"You have used {used_kwh:.1f} units against today's allocation of {allocation_kwh:.1f}."}]
    if share >= DAILY_YELLOW:
        return [{"code": "daily_yellow", "fingerprint": f"daily_yellow:{day}", "severity": "warning",
                 "title": "80% of today's allocation used",
                 "message": f"You have used {used_kwh:.1f} of today's {allocation_kwh:.1f} units ({share*100:.0f}%)."}]
    return []


def slab_crossing_alert(h, projected_units):
    """Warn when the projected month-end is past a tariff slab boundary (units above it cost more)."""
    from services.bill_calculator import _slabs
    crossed = [s[0] for s in _slabs(h) if s[0] is not None and projected_units > s[0]]
    if not crossed:
        return []
    top = max(crossed)
    return [{"code": "slab_crossing", "fingerprint": f"slab_crossing:{top:g}", "severity": "warning",
             "title": "Projected to cross a tariff slab",
             "message": f"Your projected {projected_units:.0f} units are past the {top:g}-unit slab, where each extra unit costs more."}]
