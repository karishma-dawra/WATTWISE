"""Tests for the dynamic weather-based unit allocation.

Run from the project folder with a THROWAWAY database:
    set WATTWISE_DB=%TEMP%\\ww_test.db
    python tests\\test_allocation.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if "WATTWISE_DB" not in os.environ:
    sys.exit("Set WATTWISE_DB to a throwaway database path before running these tests.")

import app as application  # noqa: E402
import models  # noqa: E402
from services import weather_rules_engine as rules_engine  # noqa: E402
from services.allocation_service import build_allocation  # noqa: E402
from services.consumption import predicted_kwh  # noqa: E402
from services.prediction_service import cycle_info  # noqa: E402

client = application.app.test_client()
login = client.post('/api/auth/login', json={'mobile': '9999999999', 'password': 'demo1234'})  # demo account owns the test household
assert login.status_code == 200, 'demo login failed'
failures = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  â€” {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def close(a, b, tol=0.01):
    return abs(a - b) <= tol


def weather_for(h, per_day):
    """Synthetic forecast: per_day(i) -> dict of temp_max, temp_min, humidity, rain_mm, rain_chance."""
    cyc = cycle_info(h)
    fc = []
    for i, d in enumerate(cyc["remaining_dates"]):
        v = per_day(i)
        fc.append({"day": d.isoformat(), "temp_max": v["temp_max"], "temp_min": v["temp_min"], "condition": "test",
                   "humidity": v.get("humidity", 50), "rain_mm": v.get("rain_mm", 0.0), "rain_chance": v.get("rain_chance", 0),
                   "apparent_max": v.get("apparent_max"), "apparent_min": None, "source": "forecast"})
    return {"available": True, "forecast": fc, "climatology": []}


# A household with budget, usage, and the demo appliances (AC, cooler, fan, heater, geyser, fridge, TV ...)
models.save_meter_details(1000, 1100, 5000)  # generous budget: these checks test weather, not the budget cap
models.create_appliance({"name": "Cooler", "category": "Cooling", "wattage": 200, "quantity": 1, "priority": "flexible", "weather_type": "cooling"})
models.create_appliance({"name": "Room Heater", "category": "Heating", "wattage": 1500, "quantity": 1, "priority": "flexible", "weather_type": "heating"})
h = models.get_household()
apps = models.list_appliances()

# ----- 1. hotter day gets more AC: 45Â°C vs 30Â°C alternating days
w = weather_for(h, lambda i: {"temp_max": 45 if i % 2 == 0 else 30, "temp_min": 30 if i % 2 == 0 else 22, "humidity": 40})
a = build_allocation(h, apps, w)
hot = [d for d in a["days"] if d["temp_max"] == 45]
mild = [d for d in a["days"] if d["temp_max"] == 30]
check("45Â°C day gets more AC kWh than 30Â°C day", hot[0]["groups"]["ac"] > mild[0]["groups"]["ac"] + 0.5,
      f"{hot[0]['groups']['ac']} vs {mild[0]['groups']['ac']}")
check("45Â°C day gets a larger allowance than 30Â°C day", hot[0]["allowance"] > mild[0]["allowance"],
      f"{hot[0]['allowance']} vs {mild[0]['allowance']}")
check("hot day has a higher weather score", hot[0]["weather_score"] > mild[0]["weather_score"])

# ----- 2. colder day gets more heating than hot day
w = weather_for(h, lambda i: {"temp_max": 15 if i % 2 == 0 else 35, "temp_min": 4 if i % 2 == 0 else 26, "humidity": 50})
a = build_allocation(h, apps, w)
cold = [d for d in a["days"] if d["temp_max"] == 15][0]
warm = [d for d in a["days"] if d["temp_max"] == 35][0]
check("15Â°C (cold) day gets more heating + geyser kWh than 35Â°C day", cold["groups"]["heating"] > warm["groups"]["heating"],
      f"{cold['groups']['heating']} vs {warm['groups']['heating']}")
check("the cold day's AC is zero (no cooling below the threshold)", cold["groups"]["ac"] == 0.0)

# ----- 3. humidity and rain influence cooling
dry = weather_for(h, lambda i: {"temp_max": 38, "temp_min": 27, "humidity": 35, "rain_mm": 0.0})
humid = weather_for(h, lambda i: {"temp_max": 38, "temp_min": 27, "humidity": 85, "rain_mm": 0.0})
rainy = weather_for(h, lambda i: {"temp_max": 38, "temp_min": 27, "humidity": 35, "rain_mm": 12.0})  # rain only: same humidity as "dry"
cool_dry = build_allocation(h, apps, dry)["days"][0]["groups"]["cooling"]
cool_humid = build_allocation(h, apps, humid)["days"][0]["groups"]["cooling"]
rainy85 = weather_for(h, lambda i: {"temp_max": 38, "temp_min": 27, "humidity": 85, "rain_mm": 12.0})
cool_rain = build_allocation(h, apps, rainy85)["days"][0]["groups"]["cooling"]  # rain at the same humidity as "humid"
check("high humidity reduces cooler-based cooling units", cool_humid < cool_dry, f"dry {cool_dry} vs humid {cool_humid}")
check("heavy rain never increases cooling units (humidity held equal)", cool_rain <= cool_humid + 1e-9, f"humid {cool_humid} vs rain {cool_rain}")
ac_dry = build_allocation(h, apps, dry)["days"][0]["groups"]["ac"]
ac_rain = build_allocation(h, apps, rainy)["days"][0]["groups"]["ac"]
check("rain alone never increases AC units (humidity held equal)", ac_rain <= ac_dry + 1e-9, f"dry {ac_dry} vs rain {ac_rain}")

# ----- 4. totals are sums of appliance predictions; monthly = used + predicted
a = build_allocation(h, apps, weather_for(h, lambda i: {"temp_max": 33 + i % 4, "temp_min": 20, "humidity": 55}))
ok_days = all(close(d["predicted_units"], sum(x["kwh"] for x in d["appliances"]), 0.002) for d in a["days"])
check("every day's total equals the sum of its appliance kWh", ok_days)
# hours are shown to 2 dp, so allow display rounding
ok_appl = all(close(x["kwh"], predicted_kwh(models.get_appliance(x["id"]), x["hours"]), 0.01) for d in a["days"] for x in d["appliances"])
check("every appliance kWh equals rated kW x duty x its hours", ok_appl)
check("monthly predicted = used + sum of daily predictions",
      close(a["predicted_month_units"], round(a["consumed"] + sum(d["predicted_units"] for d in a["days"]), 2), 0.01))
check("daily allowances sum to the units still available (not forced to the monthly budget)",
      close(sum(d["allowance"] for d in a["days"]), a["available_for_allocation"], 0.05))

# ----- 5. budget warning when predicted use exceeds the budget
tight = dict(h, monthly_budget=h["units_consumed"] + 5)
a_tight = build_allocation(tight, apps, weather_for(h, lambda i: {"temp_max": 42, "temp_min": 28, "humidity": 60}))
check("predicted over budget is flagged", a_tight["over_budget"] is True)
check("over-budget case gives a critical recommendation with a suggestion",
      any(r["severity"] == "critical" and "Reduce" in r["text"] for r in a_tight["recommendations"]))
a_ok = build_allocation(h, apps, weather_for(h, lambda i: {"temp_max": 26, "temp_min": 18, "humidity": 55}))
check("within-budget case says so", a_ok["over_budget"] is False and any("within budget" in r["text"] for r in a_ok["recommendations"]))

# ----- 6. changing weather or appliances changes the allocation
base = build_allocation(h, apps, weather_for(h, lambda i: {"temp_max": 30, "temp_min": 20, "humidity": 50}))
more = build_allocation(h, apps, weather_for(h, lambda i: {"temp_max": 40, "temp_min": 20, "humidity": 50}))
check("a hotter forecast changes the monthly prediction", more["predicted_month_units"] > base["predicted_month_units"])
extra = apps + [dict(apps[0], id=999, name="Extra AC", quantity=1)]
more_apps = build_allocation(h, extra, weather_for(h, lambda i: {"temp_max": 30, "temp_min": 20, "humidity": 50}))
check("adding an appliance changes the monthly prediction", more_apps["predicted_month_units"] > base["predicted_month_units"])

# ----- 7. live endpoint and the 30-day outlook
live = client.get("/api/allocation?refresh=1").get_json()
check("live allocation endpoint returns days with a source label",
      live["days"] and all(d.get("source_label") for d in live["days"]))
check("outlook covers at most 30 days", live["remaining_days"] <= 30)
check("live totals agree with the dashboard",
      close(client.get("/api/dashboard").get_json()["prediction"]["projected"], live["predicted_month_units"], 0.05),
      f"dashboard {client.get('/api/dashboard').get_json()['prediction']['projected']} vs allocation {live['predicted_month_units']}")

# ----- 8. rule coverage (-10 to 60 Â°C)
rep = rules_engine.coverage_report()
check("rules cover -10 to 60 Â°C", rep["complete"], str(rep["missing"]))

print("\nALL ALLOCATION CHECKS PASSED" if not failures else f"\n{len(failures)} CHECK(S) FAILED: {failures}")
sys.exit(1 if failures else 0)




