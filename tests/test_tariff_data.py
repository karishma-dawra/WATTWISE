"""State tariffs and subsidies (tariff_data.json): unknown values stay unknown, only complete tariffs are applied.

Run from the project folder with a THROWAWAY database:
    set WATTWISE_DB=%TEMP%\\ww_tariff.db
    python tests\\test_tariff_data.py
No network is needed.
"""
import copy
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
if "WATTWISE_DB" not in os.environ:
    sys.exit("Set WATTWISE_DB to a throwaway database path before running these tests.")

import app as application  # noqa: E402  (creates the database and the demo account)
import models  # noqa: E402
from services import region_service, tariff_data  # noqa: E402
from services.bill_calculator import energy_charge  # noqa: E402

failures = []


def check(name, ok, detail=""):
    print(("PASS  " if ok else "FAIL  ") + name + (f"  - {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(name)


NV = "Rates not verified yet"
raw = json.load(open(tariff_data.DEFAULT_PATH, encoding="utf-8"))
data = tariff_data.load(force=True)

# ------------------------------------------------------------------ the file
check("the not-verified text is the one the file asks for", tariff_data.NOT_VERIFIED == NV)
check("all five states and the central scheme load", [s["state"] for s in data["states"]] == ["Punjab", "Haryana", "Rajasthan", "Himachal Pradesh", "Bihar"]
      and [s["scheme_name"] for s in data["central_subsidies"]] == ["PM Surya Ghar Muft Bijli Yojana"])
check("the loader reports the file as healthy", tariff_data.status()["ok"])

pb = tariff_data.state_profile("Punjab")
check("Punjab: slabs convert to the app's format and the flat rate is the first slab", pb["slabs"] == [[300, 3.85], [None, 7.05]] and pb["tariff"] == 3.85, str(pb["slabs"]))
check("Punjab can be applied but is only partially verified", pb["can_apply"] and pb["verification_status"] == "partial" and pb["status_label"] == "Partially verified")
check("Punjab: fixed charge, fuel surcharge and duty stay unknown (None, never 0)",
      pb["fixed_charges"] is None and pb["duty_percent"] is None and pb["fuel_surcharge_pct"] is None
      and pb["unverified"] == ["fixed_charge", "fuel_surcharge_pct", "electricity_duty_pct"], str(pb["unverified"]))
check("Punjab: unknown charges read 'Rates not verified yet'", all(v == NV for v in pb["charge_text"].values()), str(pb["charge_text"]))
check("Punjab: unknown billing cycle and verification date are labelled, not blank", pb["billing_cycle_text"] == "Not verified yet" and pb["last_verified_text"] == "Not verified yet")
check("Punjab: the free-units scheme is listed, flagged to verify, and not applied",
      pb["subsidies"][0]["needs_verification"] and pb["subsidies"][0]["valid_till_text"] == "Not verified yet" and pb["free_units"] is None)
check("Punjab: slab rows show real rates", [r["rate_text"] for r in pb["slab_rows"]] == ["₹3.85 per unit", "₹7.05 per unit"] and [r["range"] for r in pb["slab_rows"]] == ["0–300 units", "301+ units"],
      str(pb["slab_rows"]))

hr = tariff_data.state_profile("haryana")  # case-insensitive
check("Haryana: the three known slab rates are shown, the open-ended slab says not verified",
      [r["rate_text"] for r in hr["slab_rows"]] == ["₹2.2 per unit", "₹2.7 per unit", "₹2.95 per unit", NV], str([r["rate_text"] for r in hr["slab_rows"]]))
check("Haryana cannot be applied: one unknown slab rate makes the whole tariff unusable", hr["can_apply"] is False and hr["slabs"] == [] and hr["tariff"] is None and "slabs" in hr["unverified"])
for st in ("Rajasthan", "Himachal Pradesh", "Bihar"):
    p = tariff_data.state_profile(st)
    check(f"{st}: no rate is invented", not p["can_apply"] and p["slabs"] == [] and p["tariff"] is None and all(r["rate_text"] == NV for r in p["slab_rows"])
          and p["subsidies"][0]["benefit_text"] == "Not verified yet" and p["verification_status"] == "not_verified", str(p["slab_rows"]))
check("an effective date that is in the file is shown, a missing one is labelled",
      tariff_data.state_profile("Himachal Pradesh")["effective_from_text"] == "2026-04-01" and tariff_data.state_profile("Rajasthan")["effective_from_text"] == "Not verified yet")
check("a state the file does not list gets no profile from it", tariff_data.state_profile("Delhi") is None)
check("region_service: file states win, other states fall back to the country average",
      region_service.profile_for("IN", "Punjab")["from_file"] and region_service.profile_for("IN", "Delhi")["level"] == "country" and region_service.profile_for("IN", "Delhi")["tariff"] == 6.5)

# every null in the file must come out as "not verified", every number as itself
leaks = []
for st in raw["states"]:
    p = tariff_data.state_profile(st["state"])
    t = st["tariff"]
    for key, label in (("fixed_charge", "fixed_charges"), ("electricity_duty_pct", "duty_percent"), ("fuel_surcharge_pct", "fuel_surcharge_pct")):
        if (t[key] is None) != (p[label] is None) or (t[key] is None and p["charge_text"][key] != NV):
            leaks.append((st["state"], key))
    for sl, row in zip(t["slabs"], p["slab_rows"]):
        if (sl["rate_per_unit"] is None) != (row["rate"] is None) or (sl["rate_per_unit"] is None and row["rate_text"] != NV):
            leaks.append((st["state"], "slab", sl))
check("no null becomes a number and no number is lost, for every state", not leaks, str(leaks))

# ------------------------------------------------------------------ validation of a bad file
def bad(mutate):
    d = copy.deepcopy(raw)
    mutate(d)
    try:
        tariff_data.validate(d)
        return False
    except tariff_data.TariffDataError:
        return True


check("a negative rate is rejected", bad(lambda d: d["states"][0]["tariff"]["slabs"][0].update(rate_per_unit=-1)))
check("a text rate is rejected", bad(lambda d: d["states"][0]["tariff"]["slabs"][0].update(rate_per_unit="3.85")))
check("an unknown verification status is rejected", bad(lambda d: d["states"][0]["tariff"].update(verification_status="official")))
check("a duplicate state is rejected", bad(lambda d: d["states"].append(copy.deepcopy(d["states"][0]))))
check("a missing states list is rejected", bad(lambda d: d.pop("states")))
d = copy.deepcopy(raw)
d["states"][0]["tariff"]["source_url"] = "javascript:alert(1)"
d["states"][0]["subsidies"][0]["apply_url"] = "file:///etc/passwd"
v = tariff_data.validate(d)
check("only http(s) links survive", v["states"][0]["tariff"]["source_url"] is None and v["states"][0]["subsidies"][0]["apply_url"] is None)
slabs_gap = copy.deepcopy(raw)
slabs_gap["states"][0]["tariff"]["slabs"][1]["from_units"] = 350  # a hole between 300 and 350
check("slabs with a gap between them are not applied", tariff_data.state_profile("Punjab", data=tariff_data.validate(slabs_gap))["can_apply"] is False)

# ------------------------------------------------------------------ routes and the household
client = application.app.test_client()
check("tariffs need a login", client.get("/api/tariffs").status_code == 401)
client.post("/api/auth/login", json={"mobile": "9999999999", "password": "demo1234"})
idx = client.get("/api/tariffs").get_json()
check("/api/tariffs lists the states with their status", [s["state"] for s in idx["states"]] == ["Punjab", "Haryana", "Rajasthan", "Himachal Pradesh", "Bihar"]
      and idx["not_verified_text"] == NV and idx["status"]["ok"] and {s["state"]: s["can_apply"] for s in idx["states"]}["Punjab"] and not {s["state"]: s["can_apply"] for s in idx["states"]}["Haryana"])
check("/api/region/profile returns the state tariff with its slab rows",
      client.get("/api/region/profile?country=IN&state=Haryana").get_json()["slab_rows"][3]["rate_text"] == NV)

h0 = models.get_household()
models.set_location(30.9, 75.85, "Ludhiana, Punjab, India", "IN", "Haryana")
r = client.post("/api/region/apply")
h1 = models.get_household()
check("applying Haryana is refused with the not-verified message and changes nothing",
      r.status_code == 400 and NV in r.get_json()["error"] and h1["tariff"] == h0["tariff"] and h1["tariff_slabs"] == h0["tariff_slabs"] and h1["tariff_source"] == h0["tariff_source"], str(r.get_json()))
models.apply_region_profile(tariff_data.state_profile("Haryana"))
h1b = models.get_household()
check("a not-appliable profile at most sets the currency", h1b["tariff"] == h0["tariff"] and h1b["tariff_slabs"] == h0["tariff_slabs"] and h1b["tariff_source"] == h0["tariff_source"] and h1b["currency_symbol"] == "₹")

models.set_location(30.9, 75.85, "Ludhiana, Punjab, India", "IN", "Punjab")
before = models.get_household()
r = client.post("/api/region/apply")
h2 = models.get_household()
check("applying Punjab works", r.status_code == 200, str(r.get_json()))
check("Punjab slabs and flat rate are applied", json.loads(h2["tariff_slabs"]) == [[300, 3.85], [None, 7.05]] and h2["tariff"] == 3.85 and h2["tariff_source"] == "region", str(h2["tariff_slabs"]))
check("unknown fixed charge and duty keep the household's own values (not replaced by guesses or zero)",
      h2["fixed_charges"] == before["fixed_charges"] and h2["duty_percent"] == before["duty_percent"] and h2["free_units"] == before["free_units"])
check("the household remembers which charges are unverified", json.loads(h2["tariff_unverified"]) == ["fixed_charge", "fuel_surcharge_pct", "electricity_duty_pct"], h2["tariff_unverified"])
check("the API payload exposes the unverified list", client.get("/api/household").get_json()["tariff_unverified"] == ["fixed_charge", "fuel_surcharge_pct", "electricity_duty_pct"])
check("350 units are billed by slab: 300 x 3.85 + 50 x 7.05 = 1507.50", abs(energy_charge(350, h2["tariff"], json.loads(h2["tariff_slabs"])) - 1507.5) < 1e-9)

body = {k: client.get("/api/household").get_json()[k] for k in ("name", "cycle_start", "cycle_end", "monthly_budget")}
r = client.put("/api/household", json={**body, "tariff": 9.0})
h3 = models.get_household()
check("the user typing their own tariff clears the unverified flag and marks it manual", r.status_code == 200 and h3["tariff_source"] == "manual" and h3["tariff_unverified"] == "", str(r.get_json()))

# ------------------------------------------------------------------ the Punjab 0-300 unit subsidy
from services.bill_calculator import bill_for  # noqa: E402

SUB = "300 free units for domestic consumers"
check("the subsidy carries its allowance as a number and is still flagged to verify",
      tariff_data.state_profile("Punjab")["subsidies"][0]["free_units"] == 300 and tariff_data.state_profile("Punjab")["subsidies"][0]["needs_verification"])
check("a negative allowance is rejected", bad(lambda d: d["states"][0]["subsidies"][0].update(free_units=-5)))
check("the subsidy needs a login", application.app.test_client().post("/api/region/subsidy", json={"scheme_name": SUB}).status_code == 401)

models.set_location(30.9, 75.85, "Ludhiana, Punjab, India", "IN", "Punjab")
client.post("/api/region/apply")
base = models.get_household()
check("before the subsidy the household has no free units", base["free_units"] == 0 and base["free_units_mode"] == "deduct" and not base["subsidy_scheme"])
check("without the subsidy, 250 units are charged by slab", bill_for(base, 250)["energy_charge"] == 962.5)

r = client.post("/api/region/subsidy", json={"scheme_name": SUB})
hs = models.get_household()
check("applying the subsidy works", r.status_code == 200, str(r.get_json()))
check("it stores 300 free units in 'cover' mode and remembers the scheme", hs["free_units"] == 300 and hs["free_units_mode"] == "cover" and hs["subsidy_scheme"] == SUB)
check("the household is flagged: unverified charges and the free-units rule", json.loads(hs["tariff_unverified"]) == ["fixed_charge", "fuel_surcharge_pct", "electricity_duty_pct", "free_units_rule"], hs["tariff_unverified"])
check("units 0-300 are free: 250 and 300 units have no energy charge", bill_for(hs, 250)["energy_charge"] == 0 and bill_for(hs, 300)["energy_charge"] == 0)
check("units above 300 keep the upper slab rate: 350 units = 50 x 7.05 = 352.50", bill_for(hs, 350)["energy_charge"] == 352.5, str(bill_for(hs, 350)))
check("500 units = 200 x 7.05 = 1410", bill_for(hs, 500)["energy_charge"] == 1410.0)
b = bill_for(hs, 350)
check("the total still adds the household's fixed charge and duty", abs(b["total"] - ((352.5 + hs["fixed_charges"]) * (1 + hs["duty_percent"] / 100) + hs["other_charges"])) < 0.01, str(b))
rb = client.post("/api/bill", json={"units": 350, "tariff": 3.85}).get_json()
check("/api/bill uses the same covered-units rule", rb["energy_charge"] == 352.5, str(rb))
check("the API payload shows the applied scheme", client.get("/api/household").get_json()["subsidy_scheme"] == SUB)

client.post("/api/region/apply")
h2 = models.get_household()
check("applying regional pricing again keeps the subsidy and its flag",
      h2["free_units"] == 300 and h2["free_units_mode"] == "cover" and h2["subsidy_scheme"] == SUB and "free_units_rule" in json.loads(h2["tariff_unverified"]), h2["tariff_unverified"])

check("an unknown scheme is refused", client.post("/api/region/subsidy", json={"scheme_name": "Made up"}).status_code == 400)
check("a scheme without a unit allowance is refused", client.post("/api/region/subsidy", json={"scheme_name": "PM Surya Ghar Muft Bijli Yojana"}).status_code == 400)
check("scheme_name is required", client.post("/api/region/subsidy", json={}).status_code == 400)

r = client.delete("/api/region/subsidy")
h3 = models.get_household()
check("removing the subsidy goes back to no free units and the plain mode", r.status_code == 200 and h3["free_units"] == 0 and h3["free_units_mode"] == "deduct" and h3["subsidy_scheme"] == "")
check("removing it clears only its own flag", json.loads(h3["tariff_unverified"]) == ["fixed_charge", "fuel_surcharge_pct", "electricity_duty_pct"], h3["tariff_unverified"])
check("after removal 350 units are charged by slab again", bill_for(h3, 350)["energy_charge"] == 1507.5)

client.post("/api/region/subsidy", json={"scheme_name": SUB})
body = {k: client.get("/api/household").get_json()[k] for k in ("name", "cycle_start", "cycle_end", "monthly_budget", "tariff")}
client.put("/api/household", json={**body, "free_units": 100})
h4 = models.get_household()
check("typing your own free-units number replaces the subsidy: plain deduction, no flags",
      h4["free_units"] == 100 and h4["free_units_mode"] == "deduct" and h4["subsidy_scheme"] == "" and h4["tariff_unverified"] == "" and h4["tariff_source"] == "manual")

models.set_location(30.9, 75.85, "Karnal, Haryana, India", "IN", "Haryana")
check("a state with no such scheme refuses it", client.post("/api/region/subsidy", json={"scheme_name": SUB}).status_code == 400)

print("\nALL TARIFF CHECKS PASSED" if not failures else f"\n{len(failures)} CHECK(S) FAILED: {failures}")
sys.exit(1 if failures else 0)
