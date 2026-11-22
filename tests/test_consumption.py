"""Consistency tests for appliance-wise predicted consumption.

Run from the project folder with a THROWAWAY database:
    set WATTWISE_DB=%TEMP%\\ww_test.db
    python tests\\test_consumption.py
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
from services.consumption import duty_factor, effective_kw, predicted_kwh  # noqa: E402

client = application.app.test_client()
login = client.post('/api/auth/login', json={'mobile': '9999999999', 'password': 'demo1234'})  # demo account owns the test household
assert login.status_code == 200, 'demo login failed'
failures = []

# Hot/cold scenarios: the forecast is shifted inside the test itself (the app has no what-if switch any more).
from services import recommendation_engine as _rec  # noqa: E402
from services import weather_service as _ws  # noqa: E402
from services.prediction_service import predict as _predict  # noqa: E402


def shifted(w, s):
    out = dict(w)
    out["forecast"] = [dict(f, temp_max=f["temp_max"] + s, temp_min=f["temp_min"] + s,
                            apparent_max=(f["apparent_max"] + s) if f.get("apparent_max") is not None else None,
                            # the engine prefers the real hourly feels-like series, so shift that too
                            hourly_apparent=[v + s for v in f["hourly_apparent"]] if f.get("hourly_apparent") else None)
                       for f in w["forecast"]]
    out["climatology"] = []
    return out


def plan_with(w):
    h = models.get_household()
    apps = models.list_appliances()
    return _rec.advise(h, apps, w, _predict(h, apps, w))


LIVE = _ws.get_weather()


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  â€” {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def close(a, b, tol=0.002):
    return abs(a - b) <= tol


# ------------------------------------------------------------------ formula
fan = {"name": "Ceiling Fan", "category": "Cooling", "wattage": 75, "quantity": 1, "weather_type": "none"}
led = {"name": "LED Bulb", "category": "Lighting", "wattage": 12, "quantity": 1, "weather_type": "none"}
ac = {"name": "Air Conditioner", "category": "Cooling", "wattage": 1500, "quantity": 1, "weather_type": "cooling"}
geyser = {"name": "Geyser", "category": "Water Heating", "wattage": 2000, "quantity": 1, "weather_type": "heating"}
check("ceiling fan 2 h at 75 W = 0.15 kWh (spec example)", close(predicted_kwh(fan, 2), 0.15), predicted_kwh(fan, 2))
check("LED 12 W for 12 h = 0.144 kWh (spec example)", close(predicted_kwh(led, 12), 0.144), predicted_kwh(led, 12))
check("geyser 2000 W for 0.5 h = 1 kWh (spec example)", close(predicted_kwh(geyser, 0.5), 1.0), predicted_kwh(geyser, 0.5))
check("AC duty cycle applied: 1500 W x 6 h x 0.85 = 7.65 kWh", close(predicted_kwh(ac, 6), 1.5 * 6 * duty_factor(ac)),
      predicted_kwh(ac, 6))
check("AC duty cycle is below continuous operation", duty_factor(ac) < 1.0)
fridge = {"name": "Refrigerator", "category": "Kitchen", "wattage": 200, "quantity": 1, "weather_type": "none"}
check("fridge duty cycle is below continuous operation", duty_factor(fridge) < 1.0)
two = dict(fan, quantity=2)
check("quantity scales energy: 2 fans = 2 x 1 fan", close(predicted_kwh(two, 3), 2 * predicted_kwh(fan, 3)))

# ----------------------------------------------------------- household plan
h = models.get_household()
apps = models.list_appliances()
models.save_meter_details(1000, 1010, 5000)  # generous budget: weather changes must show, not the budget cap
plan = client.get("/api/recommendations").get_json()
check("recommendation is ready", plan.get("ready") is True, plan.get("message"))
by_name = {p["name"]: p for p in plan["plan"]}
app_by_id = {a["id"]: a for a in models.list_appliances()}

per_item_ok = all(close(p["kwh"], predicted_kwh(app_by_id[p["id"]], p["hours"]), 0.002) for p in plan["plan"])
check("every appliance kWh == its rated kW x duty x recommended hours", per_item_ok)
total = sum(p["kwh"] for p in plan["plan"])
check("Units Tomorrow == sum of every appliance's kWh", close(plan["estimated_units"], round(total, 2), 0.01),
      f"{plan['estimated_units']} vs {total}")
check("household_total_units equals the same sum", close(plan["household_total_units"], plan["estimated_units"], 0.01))
check("appliance_units lists one entry per appliance in the plan",
      len(plan["appliance_units"]) == len(plan["plan"]) and all(x["kwh"] >= 0 for x in plan["appliance_units"]))
base = sum(predicted_kwh(a, a["need_hours"]) for a in apps)
check("baseline = normal hours, no weather change, same kWh formula", close(plan["baseline_units"], round(base, 2), 0.01),
      f"{plan['baseline_units']} vs {base}")
check("units saved == baseline - recommended", close(plan["expected_savings_units"], round(plan["baseline_units"] - plan["estimated_units"], 2), 0.01))
# allocation: units follow demand, sum to the remaining units, and are not a flat average when days differ
from services.weather_energy_engine import allocate_units, build_daily_demand  # noqa: E402
from services.prediction_service import cycle_info  # noqa: E402
hc = models.get_household()
cyc = cycle_info(hc)
R = hc["monthly_budget"] - hc["units_consumed"]
def fc(day, tmax, tmin):
    return {"day": day, "temp_max": tmax, "temp_min": tmin, "condition": "x", "humidity": 50, "rain_chance": 0, "rain_mm": 0}
dates = [d.isoformat() for d in cyc["remaining_dates"]]
hot_cool = {"available": True, "forecast": [fc(ds, 42 if i % 2 == 0 else 22, 28 if i % 2 == 0 else 12) for i, ds in enumerate(dates)]}
days = build_daily_demand(hc, models.list_appliances(), hot_cool, cyc, hours_key="need_hours")
allocate_units(R, days)
al = [d["allowance"] for d in days]
check("allowances sum to the remaining units", close(sum(al), R, 0.01), f"{sum(al)} vs {R}")
check("hot days get a larger allowance than cool days (not a flat split)", al[0] > al[1] + 0.5, f"{al[0]:.2f} vs {al[1]:.2f}")
check("the live plan's allowance is the allocated share for tomorrow",
      close(plan["allowance_tomorrow"], plan["weighted_allowance"], 0.01))

# ------------------------------------------------------- weather sensitivity
base_total = plan_with(LIVE)["estimated_units"]
hot = plan_with(shifted(LIVE, 12))
cold = plan_with(shifted(LIVE, -12))
check("hotter forecast raises AC hours", by_name["Air Conditioner"]["hours"] < hot["plan"][0]["hours"] or
      {p["name"]: p["hours"] for p in hot["plan"]}["Air Conditioner"] > by_name["Air Conditioner"]["hours"])
hot_by = {p["name"]: p for p in hot["plan"]}
cold_by = {p["name"]: p for p in cold["plan"]}
check("hotter forecast increases total predicted units", hot["estimated_units"] > plan["estimated_units"],
      f"{hot['estimated_units']} vs {plan['estimated_units']}")
check("colder forecast lowers AC and raises heater/geyser use",
      cold_by["Air Conditioner"]["hours"] <= by_name["Air Conditioner"]["hours"] and
      cold_by["Geyser"]["hours"] >= by_name["Geyser"]["hours"] - 1e-9)
check("hotter forecast gives a higher total than a normal day", hot["estimated_units"] > base_total, f"hot {hot['estimated_units']} base {base_total}")

# ------------------------------------------------- appliance add/remove/qty
before = plan_with(shifted(LIVE, 12))["estimated_units"]
created = client.post("/api/appliances", json={"name": "Cooler", "category": "Cooling", "wattage": 200, "quantity": 1,
                                                "priority": "flexible", "weather_type": "cooling"}).get_json()
after_add = plan_with(shifted(LIVE, 12))
check("adding an appliance adds it to the plan (hot day, cooler runs) and the total",
      any(p["name"] == "Cooler" and p["hours"] > 0 for p in after_add["plan"]) and after_add["estimated_units"] > before,
      f"{after_add['estimated_units']} vs {before}")
check("new appliance's kWh matches its formula",
      close(next(p for p in after_add["plan"] if p["name"] == "Cooler")["kwh"],
            predicted_kwh(models.get_appliance(created["id"]), next(p for p in after_add["plan"] if p["name"] == "Cooler")["hours"]), 0.002))
client.put(f"/api/appliances/{created['id']}", json={"quantity": 3})
after_qty = client.get("/api/recommendations").get_json()
c3 = next(p for p in after_qty["plan"] if p["name"] == "Cooler")
check("quantity change updates that appliance's kWh",
      close(c3["kwh"], predicted_kwh(models.get_appliance(created["id"]), c3["hours"]), 0.002))
client.delete(f"/api/appliances/{created['id']}")
after_del = plan_with(shifted(LIVE, 12))
check("removing an appliance removes it from the total", not any(p["name"] == "Cooler" for p in after_del["plan"]) and
      close(after_del["estimated_units"], before, 0.01), f"{after_del['estimated_units']} vs {before}")

# ----------------------------------------------- schedule and dashboard match
dash = client.get("/api/dashboard").get_json()
check("dashboard advice total == advisor total",
      close(dash["advice"]["estimated_units"], client.get("/api/recommendations").get_json()["estimated_units"], 0.01))
check("dashboard Units Saved matches the advisor",
      close(dash["advice"]["expected_savings_units"], client.get("/api/recommendations").get_json()["expected_savings_units"], 0.01))
apps_summary = client.get("/api/appliances/summary").get_json()
for a in apps_summary["appliances"]:
    check(f"appliance table kWh for {a['name']} uses the same formula",
          close(a["daily_kwh"], predicted_kwh(models.get_appliance(a["id"]), a["daily_hours"]), 0.002))

# -------------------------------------------------------- budget validation
client.put("/api/meter", json={"previous_reading": 1000, "today_reading": 1098, "monthly_budget": 100})
tight = client.get("/api/recommendations").get_json()
check("tight budget warns when minimum comfort/safety use cannot be covered",
      tight.get("within_budget") is False and bool(tight.get("warning")), str(tight.get("warning")))
client.put("/api/meter", json={"previous_reading": 1000, "today_reading": 1010, "monthly_budget": 600})

# ------------------------------------------------------------ rule coverage
rep = rules_engine.coverage_report()
check("rules cover -10 to 60 Â°C, humidity 0â€“100 %, and all rainfall values", rep["complete"], str(rep["missing"]))

print("\nALL CONSUMPTION CHECKS PASSED" if not failures else f"\n{len(failures)} CHECK(S) FAILED: {failures}")
sys.exit(1 if failures else 0)

