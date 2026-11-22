"""Run times: hour-by-hour split of each appliance, fans at the warm hours, AC off on a 24 degree day.

Run from the project folder with a THROWAWAY database:
    set WATTWISE_DB=%TEMP%\\ww_schedule.db
    python tests\\test_schedule.py
"""
import os
import sys
from datetime import date, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if "WATTWISE_DB" not in os.environ:
    sys.exit("Set WATTWISE_DB to a throwaway database path before running these tests.")

import app  # noqa: E402,F401  (creates the database and the demo account)
import models  # noqa: E402
from services.weather_energy_engine import allocate_units, build_daily_demand, schedule_windows  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  - {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


START = date(2026, 11, 23)
CYC = {"remaining_dates": [START + timedelta(i) for i in range(3)]}


def weather(tmax, tmin, humidity=50):
    return {"available": True, "climatology": [], "forecast": [
        {"day": d.isoformat(), "temp_max": tmax, "temp_min": tmin, "humidity": humidity, "rain_mm": 0.0,
         "rain_chance": 0, "apparent_max": None, "apparent_min": None, "condition": "test", "source": "forecast"}
        for d in CYC["remaining_dates"]]}


def item(day, name):
    return next(i for i in day["items"] if i["name"] == name)


h = models.get_household()
apps = models.list_appliances()

# 1. 24 degrees all day: AC off, fan still runs at the warm hours
d24 = build_daily_demand(h, apps, weather(24, 14), CYC)[0]
ac = item(d24, "Air Conditioner")
fan = item(d24, "Ceiling Fan")
check("24 degree day: AC gets 0 hours and no run time", ac["hours"] == 0 and schedule_windows(ac["hourly"]) == [],
      f"hours={ac['hours']}")
check("24 degree day: fan still runs at the warm hours", fan["hours"] > 0 and len(schedule_windows(fan["hourly"])) > 0,
      f"hours={fan['hours']} windows={schedule_windows(fan['hourly'])}")
peak = [round(v, 2) for v in fan["hourly"]]
check("fan's run time sits in the afternoon, not at night",
      sum(fan["hourly"][12:19]) > sum(fan["hourly"][0:8]) + sum(fan["hourly"][20:24]),
      f"afternoon={sum(fan['hourly'][12:19]):.2f} night={sum(fan['hourly'][0:8]) + sum(fan['hourly'][20:24]):.2f}")

# 2. 38 degrees all day: AC reaches its maximum, spread over the day
d38 = build_daily_demand(h, apps, weather(38, 38), CYC)[0]
ac38 = item(d38, "Air Conditioner")
check("38 degree day: AC reaches its 14 h maximum", abs(ac38["hours"] - 14.0) < 1e-6, f"hours={ac38['hours']}")
check("hourly profile adds up to the day's hours", abs(sum(ac38["hourly"]) - ac38["hours"]) < 1e-6)
check("38 degree day: AC run time is listed", len(schedule_windows(ac38["hourly"])) > 0)

# 3. run windows are formatted and cover only the hours used
w = schedule_windows([0] * 12 + [1] * 3 + [0] * 9)
check("windows read as start-end ranges", w == ["12:00-15:00"], str(w))
check("hours below 3 minutes are not shown", schedule_windows([0.02] * 24) == [])

# 4. a shortfall keeps the hourly profile consistent with the reduced hours
days = build_daily_demand(h, apps, weather(38, 26), CYC)
fixed = sum(x["fixed_kwh"] for x in days)
allocate_units(fixed + 30.0, days)
ac_after = item(days[0], "Air Conditioner")
check("after shortfall scaling, hourly profile still matches the hours",
      abs(sum(ac_after["hourly"]) - ac_after["hours"]) < 1e-6,
      f"sum={sum(ac_after['hourly']):.4f} hours={ac_after['hours']:.4f}")
check("after shortfall scaling, the allowances still add up to the units left",
      abs(sum(x["allowance"] for x in days) - (fixed + 30.0)) < 1e-6)

print("\nALL SCHEDULE CHECKS PASSED" if not failures else f"\n{len(failures)} CHECK(S) FAILED: {failures}")
sys.exit(1 if failures else 0)
