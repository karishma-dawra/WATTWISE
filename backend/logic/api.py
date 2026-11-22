"""WattWise API: the single entry point for a UI or backend.

    from api import run_wattwise
    result = run_wattwise(household, forecast, daily_consumption, today_units_so_far)

Input and output are plain JSON-serializable dicts, lists, strings, numbers and
booleans. No dataclass objects leave this file. See README.md for the exact format.
Bad input raises ValueError with a message that says which field is wrong.
"""

import calendar
import json
import os
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

from advisor import VALID_CLASSES, classify_appliance, recommend_hours, split_into_slots
from alerts import generate_alerts
from budget import (
    DAYS_PER_WEEK, PROJECTION_NOTE, BudgetTracker, PilotResult, UsagePilot, allocate_daily_budget, check_budget_exceed,
    min_runs_per_week, predict_daily_units, predict_monthly_units, schedule_flexible_runs,
)
from models import Appliance, Household, WeatherDay

DECIMALS = 2   # all hours, units and percentages in the output are rounded to this many decimals


# ---------------------------------------------------------------------------
# INPUT PARSING AND VALIDATION (dict -> model objects)
# ---------------------------------------------------------------------------

def _get(data: Any, key: str, where: str) -> Any:
    """Return data[key], or raise a clear error if data is not a dict or the key is missing."""
    if not isinstance(data, dict):
        raise ValueError(f"{where} must be an object (dict)")
    if key not in data:
        raise ValueError(f"{where} is missing '{key}'")
    return data[key]


def _number(value: Any, name: str, minimum: Optional[float] = None,
            maximum: Optional[float] = None) -> float:
    """Check that value is a real number (not a bool) within [minimum, maximum]."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"{name} must be a finite number")
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be at least {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be at most {maximum}")
    return float(value)


def _whole_number(value: Any, name: str, minimum: int) -> int:
    """Check that value is a whole number of at least `minimum`."""
    number = _number(value, name, minimum)
    if number != int(number):
        raise ValueError(f"{name} must be a whole number")
    return int(number)


def _text(value: Any, name: str) -> str:
    """Check that value is a non-empty string."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _category(value: Any, name: str) -> Tuple[str, Optional[str]]:
    """Optional appliance category. Returns (category, warning_or_None).

    - missing / null / empty        -> ("", None): classify by name
    - weather_dependent/essential/flexible (any case) -> (lowercase word, None)
    - any other text                -> (the text, a warning): classify by name instead
    - not text (e.g. a number)      -> ValueError
    """
    if value is None:
        return "", None
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    cleaned = value.strip()
    if cleaned.lower() in VALID_CLASSES:
        return cleaned.lower(), None
    if not cleaned:
        return "", None
    return cleaned, f'{name} "{cleaned}" is not recognized; classified by name instead'


def _parse_household(data: Any) -> Tuple[Household, Optional[int], List[str]]:
    """Build a Household from a dict.

    Also returns the optional days_in_month override and a list of warning strings.
    """
    warnings: List[str] = []
    budget = _number(_get(data, "monthly_budget_units", "household"),
                     "household.monthly_budget_units", minimum=0.01)
    family = _whole_number(_get(data, "family_size", "household"), "household.family_size", 1)

    days_in_month = None
    if "days_in_month" in data and data["days_in_month"] is not None:
        days_in_month = _whole_number(data["days_in_month"], "household.days_in_month", 28)
        if days_in_month > 31:
            raise ValueError("household.days_in_month must be at most 31")

    raw_appliances = _get(data, "appliances", "household")
    if not isinstance(raw_appliances, list):
        raise ValueError("household.appliances must be a list")

    appliances: List[Appliance] = []
    seen = set()
    for i, raw in enumerate(raw_appliances):
        where = f"household.appliances[{i}]"
        name = _text(_get(raw, "name", where), f"{where}.name")
        if name.lower() in seen:
            raise ValueError(f"{where}.name '{name}' is a duplicate; appliance names must be unique")
        seen.add(name.lower())
        category, warning = _category(raw.get("category"), f"{where}.category")
        appliance = Appliance(
            name=name,
            wattage=_number(_get(raw, "wattage", where), f"{where}.wattage", minimum=0),
            quantity=_whole_number(_get(raw, "quantity", where), f"{where}.quantity", 1),
            category=category,
            avg_hours_per_day=_number(_get(raw, "avg_hours_per_day", where),
                                      f"{where}.avg_hours_per_day", minimum=0, maximum=24),
        )
        try:
            classify_appliance(appliance)   # catches e.g. "weather_dependent" with an unknown name
        except ValueError as error:
            raise ValueError(f"{where}: {error}") from None
        appliances.append(appliance)
        if warning:
            warnings.append(warning)
    return Household(budget, family, appliances), days_in_month, warnings


def _parse_forecast(data: Any) -> List[WeatherDay]:
    """Build a list of WeatherDay from a list of dicts."""
    if not isinstance(data, list) or not data:
        raise ValueError("forecast must be a non-empty list (the first item is today)")
    days: List[WeatherDay] = []
    for i, raw in enumerate(data):
        where = f"forecast[{i}]"
        text = _text(_get(raw, "date", where), f"{where}.date")
        try:
            day = date.fromisoformat(text)
        except ValueError:
            raise ValueError(f"{where}.date must look like YYYY-MM-DD, got '{text}'") from None
        max_temp = _number(_get(raw, "max_temp", where), f"{where}.max_temp", -60, 70)
        min_temp = _number(_get(raw, "min_temp", where), f"{where}.min_temp", -60, 70)
        if min_temp > max_temp:
            raise ValueError(f"{where}: min_temp cannot be above max_temp")
        humidity = _number(_get(raw, "humidity", where), f"{where}.humidity", 0, 100)
        days.append(WeatherDay(day, max_temp, min_temp, humidity))
    return days


def validate_forecast(forecast: Any) -> None:
    """Raise ValueError if `forecast` would be rejected by run_wattwise; return None if it is fine.

    Lets other code (such as weather.py) apply exactly the same checks.
    """
    _parse_forecast(forecast)


def _parse_consumption(data: Any) -> List[float]:
    """Check the list of completed-day consumption values."""
    if not isinstance(data, list):
        raise ValueError("daily_consumption must be a list of numbers")
    return [_number(v, f"daily_consumption[{i}]", minimum=0) for i, v in enumerate(data)]


# ---------------------------------------------------------------------------
# OUTPUT BUILDING (model objects -> plain dicts)
# ---------------------------------------------------------------------------

def _r(value: float) -> float:
    """Round a number for output."""
    return round(value, DECIMALS)


def _units(appliance: Appliance, hours: float) -> float:
    """Units (kWh) the appliance uses in `hours` hours."""
    return appliance.wattage * appliance.quantity * hours / 1000


def _classification(household: Household) -> List[Dict[str, Any]]:
    return [{**a.to_dict(), "classification": classify_appliance(a)} for a in household.appliances]


def _today_plan(household: Household, today: WeatherDay) -> Dict[str, Any]:
    hours = recommend_hours(household.appliances, today)
    rows = []
    for a in household.appliances:
        slots = split_into_slots(a, today)   # [] unless weather-dependent
        rows.append({
            "name": a.name,
            "classification": classify_appliance(a),
            "recommended_hours": _r(hours[a.name]),
            "units": _r(_units(a, hours[a.name])),
            "time_slots": [{"slot": name, "hours": _r(h)} for name, h in slots],
        })
    return {
        "date": today.date.isoformat(),
        "max_temp": today.max_temp,
        "min_temp": today.min_temp,
        "humidity": today.humidity,
        "appliances": rows,
        "total_units": _r(sum(row["units"] for row in rows)),
    }


def _monthly_prediction(household: Household, forecast: List[WeatherDay], check) -> Dict[str, Any]:
    total = predict_monthly_units(household, forecast)
    return {
        "days": [{"date": d.date.isoformat(), "predicted_units": _r(predict_daily_units(household, d))}
                 for d in forecast],
        "forecast_total_units": _r(total),
        "forecast_average_units_per_day": _r(total / len(forecast)),
        "projected_month_total_units": _r(check.predicted_total),
    }


def _budget_status(tracker: BudgetTracker, check) -> Dict[str, Any]:
    return {
        "monthly_budget_units": _r(tracker.monthly_budget_units),
        "days_in_month": tracker.days_in_month,
        "consumed_units": _r(tracker.consumed_units),
        "remaining_units": _r(tracker.remaining_units),
        "percent_used": _r(tracker.percent_used),
        "days_left": tracker.days_left,
        "predicted_total": _r(check.predicted_total),
        "predicted_percent": _r(check.predicted_percent),
        "will_exceed": check.will_exceed,
        "excess_units": _r(check.excess_units),
        "severity": check.severity,
        "projection_note": PROJECTION_NOTE,
    }


def _daily_allocation(shares: List[float], forecast: List[WeatherDay]) -> Dict[str, Any]:
    """One entry per remaining day. Days past the forecast continue the calendar one day at a time."""
    days = []
    for i, share in enumerate(shares):
        if i < len(forecast):
            day = forecast[i].date
        else:
            day = forecast[-1].date + timedelta(days=i - len(forecast) + 1)
        days.append({"date": day.isoformat(), "units": _r(share)})
    return {"today_units": _r(shares[0]), "days": days}


def _optimized_plan(household: Household, forecast: List[WeatherDay], tracker: BudgetTracker,
                    result: PilotResult) -> Dict[str, Any]:
    original = recommend_hours(household.appliances, forecast[0])

    rows = []
    for plan in result.plans:
        a = plan.appliance
        rows.append({
            "name": a.name,
            "classification": classify_appliance(a),
            "original_hours": _r(original[a.name]),
            "adjusted_hours": _r(plan.recommended_hours),
            "units": _r(_units(a, plan.recommended_hours)),
            "time_windows": [{"start_hour": s, "end_hour": e} for s, e in plan.time_slots],
        })

    # Must-run days for flexible appliances over the same rolling week plan_today used
    week = forecast[:min(DAYS_PER_WEEK, tracker.days_left)]
    by_name = {a.name: a for a in household.appliances}
    weekly_runs = {
        name: {
            "min_runs_per_week": min_runs_per_week(by_name[name]),
            "must_run_dates": [week[i].date.isoformat() for i in day_indexes],
        }
        for name, day_indexes in schedule_flexible_runs(household, week).items()
    }

    return {
        "date": forecast[0].date.isoformat(),
        "daily_allocation": _r(result.daily_allocation),
        "predicted_units": _r(result.predicted_units),
        "adjusted_units": _r(result.adjusted_units),
        "unachievable": result.unachievable,
        "minimum_units": _r(result.minimum_units),
        "explanation": result.explanation,
        "appliances": rows,
        "weekly_flexible_runs": weekly_runs,
    }


# ---------------------------------------------------------------------------
# THE ENTRY POINT
# ---------------------------------------------------------------------------

def run_wattwise(
    household: Dict[str, Any],
    forecast: List[Dict[str, Any]],
    daily_consumption: List[float],
    today_units_so_far: float,
) -> Dict[str, Any]:
    """Run all of WattWise and return one JSON-serializable dict.

    household:          budget, family size, appliances (and optional days_in_month)
    forecast:           weather per day, forecast[0] is today (at least 1 day)
    daily_consumption:  units used on each COMPLETED day this month (today not included)
    today_units_so_far: units used so far today

    Returns a dict with the keys: classification, today_plan, monthly_prediction,
    budget_status, daily_allocation, optimized_plan, alerts, warnings.
    "warnings" is a list of strings about input that was accepted but adjusted
    (for example an unrecognized appliance category).
    Raises ValueError if the input is invalid.
    """
    home, days_override, warnings = _parse_household(household)
    days = _parse_forecast(forecast)
    entries = _parse_consumption(daily_consumption)
    today_units = _number(today_units_so_far, "today_units_so_far", minimum=0)

    days_in_month = days_override or calendar.monthrange(days[0].date.year, days[0].date.month)[1]
    if len(entries) >= days_in_month:
        raise ValueError(
            f"daily_consumption has {len(entries)} entries but the month has {days_in_month} days; "
            f"it must only hold completed days, so today is not included")

    tracker = BudgetTracker(home.monthly_budget_units, entries, days_in_month)
    check = check_budget_exceed(tracker, home, days)
    shares = allocate_daily_budget(tracker, days)
    pilot_result = UsagePilot().plan_today(tracker, home, days)
    alerts = generate_alerts(today_units, shares[0], check, days[1:],
                             plan_achievable=not pilot_result.unachievable)

    return {
        "classification": _classification(home),
        "today_plan": _today_plan(home, days[0]),
        "monthly_prediction": _monthly_prediction(home, days, check),
        "budget_status": _budget_status(tracker, check),
        "daily_allocation": _daily_allocation(shares, days),
        "optimized_plan": _optimized_plan(home, days, tracker, pilot_result),
        "alerts": [a.to_dict() for a in alerts],
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# TEST CASES (run with: python api.py)
# ---------------------------------------------------------------------------

def _sample() -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[float], float]:
    """Small valid input used by the tests."""
    household = {
        "monthly_budget_units": 300,
        "family_size": 4,
        "appliances": [
            {"name": "AC", "wattage": 1500, "quantity": 1, "avg_hours_per_day": 8},
            {"name": "Fridge", "wattage": 150, "quantity": 1, "avg_hours_per_day": 24},
            {"name": "Washing Machine", "wattage": 500, "quantity": 1,
             "avg_hours_per_day": 1},
        ],
    }
    forecast = [{"date": f"2026-05-{11 + i}", "max_temp": 41 + i, "min_temp": 29, "humidity": 45}
                for i in range(4)]
    return household, forecast, [10.0] * 10, 5.0


def _expect_error(household, forecast, consumption, today, text: str) -> None:
    """Assert run_wattwise raises ValueError whose message contains `text`."""
    try:
        run_wattwise(household, forecast, consumption, today)
    except ValueError as error:
        assert text in str(error), str(error)
    else:
        raise AssertionError(f"expected ValueError containing '{text}'")


def _test_output_keys_and_json() -> None:
    """Exactly the 8 documented keys, and the output survives a JSON round trip unchanged."""
    result = run_wattwise(*_sample())
    assert list(result) == ["classification", "today_plan", "monthly_prediction", "budget_status",
                            "daily_allocation", "optimized_plan", "alerts", "warnings"]
    assert result["warnings"] == []   # the clean sample input produces no warnings
    assert json.loads(json.dumps(result)) == result


def _test_numbers_are_consistent() -> None:
    """Allocations add up to the remaining budget; the optimized plan fits today's allocation."""
    result = run_wattwise(*_sample())
    status, allocation, plan = result["budget_status"], result["daily_allocation"], result["optimized_plan"]
    assert status["days_left"] == 21 and len(allocation["days"]) == 21   # May has 31 days, 10 are done
    assert abs(sum(d["units"] for d in allocation["days"]) - status["remaining_units"]) < 0.1
    assert allocation["today_units"] == allocation["days"][0]["units"] == plan["daily_allocation"]
    assert plan["unachievable"] or plan["adjusted_units"] <= plan["daily_allocation"] + 0.05


def _budget_alert_message(result: Dict[str, Any]) -> str:
    """Return the budget_exceed alert message (the test inputs always trigger one)."""
    return next(a["message"] for a in result["alerts"] if a["type"] == "budget_exceed")


def _test_budget_alert_states_assumption_and_plan_outcome() -> None:
    """The projection says 'at current usage'; the second sentence follows unachievable."""
    household, forecast, consumption, today = _sample()

    normal = run_wattwise({**household, "monthly_budget_units": 400}, forecast, consumption, today)
    assert not normal["optimized_plan"]["unachievable"]
    message = _budget_alert_message(normal)
    assert message.startswith("At current usage, the month is projected at ")
    assert "against a 400 budget" in message
    assert message.endswith("Following today's optimized plan keeps today within its allocation.")

    edge = run_wattwise({**household, "monthly_budget_units": 120}, forecast, consumption, today)
    assert edge["optimized_plan"]["unachievable"]
    message = _budget_alert_message(edge)
    assert "against a 120 budget" in message
    assert message.endswith("Even minimum usage cannot meet today's allocation.")
    assert "projection assumes the current usage pattern" in edge["budget_status"]["projection_note"]


def _test_input_not_modified() -> None:
    """run_wattwise never changes the dicts it is given."""
    args = _sample()
    before = json.dumps(args)
    run_wattwise(*args)
    assert json.dumps(args) == before


def _test_bad_input_gives_clear_errors() -> None:
    household, forecast, consumption, today = _sample()
    bad_hh = json.loads(json.dumps(household))
    del bad_hh["appliances"][0]["wattage"]
    _expect_error(bad_hh, forecast, consumption, today, "household.appliances[0] is missing 'wattage'")

    bad_forecast = json.loads(json.dumps(forecast))
    bad_forecast[1]["humidity"] = 150
    _expect_error(household, bad_forecast, consumption, today, "forecast[1].humidity must be at most 100")

    _expect_error(household, [], consumption, today, "forecast must be a non-empty list")
    _expect_error(household, forecast, [1.0] * 31, today, "daily_consumption has 31 entries")
    _expect_error(household, forecast, consumption, -1, "today_units_so_far must be at least 0")


def _test_category_validation() -> None:
    """A valid category is used directly, empty/missing/unrecognized falls back to the name
    (unrecognized also adds a warning), and only a non-text category or
    'weather_dependent' on an unrecognized name raises ValueError."""
    household, forecast, consumption, today = _sample()

    def run(category_values):
        """Set the categories (None = omit the key), run, return (classes by name, warnings)."""
        hh = json.loads(json.dumps(household))
        for appliance, value in zip(hh["appliances"], category_values):
            appliance.pop("category", None)
            if value is not None:
                appliance["category"] = value
        result = run_wattwise(hh, forecast, consumption, today)
        return {a["name"]: a["classification"] for a in result["classification"]}, result["warnings"]

    # Name fallback: missing, null and empty all classify by name (AC, Fridge, Washing Machine)
    by_name = {"AC": "weather_dependent", "Fridge": "essential", "Washing Machine": "flexible"}
    assert run([None, None, None]) == (by_name, [])
    assert run(["", "  ", None]) == (by_name, [])

    # Category override: used directly, even against the name, no warning
    assert run(["essential", "FLEXIBLE", "essential"]) == (
        {"AC": "essential", "Fridge": "flexible", "Washing Machine": "essential"}, [])

    # Unrecognized text: no error, classified by name, one warning per such appliance
    classes, warnings = run([None, "cooling", "  Misc  "])
    assert classes == by_name
    assert warnings == [
        'household.appliances[1].category "cooling" is not recognized; classified by name instead',
        'household.appliances[2].category "Misc" is not recognized; classified by name instead',
    ]

    # Still an error: category that is not text
    bad_hh = json.loads(json.dumps(household))
    bad_hh["appliances"][1]["category"] = 5
    _expect_error(bad_hh, forecast, consumption, today,
                  "household.appliances[1].category must be a string")

    # Still an error: "weather_dependent" on a name with no known weather curve
    bad_hh = json.loads(json.dumps(household))
    bad_hh["appliances"].append({"name": "Water Pump", "wattage": 750, "quantity": 1,
                                 "category": "weather_dependent", "avg_hours_per_day": 2})
    _expect_error(bad_hh, forecast, consumption, today, "household.appliances[3]")


def _test_vectors_match_current_code() -> None:
    """Every vector in test_vectors.json still produces its expected_output exactly.
    (If this fails after an intended change, run: python generate_vectors.py)"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "test_vectors.json")
    if not os.path.exists(path):
        return   # the file is optional
    with open(path, encoding="utf-8") as f:
        vectors = json.load(f)["vectors"]
    assert len(vectors) == 9
    for vector in vectors:
        args = vector["input"]
        result = run_wattwise(args["household"], args["forecast"], args["daily_consumption"],
                              args["today_units_so_far"])
        assert result == vector["expected_output"], vector["name"]


def _test_duplicate_names_rejected() -> None:
    household, forecast, consumption, today = _sample()
    household["appliances"].append(dict(household["appliances"][0]))
    _expect_error(household, forecast, consumption, today, "duplicate")


if __name__ == "__main__":
    tests = [
        _test_output_keys_and_json,
        _test_numbers_are_consistent,
        _test_budget_alert_states_assumption_and_plan_outcome,
        _test_input_not_modified,
        _test_bad_input_gives_clear_errors,
        _test_category_validation,
        _test_vectors_match_current_code,
        _test_duplicate_names_rejected,
    ]
    for test in tests:
        test()
    print(f"All {len(tests)} api tests passed.")
