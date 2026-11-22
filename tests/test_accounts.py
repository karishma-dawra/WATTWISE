"""Multi-user account tests: registration, login, logout, isolation, profile persistence, recovery, password storage.

Run from the project folder with a THROWAWAY database:
    set WATTWISE_DB=%TEMP%\\ww_accounts.db
    python tests\\test_accounts.py
"""
import os
import sqlite3
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if "WATTWISE_DB" not in os.environ:
    sys.exit("Set WATTWISE_DB to a throwaway database path before running these tests.")

import app as application  # noqa: E402
from database import DB_PATH  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


def new_client():
    return application.app.test_client()


def j(resp):
    try:
        return resp.get_json() or {}
    except Exception:
        return {}


REG_A = {"full_name": "Asha Sharma", "mobile": "9876543210", "password": "asha-pass", "email": "asha@example.com",
         "city": "Ludhiana", "language": "en"}
REG_B = {"full_name": "Ravi Kumar", "mobile": "9123456780", "password": "ravi-pass", "email": "",
         "city": "Jaipur", "language": "en"}

# ------------------------------------------------ protection before login
anon = new_client()
check("API needs login (401)", anon.get("/api/household").status_code == 401)
check("protected page redirects to login", anon.get("/dashboard").status_code == 302 and "/login" in anon.get("/dashboard").headers.get("Location", ""))
check("public login page loads", anon.get("/login").status_code == 200)
check("public register page loads", anon.get("/register").status_code == 200)

# ------------------------------------------------ registration rules
r = anon.post("/api/auth/register", json={**REG_A, "mobile": "12345"})
check("invalid mobile rejected", r.status_code == 400, j(r).get("error"))
r = anon.post("/api/auth/register", json={**REG_A, "password": "123"})
check("short password rejected", r.status_code == 400, j(r).get("error"))
r = anon.post("/api/auth/register", json={**REG_A, "email": "not-an-email"})
check("bad email rejected", r.status_code == 400, j(r).get("error"))

ua = new_client()
r = ua.post("/api/auth/register", json=REG_A)
check("register user A", r.status_code == 201 and j(r)["user"]["full_name"] == "Asha Sharma", j(r).get("error"))
ua_id = j(r)["user"]["id"]
check("registration logs the new user in", ua.get("/api/auth/me").get_json()["user"]["id"] == ua_id)

ub = new_client()
r = ub.post("/api/auth/register", json=REG_B)
check("register user B (second independent account)", r.status_code == 201)
ub_id = j(r)["user"]["id"]
check("accounts have different ids", ua_id != ub_id)

r = new_client().post("/api/auth/register", json={**REG_B, "mobile": "+91 98765 43210", "full_name": "Dup"})
check("duplicate mobile (any format) is rejected", r.status_code == 400 and "already registered" in j(r).get("error", ""), j(r).get("error"))

# ------------------------------------------------ login / logout / switching
x = new_client()
r = x.post("/api/auth/login", json={"mobile": REG_A["mobile"], "password": "wrong-password"})
check("wrong password rejected", r.status_code == 401)
r = x.post("/api/auth/login", json={"mobile": "9000000000", "password": "whatever1"})
check("unknown mobile rejected with same message", r.status_code == 401)
r = x.post("/api/auth/login", json={"mobile": REG_A["mobile"], "password": REG_A["password"]})
check("login with mobile + password", r.status_code == 200 and j(r)["user"]["full_name"] == "Asha Sharma")
check("logged-in user can read profile", x.get("/api/profile").status_code == 200)
x.post("/api/auth/logout")
check("logout ends the session", x.get("/api/household").status_code == 401)

# ------------------------------------------------ per-account data and isolation
ua.put("/api/meter", json={"previous_reading": 1000, "today_reading": 1120, "monthly_budget": 250})
ua.post("/api/appliances", json={"name": "Ceiling Fan", "category": "Cooling", "wattage": 75, "quantity": 3,
                                  "priority": "essential", "weather_type": "none"})
ub.put("/api/meter", json={"previous_reading": 500, "today_reading": 530, "monthly_budget": 400})

ha, hb = ua.get("/api/household").get_json(), ub.get("/api/household").get_json()
check("user A sees A's budget (250)", ha["monthly_budget"] == 250, str(ha["monthly_budget"]))
check("user B sees B's budget (400), not A's", hb["monthly_budget"] == 400, str(hb["monthly_budget"]))
check("user A's units are A's own (120)", abs(ha["units_consumed"] - 120) < 0.01)
check("user B's units are B's own (30)", abs(hb["units_consumed"] - 30) < 0.01)
check("user A's appliances are not visible to B", len(ua.get("/api/appliances").get_json()) == 1 and
      len(ub.get("/api/appliances").get_json()) == 0)
check("B cannot change A's appliance", ub.put(f"/api/appliances/{ua.get('/api/appliances').get_json()[0]['id']}",
                                             json={"quantity": 9}).status_code == 404)
check("B's weather/dashboard shows B's numbers",
      abs(ub.get("/api/dashboard").get_json()["prediction"]["consumed"] - 30) < 0.01)

# ------------------------------------------------ persistence after login
x2 = new_client()
x2.post("/api/auth/login", json={"mobile": REG_A["mobile"], "password": REG_A["password"]})
check("A's saved budget is restored after login", x2.get("/api/household").get_json()["monthly_budget"] == 250)
check("A's appliance is restored after login", len(x2.get("/api/appliances").get_json()) == 1)

# ------------------------------------------------ profile
r = x2.put("/api/profile/personal", json={"full_name": "Asha S.", "mobile": REG_A["mobile"], "email": "asha.s@example.com", "city": "Chandigarh"})
check("personal profile saves", r.status_code == 200 and j(r)["personal"]["city"] == "Chandigarh")
r = x2.put("/api/profile/personal", json={"mobile": REG_B["mobile"]})
check("changing to another account's mobile is rejected", r.status_code == 400, j(r).get("error"))
r = x2.put("/api/profile/household", json={"meter_type": "Hologram"})
check("invalid meter type rejected", r.status_code == 400)
r = x2.put("/api/profile/household", json={"meter_type": "Digital", "connection_type": "Commercial", "provider": "PSPCL",
                                            "consumer_number": "1234567890", "sanctioned_load": 5, "home_type": "Independent House",
                                            "rooms": 4, "floors": 2, "family_size": 5, "bill_date": "15"})
check("meter and household details save", r.status_code == 200, j(r).get("error"))
x2.post("/api/auth/logout")
x3 = new_client(); x3.post("/api/auth/login", json={"mobile": REG_A["mobile"], "password": REG_A["password"]})
hh = x3.get("/api/profile").get_json()["household"]
check("profile details restored after relogin", hh["provider"] == "PSPCL" and hh["consumer_number"] == "1234567890" and hh["family_size"] == 5,
      str({k: hh.get(k) for k in ("provider", "consumer_number", "family_size")}))
check("personal details restored after relogin", x3.get("/api/profile").get_json()["personal"]["city"] == "Chandigarh")

# ------------------------------------------------ language is per account
ub2 = new_client(); ub2.post("/api/auth/login", json={"mobile": REG_B["mobile"], "password": REG_B["password"]})
r = ub2.post("/api/lang", json={"language": "hi"})
check("language switch saves for B", r.status_code == 200)
ub2.post("/api/auth/logout")
ub3 = new_client(); ub3.post("/api/auth/login", json={"mobile": REG_B["mobile"], "password": REG_B["password"]})
check("B's Hindi preference restored after login", ub3.get("/api/auth/me").get_json()["language"] == "hi")
check("A is unaffected (still English)", x3.get("/api/auth/me").get_json()["language"] == "en")

# ------------------------------------------------ recovery and password storage
rc = new_client()
r = rc.post("/api/auth/recover", json={"mobile": REG_B["mobile"], "city": "Delhi", "new_password": "new-ravi-pass"})
check("recovery with the wrong city is rejected", r.status_code == 400)
r = rc.post("/api/auth/recover", json={"mobile": REG_B["mobile"], "city": "jaipur", "new_password": "new-ravi-pass"})
check("recovery with the registered city works", r.status_code == 200)
check("old password no longer works", new_client().post("/api/auth/login", json={"mobile": REG_B["mobile"], "password": REG_B["password"]}).status_code == 401)
check("new password works", new_client().post("/api/auth/login", json={"mobile": REG_B["mobile"], "password": "new-ravi-pass"}).status_code == 200)
con = sqlite3.connect(DB_PATH)
rows = con.execute("SELECT password_hash FROM users").fetchall(); con.close()
check("passwords are stored hashed, never in plain text",
      all(r[0] and "asha-pass" not in r[0] and "ravi-pass" not in r[0] and ("scrypt" in r[0] or "pbkdf2" in r[0]) for r in rows))

# ------------------------------------------------ demo account
demo = new_client()
r = demo.post("/api/auth/login", json={"mobile": "9999999999", "password": "demo1234"})
check("demo account works", r.status_code == 200)

print("\nALL ACCOUNT CHECKS PASSED" if not failures else f"\n{len(failures)} CHECK(S) FAILED: {failures}")
sys.exit(1 if failures else 0)
