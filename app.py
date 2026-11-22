"""WATTWISE – Smart Electricity Management Platform (Flask app)."""
import json
import os
from datetime import date, timedelta

from flask import Flask, g, jsonify, redirect, render_template, request, session
from werkzeug.exceptions import HTTPException

import models
from services import accounts
from database import get_db, init_db, startup_lock
from models import ValidationError
from services import region_service, alert_service, weather_rules_engine as rules_engine, budget_service, recommendation_engine, weather_service
from services import appliance_catalog, notification_service
from services.bill_calculator import estimate_bill, bill_for
from services.prediction_service import appliance_summary, cycle_info, predict

app = Flask(__name__)


def _secret_key():
    """Signs the login session cookie. Set SECRET_KEY in production so logins survive restarts; otherwise a random
    key is kept in instance/ (never shipped)."""
    env = os.environ.get("SECRET_KEY")
    if env:
        return env.encode()
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance", "secret.key")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(os.urandom(32))
    with open(path, "rb") as f:
        return f.read()


app.secret_key = _secret_key()
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")
PUBLIC_PAGES = {"/", "/login", "/register", "/recover"}
PUBLIC_API = {"/api/auth/login", "/api/auth/register", "/api/auth/recover", "/api/auth/me", "/api/auth/logout", "/api/lang"}


@app.before_request
def _require_login():
    """Every page and API except the public ones needs a logged-in account. Each request is bound to that account."""
    g.user_id = session.get("uid")
    p = request.path
    if p.startswith("/static/") or p in PUBLIC_PAGES or p in PUBLIC_API:
        return None
    if g.user_id is None:
        if p.startswith("/api/"):
            return jsonify({"error": "Please log in to continue."}), 401
        return redirect("/login?next=" + p)
    return None


def current_lang():
    if g.get("user_id"):
        u = accounts.get_user(g.user_id)
        if u:
            return u["language"]
    return session.get("lang", "en")


@app.context_processor
def inject_account():
    uid = g.get("user_id")
    user = accounts.get_user(uid) if uid else None
    return {"account": user, "lang": current_lang()}


# ------------------------------------------------------------------ helpers
def err(msg, code=400):
    return jsonify({"error": msg}), code


@app.errorhandler(ValidationError)
def _validation(e):
    return err(str(e), 400)


@app.errorhandler(404)
def _404(e):
    if request.path.startswith("/api/"):
        return err("Not found", 404)
    return render_template("index.html", not_found=True), 404


@app.errorhandler(Exception)
def _500(e):
    if isinstance(e, HTTPException):
        return err(e.description, e.code) if request.path.startswith("/api/") else e
    if request.path.startswith("/api/"):
        app.logger.exception("API error")
        return err("Internal server error: " + e.__class__.__name__, 500)
    raise e


def parse_overrides(data):
    o = {}
    for k, v in (data or {}).items():
        try:
            hv = float(v)
        except (TypeError, ValueError):
            raise ValidationError("Override hours must be numbers")
        if not 0 <= hv <= 24:
            raise ValidationError("Override hours must be between 0 and 24")
        o[int(k)] = hv
    return o


def current_weather(refresh=False):
    """Cached Open-Meteo weather (refreshed every 3 hours, or on demand)."""
    return weather_service.get_weather(refresh=refresh)


def compute(overrides=None, refresh_weather=False):
    h = models.get_household()
    apps = models.list_appliances()
    weather = current_weather(refresh_weather)
    pred = predict(h, apps, weather, overrides=overrides)
    advice = recommendation_engine.advise(h, apps, weather, pred)
    return h, apps, weather, pred, advice


def public_pred(pred):
    return {k: v for k, v in pred.items() if k != "series"}


def household_payload(h):
    c = cycle_info(h)
    slabs = json.loads(h["tariff_slabs"]) if h.get("tariff_slabs") else []
    unverified = json.loads(h["tariff_unverified"]) if h.get("tariff_unverified") else []  # charges the applied tariff lacks
    return {**h, "tariff_slabs": slabs, "tariff_slabs_text": region_service.slabs_text(slabs), "tariff_unverified": unverified,
            "cycle": {k: v for k, v in c.items() if k != "remaining_dates"}}


@app.context_processor
def inject_currency():
    try:
        return {"currency": models.get_household().get("currency_symbol") or ""}
    except Exception:
        return {"currency": ""}


# -------------------------------------------------------------------- pages
@app.route("/")
def index():
    return render_template("index.html")


for _p in ("dashboard", "setup", "appliances", "weather", "advisor", "planner", "alerts", "notifications"):
    app.add_url_rule(f"/{_p}", _p, (lambda p: lambda: render_template(f"{p}.html", page=p))(_p))


# ---------------------------------------------------------------- household
@app.get("/api/household")
def api_household():
    return jsonify(household_payload(models.get_household()))


@app.put("/api/household")
def api_household_update():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return err("Invalid JSON body")
    return jsonify(household_payload(models.update_household(data)))


@app.post("/api/demo/reset")
def api_demo_reset():
    models.reset_demo()
    weather_service.get_weather(refresh=True)
    return jsonify({"ok": True, "household": household_payload(models.get_household())})


@app.post("/api/household/clear")
def api_household_clear():
    models.clear_household()
    return jsonify({"ok": True, "household": household_payload(models.get_household())})


@app.get("/api/presets")
def api_presets():
    return jsonify({"presets": models.PRESETS, "categories": models.CATEGORIES})


# --------------------------------------------------------------- appliances
@app.get("/api/appliances")
def api_appliances():
    return jsonify(models.list_appliances())


@app.get("/api/weather-rules")
def api_weather_rules():
    """The active weather-to-appliance rule database and whether it loaded cleanly."""
    st = rules_engine.rules_status()
    try:
        rules = rules_engine.load_rules()
    except rules_engine.RuleError as e:
        return jsonify({"status": st, "rules": None})
    return jsonify({"status": st, "rules": rules, "coverage": rules_engine.coverage_report(rules)})


@app.get("/api/weather-rules/evaluate")
def api_weather_rules_evaluate():
    """Test the rules for any weather values against every registered appliance (no data is saved)."""
    q = request.args
    def num(k, default=None):
        v = q.get(k)
        if v in (None, ""):
            return default
        try:
            return float(v)
        except ValueError:
            raise ValidationError(f"'{k}' must be a number")
    day = {"temp_max": num("temp", num("temp_max")), "temp_min": num("low", num("temp_min")),
           "apparent_max": num("apparent"), "humidity": num("humidity"),
           "rain_mm": num("rain_mm", 0.0), "rain_chance": num("rain_prob")}
    if day["temp_max"] is None and day["apparent_max"] is None:
        raise ValidationError("Give at least 'temp' (daily high in °C)")
    if day["temp_min"] is None:
        day["temp_min"] = (day["temp_max"] if day["temp_max"] is not None else day["apparent_max"]) - 8
    if day["humidity"] is not None and not 0 <= day["humidity"] <= 100:
        raise ValidationError("'humidity' must be between 0 and 100")
    rules = rules_engine.load_rules()
    out = []
    for a in models.list_appliances():
        ev = rules_engine.evaluate(a, day, a["need_hours"], rules)
        out.append({"name": a["name"], "kind": ev["kind"], "typical_hours": a["need_hours"], "recommended_hours": ev["hours"],
                    "floor_hours": ev["floor_hours"], "duration": fmt_hours(ev["hours"]), "summary": ev["summary"],
                    "warnings": ev["warnings"]})
    return jsonify({"input": day, "appliances": out})


def fmt_hours(h):
    m = int(round(h * 60))
    if m <= 0:
        return "Off"
    return f"{m} min" if m < 60 else (f"{m // 60} h" if m % 60 == 0 else f"{m // 60} h {m % 60} min")


@app.get("/api/allocation")
def api_allocation():
    """Dynamic weather-based allocation of the remaining units across the billing days (see services/allocation_service)."""
    from services.allocation_service import build_allocation
    h = models.get_household()
    w = current_weather(request.args.get("refresh") == "1")
    return jsonify(build_allocation(h, models.list_appliances(), w))


def _day_forecast(weather, day):
    """The forecast or climate row for one date, or None."""
    rows = (weather or {}).get("forecast", []) + (weather or {}).get("climatology", [])
    return next((r for r in rows if r["day"] == day), None)


@app.get("/api/hourly-plan")
def api_hourly_plan():
    """Module 2: 24-hour ON/OFF, intensity and kWh per appliance for one date (default today)."""
    from services.weather_energy_engine import hourly_comfort_table
    day = request.args.get("date") or models.today().isoformat()
    try:
        date.fromisoformat(day)
    except ValueError:
        return err("date must be YYYY-MM-DD")
    h = models.get_household()
    apps = models.list_appliances()
    weather = current_weather()
    row = _day_forecast(weather, day)
    if row is None:
        return err("No forecast or climate average for that date. The forecast covers 30 days from today.", 404)
    rows = hourly_comfort_table(apps, row, models.load_hourly(day) or None)
    models.save_hourly_plan(day, rows)
    totals = {}
    for r in rows:
        totals[r["appliance"]] = round(totals.get(r["appliance"], 0.0) + r["kwh"], 3)
    temps = [r["temp"] for r in rows if r["appliance_id"] == (apps[0]["id"] if apps else None)]
    return jsonify({"date": day, "source": rows[0]["source"] if rows else None, "rows": rows,
                    "temp_by_hour": temps, "kwh_by_appliance": totals, "total_kwh": round(sum(totals.values()), 3)})


@app.get("/api/daily-allocation")
def api_daily_allocation():
    """Modules 3-4: predicted kWh per remaining day (ML when trained, otherwise the formula) and the allocation."""
    from services.allocation_service import daily_allocation
    h = models.get_household()
    out = daily_allocation(h, models.list_appliances(), current_weather(request.args.get("refresh") == "1"))
    models.save_daily_plan([{"day": d["day"], "predicted_kwh": d["predicted_kwh"], "allocation_kwh": d["allocation_kwh"],
                             "feasible": out["feasible"]} for d in out["days"]])
    if request.args.get("hourly") != "1":  # the 24-hour split is only sent on request
        for d in out["days"]:
            d.pop("hourly_allocation_kwh", None)
    return jsonify(out)


@app.get("/api/optimize")
def api_optimize():
    """Module 5: fit one date's predicted use to its allocation (default: tomorrow)."""
    from services.allocation_service import daily_allocation, optimize_day
    from services.bill_calculator import bill_for
    h = models.get_household()
    apps = models.list_appliances()
    alloc = daily_allocation(h, apps, current_weather())
    day = request.args.get("date") or (models.today() + timedelta(days=1)).isoformat()
    row = next((d for d in alloc["days"] if d["day"] == day), None)
    if row is None:
        return err("That date is not one of the remaining billing days.", 404)
    plan = optimize_day(h, apps, row["predicted_kwh"], row["allocation_kwh"], row["ac_kwh"], row["feels_like_max"],
                        row["humidity"], row["essential_floor_kwh"])
    projected = h["units_consumed"] + sum(d["predicted_kwh"] for d in alloc["days"])
    plan["saved_money"] = round(bill_for(h, projected)["total"] - bill_for(h, projected - plan["saved_kwh"])["total"], 2)
    plan["date"] = day
    models.save_daily_plan([{"day": day, "predicted_kwh": row["predicted_kwh"], "allocation_kwh": row["allocation_kwh"],
                             "optimized_kwh": plan["optimized_kwh"], "feasible": plan["fits"]}])
    return jsonify(plan)


@app.post("/api/recommendations/apply")
def api_recommendations_apply():
    """Save the calculated hours as the appliance schedule. Requires explicit confirmation from the user."""
    body = request.get_json(silent=True) or {}
    if body.get("confirmed") is not True:
        return err("Confirmation required before the schedule is changed.", 400)
    h, apps, weather, pred, advice = compute()
    if not advice.get("ready"):
        return err(advice.get("message", "No recommendation available."), 400)
    before = {a["id"]: a["daily_hours"] for a in apps}
    models.apply_schedule({p["id"]: p["hours"] for p in advice["plan"]})
    models.save_recommendation(advice["target_date"], {**advice, "applied": True})
    changes = [{"id": p["id"], "name": p["name"], "from": before.get(p["id"]), "to": p["hours"]} for p in advice["plan"]
               if before.get(p["id"]) is not None and abs(before[p["id"]] - p["hours"]) > 1e-9]
    return jsonify({"ok": True, "changes": changes, "appliances": models.list_appliances()})


@app.get("/api/meter")
def api_meter_get():
    return jsonify(models.meter_state())


@app.put("/api/meter")
def api_meter_put():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return err("Invalid JSON body")
    return jsonify(models.save_meter_details(d.get("previous_reading"), d.get("today_reading"), d.get("monthly_budget"),
                                             bool(d.get("meter_reset")), d.get("reading_before_reset")))


@app.get("/api/appliances/summary")
def api_appliances_summary():
    h = models.get_household()
    days = cycle_info(h)["total_days"]
    return jsonify(appliance_summary(models.list_appliances(), days))


@app.post("/api/appliances")
def api_appliance_create():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return err("Invalid JSON body")
    return jsonify(models.create_appliance(data)), 201


@app.put("/api/appliances/<int:aid>")
def api_appliance_update(aid):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return err("Invalid JSON body")
    a = models.update_appliance(aid, data)
    return jsonify(a) if a else err("Appliance not found", 404)


@app.delete("/api/appliances/<int:aid>")
def api_appliance_delete(aid):
    return jsonify({"ok": True}) if models.delete_appliance(aid) else err("Appliance not found", 404)


# ------------------------------------------------------------------ weather
@app.get("/api/geocode")
def api_geocode():
    try:
        return jsonify(weather_service.geocode(request.args.get("q", "")))
    except weather_service.WeatherError as e:
        return err(str(e), 502 if "failed" in str(e) else 400)


@app.get("/api/weather")
def api_weather():
    w = current_weather(request.args.get("refresh") == "1")
    h = models.get_household()
    if w.get("available"):
        days = w["forecast"]
        for d in days:
            d["hot"] = d["temp_max"] >= alert_service.HEAT_WARN
            d["cold"] = d["temp_min"] <= alert_service.COLD_WARN
        # weather impact: modelled demand per forecast day vs reference conditions
        apps = models.list_appliances()
        pred = predict(h, apps, w)
        by_day = {d["date"]: d for d in pred["days"]}
        base = pred["model_daily_kwh"]
        for d in days:
            m = by_day.get(d["day"])
            d["modelled_kwh"] = round(m["demand_kwh"], 2) if m else None
            d["delta_vs_reference"] = round(m["demand_kwh"] - base, 2) if m else None
        w["impact"] = _impact_text(h, days, pred)
        w["assumptions"] = {"rules_file": "weather_appliance_rules.json", "rules": rules_engine.rules_status()}
    return jsonify(w)


def _heat_word(temp):
    """Plain wording for the heat of a day (no internal thresholds are shown to the user)."""
    if temp >= 35:
        return "Expect noticeably higher cooling use."
    if temp >= 30:
        return "Cooling use may rise a little."
    return "Little or no extra cooling needed."


def _cold_word(temp):
    if temp <= 10:
        return "Heater and geyser use may rise."
    if temp <= 20:
        return "Hot-water use may rise."
    return "Little or no heating needed."


def _impact_text(h, days, pred):
    """Plain-language weather impact, taken from the weather rule database."""
    if not days:
        return []
    rules = rules_engine.load_rules()
    out = []
    mx = max(days, key=lambda d: d["temp_max"])
    mn = min(days, key=lambda d: d["temp_min"])
    avg = sum(d["temp_max"] for d in days) / len(days)
    out.append(f"Forecast highs average {avg:.0f}°C and peak at {mx['temp_max']:.0f}°C on {mx['day']}.")
    hb = rules_engine.band_for(rules["humidity_bands"], mx.get("humidity") or 0)[0] if mx.get("humidity") is not None else None
    ap = rules_engine.apparent_high(mx, hb)
    band, _ = rules_engine.band_for(rules["cooling_bands"], ap if ap is not None else mx["temp_max"])
    out.append(f"Feels-like on the hottest day: about {ap:.0f}°C. " + _heat_word(ap) if ap is not None else "Hottest day: " + _heat_word(mx["temp_max"]))
    lo_band, _ = rules_engine.band_for(rules["heating_bands"], mn["temp_min"])
    out.append(f"Coldest morning: {mn['temp_min']:.0f}°C on {mn['day']}. " + _cold_word(mn["temp_min"]))
    if mx.get("rain_mm") is not None and mx["rain_mm"] >= 0.1:
        out.append(f"Rain of {mx['rain_mm']:.1f} mm is forecast on some days.")
    if pred["weather_effect"]:
        out.append(f"Net effect on your month-end projection: {pred['weather_effect']:+.1f} units vs a straight-line estimate.")
    out.append("These are estimates based on the forecast, not exact appliance measurements.")
    return out


# ------------------------------------------------------------ accounts
@app.get("/login")
def page_login():
    return render_template("login.html", page="login")


@app.get("/register")
def page_register():
    return render_template("register.html", page="register")


@app.get("/recover")
def page_recover():
    return render_template("recover.html", page="recover")


@app.get("/profile")
def page_profile():
    return render_template("profile.html", page="profile")


@app.post("/api/auth/register")
def api_register():
    d = request.get_json(silent=True) or {}
    user = accounts.register(d.get("full_name"), d.get("mobile"), d.get("password"), d.get("email", ""),
                             d.get("city", ""), d.get("language") or session.get("lang", "en"))
    session.clear()
    session["uid"] = user["id"]
    session["lang"] = user["language"]
    return jsonify({"user": user}), 201


@app.post("/api/auth/login")
def api_login():
    d = request.get_json(silent=True) or {}
    user = accounts.authenticate(d.get("mobile"), d.get("password"))
    if user is None:
        return err("Mobile number or password is incorrect.", 401)
    lang = session.get("lang")
    session.clear()
    session["uid"] = user["id"]
    session["lang"] = user["language"] or lang or "en"
    return jsonify({"user": user})


@app.post("/api/auth/logout")
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/auth/me")
def api_me():
    uid = session.get("uid")
    user = accounts.get_user(uid) if uid else None
    return jsonify({"user": user, "language": user["language"] if user else session.get("lang", "en")})


@app.post("/api/auth/recover")
def api_recover():
    d = request.get_json(silent=True) or {}
    accounts.recover_password(d.get("mobile"), d.get("city"), d.get("new_password"))
    return jsonify({"ok": True})


@app.post("/api/auth/change-password")
def api_change_password():
    d = request.get_json(silent=True) or {}
    accounts.change_password(g.user_id, d.get("current"), d.get("new"))
    return jsonify({"ok": True})


@app.post("/api/lang")
def api_lang_public():
    lang = (request.get_json(silent=True) or {}).get("language")
    if lang not in accounts.LANGUAGES:
        return err("Language must be en or hi", 400)
    session["lang"] = lang
    if g.get("user_id"):
        accounts.set_language(g.user_id, lang)
    return jsonify({"language": lang})


@app.get("/api/profile")
def api_profile_get():
    u = accounts.get_user(g.user_id)
    h = household_payload(models.get_household())
    return jsonify({"personal": u, "household": h})


@app.put("/api/profile/personal")
def api_profile_personal():
    d = request.get_json(silent=True) or {}
    u = accounts.update_personal(g.user_id, d)
    if d.get("language") in accounts.LANGUAGES:
        accounts.set_language(g.user_id, d["language"])
        session["lang"] = d["language"]
        u = accounts.get_user(g.user_id)
    return jsonify({"personal": u})


@app.put("/api/profile/household")
def api_profile_household():
    d = request.get_json(silent=True) or {}
    return jsonify({"household": household_payload(models.update_profile_extras(d))})


# ----------------------------------------------------------- location/region
@app.get("/api/region/profile")
def api_region_profile():
    """Pricing profile for a country/state (defaults to the household's own region)."""
    h = models.get_household()
    cc = request.args.get("country") or h.get("country_code")
    st = request.args.get("state") if request.args.get("state") is not None else h.get("region_name")
    return jsonify(region_service.profile_for(cc, st) or {"level": "none", "note": "Set your location to see regional pricing."})


@app.get("/api/tariffs")
def api_tariffs():
    """Every state in tariff_data.json with its verification status, and whether the file loaded cleanly."""
    from services import tariff_data
    try:
        return jsonify({"states": tariff_data.index(region_service.symbol_for("IN")), "status": tariff_data.status(),
                        "not_verified_text": tariff_data.NOT_VERIFIED})
    except tariff_data.TariffDataError as e:
        return jsonify({"states": [], "status": {"ok": False, "error": str(e)}, "not_verified_text": tariff_data.NOT_VERIFIED})


def _household_subsidy(scheme_name):
    """The free-units scheme with this name that applies to the household's state (or the central ones), or None."""
    h = models.get_household()
    prof = region_service.profile_for(h.get("country_code"), h.get("region_name"))
    if not prof or not prof.get("from_file"):
        return None
    return next((s for s in prof["subsidies"] + prof["central_subsidies"] if s["scheme_name"] == scheme_name and s.get("free_units")), None)


@app.post("/api/region/subsidy")
def api_region_subsidy():
    """Apply a free-units scheme from tariff_data.json (for example Punjab's 300 free units: units 0-300 are covered, units
    above 300 keep their slab rate). The scheme's exact rule is not verified, so the household is flagged until removed."""
    d = request.get_json(silent=True)
    name = d.get("scheme_name") if isinstance(d, dict) else None
    if not isinstance(name, str):
        return err("scheme_name is required")
    s = _household_subsidy(name)
    if not s:
        return err("This scheme has no free-unit allowance to apply for your state.")
    models.apply_subsidy(s["scheme_name"], s["free_units"])
    return jsonify({"ok": True, "subsidy": s, "household": household_payload(models.get_household())})


@app.delete("/api/region/subsidy")
def api_region_subsidy_remove():
    models.remove_subsidy()
    return jsonify({"ok": True, "household": household_payload(models.get_household())})


@app.post("/api/region/apply")
def api_region_apply():
    h = models.get_household()
    prof = region_service.profile_for(h.get("country_code"), h.get("region_name"))
    if not prof or prof["level"] == "none":
        return err("No pricing profile is available for this region. Enter your tariff manually.")
    if prof.get("can_apply") is False:
        return err(f"{prof['not_verified_text']} for {prof['region_name']}: its slab tariff cannot be applied. Enter your own tariff from your bill.")
    models.apply_region_profile(prof)
    return jsonify({"profile": prof, "household": household_payload(models.get_household())})


@app.post("/api/location/detect")
def api_location_detect():
    """Browser geolocation -> reverse lookup -> save location (+ optionally apply regional pricing)."""
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return err("Invalid JSON body")
    lat = models._num(d, "latitude", -90, 90)
    lon = models._num(d, "longitude", -180, 180)
    try:
        geo = region_service.reverse_geocode(lat, lon)
    except region_service.RegionError as e:
        # still keep the coordinates so weather works; region pricing needs the lookup
        models.set_location(lat, lon, f"Detected location ({lat:.2f}, {lon:.2f})", "", "")
        return jsonify({"ok": True, "partial": True, "warning": str(e) + " Weather will work, but regional pricing needs a place lookup — search your city in Setup.",
                        "household": household_payload(models.get_household())})
    models.set_location(lat, lon, geo["label"], geo["country_code"], geo["state"])
    h = models.get_household()
    prof = region_service.profile_for(geo["country_code"], geo["state"])
    applied = False
    appliable = bool(prof) and prof["level"] != "none" and prof.get("can_apply", True)  # unverified state rates are never applied
    if d.get("apply_tariff") and appliable:
        models.apply_region_profile(prof)
        applied = True
    elif prof and prof.get("currency_symbol") and h["tariff_source"] != "manual":
        models.apply_region_profile(prof) if prof["level"] != "none" else None  # not-appliable profiles only set the currency
        applied = appliable
    weather_service.get_weather(refresh=True)
    return jsonify({"ok": True, "geo": geo, "profile": prof, "applied": applied,
                    "tariff_was_manual": h["tariff_source"] == "manual" and not applied,
                    "household": household_payload(models.get_household())})


# ----------------------------------------------------------------- readings
@app.get("/api/readings")
def api_readings():
    return jsonify(models.list_readings())


@app.post("/api/readings")
def api_reading_add():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return err("Invalid JSON body")
    r = models.add_reading(d.get("reading_date") or models.today().isoformat(), d.get("units_consumed"), d.get("meter_reading"))
    try:
        refresh_alerts()  # a new reading can cross a budget threshold or be a spike: raise the notification now
    except Exception:
        app.logger.exception("alert refresh after a reading failed")
    return jsonify(r), 201


@app.delete("/api/readings/<int:rid>")
def api_reading_delete(rid):
    return jsonify({"ok": True}) if models.delete_reading(rid) else err("Reading not found", 404)


# -------------------------------------------------- consumption/budget/advice
@app.get("/api/consumption")
def api_consumption():
    _, _, _, pred, _ = compute()
    return jsonify(public_pred(pred))


@app.route("/api/budget/plan", methods=["GET", "POST"])
def api_budget_plan():
    ov = parse_overrides((request.get_json(silent=True) or {}).get("overrides")) if request.method == "POST" else None
    h = models.get_household()
    plan = budget_service.build_plan(h, models.list_appliances(), current_weather(), ov)
    return jsonify(plan)


@app.route("/api/recommendations", methods=["GET", "POST"])
def api_recommendations():
    h, apps, weather, pred, advice = compute()  # the advisor never takes hours from the user
    if advice.get("ready"):
        models.save_recommendation(advice["target_date"], advice)
    advice["weather_available"] = weather.get("available", False)
    return jsonify(advice)


# ------------------------------------------------------------------- alerts
def refresh_alerts():
    h, apps, weather, pred, advice = compute()
    generated = alert_service.generate_alerts(h, apps, weather, pred, advice)
    generated += alert_service.slab_crossing_alert(h, pred["projected"])
    generated += today_pace_alerts(h, apps, weather)
    models.sync_alerts(generated)
    notify(apps, advice, generated)
    return models.list_alerts()


def notify(apps, advice, generated):
    """Record the alert conditions found right now as personal notifications (duplicates inside 24 hours are skipped).
    A failure here must never break the page or request that triggered it."""
    try:
        notification_service.sync_from_alerts(models.hid(), generated, models.list_daily(), apps, advice)
    except Exception:
        app.logger.exception("notification sync failed")


def today_pace_alerts(h, apps, weather):
    """Module 6: today's logged units against today's allocation (yellow 80 %, red 100 %)."""
    from services.allocation_service import daily_allocation
    alloc = daily_allocation(h, apps, weather, include_today=True)
    today = models.today().isoformat()
    row = next((r for r in alloc["days"] if r["day"] == today), None)
    used = next((d["units"] for d in models.list_daily() if d["day"] == today), None)
    return alert_service.daily_pace_alerts(row["allocation_kwh"], used, today) if row else []


@app.get("/api/alerts")
def api_alerts():
    return jsonify(refresh_alerts())


@app.post("/api/alerts/refresh")
def api_alerts_refresh():
    return jsonify(refresh_alerts())


@app.post("/api/alerts/<int:aid>/dismiss")
def api_alert_dismiss(aid):
    return jsonify({"ok": True}) if models.dismiss_alert(aid) else err("Alert not found", 404)


# ----------------------------------------------------------- personal notifications
@app.get("/api/notifications")
def api_notifications():
    """The user's notifications, newest first. ?unread=true and ?severity=critical|warning|info filter the list.
    unread_count is always the total number of unread notifications (it feeds the bell badge)."""
    uid = models.hid()
    if notification_service.due(uid):  # re-check the alert conditions at most every 15 minutes
        try:
            refresh_alerts()
        except Exception:
            app.logger.exception("alert refresh for notifications failed")
    unread = request.args.get("unread", "").lower() in ("1", "true", "yes")
    try:
        items = notification_service.list_notifications(uid, unread, request.args.get("severity") or None)
    except ValueError as e:
        return err(str(e))
    return jsonify({"notifications": items, "unread_count": notification_service.unread_count(uid)})


@app.patch("/api/notifications/read-all")
def api_notifications_read_all():
    return jsonify({"ok": True, "updated": notification_service.mark_all_read(models.hid())})


@app.patch("/api/notifications/<int:nid>/read")
def api_notification_read(nid):
    return jsonify({"ok": True}) if notification_service.mark_read(models.hid(), nid) else err("Notification not found", 404)


@app.delete("/api/notifications/<int:nid>")
def api_notification_delete(nid):
    return jsonify({"ok": True}) if notification_service.delete(models.hid(), nid) else err("Notification not found", 404)


# ------------------------------------------------- appliance catalog (autofill)
@app.get("/api/catalog/search")
def api_catalog_search():
    """Look a model number up in the catalog. `match` is an exact model; `matches` are close suggestions."""
    conn = get_db()
    try:
        exact, rows = appliance_catalog.search(conn, request.args.get("q", ""))
    finally:
        conn.close()
    return jsonify({"match": exact, "matches": rows, "form": appliance_catalog.form_fields(exact) if exact else None,
                    "message": None if exact else (appliance_catalog.NOT_FOUND if not rows else "No exact match. Pick a suggestion or enter the details manually.")})


@app.post("/api/catalog/parse-sticker")
def api_catalog_parse_sticker():
    """Read a rating sticker. The browser does the barcode scan / OCR and sends only the recognised text."""
    d = request.get_json(silent=True)
    if not isinstance(d, dict) or not isinstance(d.get("text"), str) or not d["text"].strip():
        return err("No sticker text to read.")
    conn = get_db()
    try:
        return jsonify(appliance_catalog.parse_sticker(conn, d["text"]))
    finally:
        conn.close()


# ---------------------------------------------------------------- dashboard
@app.get("/api/dashboard")
def api_dashboard():
    h, apps, weather, pred, advice = compute(refresh_weather=request.args.get("refresh") == "1")
    generated = alert_service.generate_alerts(h, apps, weather, pred, advice)
    models.sync_alerts(generated)
    notify(apps, advice, generated)
    if advice.get("ready"):
        models.save_recommendation(advice["target_date"], advice)
    cyc = pred["cycle"]
    daily = models.list_daily()
    start = date.fromisoformat(cyc["start"])
    daily_map = {d["day"]: d for d in daily}
    labels, actual, planned, cumulative, proj_line, budget_line = [], [], [], [], [], []
    plan = budget_service.build_plan(h, apps, weather)
    plan_map = {p["date"]: p for p in plan["daily_plan"]}
    cum = 0.0
    run = 0.0
    for i in range(cyc["total_days"]):
        d = (start + timedelta(days=i)).isoformat()
        labels.append(d)
        if d in daily_map:
            actual.append(daily_map[d]["units"])
            run += daily_map[d]["units"]
            cumulative.append(round(run, 2))
            proj_line.append(None)
        else:
            actual.append(None)
            cumulative.append(None)
            proj_line.append(None)
        planned.append(plan_map[d]["allowance"] if d in plan_map else None)
        budget_line.append(round(h["monthly_budget"] * (i + 1) / cyc["total_days"], 2))
    # projected cumulative line, anchored on the last actual point
    last_idx = max((i for i, v in enumerate(cumulative) if v is not None), default=None)
    run = cumulative[last_idx] if last_idx is not None else 0.0
    if last_idx is not None:
        proj_line[last_idx] = run
    for i, lab in enumerate(labels):
        if lab in plan_map and (last_idx is None or i > last_idx):
            run += plan_map[lab]["projected_usage"]
            proj_line[i] = round(run, 2)
    summary = appliance_summary(apps, cyc["total_days"])
    return jsonify({
        "household": household_payload(h), "prediction": public_pred(pred), "weather": weather,
        "advice": advice, "alerts": models.list_alerts()[:6], "appliance_summary": summary,
        "charts": {"labels": labels, "actual_daily": actual, "allowance": planned, "cumulative_actual": cumulative,
                   "cumulative_projected": proj_line, "budget_line": budget_line,
                   "projected_daily": [plan_map[d]["projected_usage"] if d in plan_map else None for d in labels],
                   "has_actual": any(v is not None for v in actual),
                   "actual_is_demo": bool(daily) and all(d["source"] == "demo" for d in daily)},
    })


# --------------------------------------------------------------------- bill
@app.post("/api/bill")
def api_bill():
    d = request.get_json(silent=True)
    if not isinstance(d, dict):
        return err("Invalid JSON body")
    try:
        units = models._num(d, "units", 0, 1e6)
        tariff = models._num(d, "tariff", 0, 1000)
        fixed = models._num(d, "fixed_charges", 0, 1e6, required=False, default=0)
        other = models._num(d, "other_charges", 0, 1e6, required=False, default=0)
    except ValidationError as e:
        return err(str(e))
    h = household_payload(models.get_household())
    use_slabs = d.get("use_slabs", True) and h["tariff_slabs"]
    return jsonify(estimate_bill(units, tariff, fixed, other, h["tariff_slabs"] if use_slabs else None,
                                 h["duty_percent"], h["free_units"], h["currency_symbol"], h.get("free_units_mode") or "deduct"))


with startup_lock():
    init_db()
    models.ensure_household()
    accounts.ensure_demo_account()

if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1", port=int(os.environ.get("PORT", 8000)))

