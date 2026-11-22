"""Optional weather fetching from Open-Meteo (free, no API key).

This file is NOT part of the WattWise logic: run_wattwise never imports it. Use it to
build the `forecast` argument for run_wattwise, or ignore it and pass your own forecast.
Python 3 standard library only (urllib + json).
"""

import json
import math
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta
from typing import Any, Dict, List, Tuple

from api import validate_forecast

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

GEOCODING_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
DAILY_VARIABLES = "temperature_2m_max,temperature_2m_min,relative_humidity_2m_mean"

TIMEOUT_SECONDS = 10            # give up on a request after this long
MIN_DAYS = 1                    # Open-Meteo forecast_days limits
MAX_DAYS = 16
FILL_DECIMALS = 1               # averages used to fill missing days are rounded to this

# Hardcoded offline forecast: (max_temp, min_temp, humidity) for 7 days from 11 May 2026
SAMPLE_START_DATE = "2026-05-11"
SAMPLE_FORECAST_DATA = [
    (41, 29, 45), (42, 30, 50), (44, 31, 40), (45, 31, 35),
    (43, 30, 55), (40, 28, 65), (37, 27, 70),
]


class WeatherFetchError(Exception):
    """Weather data could not be fetched or was not usable. The message is readable as is."""


class CityNotFoundError(ValueError):
    """The geocoding service has no result for the city name (a ValueError, as documented)."""


# ---------------------------------------------------------------------------
# LOW-LEVEL HTTP
# ---------------------------------------------------------------------------

def _is_timeout(error: BaseException) -> bool:
    return isinstance(error, (socket.timeout, TimeoutError))


def _http_error_reason(error: urllib.error.HTTPError) -> str:
    """Open-Meteo explains errors as {"error": true, "reason": "..."}; return that text if present."""
    try:
        body = json.loads(error.read().decode("utf-8"))
        return str(body.get("reason", ""))
    except Exception:       # any problem reading the body just means "no extra detail"
        return ""


def _get_json(url: str, what: str) -> Dict[str, Any]:
    """GET a URL and return the parsed JSON object. Any failure raises WeatherFetchError.

    `what` names the service in error messages, e.g. "geocoding service".
    """
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:        # server answered with an error status
        reason = _http_error_reason(error)
        detail = f": {reason}" if reason else ""
        raise WeatherFetchError(f"{what} returned HTTP {error.code}{detail}") from error
    except urllib.error.URLError as error:         # no connection, DNS failure, ...
        if _is_timeout(error.reason):
            raise WeatherFetchError(f"{what} timed out after {TIMEOUT_SECONDS} seconds") from error
        raise WeatherFetchError(f"could not reach the {what} ({error.reason})") from error
    except OSError as error:                       # timeouts and dropped connections while reading
        if _is_timeout(error):
            raise WeatherFetchError(f"{what} timed out after {TIMEOUT_SECONDS} seconds") from error
        raise WeatherFetchError(f"network error while contacting the {what} ({error})") from error

    try:
        data = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as error:   # JSONDecodeError is a ValueError
        raise WeatherFetchError(f"{what} sent a response that is not valid JSON") from error
    if not isinstance(data, dict):
        raise WeatherFetchError(f"{what} sent an unexpected response (expected a JSON object)")
    return data


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


# ---------------------------------------------------------------------------
# 1. GEOCODING
# ---------------------------------------------------------------------------

def geocode_city(city_name: str) -> Dict[str, Any]:
    """Find a city. Returns {"name", "latitude", "longitude"} for the best match.

    Raises CityNotFoundError (a ValueError, message "city not found: ...") if there is
    no result, and WeatherFetchError if the service cannot be used.
    """
    if not isinstance(city_name, str) or not city_name.strip():
        raise ValueError("city name must be a non-empty string")
    city = city_name.strip()

    url = GEOCODING_URL + "?" + urllib.parse.urlencode({"name": city, "count": 1})
    data = _get_json(url, "geocoding service")

    results = data.get("results")
    if not results:                       # Open-Meteo leaves "results" out when nothing matches
        raise CityNotFoundError(f"city not found: {city}")
    if not isinstance(results, list) or not isinstance(results[0], dict):
        raise WeatherFetchError("geocoding service sent an unexpected response (bad 'results')")

    best = results[0]
    name, lat, lon = best.get("name"), best.get("latitude"), best.get("longitude")
    if not isinstance(name, str) or not _is_number(lat) or not _is_number(lon):
        raise WeatherFetchError("geocoding service sent a result without a usable name or position")
    return {"name": name, "latitude": float(lat), "longitude": float(lon)}


# ---------------------------------------------------------------------------
# 2. FORECAST
# ---------------------------------------------------------------------------

def _convert_daily(daily: Any) -> List[Dict[str, Any]]:
    """Turn Open-Meteo's "daily" arrays into the forecast format run_wattwise expects."""
    if not isinstance(daily, dict):
        raise WeatherFetchError("forecast service sent a response without a 'daily' section")

    fields = {
        "time": "time",
        "max_temp": "temperature_2m_max",
        "min_temp": "temperature_2m_min",
        "humidity": "relative_humidity_2m_mean",
    }
    arrays = {}
    for key, api_name in fields.items():
        values = daily.get(api_name)
        if not isinstance(values, list):
            raise WeatherFetchError(f"forecast service sent no '{api_name}' list")
        arrays[key] = values

    count = len(arrays["time"])
    if count == 0:
        raise WeatherFetchError("forecast service sent no days")
    if any(len(values) != count for values in arrays.values()):
        raise WeatherFetchError("forecast service sent lists of different lengths")

    # At the far end of the forecast range Open-Meteo sends a last day where EVERY value is
    # null (for example the 16th day when 16 days are requested). That just means "no data
    # that far ahead", so drop such trailing days. Nulls anywhere else are real problems.
    while count > 0 and all(arrays[key][count - 1] is None for key in ("max_temp", "min_temp", "humidity")):
        count -= 1
    if count == 0:
        raise WeatherFetchError("forecast service has no usable days (all values are null)")

    forecast = []
    for i in range(count):
        day = arrays["time"][i]
        row: Dict[str, Any] = {"date": day}
        for key in ("max_temp", "min_temp", "humidity"):
            value = arrays[key][i]
            if value is None:
                raise WeatherFetchError(
                    f"forecast service has no value for {fields[key]} on {day} (null in the data)")
            if not _is_number(value):
                raise WeatherFetchError(
                    f"forecast service sent a non-number for {fields[key]} on {day}: {value!r}")
            row[key] = float(value)
        forecast.append(row)
    return forecast


def fetch_forecast(latitude: float, longitude: float, days: int = 7) -> List[Dict[str, Any]]:
    """Fetch a daily forecast and return it in the format run_wattwise expects:
    [{"date": "YYYY-MM-DD", "max_temp": float, "min_temp": float, "humidity": float}, ...]

    days must be a whole number from 1 to 16 (ValueError otherwise, before any network call).
    The result can be shorter than `days`: trailing days that Open-Meteo has no data for
    (all values null, typically the 16th day) are dropped.
    Every other problem raises WeatherFetchError with a readable message.
    """
    if isinstance(days, bool) or not isinstance(days, int) or not MIN_DAYS <= days <= MAX_DAYS:
        raise ValueError(f"days must be a whole number from {MIN_DAYS} to {MAX_DAYS}, got {days!r}")
    if not _is_number(latitude) or not -90 <= latitude <= 90:
        raise ValueError(f"latitude must be a number from -90 to 90, got {latitude!r}")
    if not _is_number(longitude) or not -180 <= longitude <= 180:
        raise ValueError(f"longitude must be a number from -180 to 180, got {longitude!r}")

    params = {
        "latitude": latitude,
        "longitude": longitude,
        "daily": DAILY_VARIABLES,
        "timezone": "auto",         # dates are the local calendar days of that place
        "forecast_days": days,
    }
    url = FORECAST_URL + "?" + urllib.parse.urlencode(params, safe=",")
    data = _get_json(url, "forecast service")

    forecast = _convert_daily(data.get("daily"))

    # Same checks run_wattwise applies (valid dates, humidity 0-100, min <= max, ...)
    try:
        validate_forecast(forecast)
    except ValueError as error:
        raise WeatherFetchError(f"forecast service sent data that is not usable: {error}") from error
    return forecast


# ---------------------------------------------------------------------------
# 3. BY CITY NAME
# ---------------------------------------------------------------------------

def fetch_forecast_for_city(city_name: str, days: int = 7) -> List[Dict[str, Any]]:
    """Look up the city, then fetch its forecast (see geocode_city and fetch_forecast)."""
    place = geocode_city(city_name)
    return fetch_forecast(place["latitude"], place["longitude"], days)


# ---------------------------------------------------------------------------
# 4. FILL THE REST OF THE MONTH
# ---------------------------------------------------------------------------

def fill_month_forecast(
    forecast: List[Dict[str, Any]], days_in_month: int, start_date: str
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Extend a forecast so it covers every remaining day of the month.

    Remaining days = days_in_month - (day of month of start_date) + 1, so a start_date
    of the 11th in a 31-day month needs 21 days. If the forecast is shorter, each missing
    day repeats the average max_temp, min_temp and humidity of the given days (rounded to
    1 decimal), dated one calendar day after the last forecast day. Longer forecasts are
    returned unchanged. Returns (forecast, warnings); the input list is not modified.
    """
    validate_forecast(forecast)             # ValueError if empty or malformed
    start = date.fromisoformat(start_date)
    if not isinstance(days_in_month, int) or isinstance(days_in_month, bool) \
            or not 28 <= days_in_month <= 31:
        raise ValueError(f"days_in_month must be a whole number from 28 to 31, got {days_in_month!r}")
    remaining = days_in_month - start.day + 1
    if remaining < 1:
        raise ValueError(f"start_date {start_date} is after the end of a {days_in_month}-day month")

    result = [dict(day) for day in forecast]
    warnings: List[str] = []

    if forecast[0]["date"] != start_date:
        warnings.append(f"forecast starts on {forecast[0]['date']} but start_date is {start_date}; "
                        f"the number of remaining days is counted from start_date")

    have = len(result)
    if have >= remaining:
        return result, warnings

    def average(key: str) -> float:
        return round(sum(day[key] for day in forecast) / have, FILL_DECIMALS)

    filler = {"max_temp": average("max_temp"), "min_temp": average("min_temp"),
              "humidity": average("humidity")}
    last = date.fromisoformat(forecast[-1]["date"])
    for i in range(remaining - have):
        result.append({"date": (last + timedelta(days=i + 1)).isoformat(), **filler})

    first_filled, last_filled = result[have]["date"], result[-1]["date"]
    if remaining - have == 1:
        warnings.append(f"forecast day {remaining} is estimated using the average of the fetched "
                        f"days ({first_filled})")
    else:
        warnings.append(f"forecast days {have + 1}-{remaining} are estimated using the average of "
                        f"the fetched days ({first_filled} to {last_filled})")
    return result, warnings


# ---------------------------------------------------------------------------
# 5. OFFLINE FALLBACK
# ---------------------------------------------------------------------------

def load_sample_forecast() -> List[Dict[str, Any]]:
    """The hardcoded 7-day sample forecast (11-17 May 2026). Returns a fresh list every call."""
    start = date.fromisoformat(SAMPLE_START_DATE)
    return [{"date": (start + timedelta(days=i)).isoformat(),
             "max_temp": hi, "min_temp": lo, "humidity": hum}
            for i, (hi, lo, hum) in enumerate(SAMPLE_FORECAST_DATA)]


# Cold-wave sample (moved here from main.py): (max_temp, min_temp, humidity) from 11 Jan 2026
COLD_SAMPLE_START_DATE = "2026-01-11"
COLD_SAMPLE_FORECAST_DATA = [(15, 8, 60), (14, 7, 60), (12, 4, 60), (11, 2, 60)]


def load_hot_sample_forecast() -> List[Dict[str, Any]]:
    """The hot sample forecast (same data as load_sample_forecast: 41-45 C in May)."""
    return load_sample_forecast()


def load_cold_sample_forecast() -> List[Dict[str, Any]]:
    """A 4-day winter forecast (11-14 Jan 2026) that triggers a cold-wave alert. Fresh list every call."""
    start = date.fromisoformat(COLD_SAMPLE_START_DATE)
    return [{"date": (start + timedelta(days=i)).isoformat(),
             "max_temp": hi, "min_temp": lo, "humidity": hum}
            for i, (hi, lo, hum) in enumerate(COLD_SAMPLE_FORECAST_DATA)]


def fetch_forecast_safe(city_name: str, days: int = 7) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Try the live forecast for a city; on failure use the sample forecast instead.

    Returns (forecast, warnings). Warnings is [] when live data was used, otherwise
    ["live weather unavailable (<reason>); using sample data"]. Network problems, bad
    responses and unknown cities are all caught, so this never crashes a demo.
    A bad `days` value is a programming mistake and still raises ValueError.
    """
    try:
        return fetch_forecast_for_city(city_name, days), []
    except (WeatherFetchError, CityNotFoundError) as error:
        sample = load_sample_forecast()[:days]
        return sample, [f"live weather unavailable ({error}); using sample data"]


# ---------------------------------------------------------------------------
# TEST CASES (run with: python weather.py). No internet needed: urlopen is mocked.
# ---------------------------------------------------------------------------

def _fake_response(payload: Any):
    """A stand-in for what urllib.request.urlopen returns (works with `with`)."""
    from unittest.mock import MagicMock
    text = payload if isinstance(payload, str) else json.dumps(payload)
    response = MagicMock()
    response.__enter__.return_value = response
    response.read.return_value = text.encode("utf-8")
    return response


def _daily(n: int = 3, **overrides: Any) -> Dict[str, Any]:
    """A sample Open-Meteo forecast response with n days (arrays can be overridden)."""
    daily = {
        "time": [f"2026-05-{11 + i}" for i in range(n)],
        "temperature_2m_max": [40.5 + i for i in range(n)],
        "temperature_2m_min": [28.0 + i for i in range(n)],
        "relative_humidity_2m_mean": [45 + i for i in range(n)],
    }
    daily.update(overrides)
    return {"latitude": 28.6, "longitude": 77.2, "daily": daily}


def _expect(exception_type, text: str, function, *args) -> None:
    """Assert that function(*args) raises exception_type with `text` in its message."""
    try:
        function(*args)
    except exception_type as error:
        assert text in str(error), f"expected {text!r} in {str(error)!r}"
    else:
        raise AssertionError(f"expected {exception_type.__name__} containing {text!r}")


def _test_fetch_forecast_conversion_and_request() -> None:
    from unittest.mock import patch
    with patch("urllib.request.urlopen", return_value=_fake_response(_daily(3))) as mock:
        forecast = fetch_forecast(28.6, 77.2, days=3)
    assert forecast == [
        {"date": "2026-05-11", "max_temp": 40.5, "min_temp": 28.0, "humidity": 45.0},
        {"date": "2026-05-12", "max_temp": 41.5, "min_temp": 29.0, "humidity": 46.0},
        {"date": "2026-05-13", "max_temp": 42.5, "min_temp": 30.0, "humidity": 47.0},
    ]
    assert all(isinstance(day[k], float) for day in forecast for k in ("max_temp", "min_temp", "humidity"))

    url = mock.call_args[0][0]
    assert url.startswith("https://api.open-meteo.com/v1/forecast?")
    assert "daily=temperature_2m_max,temperature_2m_min,relative_humidity_2m_mean" in url
    assert "timezone=auto" in url and "forecast_days=3" in url
    assert "latitude=28.6" in url and "longitude=77.2" in url
    assert mock.call_args[1]["timeout"] == 10


def _test_days_must_be_1_to_16() -> None:
    from unittest.mock import patch
    with patch("urllib.request.urlopen") as mock:
        for bad in (0, 17, -3, "7", 7.5, True):
            _expect(ValueError, "days must be a whole number from 1 to 16", fetch_forecast, 28.6, 77.2, bad)
        _expect(ValueError, "latitude", fetch_forecast, 95.0, 77.2, 3)
        _expect(ValueError, "longitude", fetch_forecast, 28.6, 200.0, 3)
        assert not mock.called                      # rejected before any network call
    for good in (1, 16):                            # both limits are accepted
        with patch("urllib.request.urlopen", return_value=_fake_response(_daily(1))) as mock:
            fetch_forecast(28.6, 77.2, good)
        assert f"forecast_days={good}" in mock.call_args[0][0]


def _test_null_and_bad_values() -> None:
    from unittest.mock import patch

    def fetch_with(payload):
        with patch("urllib.request.urlopen", return_value=_fake_response(payload)):
            return fetch_forecast(28.6, 77.2, 3)

    _expect(WeatherFetchError, "no value for relative_humidity_2m_mean on 2026-05-12",
            fetch_with, _daily(3, relative_humidity_2m_mean=[45, None, 47]))
    _expect(WeatherFetchError, "no value for temperature_2m_max on 2026-05-11",
            fetch_with, _daily(3, temperature_2m_max=[None, 41, 42]))
    _expect(WeatherFetchError, "non-number", fetch_with, _daily(3, temperature_2m_min=[28, "warm", 30]))
    _expect(WeatherFetchError, "non-number", fetch_with, _daily(3, temperature_2m_min=[28, True, 30]))
    # Same checks as run_wattwise: humidity 0-100, valid dates, min <= max
    _expect(WeatherFetchError, "humidity must be at most 100",
            fetch_with, _daily(3, relative_humidity_2m_mean=[45, 120, 47]))
    _expect(WeatherFetchError, "min_temp cannot be above max_temp",
            fetch_with, _daily(3, temperature_2m_min=[28, 50, 30]))
    _expect(WeatherFetchError, "YYYY-MM-DD", fetch_with, _daily(3, time=["2026-05-11", "tomorrow", "2026-05-13"]))


def _test_trailing_all_null_days_are_dropped() -> None:
    """Open-Meteo's 16th day can be all null (seen in real data): drop it, keep the rest."""
    from unittest.mock import patch

    def fetch_with(payload):
        with patch("urllib.request.urlopen", return_value=_fake_response(payload)):
            return fetch_forecast(28.6, 77.2, 4)

    last_day_null = _daily(4, temperature_2m_max=[40, 41, 42, None],
                           temperature_2m_min=[28, 29, 30, None],
                           relative_humidity_2m_mean=[45, 46, 47, None])
    forecast = fetch_with(last_day_null)
    assert [d["date"] for d in forecast] == ["2026-05-11", "2026-05-12", "2026-05-13"]

    two_days_null = _daily(4, temperature_2m_max=[40, 41, None, None],
                           temperature_2m_min=[28, 29, None, None],
                           relative_humidity_2m_mean=[45, 46, None, None])
    assert len(fetch_with(two_days_null)) == 2

    # Only SOME values null on the last day is a real problem, not "no data that far ahead"
    partly_null = _daily(4, relative_humidity_2m_mean=[45, 46, 47, None])
    _expect(WeatherFetchError, "no value for relative_humidity_2m_mean on 2026-05-14", fetch_with, partly_null)
    # A null day in the middle, followed by good data, is also an error
    middle_null = _daily(4, temperature_2m_max=[40, None, 42, 43],
                         temperature_2m_min=[28, None, 30, 31],
                         relative_humidity_2m_mean=[45, None, 47, 48])
    _expect(WeatherFetchError, "on 2026-05-12", fetch_with, middle_null)
    # Nothing usable at all
    all_null = _daily(2, temperature_2m_max=[None, None], temperature_2m_min=[None, None],
                      relative_humidity_2m_mean=[None, None])
    _expect(WeatherFetchError, "no usable days", fetch_with, all_null)


def _test_bad_responses() -> None:
    from unittest.mock import patch

    def fetch_with(response):
        with patch("urllib.request.urlopen", return_value=response):
            return fetch_forecast(28.6, 77.2, 3)

    _expect(WeatherFetchError, "not valid JSON", fetch_with, _fake_response("<html>oops</html>"))
    _expect(WeatherFetchError, "expected a JSON object", fetch_with, _fake_response("[1, 2]"))
    _expect(WeatherFetchError, "'daily' section", fetch_with, _fake_response({"latitude": 1}))
    _expect(WeatherFetchError, "no 'temperature_2m_max' list",
            fetch_with, _fake_response({"daily": {"time": ["2026-05-11"]}}))
    _expect(WeatherFetchError, "different lengths",
            fetch_with, _fake_response(_daily(3, temperature_2m_max=[40, 41])))
    _expect(WeatherFetchError, "no days", fetch_with, _fake_response(_daily(0)))


def _test_network_and_http_errors() -> None:
    from unittest.mock import patch

    def fetch_failing(error):
        with patch("urllib.request.urlopen", side_effect=error):
            return fetch_forecast(28.6, 77.2, 3)

    http_500 = urllib.error.HTTPError("https://x", 500, "Server Error", {}, None)
    _expect(WeatherFetchError, "forecast service returned HTTP 500", fetch_failing, http_500)
    _expect(WeatherFetchError, "could not reach the forecast service (Name or service not known)",
            fetch_failing, urllib.error.URLError("Name or service not known"))
    _expect(WeatherFetchError, "timed out after 10 seconds", fetch_failing, socket.timeout("timed out"))
    _expect(WeatherFetchError, "timed out after 10 seconds",
            fetch_failing, urllib.error.URLError(socket.timeout("timed out")))
    _expect(WeatherFetchError, "network error", fetch_failing, ConnectionResetError("reset"))


def _test_geocoding() -> None:
    from unittest.mock import patch
    found = {"results": [{"name": "Delhi", "latitude": 28.65195, "longitude": 77.23149,
                          "country": "India"}]}
    with patch("urllib.request.urlopen", return_value=_fake_response(found)) as mock:
        place = geocode_city("  Delhi ")
    assert place == {"name": "Delhi", "latitude": 28.65195, "longitude": 77.23149}
    assert mock.call_args[0][0] == "https://geocoding-api.open-meteo.com/v1/search?name=Delhi&count=1"

    with patch("urllib.request.urlopen", return_value=_fake_response(found)) as mock:
        geocode_city("New York")
    assert "name=New+York&count=1" in mock.call_args[0][0]    # special characters are encoded

    with patch("urllib.request.urlopen", return_value=_fake_response({"generationtime_ms": 0.2})):
        _expect(ValueError, "city not found: Atlantis", geocode_city, "Atlantis")
    with patch("urllib.request.urlopen", return_value=_fake_response({"results": []})):
        _expect(CityNotFoundError, "city not found: Atlantis", geocode_city, "Atlantis")
    with patch("urllib.request.urlopen", return_value=_fake_response({"results": [{"name": "X"}]})):
        _expect(WeatherFetchError, "usable name or position", geocode_city, "X")
    _expect(ValueError, "non-empty", geocode_city, "   ")


def _test_fetch_forecast_for_city() -> None:
    from unittest.mock import patch
    geo = {"results": [{"name": "Delhi", "latitude": 28.6, "longitude": 77.2}]}
    with patch("urllib.request.urlopen",
               side_effect=[_fake_response(geo), _fake_response(_daily(2))]) as mock:
        forecast = fetch_forecast_for_city("Delhi", days=2)
    assert [d["date"] for d in forecast] == ["2026-05-11", "2026-05-12"]
    assert mock.call_count == 2
    assert "latitude=28.6" in mock.call_args_list[1][0][0]     # forecast used the geocoded position


def _days_from(start_iso: str, count: int) -> List[Dict[str, Any]]:
    """A forecast of `count` days from start_iso. Day i has max 30+i, min 20+i, humidity 40+i."""
    first = date.fromisoformat(start_iso)
    return [{"date": (first + timedelta(days=i)).isoformat(), "max_temp": 30.0 + i,
             "min_temp": 20.0 + i, "humidity": 40.0 + i} for i in range(count)]


def _test_fill_month_forecast() -> None:
    # 16 fetched days (1-16 May) in a 31-day month, starting on the 1st: 15 days are filled
    fetched = _days_from("2026-05-01", 16)
    filled, warnings = fill_month_forecast(fetched, 31, "2026-05-01")
    assert len(filled) == 31 and filled[:16] == fetched
    assert [d["date"] for d in filled[16:19]] == ["2026-05-17", "2026-05-18", "2026-05-19"]
    assert filled[-1]["date"] == "2026-05-31"
    # Average of the 16 fetched days: max 37.5, min 27.5, humidity 47.5
    assert all(d["max_temp"] == 37.5 and d["min_temp"] == 27.5 and d["humidity"] == 47.5
               for d in filled[16:])
    assert warnings == ["forecast days 17-31 are estimated using the average of the fetched days "
                        "(2026-05-17 to 2026-05-31)"]
    assert len(fetched) == 16                       # the input list was not changed

    # Mid-month: from the 11th of a 31-day month 21 days are needed, 7 are given
    filled, warnings = fill_month_forecast(_days_from("2026-05-11", 7), 31, "2026-05-11")
    assert len(filled) == 21 and filled[-1]["date"] == "2026-05-31"
    assert warnings == ["forecast days 8-21 are estimated using the average of the fetched days "
                        "(2026-05-18 to 2026-05-31)"]

    # Exactly one missing day reads naturally
    filled, warnings = fill_month_forecast(_days_from("2026-05-11", 20), 31, "2026-05-11")
    assert len(filled) == 21
    assert warnings == ["forecast day 21 is estimated using the average of the fetched days (2026-05-31)"]

    # Enough days already (or more): returned unchanged with no warnings
    enough = _days_from("2026-05-11", 21)
    assert fill_month_forecast(enough, 31, "2026-05-11") == (enough, [])
    longer = _days_from("2026-05-11", 25)
    assert fill_month_forecast(longer, 31, "2026-05-11") == (longer, [])

    # A forecast that does not start on start_date is reported
    _, warnings = fill_month_forecast(_days_from("2026-05-01", 16), 31, "2026-05-17")
    assert warnings == ["forecast starts on 2026-05-01 but start_date is 2026-05-17; "
                        "the number of remaining days is counted from start_date"]

    # Bad arguments
    _expect(ValueError, "after the end", fill_month_forecast, fetched, 28, "2026-05-29")
    _expect(ValueError, "28 to 31", fill_month_forecast, fetched, 40, "2026-05-01")
    _expect(ValueError, "forecast must be a non-empty list", fill_month_forecast, [], 31, "2026-05-01")


def _test_filled_forecast_works_with_run_wattwise() -> None:
    """The extended forecast is a valid run_wattwise input and the result is plain JSON."""
    from api import run_wattwise
    household = {"monthly_budget_units": 550, "family_size": 4, "appliances": [
        {"name": "AC", "wattage": 1500, "quantity": 1, "avg_hours_per_day": 8},
        {"name": "Fridge", "wattage": 150, "quantity": 1, "avg_hours_per_day": 24}]}
    forecast, warnings = fill_month_forecast(load_sample_forecast(), 31, "2026-05-11")
    result = run_wattwise(household, forecast, [14.0] * 10, 5.0)
    assert len(forecast) == 21 and len(result["monthly_prediction"]["days"]) == 21
    assert result["budget_status"]["days_left"] == 21
    json.dumps(result)


def _test_sample_forecast() -> None:
    sample = load_sample_forecast()
    assert len(sample) == 7 and sample[0] == {
        "date": "2026-05-11", "max_temp": 41, "min_temp": 29, "humidity": 45}
    validate_forecast(sample)                       # the sample passes the same checks
    sample[0]["max_temp"] = 99
    assert load_sample_forecast()[0]["max_temp"] == 41   # each call returns a fresh copy


def _test_fetch_forecast_safe_fallback() -> None:
    from unittest.mock import patch
    geo = {"results": [{"name": "Delhi", "latitude": 28.6, "longitude": 77.2}]}

    # Success: live data, no warnings
    with patch("urllib.request.urlopen", side_effect=[_fake_response(geo), _fake_response(_daily(3))]):
        forecast, warnings = fetch_forecast_safe("Delhi", 3)
    assert warnings == [] and forecast[0]["max_temp"] == 40.5

    # No internet: sample data plus a warning that says why
    with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("no route")):
        forecast, warnings = fetch_forecast_safe("Delhi", 7)
    assert forecast == load_sample_forecast()
    assert warnings == ["live weather unavailable (could not reach the geocoding service (no route)); "
                        "using sample data"]

    # Unknown city, HTTP error and garbage responses fall back too; days trims the sample
    with patch("urllib.request.urlopen", return_value=_fake_response({})):
        forecast, warnings = fetch_forecast_safe("Atlantis", 3)
    assert len(forecast) == 3 and warnings == ["live weather unavailable (city not found: Atlantis); "
                                               "using sample data"]
    with patch("urllib.request.urlopen", return_value=_fake_response("not json")):
        _, warnings = fetch_forecast_safe("Delhi", 7)
    assert "not valid JSON" in warnings[0] and warnings[0].endswith("using sample data")

    # A programming mistake is still an error
    _expect(ValueError, "days must be", fetch_forecast_safe, "Delhi", 99)


if __name__ == "__main__":
    tests = [
        _test_fetch_forecast_conversion_and_request,
        _test_days_must_be_1_to_16,
        _test_null_and_bad_values,
        _test_trailing_all_null_days_are_dropped,
        _test_bad_responses,
        _test_network_and_http_errors,
        _test_geocoding,
        _test_fetch_forecast_for_city,
        _test_fill_month_forecast,
        _test_filled_forecast_works_with_run_wattwise,
        _test_sample_forecast,
        _test_fetch_forecast_safe_fallback,
    ]
    for test in tests:
        test()
    print(f"All {len(tests)} weather tests passed.")
