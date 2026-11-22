"""Location detection (reverse geocoding) and regional electricity-pricing profiles.

IMPORTANT: profile numbers are INDICATIVE starting points, not official tariffs. Utilities revise tariffs every year and
published sources often disagree, so every profile carries a confidence note and the user can edit all values in Setup.
"""
import requests

TIMEOUT = 10
REVERSE_URL = "https://nominatim.openstreetmap.org/reverse"
UA = {"User-Agent": "WattWise/1.0 (energy budgeting app)"}

CURRENCIES = {  # country code -> symbol
    "IN": "₹", "US": "$", "GB": "£", "AU": "A$", "CA": "C$", "AE": "AED ", "DE": "€", "FR": "€", "ES": "€", "IT": "€",
    "NL": "€", "PK": "Rs ", "BD": "৳", "LK": "Rs ", "NP": "Rs ", "ZA": "R", "SG": "S$", "MY": "RM ", "SA": "SAR ",
    "NZ": "NZ$", "JP": "¥", "CN": "¥", "BR": "R$", "ID": "Rp ", "PH": "₱", "NG": "₦", "KE": "KSh ",
}

# Flat national-average style defaults (typical household energy rate per kWh). No slabs unless a region profile exists.
COUNTRY_DEFAULTS = {
    "IN": {"tariff": 6.5}, "US": {"tariff": 0.17}, "GB": {"tariff": 0.27}, "AU": {"tariff": 0.33},
    "CA": {"tariff": 0.14}, "AE": {"tariff": 0.30}, "DE": {"tariff": 0.38},
}

# Region-specific profiles: (country, state) -> slabs [(upper_bound_units | None, rate)], fixed charge, duty %.
# Indian states are NOT listed here: they come from tariff_data.json (services/tariff_data.py), which records what is
# verified and what is not. Add profiles for other countries here.
REGION_PROFILES = {}


def symbol_for(country_code):
    return CURRENCIES.get((country_code or "").upper(), "")


def profile_for(country_code, state=None):
    """Return the best-matching pricing profile (never None for a known country; None if nothing is known)."""
    cc = (country_code or "").upper()
    if not cc:
        return None
    if cc == "IN" and state:
        from services import tariff_data  # the file lists the states it covers; others fall through to the country default
        try:
            from_file = tariff_data.state_profile(state, symbol_for("IN"))
        except tariff_data.TariffDataError:
            from_file = None
        if from_file:
            return from_file
    prof = REGION_PROFILES.get((cc, state or ""))
    base = COUNTRY_DEFAULTS.get(cc)
    if prof:
        return {**prof, "tariff": prof["slabs"][0][1], "currency_symbol": symbol_for(cc), "country_code": cc,
                "region_name": state, "level": "region"}
    if base:
        return {"slabs": [], "tariff": base["tariff"], "fixed_charges": 0, "duty_percent": 0, "free_units": 0,
                "confidence": "generic", "currency_symbol": symbol_for(cc), "country_code": cc, "region_name": state,
                "level": "country",
                "note": "Typical national-average flat rate. Your utility's real tariff, slabs, duties and subsidies will differ — edit these values."}
    return {"slabs": [], "tariff": None, "fixed_charges": None, "duty_percent": 0, "free_units": 0, "confidence": "none",
            "currency_symbol": symbol_for(cc), "country_code": cc, "region_name": state, "level": "none",
            "note": "No built-in tariff for this country. Enter your own tariff in Setup."}


class RegionError(Exception):
    pass


def reverse_geocode(lat, lon):
    """Turn coordinates into a place name + region using OpenStreetMap Nominatim (only coordinates are sent)."""
    try:
        r = requests.get(REVERSE_URL, params={"format": "jsonv2", "lat": lat, "lon": lon, "zoom": 10, "accept-language": "en"},
                         headers=UA, timeout=TIMEOUT)
        r.raise_for_status()
        j = r.json()
        a = j.get("address") or {}
        if not a:
            raise RegionError("No address found for these coordinates.")
        place = a.get("city") or a.get("town") or a.get("village") or a.get("county") or a.get("state_district") or ""
        state = a.get("state") or a.get("region") or ""
        country = a.get("country") or ""
        return {"place": place, "state": state, "country": country, "country_code": (a.get("country_code") or "").upper(),
                "label": ", ".join(p for p in (place, state, country) if p)}
    except RegionError:
        raise
    except (requests.RequestException, ValueError) as e:
        raise RegionError(f"Could not look up your region ({e.__class__.__name__}).")


def parse_slabs(text):
    """'100:4.49, 300:6.34, rest:7.30' -> [[100, 4.49], [300, 6.34], [None, 7.30]]. Raises ValueError on bad input."""
    text = (text or "").strip()
    if not text:
        return []
    out, last = [], 0.0
    parts = [p.strip() for p in text.replace("\n", ",").split(",") if p.strip()]
    for i, p in enumerate(parts):
        if ":" not in p:
            raise ValueError("Each slab must look like 100:4.49 (up to 100 units at 4.49) or rest:7.30")
        up, rate = [x.strip().lower() for x in p.split(":", 1)]
        rate = float(rate)
        if rate < 0 or rate > 10000:
            raise ValueError("Slab rates must be between 0 and 10000")
        if up in ("rest", "above", "inf", "*", ""):
            if i != len(parts) - 1:
                raise ValueError("The 'rest' slab must be last")
            out.append([None, rate])
        else:
            u = float(up)
            if u <= last:
                raise ValueError("Slab limits must increase (e.g. 100:4.5, 300:6.3, rest:7.3)")
            out.append([u, rate]); last = u
    if out and out[-1][0] is not None:
        raise ValueError("The final slab must be 'rest:<rate>' so every unit has a price")
    return out


def slabs_text(slabs):
    return ", ".join(f"{'rest' if u is None else f'{u:g}'}:{r:g}" for u, r in (slabs or []))
