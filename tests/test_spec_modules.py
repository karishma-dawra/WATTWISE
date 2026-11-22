"""Checks for the weather-scheduling modules: comfort need (Module 2), daily prediction (Module 3),
allocation (Module 4), optimizer (Module 5), cache and parsing (Module 1), live alerts (Module 6).

Run from the project folder with a THROWAWAY database:
    set WATTWISE_DB=%TEMP%\\ww_spec.db
    python tests\\test_spec_modules.py

No network is needed: weather is passed in directly. The route checks accept an offline 404 for forecasts.
"""
import os
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if "WATTWISE_DB" not in os.environ:
    sys.exit("Set WATTWISE_DB to a throwaway database path before running these tests.")

import app as application  # noqa: E402  (creates the database and the demo account)
import models  # noqa: E402
from services import alert_service, allocation_service, prediction_service, weather_service  # noqa: E402
from services.weather_energy_engine import (allocate_units, build_daily_demand, cool_need, degree_hours,  # noqa: E402
                                           heat_need, hourly_comfort_table, light_need)

failures = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  - {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def rows_for(apps, temp, humidity=50.0, cloud=None, sunrise="2026-11-23T06:30", sunset="2026-11-23T17:50"):
    """Hourly comfort table for a day whose feels-like temperature is flat at `temp`."""
    day = {"day": "2026-11-23", "temp_max": temp, "temp_min": temp, "humidity": humidity,
           "sunrise": sunrise, "sunset": sunset}
    hourly = [{"apparent": temp, "temp": temp, "humidity": humidity, "cloud_cover": cloud} for _ in range(24)]
    return hourly_comfort_table(apps, day, hourly)


def kwh_of(table, kind):
    return sum(r["kwh"] for r in table if r["kind"] == kind)


def on_of(table, kind):
    return any(r["on"] for r in table if r["kind"] == kind)


apps = models.list_appliances()
h = models.get_household()
# the demo household has no cooler or heater: add copies of the AC with those names so their rules can be checked
AC = next(a for a in apps if a["name"] == "Air Conditioner")
COOLER = {**AC, "id": -1, "name": "Air Cooler", "wattage": 200.0, "quantity": 1}
HEATER = {**AC, "id": -2, "name": "Room Heater", "wattage": 1500.0, "quantity": 1}
apps_all = apps + [COOLER, HEATER]

# ---------------------------------------------------------------- Module 2: comfort need
check("cool_need is 0 at 24 °C and 1 at 36 °C", cool_need(24) == 0 and cool_need(36) == 1)
check("heat_need is 0 at 18 °C and 1 at 5 °C", heat_need(18) == 0 and heat_need(5) == 1)
check("lights on after sunset and before sunrise", light_need(20, 6.5, 18.0, 0) == 1.0 and light_need(3, 6.5, 18.0, 0) == 1.0)
check("daytime lighting 0.3 only when cloud cover is above 80 %",
      light_need(12, 6.5, 18.0, 90) == 0.3 and light_need(12, 6.5, 18.0, 50) == 0.0)

check("AC never ON at 24 °C", kwh_of(rows_for(apps_all, 24.0), "ac") == 0)
check("AC stays OFF at 25 °C (need below the 0.1 threshold)", kwh_of(rows_for(apps, 25.0), "ac") == 0)
check("AC runs at 34 °C", kwh_of(rows_for(apps, 34.0), "ac") > 0)
check("fan ON in the 22–28 °C band", on_of(rows_for(apps, 26.0), "fan"))
check("fan OFF at 30 °C", not on_of(rows_for(apps, 30.0), "fan"))

hot_dry = rows_for(apps_all, 32.0, humidity=40.0)
hot_humid = rows_for(apps_all, 32.0, humidity=65.0)
check("cooler preferred over AC when hot (>30 °C) and dry (<50 %)",
      on_of(hot_dry, "cooler") and kwh_of(hot_dry, "ac") == 0)
check("humid heat (>=60 %) uses AC, not the cooler",
      not on_of(hot_humid, "cooler") and kwh_of(hot_humid, "ac") > 0)

check("heater ON at 10 °C", on_of(rows_for(apps_all, 10.0), "heater"))
check("heater OFF at 20 °C", not on_of(rows_for(apps_all, 20.0), "heater"))

geyser_hours = {r["hour"] for r in rows_for(apps, 10.0) if r["kind"] == "geyser" and r["on"]}
check("geyser runs only in the 2 hours before the 06:00 wake time", geyser_hours and geyser_hours <= {4, 5},
      str(sorted(geyser_hours)))
check("geyser runs more on a colder day", kwh_of(rows_for(apps, 4.0), "geyser") > kwh_of(rows_for(apps, 12.0), "geyser"))

# ---------------------------------------------------------------- Module 3: degree hours and formula
check("CDH = sum of max(0, T-24) over the day", abs(degree_hours([30.0] * 24)[0] - 6.0 * 24) < 1e-6)
check("HDH = sum of max(0, 18-T) over the day", degree_hours([10.0] * 24)[1] == 8.0 * 24)
k = prediction_service.calibrate_k(apps)
check("calibrated k_cool and k_heat are positive and base load is non-negative",
      k["k_cool"] > 0 and k["k_heat"] > 0 and k["base_load"] >= 0, str(k))
check("formula predicts more kWh on a hotter day",
      prediction_service.formula_kwh({"cdh": 40.0, "hdh": 0.0, "is_weekend": 0}, k) >
      prediction_service.formula_kwh({"cdh": 0.0, "hdh": 0.0, "is_weekend": 0}, k))
check("weekend factor raises the formula",
      prediction_service.formula_kwh({"cdh": 0.0, "hdh": 0.0, "is_weekend": 1}, k) >
      prediction_service.formula_kwh({"cdh": 0.0, "hdh": 0.0, "is_weekend": 0}, k))

# ML path: feed 30 synthetic training days (kWh = 3 + 0.2 * CDH) so no network or real history is needed
real_rows = prediction_service._training_rows
prediction_service._ml_cache.clear()
X_syn = [[25.0, 30.0 + i % 5, float(i % 12), 0.0, 50.0, i % 7, int(i % 7 >= 5), 3.0] for i in range(30)]
prediction_service._training_rows = lambda _h: (X_syn, [3.0 + 0.2 * x[2] for x in X_syn])
try:
    ml_days = prediction_service.predict_daily(h, apps, None, [date(2026, 11, 23)])
    check("with 30 training days the ML model is used", ml_days[0]["method"] == "ml", str(ml_days[0]["method"]))
    cached = prediction_service._ml_cache.get(h.get("id"))
    prediction_service._training_rows = lambda _h: (_ for _ in ()).throw(AssertionError("retrained too early"))
    again = prediction_service.predict_daily(h, apps, None, [date(2026, 11, 23)])
    check("the trained model is reused within the week (no retrain)", again[0]["method"] == "ml" and cached is not None)
    cached["trained_on"] = models.today() - timedelta(days=8)
    prediction_service._training_rows = lambda _h: (X_syn, [3.0 + 0.2 * x[2] for x in X_syn])
    prediction_service.predict_daily(h, apps, None, [date(2026, 11, 23)])
    check("the model is retrained after 7 days", prediction_service._ml_cache[h.get("id")]["trained_on"] == models.today())
finally:
    prediction_service._training_rows = real_rows
    prediction_service._ml_cache.clear()
few = prediction_service.predict_daily(h, apps, None, [date(2026, 11, 23)])
check("with too little history the formula is used and says why",
      few[0]["method"] == "formula" and "training days" in (few[0]["note"] or ""), str(few[0]["note"]))

# ---------------------------------------------------------------- Module 4: allocation follows the weather
START = date(2026, 11, 23)
CYC = {"remaining_dates": [START + timedelta(i) for i in range(2)]}


def weather(tmax, tmin):
    return {"available": True, "climatology": [], "forecast": [
        {"day": d.isoformat(), "temp_max": tmax, "temp_min": tmin, "humidity": 50, "rain_mm": 0.0, "rain_chance": 0,
         "apparent_max": None, "apparent_min": None, "condition": "test", "source": "forecast"}
        for d in CYC["remaining_dates"]]}


mixed = build_daily_demand(h, apps, {"available": True, "climatology": [], "forecast": [
    {"day": CYC["remaining_dates"][0].isoformat(), "temp_max": 40, "temp_min": 30, "humidity": 50, "rain_mm": 0.0,
     "rain_chance": 0, "apparent_max": None, "apparent_min": None, "condition": "test", "source": "forecast"},
    {"day": CYC["remaining_dates"][1].isoformat(), "temp_max": 26, "temp_min": 18, "humidity": 50, "rain_mm": 0.0,
     "rain_chance": 0, "apparent_max": None, "apparent_min": None, "condition": "test", "source": "forecast"}]}, CYC)
allocate_units(sum(d["fixed_kwh"] for d in mixed) + 40.0, mixed)
check("daily allowance grows on the hotter day (same budget, same month)", mixed[0]["allowance"] > mixed[1]["allowance"],
      f"hot {mixed[0]['allowance']:.2f} vs mild {mixed[1]['allowance']:.2f}")

tiny = {**h, "monthly_budget": 1.0, "units_consumed": 0.0}
out = allocation_service.daily_allocation(tiny, apps, weather(34, 24))
check("budget below the essential floors is flagged infeasible with a shortfall",
      out["feasible"] is False and out["shortfall_units"] > 0 and out["warnings"], str(out.get("shortfall_units")))

# regression: a new account with no appliances used to crash the allocation (max() of an empty list) and with it /api/alerts
none = allocation_service.daily_allocation(h, [], weather(34, 24), include_today=True)
check("no appliances: allocation returns an empty plan instead of failing",
      none["days"] == [] and none["feasible"] and none["warnings"], str(none))

big = {**h, "monthly_budget": 100000.0, "units_consumed": 0.0}
out = allocation_service.daily_allocation(big, apps, weather(34, 24))
check("with a large budget every day is feasible and gets an allocation",
      out["feasible"] and all(d["allocation_kwh"] > 0 for d in out["days"]))


from services.weather_energy_engine import usage_windows  # noqa: E402
check("washing machine, iron, TV and lights get no predicted time (None = the user decides)",
      all(usage_windows(k, 1.0, [0.0] * 24) is None for k in ("laundry", "baseline", "lighting")))
check("weather-driven and clock-driven appliances still get a time",
      usage_windows("fridge", 8, [0.0] * 24) and usage_windows("ac", 6, [0.0] * 12 + [1.0] * 12))

# ---------------------------------------------------------------- Module 5: optimizer
fits = allocation_service.optimize_day(h, apps, 5.0, 6.0, 2.0, 34, 50, 1.0)
check("a day that fits needs no cuts", fits["fits"] and fits["saved_kwh"] == 0 and fits["actions"] == [])
cut = allocation_service.optimize_day(h, apps, 10.0, 8.0, 6.0, 34, 45, 1.0)
check("over-allocation is cut by raising the AC setpoint first; user-operated loads are never scheduled",
      cut["actions"][0]["step"] == 2 and not any("washing" in a["action"].lower() for a in cut["actions"]) and cut["saved_kwh"] > 0,
      str(cut["actions"]))
check("AC setpoint saving is capped at 3 steps", cut["saved_kwh"] <= 6.0 * (1 - 0.94 ** 3) + 6.0 * 0.9 + 1e-6)
never = allocation_service.optimize_day(h, apps, 10.0, 0.5, 0.0, 34, 45, 4.0)
check("essential load is never cut below its floor", never["optimized_kwh"] >= 4.0 - 1e-9, str(never))

# ---------------------------------------------------------------- Module 1: cache and parsing
check("weather cache window is 3 hours", weather_service.CACHE_MINUTES == 180)
rows = weather_service.parse_hourly({"time": ["2026-11-23T00:00", "2026-11-23T01:00"], "temperature_2m": [20.0, 19.5],
                                     "apparent_temperature": [21.0, 20.0], "relative_humidity_2m": [60, 62],
                                     "cloud_cover": [10, 90], "shortwave_radiation": [0, 0], "precipitation": [0, 0.2]})
check("hourly parser keeps day, hour and all six variables",
      rows[1]["hour"] == 1 and rows[1]["apparent"] == 20.0 and rows[1]["cloud_cover"] == 90 and rows[1]["precipitation"] == 0.2)

# ---------------------------------------------------------------- Module 6: alerts
check("no daily alert below 80 %", alert_service.daily_pace_alerts(10.0, 7.9, "d") == [])
check("yellow alert at 80 %", alert_service.daily_pace_alerts(10.0, 8.0, "d")[0]["severity"] == "warning")
check("red alert at 100 %", alert_service.daily_pace_alerts(10.0, 10.0, "d")[0]["severity"] == "critical")
SLABS = "[[100, 3.5], [200, 5.0], [null, 7.0]]"
check("slab alert when projected units pass a slab boundary",
      len(alert_service.slab_crossing_alert({"tariff_slabs": SLABS}, 130)) == 1)
check("no slab alert below the first boundary", alert_service.slab_crossing_alert({"tariff_slabs": SLABS}, 90) == [])

# ---------------------------------------------------------------- routes (demo login; an offline 404 is acceptable)
client = application.app.test_client()
login = client.post("/api/auth/login", json={"mobile": "9999999999", "password": "demo1234"})
check("demo login works", login.status_code == 200, str(login.status_code))
r = client.get("/api/daily-allocation")
check("/api/daily-allocation responds", r.status_code == 200, str(r.status_code))
check("the 24-hour split is left out of the response by default",
      all("hourly_allocation_kwh" not in d for d in r.get_json()["days"]))
r = client.get("/api/optimize")
check("/api/optimize responds without a server error", r.status_code in (200, 404), str(r.status_code))
r = client.get("/api/hourly-plan?date=2026-11-23")
check("/api/hourly-plan responds without a server error", r.status_code in (200, 404), str(r.status_code))
check("/api/hourly-plan rejects a malformed date", client.get("/api/hourly-plan?date=23-11-2026").status_code == 400)

print("\nALL SPEC MODULE CHECKS PASSED" if not failures else f"\n{len(failures)} CHECK(S) FAILED: {failures}")
sys.exit(1 if failures else 0)


