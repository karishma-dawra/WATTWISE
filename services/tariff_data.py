"""State electricity tariffs and subsidies, read from tariff_data.json.

The file says what it does not know: `null` means "not verified yet". This module never turns a null into a number.
Every unknown rate is reported as NOT_VERIFIED ("Rates not verified yet"), a tariff with any unknown slab rate cannot be
applied to a household (can_apply is False), and fixed charge, fuel surcharge and duty that are unknown are left out of
the bill estimate and listed in `unverified` so the app can say so.

The file is re-read when it changes (like weather_appliance_rules.json). A broken file keeps the last good data and
records the error; nothing in it is trusted as official until `verification_status` says so.
"""
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PATH = Path(os.environ.get("WATTWISE_TARIFFS", BASE_DIR / "tariff_data.json"))
NOT_VERIFIED = "Rates not verified yet"            # shown wherever a rate is null
NOT_VERIFIED_INFO = "Not verified yet"             # shown for other missing details (dates, benefits, billing cycle ...)
STATUS = {"verified": "Verified", "partial": "Partially verified", "not_verified": "Not verified"}
RATE_FIELDS = (("fixed_charge", "Fixed charge"), ("fuel_surcharge_pct", "Fuel surcharge"), ("electricity_duty_pct", "Electricity duty"))
_cache = {"mtime": None, "data": None, "error": None}


class TariffDataError(ValueError):
    pass


def _num(v, where, lo=0):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < lo:
        raise TariffDataError(f"{where}: expected a number >= {lo} or null, got {v!r}")
    return v


def _url(v):
    """Links come from a file: only http(s) links are kept."""
    return v if isinstance(v, str) and v.startswith(("http://", "https://")) else None


def _text(v, where):
    if v is not None and not isinstance(v, str):
        raise TariffDataError(f"{where}: expected text or null, got {v!r}")
    return v


def _subsidy(s, where):
    if not isinstance(s, dict) or not s.get("scheme_name"):
        raise TariffDataError(f"{where}: needs a scheme_name")
    return {"scheme_name": str(s["scheme_name"]), "free_units": _num(s.get("free_units"), where + " free_units"),  # monthly units covered, if it is a free-units scheme
            "benefit": _text(s.get("benefit"), where), "eligibility": _text(s.get("eligibility"), where),
            "valid_till": _text(s.get("valid_till"), where), "apply_url": _url(s.get("apply_url")), "status": str(s.get("status") or "to_verify")}


def validate(raw):
    """Normalised copy of the file, or TariffDataError saying exactly what is wrong."""
    if not isinstance(raw, dict) or not isinstance(raw.get("states"), list):
        raise TariffDataError("the file needs a 'states' list")
    states, seen = [], set()
    for i, s in enumerate(raw["states"]):
        where = f"states[{i}]"
        if not isinstance(s, dict) or not s.get("state"):
            raise TariffDataError(f"{where}: needs a state name")
        name = str(s["state"]).strip()
        if name.lower() in seen:
            raise TariffDataError(f"{where}: duplicate state '{name}'")
        seen.add(name.lower())
        t = s.get("tariff")
        if not isinstance(t, dict) or not isinstance(t.get("slabs"), list) or not t["slabs"]:
            raise TariffDataError(f"{where} ({name}): tariff needs a non-empty 'slabs' list")
        slabs = []
        for j, sl in enumerate(t["slabs"]):
            w = f"{where} ({name}) slab {j}"
            if not isinstance(sl, dict):
                raise TariffDataError(f"{w}: expected an object")
            slabs.append({"from_units": _num(sl.get("from_units"), w), "to_units": _num(sl.get("to_units"), w), "rate_per_unit": _num(sl.get("rate_per_unit"), w)})
        status = t.get("verification_status") or "not_verified"
        if status not in STATUS:
            raise TariffDataError(f"{where} ({name}): verification_status must be one of {', '.join(STATUS)}")
        tariff = {"consumer_category": t.get("consumer_category") or "domestic", "slabs": slabs,
                  "billing_cycle": _text(t.get("billing_cycle"), where), "effective_from": _text(t.get("effective_from"), where),
                  "source_url": _url(t.get("source_url")), "last_verified_on": _text(t.get("last_verified_on"), where), "verification_status": status}
        for key, _ in RATE_FIELDS:
            tariff[key] = _num(t.get(key), f"{where} ({name}) {key}")
        states.append({"state": name, "discoms": [str(d) for d in s.get("discoms") or []], "regulator": s.get("regulator"),
                       "tariff": tariff, "subsidies": [_subsidy(x, f"{where} ({name}) subsidy") for x in s.get("subsidies") or []]})
    return {"states": states, "central_subsidies": [_subsidy(x, "central_subsidies") for x in raw.get("central_subsidies") or []]}


def load(path=None, force=False):
    """The validated data. Reloads when the file changes; a broken file keeps the last good data (see status())."""
    path = Path(path) if path else DEFAULT_PATH
    try:
        mtime = path.stat().st_mtime
    except OSError:
        if _cache["data"] is None:
            raise TariffDataError(f"tariff file not found: {path}")
        return _cache["data"]
    if force or _cache["mtime"] != mtime:
        try:
            data = validate(json.loads(path.read_text(encoding="utf-8")))
            _cache.update(mtime=mtime, data=data, error=None)
        except (ValueError, TypeError) as e:
            _cache["mtime"] = mtime
            _cache["error"] = f"invalid tariff file: {e}"
            if _cache["data"] is None:
                raise TariffDataError(_cache["error"])
    return _cache["data"]


def status():
    try:
        load()
        return {"ok": _cache["error"] is None, "error": _cache["error"], "path": str(DEFAULT_PATH)}
    except TariffDataError as e:
        return {"ok": False, "error": str(e), "path": str(DEFAULT_PATH)}


def _to_app_slabs(slabs):
    """File slabs -> the app's [[upper_bound_or_None, rate], ...]. [] unless every slab is fully known and the slabs join
    up (each starts right after the previous one ends, the last one has no upper bound)."""
    out, prev_to = [], None
    for i, s in enumerate(slabs):
        lo, hi, rate = s["from_units"], s["to_units"], s["rate_per_unit"]
        last = i == len(slabs) - 1
        if lo is None or rate is None or (hi is None) != last:
            return []
        if (prev_to is None and lo not in (0, 1)) or (prev_to is not None and lo != prev_to + 1):
            return []
        out.append([hi, rate])
        prev_to = hi
    return out


def _slab_rows(slabs, symbol):
    rows = []
    for s in slabs:
        lo, hi, rate = s["from_units"], s["to_units"], s["rate_per_unit"]
        if lo is None and hi is None:
            rng = "All units"
        elif hi is None:
            rng = f"{lo:g}+ units" if lo is not None else "Above"
        else:
            rng = f"{(lo if lo is not None else 0):g}–{hi:g} units"
        rows.append({"from_units": lo, "to_units": hi, "rate": rate, "range": rng,
                     "rate_text": f"{symbol}{rate:g} per unit" if rate is not None else NOT_VERIFIED})
    return rows


def _subsidies(items):
    return [{**s, "benefit_text": s["benefit"] or NOT_VERIFIED_INFO, "eligibility_text": s["eligibility"] or NOT_VERIFIED_INFO,
             "valid_till_text": s["valid_till"] or NOT_VERIFIED_INFO, "needs_verification": s["status"] != "verified"} for s in items]


def state_profile(state, symbol="₹", data=None):
    """The region pricing profile for an Indian state, or None if the file does not cover it.
    Same keys as region_service profiles, plus what is verified and what is not."""
    data = data or load()
    row = next((s for s in data["states"] if s["state"].lower() == (state or "").strip().lower()), None)
    if row is None:
        return None
    t = row["tariff"]
    slabs = _to_app_slabs(t["slabs"])
    unverified = ([] if slabs else ["slabs"]) + [k for k, _ in RATE_FIELDS if t[k] is None]
    labels = dict(RATE_FIELDS)
    fields = {k: (f"{symbol}{t[k]:g}" if k == "fixed_charge" else f"{t[k]:g}%") if t[k] is not None else NOT_VERIFIED for k, _ in RATE_FIELDS}
    return {
        "level": "region", "from_file": True, "country_code": "IN", "region_name": row["state"], "currency_symbol": symbol,
        "state": row["state"], "discoms": row["discoms"], "regulator": row["regulator"], "consumer_category": t["consumer_category"],
        "slabs": slabs, "slab_rows": _slab_rows(t["slabs"], symbol), "slabs_complete": bool(slabs),
        "tariff": slabs[0][1] if slabs else None,
        "fixed_charges": t["fixed_charge"], "duty_percent": t["electricity_duty_pct"], "fuel_surcharge_pct": t["fuel_surcharge_pct"],
        "free_units": None,  # free-unit schemes are listed under subsidies and never applied automatically
        "charge_text": fields, "charge_labels": labels,
        "billing_cycle": t["billing_cycle"], "billing_cycle_text": t["billing_cycle"] or NOT_VERIFIED_INFO,
        "effective_from": t["effective_from"], "effective_from_text": t["effective_from"] or NOT_VERIFIED_INFO,
        "last_verified_on": t["last_verified_on"], "last_verified_text": t["last_verified_on"] or NOT_VERIFIED_INFO,
        "source_url": t["source_url"], "verification_status": t["verification_status"], "status_label": STATUS[t["verification_status"]],
        "confidence": {"verified": "high", "partial": "partial", "not_verified": "none"}[t["verification_status"]],
        "unverified": unverified, "can_apply": bool(slabs), "not_verified_text": NOT_VERIFIED,
        "subsidies": _subsidies(row["subsidies"]), "central_subsidies": _subsidies(data["central_subsidies"]),
        "note": (f"{STATUS[t['verification_status']]}. Anything shown as '{NOT_VERIFIED}' is left out of the bill estimate, so the "
                 "estimate can be lower than your real bill. Check your DISCOM bill."),
    }


def index(symbol="₹"):
    """One line per state for lists: status, whether the tariff can be applied, and what is still unverified."""
    data = load()
    out = []
    for s in data["states"]:
        p = state_profile(s["state"], symbol, data)
        out.append({k: p[k] for k in ("state", "regulator", "discoms", "verification_status", "status_label", "can_apply", "unverified", "effective_from", "source_url")})
    return out
