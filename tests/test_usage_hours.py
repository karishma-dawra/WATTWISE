"""Optional usage hours: a value entered by the user is a MINIMUM daily run time.

Run from the project folder with a THROWAWAY database:
    set WATTWISE_DB=%TEMP%\\ww_usage.db
    python tests\\test_usage_hours.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if "WATTWISE_DB" not in os.environ:
    sys.exit("Set WATTWISE_DB to a throwaway database path before running these tests.")

import app as application  # noqa: E402
import models  # noqa: E402
from services.consumption import predicted_kwh  # noqa: E402
from services.prediction_service import cycle_info  # noqa: E402
from services.weather_energy_engine import build_daily_demand  # noqa: E402

client = application.app.test_client()
login = client.post("/api/auth/login", json={"mobile": "9999999999", "password": "demo1234"})
assert login.status_code == 200, "demo login failed"

failures = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def by_id(items, aid):
    return next(x for x in items if x["id"] == aid)


# 1. optional: blank -> estimate, entered -> used
r = client.post("/api/appliances", json={"name": "Air Conditioner", "category": "Cooling", "wattage": 1500, "quantity": 1,
                                          "priority": "flexible", "weather_type": "cooling"})
ac_blank = r.get_json()
check("blank usage hours is accepted", r.status_code == 201, str(r.get_json()))
check("blank usage hours -> estimate is used", ac_blank["user_hours"] is None and ac_blank["daily_hours"] == 6.0,
      f"user={ac_blank['user_hours']} daily={ac_blank['daily_hours']}")
client.delete(f"/api/appliances/{ac_blank['id']}")

r = client.post("/api/appliances", json={"name": "Air Conditioner", "category": "Cooling", "wattage": 1500, "quantity": 1,
                                          "priority": "flexible", "weather_type": "cooling", "usage_hours": 5})
ac = r.get_json()
check("entered usage hours are stored", r.status_code == 201 and ac["user_hours"] == 5.0, str(r.get_json()))
check("entered hours flagged as the user's entry", ac["usage_hours_entered"] is True)

# 2. validation
for bad in (0.1, 25, -2, "abc"):
    rr = client.post("/api/appliances", json={"name": "Test Heater", "category": "Heating", "wattage": 1000, "quantity": 1,
                                               "priority": "flexible", "weather_type": "heating", "usage_hours": bad})
    check(f"usage hours {bad!r} rejected", rr.status_code == 400, str(rr.status_code))

# 3. the minimum holds on a mild day, where the weather alone would switch the AC off
h = models.get_household()
cyc = cycle_info(h)
mild = {"available": True, "forecast": [{"day": d.isoformat(), "temp_max": 22, "temp_min": 14, "condition": "x",
                                         "humidity": 50, "rain_mm": 0, "rain_chance": 0, "source": "forecast"}
                                        for d in cyc["remaining_dates"]], "climatology": []}
apps = models.list_appliances()
days = build_daily_demand(h, apps, mild, cyc)
ac_items = [by_id(d["items"], ac["id"]) for d in days]  # the new AC (the demo household also has one)
check("weather alone would give 0 h of AC on this mild day (so the minimum is what matters)",
      all(a["hours"] >= 5 - 1e-9 for a in ac_items))
check("AC runs at least the entered 5 hours every day", min(a["hours"] for a in ac_items) >= 5 - 1e-9,
      f"min={min(a['hours'] for a in ac_items)}")
ac_kwh = [a["kwh"] for a in ac_items]
ac_app = models.get_appliance(ac["id"])
check("its kWh is at least the minimum hours at its rated power", all(k >= predicted_kwh(ac_app, 5) - 1e-6 for k in ac_kwh))

# 4. the minimum also holds in the advisor plan, and is shown there
plan = client.get("/api/recommendations").get_json()
ac_plan = by_id(plan["plan"], ac["id"])
check("advisor plan keeps at least the entered hours", ac_plan["hours"] >= 5 - 1e-9, str(ac_plan["hours"]))
check("advisor plan shows the entered minimum", ac_plan.get("min_hours") == 5.0)

# 5. a minimum above the normal daily cap is kept (the user chose it)
r = client.put(f"/api/appliances/{ac['id']}", json={"usage_hours": 16})
check("updating usage hours to 16 h is accepted", r.status_code == 200 and r.get_json()["user_hours"] == 16.0)
plan16 = client.get("/api/recommendations").get_json()
check("a 16 h minimum is not cut to the normal AC cap (14 h)", by_id(plan16["plan"], ac["id"])["hours"] >= 16 - 1e-9,
      str(by_id(plan16["plan"], ac["id"])["hours"]))

# 6. clearing the field returns to the estimate
r = client.put(f"/api/appliances/{ac['id']}", json={"usage_hours": ""})
check("clearing usage hours reverts to the estimate",
      r.status_code == 200 and r.get_json()["user_hours"] is None and r.get_json()["daily_hours"] == 6.0,
      str(r.get_json()))

# 7. a minimum the budget cannot cover is flagged, never hidden
client.put(f"/api/appliances/{ac['id']}", json={"usage_hours": 12})
client.put("/api/meter", json={"previous_reading": 1000, "today_reading": 1095, "monthly_budget": 100})
tight = client.get("/api/recommendations").get_json()
check("a minimum the budget cannot cover gives a warning", tight.get("within_budget") is False and bool(tight.get("warning")),
      str(tight.get("warning")))
client.put("/api/meter", json={"previous_reading": 1000, "today_reading": 1010, "monthly_budget": 600})

print("\nALL USAGE-HOURS CHECKS PASSED" if not failures else f"\n{len(failures)} CHECK(S) FAILED: {failures}")
sys.exit(1 if failures else 0)
