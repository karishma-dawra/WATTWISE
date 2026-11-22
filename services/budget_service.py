"""Smart Budget Planner: daily plan across the remaining billing period."""
from services.prediction_service import predict


def build_plan(h, apps, weather, overrides=None):
    pred = predict(h, apps, weather, overrides=overrides)
    days = pred["days"]
    rem_units = max(0.0, pred["remaining_units"])
    rem_days = len(days)
    flat = rem_units / rem_days if rem_days else 0.0
    plan, cum_proj = [], pred["consumed"]
    for d in days:
        allowance = d["allowance"]  # weather-based allocation from weather_energy_engine
        cum_proj += d["projected_kwh"]
        plan.append({
            "date": d["date"], "temp_max": d["temp_max"], "temp_min": d["temp_min"], "condition": d["condition"],
            "forecasted": d["forecasted"], "allowance": round(allowance, 2),
            "projected_usage": d["projected_kwh"], "over_allowance": d["projected_kwh"] > allowance + 1e-9,
            "ac_demand": round(d["cooling_kwh"], 2), "heater_demand": round(d["heating_kwh"], 2),
            "essential_demand": round(d["essential_kwh"], 2), "humidity": d.get("humidity"), "rain_chance": d.get("rain_chance"),
            "effective_temp": d.get("effective_temp"), "cumulative_projected": round(cum_proj, 2),
        })
    return {
        "total_units": pred["budget"], "consumed": pred["consumed"], "remaining_units": pred["remaining_units"],
        "remaining_days": rem_days, "recommended_daily_limit": round(flat, 2),
        "projected": pred["projected"], "risk": pred["risk"], "units_over_budget": pred["units_over_budget"],
        "bill": pred["bill"], "weather_effect": pred["weather_effect"], "daily_plan": plan,
        "overrides_applied": bool(overrides), "method": pred["method"],
    }
