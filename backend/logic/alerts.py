"""Alerts: turn budget numbers and the weather forecast into simple warnings.

Each check returns Alert objects (or None / an empty list when all is well).
Alert has a type, a severity and a human-readable message, plus to_dict().
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from budget import CRITICAL, SAFE, WARNING, BudgetCheck
from models import WeatherDay

# ---------------------------------------------------------------------------
# CONSTANTS (easy to tweak)
# ---------------------------------------------------------------------------

INFO = "info"   # extra severity label (budget.py already defines safe/warning/critical)

# High consumption today, as a % of the daily allocation
HIGH_USE_WARNING_PERCENT = 90.0     # above this -> warning
HIGH_USE_CRITICAL_PERCENT = 100.0   # above this -> critical

# Weather extremes (°C)
UPCOMING_DAYS = 3                   # how many days ahead to look
HEATWAVE_TEMP = 40.0                # max temp at or above this -> heatwave
HEATWAVE_CRITICAL_TEMP = 45.0       # max temp at or above this -> critical
COLD_WAVE_TEMP = 5.0                # min temp at or below this -> cold wave
COLD_WAVE_CRITICAL_TEMP = 0.0       # min temp at or below this -> critical

# Alert types
HIGH_CONSUMPTION = "high_consumption"
BUDGET_EXCEED = "budget_exceed"
HEATWAVE = "heatwave"
COLD_WAVE = "cold_wave"

# Used to sort alerts: most severe first
SEVERITY_ORDER = {CRITICAL: 0, WARNING: 1, INFO: 2}


@dataclass
class Alert:
    """One alert for the user."""
    type: str       # e.g. "heatwave"
    severity: str   # "info", "warning" or "critical"
    message: str    # human-readable text, including a suggestion

    def to_dict(self) -> Dict[str, Any]:
        """Return this alert as a plain dictionary."""
        return {"type": self.type, "severity": self.severity, "message": self.message}


# ---------------------------------------------------------------------------
# INDIVIDUAL CHECKS
# ---------------------------------------------------------------------------

def check_high_consumption(today_units: float, daily_allocation: float) -> Optional[Alert]:
    """Alert if today's actual use is close to or above today's allocation."""
    if daily_allocation <= 0:
        if today_units > 0:
            return Alert(HIGH_CONSUMPTION, CRITICAL,
                         f"You have used {today_units:.1f} units today but no budget is left "
                         f"for the day. Switch off non-essential appliances.")
        return None

    percent = today_units / daily_allocation * 100
    if percent > HIGH_USE_CRITICAL_PERCENT:
        return Alert(HIGH_CONSUMPTION, CRITICAL,
                     f"Today's use is {today_units:.1f} units, {percent:.0f}% of the "
                     f"{daily_allocation:.1f}-unit daily allocation. Switch off non-essential "
                     f"appliances for the rest of the day.")
    if percent > HIGH_USE_WARNING_PERCENT:
        return Alert(HIGH_CONSUMPTION, WARNING,
                     f"Today's use is {today_units:.1f} units, {percent:.0f}% of the "
                     f"{daily_allocation:.1f}-unit daily allocation. Postpone flexible "
                     f"appliances such as the washing machine or iron.")
    return None


PLAN_FITS_TEXT = "Following today's optimized plan keeps today within its allocation."
PLAN_CANNOT_FIT_TEXT = "Even minimum usage cannot meet today's allocation."


def check_budget_alert(check: BudgetCheck, plan_achievable: Optional[bool] = None) -> Optional[Alert]:
    """Alert based on the end-of-month prediction from check_budget_exceed().

    The projection assumes the current usage pattern, and the message says so.
    plan_achievable says what today's optimized plan can do:
      True  -> adds "Following today's optimized plan keeps today within its allocation."
      False -> adds "Even minimum usage cannot meet today's allocation."
      None  -> unknown, so no second sentence.
    """
    if check.severity == CRITICAL:
        first = (f"At current usage, the month is projected at {check.predicted_total:.0f} units "
                 f"against a {check.budget_units:.0f} budget "
                 f"({check.excess_units:.0f} units over).")
    elif check.severity == WARNING:
        first = (f"At current usage, the month is projected at {check.predicted_total:.0f} units "
                 f"against a {check.budget_units:.0f} budget "
                 f"({check.predicted_percent:.0f}%), which is close to the limit.")
    else:
        return None

    if plan_achievable is True:
        message = f"{first} {PLAN_FITS_TEXT}"
    elif plan_achievable is False:
        message = f"{first} {PLAN_CANNOT_FIT_TEXT}"
    else:
        message = first
    return Alert(BUDGET_EXCEED, check.severity, message)


def check_weather_alerts(upcoming: List[WeatherDay], days: int = UPCOMING_DAYS) -> List[Alert]:
    """Heatwave / cold wave alerts for the next `days` days.

    `upcoming` should start from tomorrow (or whichever day you want to look ahead from).
    """
    window = upcoming[:days]
    alerts: List[Alert] = []

    hot_days = [d for d in window if d.max_temp >= HEATWAVE_TEMP]
    if hot_days:
        peak = max(hot_days, key=lambda d: d.max_temp)
        severity = CRITICAL if peak.max_temp >= HEATWAVE_CRITICAL_TEMP else WARNING
        alerts.append(Alert(
            HEATWAVE, severity,
            f"Heatwave expected on {_dates(hot_days)}, peaking at {peak.max_temp:.0f}°C. "
            f"Suggestion: pre-cool the home in the morning, set the AC to 26°C or higher, "
            f"use fans to share the load, and move washing and ironing to early morning."))

    cold_days = [d for d in window if d.min_temp <= COLD_WAVE_TEMP]
    if cold_days:
        low = min(cold_days, key=lambda d: d.min_temp)
        severity = CRITICAL if low.min_temp <= COLD_WAVE_CRITICAL_TEMP else WARNING
        alerts.append(Alert(
            COLD_WAVE, severity,
            f"Cold wave expected on {_dates(cold_days)}, dropping to {low.min_temp:.0f}°C. "
            f"Suggestion: run the geyser once in the morning only, use blankets instead of "
            f"extra heating, and switch the room heater off when nobody is in the room."))

    return alerts


def _dates(days: List[WeatherDay]) -> str:
    """Format dates like '24 May, 25 May'."""
    return ", ".join(d.date.strftime("%d %b") for d in days)


# ---------------------------------------------------------------------------
# ALL ALERTS TOGETHER
# ---------------------------------------------------------------------------

def generate_alerts(
    today_units: float,
    daily_allocation: float,
    budget_check: BudgetCheck,
    upcoming_forecast: List[WeatherDay],
    plan_achievable: Optional[bool] = None,
) -> List[Alert]:
    """Run every check and return the alerts, most severe first.

    plan_achievable: True if today's optimized plan can meet today's allocation,
    False if even minimum usage cannot, None if unknown (see check_budget_alert).
    """
    alerts: List[Alert] = []

    high_use = check_high_consumption(today_units, daily_allocation)
    if high_use:
        alerts.append(high_use)

    budget_alert = check_budget_alert(budget_check, plan_achievable)
    if budget_alert:
        alerts.append(budget_alert)

    alerts.extend(check_weather_alerts(upcoming_forecast))

    alerts.sort(key=lambda a: SEVERITY_ORDER[a.severity])  # stable: keeps order within a severity
    return alerts


# ---------------------------------------------------------------------------
# TEST CASES (run with: python alerts.py)
# ---------------------------------------------------------------------------

def _days(max_temps: List[float], min_temp: float = 25.0) -> List[WeatherDay]:
    """Build a forecast with the given max temps, one per day."""
    start = date(2026, 5, 1)
    return [WeatherDay(start + timedelta(days=i), t, min_temp, 50.0) for i, t in enumerate(max_temps)]


def _test_high_consumption_levels() -> None:
    """Under 90% -> none, 95% -> warning, 120% -> critical."""
    assert check_high_consumption(8.0, 10.0) is None
    assert check_high_consumption(9.5, 10.0).severity == WARNING
    assert check_high_consumption(12.0, 10.0).severity == CRITICAL


def _test_budget_alert_levels() -> None:
    """Safe gives no alert; warning and critical map straight through."""
    safe = BudgetCheck(False, 200.0, 0.0, SAFE, 66.7, 300.0)
    warn = BudgetCheck(False, 285.0, 0.0, WARNING, 95.0, 300.0)
    crit = BudgetCheck(True, 330.0, 30.0, CRITICAL, 110.0, 300.0)
    assert check_budget_alert(safe) is None
    assert check_budget_alert(warn).severity == WARNING
    alert = check_budget_alert(crit)
    assert alert.severity == CRITICAL and alert.type == BUDGET_EXCEED and "30 units over" in alert.message


def _test_budget_message_states_the_assumption() -> None:
    """Both severities say the projection is 'at current usage' and name the budget."""
    crit = BudgetCheck(True, 587.0, 37.0, CRITICAL, 106.7, 550.0)
    warn = BudgetCheck(False, 285.0, 0.0, WARNING, 95.0, 300.0)
    assert check_budget_alert(crit).message.startswith(
        "At current usage, the month is projected at 587 units against a 550 budget")
    assert check_budget_alert(warn).message.startswith(
        "At current usage, the month is projected at 285 units against a 300 budget")


def _test_budget_message_second_sentence() -> None:
    """Achievable plan -> 'keeps today within its allocation'; unachievable -> 'even minimum
    usage cannot meet'; unknown -> no second sentence."""
    crit = BudgetCheck(True, 587.0, 37.0, CRITICAL, 106.7, 550.0)
    for severity_check in (crit, BudgetCheck(False, 285.0, 0.0, WARNING, 95.0, 300.0)):
        fits = check_budget_alert(severity_check, plan_achievable=True).message
        cannot = check_budget_alert(severity_check, plan_achievable=False).message
        unknown = check_budget_alert(severity_check).message
        assert fits.endswith("Following today's optimized plan keeps today within its allocation.")
        assert cannot.endswith("Even minimum usage cannot meet today's allocation.")
        assert "optimized plan" not in cannot and "minimum usage" not in fits
        assert "optimized plan" not in unknown and "minimum usage" not in unknown


def _test_heatwave_only_in_next_three_days() -> None:
    """A hot 4th day is outside the window; a hot 2nd day is flagged."""
    assert check_weather_alerts(_days([35, 36, 37, 44])) == []
    alerts = check_weather_alerts(_days([35, 42, 37, 36]))
    assert len(alerts) == 1 and alerts[0].type == HEATWAVE and alerts[0].severity == WARNING


def _test_cold_wave_and_critical_levels() -> None:
    """Min temp 4°C -> cold-wave warning; 46°C max -> critical heatwave."""
    cold = check_weather_alerts(_days([20, 20, 20], min_temp=4.0))
    assert len(cold) == 1 and cold[0].type == COLD_WAVE and cold[0].severity == WARNING
    assert check_weather_alerts(_days([46, 30, 30]))[0].severity == CRITICAL


def _test_generate_alerts_sorted() -> None:
    """The most severe alert comes first."""
    crit = BudgetCheck(True, 330.0, 30.0, CRITICAL, 110.0, 300.0)
    alerts = generate_alerts(9.5, 10.0, crit, _days([42, 30, 30]), plan_achievable=True)
    assert [a.severity for a in alerts] == [CRITICAL, WARNING, WARNING]
    assert PLAN_FITS_TEXT in alerts[0].message


if __name__ == "__main__":
    tests = [
        _test_high_consumption_levels,
        _test_budget_alert_levels,
        _test_budget_message_states_the_assumption,
        _test_budget_message_second_sentence,
        _test_heatwave_only_in_next_three_days,
        _test_cold_wave_and_critical_levels,
        _test_generate_alerts_sorted,
    ]
    for test in tests:
        test()
    print(f"All {len(tests)} alert tests passed.")
