"""Open-Meteo integration (geocoding + forecast) with cache fallback."""
from datetime import date, datetime, timedelta

import requests

import models

GEO_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
TIMEOUT = 10

WMO = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast", 45: "Fog", 48: "Rime fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Dense drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains", 80: "Rain showers",
    81: "Rain showers", 82: "Violent showers", 85: "Snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Thunderstorm, hail", 99: "Thunderstorm, hail",
}


class WeatherError(Exception):
    pass


def condition_for(code):
    return WMO.get(code, "Unknown")


def geocode(query):
    query = (query or "").strip()
    if len(query) < 2:
        raise WeatherError("Enter at least 2 characters of a city name")
    # Open-Meteo matches on the place name only; "Ludhiana, Punjab" -> search "Ludhiana"
    name = query.split(",")[0].strip()
    try:
        r = requests.get(GEO_URL, params={"name": name, "count": 5, "language": "en", "format": "json"}, timeout=TIMEOUT)
        r.raise_for_status()
        results = r.json().get("results") or []
    except (requests.RequestException, ValueError) as e:
        raise WeatherError(f"Location lookup failed ({e.__class__.__name__}). Check your internet connection.")
    return [{
        "name": x["name"], "admin1": x.get("admin1", ""), "country_code": (x.get("country_code") or "").upper(), "country": x.get("country", ""),
        "latitude": x["latitude"], "longitude": x["longitude"],
        "label": ", ".join(p for p in (x["name"], x.get("admin1"), x.get("country")) if p),
    } for x in results]


FORECAST_DAYS = 16          # Open-Meteo's maximum forecast horizon
OUTLOOK_DAYS = 30           # planning window: forecast for the first 16 days, climate average for the rest
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
CLIMATE_YEARS = 3           # same calendar days from the previous three years (real observations)


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


HOURLY_VARS = ("temperature_2m", "apparent_temperature", "relative_humidity_2m", "cloud_cover",
               "shortwave_radiation", "precipitation")


def parse_hourly(hourly_raw):
    """Turn Open-Meteo's column-style hourly block into one dict per local hour:
    {day, hour, temp, apparent, humidity, cloud_cover, shortwave, precipitation}. Missing values stay None."""
    times = hourly_raw.get("time") or []
    cols = {"temp": "temperature_2m", "apparent": "apparent_temperature", "humidity": "relative_humidity_2m",
            "cloud_cover": "cloud_cover", "shortwave": "shortwave_radiation", "precipitation": "precipitation"}
    out = []
    for i, ts in enumerate(times):
        row = {"day": ts[:10], "hour": int(ts[11:13])}
        for key, src in cols.items():
            vals = hourly_raw.get(src) or []
            row[key] = vals[i] if i < len(vals) else None
        out.append(row)
    return out


def fetch_open_meteo(lat, lon):
    params = {
        "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": FORECAST_DAYS,
        "current": "temperature_2m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,weather_code,precipitation_probability_max,precipitation_sum,apparent_temperature_max,apparent_temperature_min,sunrise,sunset",
        "hourly": ",".join(HOURLY_VARS),
    }
    try:
        r = requests.get(FORECAST_URL, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        j = r.json()
        d = j["daily"]
        hourly_raw = j.get("hourly") or {}
        hourly = parse_hourly(hourly_raw)
        hum = {}
        for r in hourly:
            if r.get("humidity") is not None:
                hum.setdefault(r["day"], []).append(r["humidity"])
        sunrise = d.get("sunrise") or [None] * len(d["time"])
        sunset = d.get("sunset") or [None] * len(d["time"])
        rain = d.get("precipitation_probability_max") or [None] * len(d["time"])
        rain_mm = d.get("precipitation_sum") or [None] * len(d["time"])
        app_max = d.get("apparent_temperature_max") or [None] * len(d["time"])
        app_min = d.get("apparent_temperature_min") or [None] * len(d["time"])
        forecast = [{
            "day": d["time"][i], "temp_max": d["temperature_2m_max"][i], "temp_min": d["temperature_2m_min"][i],
            "code": d["weather_code"][i], "condition": condition_for(d["weather_code"][i]),
            "humidity": _mean(hum.get(d["time"][i], [])), "rain_chance": rain[i], "rain_mm": rain_mm[i],
            "apparent_max": app_max[i], "apparent_min": app_min[i], "provider": "open-meteo",
            "sunrise": sunrise[i], "sunset": sunset[i],
        } for i in range(len(d["time"])) if d["temperature_2m_max"][i] is not None]
        c = j.get("current") or {}
        current = {"day": c.get("time", "")[:10] or d["time"][0], "temp": c.get("temperature_2m"),
                   "code": c.get("weather_code"), "condition": condition_for(c.get("weather_code"))}
        return current, forecast, hourly
    except (requests.RequestException, KeyError, ValueError, IndexError) as e:
        raise WeatherError(f"Weather service unavailable ({e.__class__.__name__}).")


def fetch_forecast(lat, lon):
    """Open-Meteo only (free, no API key). Returns (current, forecast, hourly, note)."""
    current, forecast, hourly = fetch_open_meteo(lat, lon)
    return current, forecast, hourly, None


def _shift_years(d, k):
    try:
        return d.replace(year=d.year - k)
    except ValueError:  # 29 Feb in a non-leap year
        return d.replace(year=d.year - k, day=28)


def fetch_climate_rows(lat, lon, first, last):
    """Climate-average rows for days first..last: the average of the same calendar days over the previous
    CLIMATE_YEARS years, from Open-Meteo's archive (real observations). Returns [] if unavailable."""
    if first > last:
        return []
    buckets = {}
    keys = (("temp_max", "temperature_2m_max"), ("temp_min", "temperature_2m_min"),
            ("apparent_max", "apparent_temperature_max"), ("apparent_min", "apparent_temperature_min"),
            ("rain_mm", "precipitation_sum"))
    for k in range(1, CLIMATE_YEARS + 1):
        params = {"latitude": lat, "longitude": lon, "timezone": "auto",
                  "start_date": _shift_years(first, k).isoformat(), "end_date": _shift_years(last, k).isoformat(),
                  "daily": ",".join(src for _, src in keys)}
        try:
            r = requests.get(ARCHIVE_URL, params=params, timeout=TIMEOUT)
            r.raise_for_status()
            d = r.json()["daily"]
        except (requests.RequestException, KeyError, ValueError):
            continue
        for i, ds in enumerate(d["time"]):
            b = buckets.setdefault(ds[5:], {name: [] for name, _ in keys})
            for name, src in keys:
                v = d[src][i]
                if v is not None:
                    b[name].append(v)
    rows, cur = [], first
    while cur <= last:
        md = cur.isoformat()[5:]
        b = buckets.get(md) or buckets.get("02-28" if md == "02-29" else md)
        if b:
            avg = {n: (round(sum(v) / len(v), 1) if v else None) for n, v in b.items()}
            rows.append({"day": cur.isoformat(), "temp_max": avg["temp_max"], "temp_min": avg["temp_min"],
                         "apparent_max": avg["apparent_max"], "apparent_min": avg["apparent_min"], "rain_mm": avg["rain_mm"],
                         "humidity": None, "rain_chance": None, "code": None,
                         "condition": "Climate average (same date, previous years)", "provider": "open-meteo-archive"})
        cur += timedelta(days=1)
    return rows


def climate_for_outlook(lat, lon, forecast):
    """Climate rows for the days after the forecast, up to OUTLOOK_DAYS from today."""
    today = date.today()
    last = today + timedelta(days=OUTLOOK_DAYS - 1)
    fdays = [date.fromisoformat(f["day"]) for f in forecast]
    first = (max(fdays) + timedelta(days=1)) if fdays else today
    try:
        return fetch_climate_rows(lat, lon, first, last)
    except Exception:  # the outlook is optional; forecasts must still work
        return []


CACHE_MINUTES = 180  # Module 1: refresh the forecast every 3 hours


def get_weather(refresh=False, max_age_minutes=CACHE_MINUTES):
    """Return weather dict. Uses live API when refreshing or cache is stale; falls back to cache on failure."""
    h = models.get_household()
    cur, fc = models.load_weather()
    if h["latitude"] is None or h["longitude"] is None:
        return {"available": False, "reason": "No location set. Add your city in Household Setup.", "forecast": [], "current": None}
    stale = True
    if cur and cur.get("fetched_at"):
        age = (datetime.now() - datetime.fromisoformat(cur["fetched_at"])).total_seconds() / 60
        stale = age > max_age_minutes
    error = None
    note = None
    if refresh or stale or not fc:
        try:
            current, forecast, hourly, note = fetch_forecast(h["latitude"], h["longitude"])
            models.save_weather(current, forecast, climate_for_outlook(h["latitude"], h["longitude"], forecast), hourly)
            cur, fc = models.load_weather()
        except WeatherError as e:
            error = str(e)
    if not fc:
        return {"available": False, "reason": error or "Weather unavailable", "forecast": [], "current": None}
    hourly_by_day = models.load_hourly_all()

    def hourly_apparent(day):
        rows = hourly_by_day.get(day) or []
        vals = [r.get("apparent") if r.get("apparent") is not None else r.get("temp") for r in rows]
        return [round(v, 1) for v in vals] if len(vals) == 24 and all(v is not None for v in vals) else None

    return {
        "available": True, "location": h["location_name"], "source": "Open-Meteo",
        "fetched_at": cur["fetched_at"] if cur else fc[0]["fetched_at"],
        "stale_warning": (error + " Showing last saved forecast.") if error else note,
        "providers": sorted({f.get("provider") for f in fc if f.get("provider")}) or ["open-meteo"],
        "current": ({"temp": cur["current_temp"], "condition": cur["condition"], "day": cur["day"]} if cur else None),
        "climatology": [{k: v for k, v in c.items() if k in ("day", "temp_max", "temp_min", "apparent_max", "apparent_min", "rain_mm",
                                                              "humidity", "rain_chance", "condition", "provider")} | {"source": "climatology"}
                        for c in models.load_climate()],
        "forecast": [{"day": f["day"], "temp_max": f["temp_max"], "temp_min": f["temp_min"], "source": "forecast",
                      "apparent_max": f.get("apparent_max"), "apparent_min": f.get("apparent_min"),
                      "condition": f["condition"], "code": f["weather_code"], "humidity": f.get("humidity"),
                      "rain_chance": f.get("rain_chance"), "rain_mm": f.get("rain_mm"),
                      "sunrise": f.get("sunrise"), "sunset": f.get("sunset"),
                      "hourly_apparent": hourly_apparent(f["day"]),
                      "provider": f.get("provider")} for f in fc],
    }


def fetch_past_hourly(lat, lon, first, last):
    """Observed hourly temperature, feels-like and humidity for days first..last (Open-Meteo archive).
    Returns {day: [24 hourly dicts]}; {} if the archive is unavailable. Used to train the daily load model."""
    if first > last:
        return {}
    params = {"latitude": lat, "longitude": lon, "timezone": "auto",
              "start_date": first.isoformat(), "end_date": last.isoformat(),
              "hourly": "temperature_2m,apparent_temperature,relative_humidity_2m"}
    try:
        r = requests.get(ARCHIVE_URL, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        rows = parse_hourly(r.json().get("hourly") or {})
    except (requests.RequestException, KeyError, ValueError):
        return {}
    out = {}
    for row in rows:
        out.setdefault(row["day"], []).append(row)
    return {d: v for d, v in out.items() if len(v) == 24}
