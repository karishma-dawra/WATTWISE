"""Personal notifications (create, 24-hour duplicate check, filters, read/delete, automatic triggers) and the appliance
catalog (seed, model-number search, sticker parsing, saving an autofilled appliance).

Run from the project folder with a THROWAWAY database:
    set WATTWISE_DB=%TEMP%\\ww_notif.db
    python tests\\test_notifications_catalog.py
No network is needed.
"""
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if "WATTWISE_DB" not in os.environ:
    sys.exit("Set WATTWISE_DB to a throwaway database path before running these tests.")

import app as application  # noqa: E402  (creates the database, the demo account and the catalog)
import models  # noqa: E402
from database import get_db  # noqa: E402
from services import appliance_catalog as cat  # noqa: E402
from services import notification_service as ns  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  - {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


UID = 1  # the demo account owns the test household
ns._last_run[UID] = datetime.now(timezone.utc)  # keep the 15-minute automatic re-check from adding notifications mid-test


def reset():
    conn = get_db()
    conn.execute("DELETE FROM notifications")
    conn.commit()
    conn.close()


def backdate(nid, hours):
    conn = get_db()
    conn.execute("UPDATE notifications SET created_at = datetime('now', ?) WHERE id = ?", (f"-{hours} hours", nid))
    conn.commit()
    conn.close()


# ------------------------------------------------------------------ notifications: service
reset()
n1 = ns.create_notification(UID, "budget_warning", "warning", "80% used", "msg")
check("a notification is created with is_read false and a UTC timestamp",
      n1 and n1["is_read"] is False and n1["created_at"].endswith("Z") and n1["appliance_id"] is None, str(n1))
check("the same alert inside 24 hours is not created again", ns.create_notification(UID, "budget_warning", "warning", "80% used", "other text") is None)
apps = models.list_appliances()
a0 = apps[0]["id"]
check("same title for a different appliance is a different alert",
      ns.create_notification(UID, "overuse", "warning", "Uses more", "m", appliance_id=a0) is not None
      and ns.create_notification(UID, "overuse", "warning", "Uses more", "m", appliance_id=apps[1]["id"]) is not None
      and ns.create_notification(UID, "overuse", "warning", "Uses more", "m", appliance_id=a0) is None)
backdate(n1["id"], 25)
n1b = ns.create_notification(UID, "budget_warning", "warning", "80% used", "later")
check("after 24 hours the same alert can be created again", n1b is not None and n1b["id"] != n1["id"])
for bad, msg in (({"type": "nope", "severity": "info"}, "type"), ({"type": "info", "severity": "nope"}, "severity")):
    try:
        ns.create_notification(UID, bad["type"], bad["severity"], "t", "m")
        check(f"invalid {msg} is rejected", False)
    except ValueError:
        check(f"invalid {msg} is rejected", True)

reset()
c1 = ns.create_notification(UID, "budget_warning", "critical", "Budget exceeded", "m")
w1 = ns.create_notification(UID, "high_consumption", "warning", "High use", "m")
i1 = ns.create_notification(UID, "info", "info", "Heat ahead", "m")
check("unread count counts every unread notification", ns.unread_count(UID) == 3)
check("severity filter", [n["title"] for n in ns.list_notifications(UID, severity="critical")] == ["Budget exceeded"])
check("newest first", [n["title"] for n in ns.list_notifications(UID)] == ["Heat ahead", "High use", "Budget exceeded"])
check("mark read changes the count and the unread filter", ns.mark_read(UID, c1["id"]) and ns.unread_count(UID) == 2
      and [n["title"] for n in ns.list_notifications(UID, unread=True)] == ["Heat ahead", "High use"])
check("mark read on a missing or foreign id is False", not ns.mark_read(UID, 99999) and not ns.mark_read(2, w1["id"]))
check("mark all read", ns.mark_all_read(UID) == 2 and ns.unread_count(UID) == 0)
check("delete hides it", ns.delete(UID, w1["id"]) and "High use" not in [n["title"] for n in ns.list_notifications(UID)])
check("a deleted alert does not come straight back (24-hour check still applies)",
      ns.create_notification(UID, "high_consumption", "warning", "High use", "m") is None)
check("deleting twice is False", not ns.delete(UID, w1["id"]))

# automatic triggers
reset()
alerts = [{"code": "budget_near", "severity": "warning", "title": "Budget approaching its limit", "message": "80%"},
          {"code": "budget_exceeded", "severity": "critical", "title": "Monthly budget exceeded", "message": "100%"},
          {"code": "on_track", "severity": "info", "title": "You're on track", "message": "ok"},
          {"code": "extreme_heat", "severity": "warning", "title": "Extreme heat ahead", "message": "hot"}]
made = ns.sync_from_alerts(UID, alerts)
by_title = {n["title"]: n for n in made}
check("budget alerts become budget_warning, heat becomes info, on_track is skipped",
      by_title["Budget approaching its limit"]["type"] == "budget_warning" and by_title["Monthly budget exceeded"]["severity"] == "critical"
      and by_title["Extreme heat ahead"]["type"] == "info" and "You're on track" not in by_title and len(made) == 3, str(sorted(by_title)))
check("running the same sync again creates nothing (no duplicates)", ns.sync_from_alerts(UID, alerts) == [])
flat = [{"day": f"2026-11-{d:02d}", "units": 10.0} for d in range(1, 9)]
check("no spike on a steady week", ns.detect_spike(flat) is None)
spiky = flat[:-1] + [{"day": "2026-11-08", "units": 18.0}]
sp = ns.detect_spike(spiky)
check("a day 1.5x above the previous 7-day average is a spike", sp and sp[0] == "warning", str(sp))
check("2x or more is critical", ns.detect_spike(flat[:-1] + [{"day": "2026-11-08", "units": 21.0}])[0] == "critical")
check("too little history is not a spike", ns.detect_spike(spiky[-5:]) is None)
ac = next(a for a in apps if a["name"] == "Air Conditioner")
fake = {"ready": True, "target_date": "2026-11-24", "plan": [
    {"id": ac["id"], "name": "Air Conditioner", "priority": "flexible", "kwh": 12.0}]}
ov = ns.appliance_overuse(apps, fake)
check("an appliance expected to use far more than usual is flagged with its id", len(ov) == 1 and ov[0][0] == ac["id"], str(ov))
check("essential appliances are never flagged", ns.appliance_overuse(apps, {**fake, "plan": [{**fake["plan"][0], "priority": "essential"}]}) == [])

# ------------------------------------------------------------------ notifications: routes
reset()
client = application.app.test_client()
check("notifications need a login (401)", client.get("/api/notifications").status_code == 401)
check("the page needs a login too", client.get("/notifications").status_code == 302)
client.post("/api/auth/login", json={"mobile": "9999999999", "password": "demo1234"})
x = ns.create_notification(UID, "budget_warning", "critical", "Crit", "m")
y = ns.create_notification(UID, "high_consumption", "warning", "Warn", "m")
ns.create_notification(UID, "info", "info", "Info", "m")
r = client.get("/api/notifications").get_json()
check("GET returns the list and the unread count", len(r["notifications"]) == 3 and r["unread_count"] == 3)
check("?severity=critical", [n["title"] for n in client.get("/api/notifications?severity=critical").get_json()["notifications"]] == ["Crit"])
check("a bad severity is a 400", client.get("/api/notifications?severity=bogus").status_code == 400)
check("PATCH read", client.patch(f"/api/notifications/{x['id']}/read").status_code == 200
      and [n["title"] for n in client.get("/api/notifications?unread=true").get_json()["notifications"]] == ["Info", "Warn"])
check("the unread count ignores the filter", client.get("/api/notifications?severity=critical").get_json()["unread_count"] == 2)
check("PATCH read on a missing id is 404", client.patch("/api/notifications/99999/read").status_code == 404)
check("PATCH read-all", client.patch("/api/notifications/read-all").get_json()["updated"] == 2
      and client.get("/api/notifications").get_json()["unread_count"] == 0)
check("DELETE", client.delete(f"/api/notifications/{y['id']}").status_code == 200
      and client.delete(f"/api/notifications/{y['id']}").status_code == 404
      and len(client.get("/api/notifications").get_json()["notifications"]) == 2)
other = application.app.test_client()
reg = other.post("/api/auth/register", json={"full_name": "Other User", "mobile": "9876501234", "password": "otherpass1", "city": "", "language": "en"})
other.post("/api/auth/login", json={"mobile": "9876501234", "password": "otherpass1"})
check("another account sees none of these", other.get("/api/notifications").get_json() == {"notifications": [], "unread_count": 0}, str(reg.status_code))
check("another account cannot read or delete them", other.patch(f"/api/notifications/{x['id']}/read").status_code == 404
      and other.delete(f"/api/notifications/{x['id']}").status_code == 404)

# an alert condition raised by real data creates the notification by itself: a reading far above the budget
reset()
h = models.get_household()
r = client.post("/api/readings", json={"reading_date": models.today().isoformat(), "units_consumed": h["monthly_budget"] + 20})
items = client.get("/api/notifications").get_json()["notifications"]
check("a reading over the budget creates a critical budget notification by itself",
      r.status_code == 201 and any(n["type"] == "budget_warning" and n["severity"] == "critical" for n in items),
      f"{r.status_code} {[(n['type'], n['severity'], n['title']) for n in items]}")
client.get("/api/alerts")
check("re-checking alerts does not duplicate it", len([n for n in client.get("/api/notifications").get_json()["notifications"] if n["title"] == "Monthly budget exceeded"]) == 1)

# ------------------------------------------------------------------ catalog
conn = get_db()
n_rows = conn.execute("SELECT COUNT(*) FROM appliances_catalog").fetchone()[0]
types = {r[0] for r in conn.execute("SELECT DISTINCT type FROM appliances_catalog")}
check("the catalog has at least 30 appliances", n_rows >= 30, str(n_rows))
check("every catalog type maps to a form category", types <= set(cat.TYPE_DEFAULTS), str(types - set(cat.TYPE_DEFAULTS)))
check("wattages are realistic (1 W to 3.5 kW)", conn.execute("SELECT COUNT(*) FROM appliances_catalog WHERE wattage < 1 OR wattage > 3500").fetchone()[0] == 0)
cat.seed(conn)
conn.commit()
check("seeding twice does not duplicate rows", conn.execute("SELECT COUNT(*) FROM appliances_catalog").fetchone()[0] == n_rows)
want = {"Ceiling Fan", "Air Conditioner", "Refrigerator", "Geyser", "Television", "Washing Machine", "LED Bulb"}
check("the types the spec names are all there", want <= types, str(want - types))

exact, rows = cat.search(conn, "ps-q19 ynze")
check("exact match ignores case, spaces and dashes", exact and exact["brand"] == "LG" and exact["type"] == "Air Conditioner", str(exact))
check("a prefix returns suggestions but no exact match", cat.search(conn, "STEALTH")[0] is None and len(cat.search(conn, "STEALTH")[1]) >= 1)
check("a very short query returns nothing", cat.search(conn, "ab") == (None, []))
check("an unknown model has no match", cat.search(conn, "ZZ-NOT-A-MODEL") == (None, []))

LG = """LG Electronics   SPLIT AIR CONDITIONER
Model: PS-Q19YNZE      BEE Star Rating: 5
Rated Power 1300 W   220-240 V~  50 Hz"""
out = cat.parse_sticker(conn, LG)
f = out["fields"]
check("sticker: model, brand, watts, voltage, frequency and stars are read",
      f.get("model_number") == "PS-Q19YNZE" and f.get("brand") == "LG" and f.get("wattage") == 1300 and f.get("voltage") == 230
      and f.get("frequency") == 50 and f.get("star_rating") == 5, str(f))
check("sticker: a catalog match fills the whole form", out["match"] and out["form"]["name"] == "LG Air Conditioner"
      and out["form"]["weather_type"] == "cooling" and out["form"]["category"] == "Cooling" and out["form"]["wattage"] == 1300, str(out["form"]))
unknown = cat.parse_sticker(conn, "XYZ Appliances\nModel No: ZX-900-Q\nPower: 750W 230V 50Hz\n3 Star")
fu = unknown["fields"]
check("sticker without a catalog match: only what is printed is returned",
      unknown["match"] is None and unknown["form"] is None and fu == {"wattage": 750, "voltage": 230, "frequency": 50, "star_rating": 3, "model_number": "ZX-900-Q"}
      and unknown["message"], str(unknown))
check("sticker: kW is converted to W", cat.parse_sticker(conn, "Input Power 1.5 kW 230V")["fields"]["wattage"] == 1500)
check("sticker: a power label beats a bigger unrelated number",
      cat.parse_sticker(conn, "Capacity 2000 W max heater element\nRated Power: 1200 W")["fields"]["wattage"] == 1200)
check("sticker: a bare model token still finds the catalog row", cat.parse_sticker(conn, "SAMSUNG some text RT28C3733S8 more")["match"]["brand"] == "Samsung")
check("sticker: unreadable text gives no fields", cat.parse_sticker(conn, "lorem ipsum dolor")["fields"] == {})
conn.close()

check("route: search finds a model", client.get("/api/catalog/search?q=ps-q19ynze").get_json()["form"]["wattage"] == 1300)
nf = client.get("/api/catalog/search?q=zz-not-a-model").get_json()
check("route: an unknown model says so", nf["match"] is None and nf["form"] is None and "manually" in nf["message"], str(nf))
check("route: parse-sticker works", client.post("/api/catalog/parse-sticker", json={"text": LG}).get_json()["match"]["model_number"] == "PS-Q19YNZE")
check("route: parse-sticker rejects empty text", client.post("/api/catalog/parse-sticker", json={"text": "  "}).status_code == 400)
check("route: the catalog needs a login", application.app.test_client().get("/api/catalog/search?q=abc").status_code == 401)

# saving an autofilled appliance feeds the normal calculation (and so the alerts)
form = client.get("/api/catalog/search?q=RT28C3733S8").get_json()["form"]
before = client.get("/api/appliances/summary").get_json()["total_daily_kwh"]
saved = client.post("/api/appliances", json={**form, "usage_hours": 24})
after = client.get("/api/appliances/summary").get_json()
check("a saved autofilled appliance counts in the daily total with its catalog wattage",
      saved.status_code == 201 and abs(after["total_daily_kwh"] - before) > 0.1
      and any(a["name"] == "Samsung Refrigerator" and a["wattage"] == 150 for a in after["appliances"]), str(saved.get_json()))

print("\nALL NOTIFICATION AND CATALOG CHECKS PASSED" if not failures else f"\n{len(failures)} CHECK(S) FAILED: {failures}")
sys.exit(1 if failures else 0)
