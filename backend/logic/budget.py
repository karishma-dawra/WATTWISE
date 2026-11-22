"""Budget (part 1): predict monthly units, track consumption, check for overshoot.

Pure functions and small classes: plain inputs in, plain outputs out.
Units are kWh ("units" on an Indian electricity bill).
"""

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

from advisor import (
    FLEXIBLE, SLOTS, WEATHER_DEPENDENT,
    classify_appliance, split_hours_into_slots, weather_hours, weather_kind,
)
from models import Appliance, Household, UsagePlan, WeatherDay

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------

DEFAULT_DAYS_IN_MONTH = 30      # used if the caller does not say how long the month is
WARNING_PERCENT = 90.0          # predicted total above this % of budget -> "warning"
CRITICAL_PERCENT = 100.0        # predicted total above this % of budget -> "critical"

# Daily allocation: a day's share grows by 1 for every ALLOC_TEMP_SCALE °C
# its max temperature is above ALLOC_BASE_TEMP (days at or below it get weight 1).
ALLOC_BASE_TEMP = 25.0
ALLOC_TEMP_SCALE = 10.0

# UsagePilot: how far each group of appliances may be reduced

# Flexible appliances may be skipped on a given day, but each must still run at least
# this many days per week (a "run" = a day it is used for its normal hours).
# Matched against the lowercase appliance name; first match wins.
FLEXIBLE_MIN_RUNS_PER_WEEK = {
    "washing machine": 3,
    "dishwasher": 3,
    "iron": 2,
    "vacuum": 1,
    "tv": 4,
}
DEFAULT_FLEXIBLE_MIN_RUNS = 2   # for flexible appliances not listed above
DAYS_PER_WEEK = 7

COMFORT_MIN_FRACTION = {        # weather-dependent: never below this share of recommended hours
    "ac": 0.5,
    "fan": 0.7,
    "cooler": 0.6,
    "heater": 0.5,
    "geyser": 0.5,
}

EPS = 1e-9                      # tiny number to avoid floating-point noise

# Shown next to the month-end projection so nobody mistakes it for the optimized plan
PROJECTION_NOTE = (
    "The projection assumes the current usage pattern: weather-based recommended hours "
    "for each forecast day (the average forecast day after that), with no budget cuts applied."
)

# Severity labels
SAFE = "safe"
WARNING = "warning"
CRITICAL = "critical"


# ---------------------------------------------------------------------------
# PREDICTION
# ---------------------------------------------------------------------------

def _appliance_hours(appliance: Appliance, weather: WeatherDay) -> float:
    """Hours this appliance is expected to run on a given day.

    Weather-dependent appliances use the advisor's weather-based hours;
    all others use their current average hours.
    """
    if classify_appliance(appliance) == WEATHER_DEPENDENT:
        return weather_hours(appliance, weather)
    return appliance.avg_hours_per_day


def predict_daily_units(household: Household, weather: WeatherDay) -> float:
    """Predicted units (kWh) the household uses on one day with this weather."""
    total = 0.0
    for appliance in household.appliances:
        hours = _appliance_hours(appliance, weather)
        total += appliance.wattage * appliance.quantity * hours / 1000  # W -> kW
    return total


def predict_monthly_units(household: Household, month_forecast: List[WeatherDay]) -> float:
    """Predicted units for all days in the forecast (one WeatherDay per day)."""
    return sum(predict_daily_units(household, day) for day in month_forecast)


# ---------------------------------------------------------------------------
# TRACKING
# ---------------------------------------------------------------------------

class BudgetTracker:
    """Tracks actual consumption against a monthly budget."""

    def __init__(
        self,
        monthly_budget_units: float,
        daily_entries: List[float],
        days_in_month: int = DEFAULT_DAYS_IN_MONTH,
    ) -> None:
        """daily_entries: actual units used on each day so far (one number per day)."""
        if monthly_budget_units <= 0:
            raise ValueError("monthly_budget_units must be greater than 0")
        if len(daily_entries) > days_in_month:
            raise ValueError("More daily entries than days in the month")
        if any(units < 0 for units in daily_entries):
            raise ValueError("Daily consumption cannot be negative")

        self.monthly_budget_units = monthly_budget_units
        self.daily_entries = list(daily_entries)
        self.days_in_month = days_in_month

    def add_day(self, units: float) -> None:
        """Record one more day of actual consumption."""
        if units < 0:
            raise ValueError("Daily consumption cannot be negative")
        if len(self.daily_entries) >= self.days_in_month:
            raise ValueError("The month is already full")
        self.daily_entries.append(units)

    @property
    def consumed_units(self) -> float:
        """Total units used so far this month."""
        return sum(self.daily_entries)

    @property
    def remaining_units(self) -> float:
        """Units left in the budget (never below 0)."""
        return max(0.0, self.monthly_budget_units - self.consumed_units)

    @property
    def percent_used(self) -> float:
        """Percent of the budget used so far (can go above 100)."""
        return self.consumed_units / self.monthly_budget_units * 100

    @property
    def days_left(self) -> int:
        """Days remaining in the month (days without an entry yet)."""
        return self.days_in_month - len(self.daily_entries)


# ---------------------------------------------------------------------------
# END-OF-MONTH CHECK
# ---------------------------------------------------------------------------

@dataclass
class BudgetCheck:
    """Result of check_budget_exceed()."""
    will_exceed: bool           # True if predicted total is above the budget
    predicted_total: float      # actual so far + predicted for remaining days
    excess_units: float         # units over budget (0 if not exceeding)
    severity: str               # "safe", "warning" or "critical"
    predicted_percent: float    # predicted total as % of the budget
    budget_units: float         # the monthly budget the prediction was compared with

    def to_dict(self) -> Dict[str, Any]:
        """Return this result as a plain dictionary."""
        return {
            "will_exceed": self.will_exceed,
            "predicted_total": self.predicted_total,
            "excess_units": self.excess_units,
            "severity": self.severity,
            "predicted_percent": self.predicted_percent,
            "budget_units": self.budget_units,
        }


def _predict_remaining_units(
    household: Household, remaining_forecast: List[WeatherDay], days_left: int
) -> float:
    """Predicted units for the remaining days of the month.

    - Uses the first `days_left` forecast days.
    - If the forecast is shorter, missing days use the forecast's average day.
    - If there is no forecast at all, missing days use each appliance's
      current average hours (no weather adjustment).
    """
    if days_left <= 0:
        return 0.0

    forecast = remaining_forecast[:days_left]
    total = predict_monthly_units(household, forecast)

    missing_days = days_left - len(forecast)
    if missing_days > 0:
        if forecast:
            average_day = total / len(forecast)
        else:
            average_day = sum(
                a.wattage * a.quantity * a.avg_hours_per_day / 1000
                for a in household.appliances
            )
        total += average_day * missing_days
    return total


def check_budget_exceed(
    tracker: BudgetTracker,
    household: Household,
    remaining_forecast: List[WeatherDay],
) -> BudgetCheck:
    """Predict the end-of-month total and say whether the budget will be exceeded.

    predicted_total = actual units so far + predicted units for remaining days.
    Severity: "critical" above 100% of budget, "warning" above 90%, else "safe".
    """
    remaining = _predict_remaining_units(household, remaining_forecast, tracker.days_left)
    predicted_total = tracker.consumed_units + remaining
    budget = tracker.monthly_budget_units
    percent = predicted_total / budget * 100

    if percent > CRITICAL_PERCENT:
        severity = CRITICAL
    elif percent > WARNING_PERCENT:
        severity = WARNING
    else:
        severity = SAFE

    return BudgetCheck(
        will_exceed=predicted_total > budget,
        predicted_total=predicted_total,
        excess_units=max(0.0, predicted_total - budget),
        severity=severity,
        predicted_percent=percent,
        budget_units=budget,
    )


# ---------------------------------------------------------------------------
# PART 2: DYNAMIC DAILY ALLOCATION
# ---------------------------------------------------------------------------

def _day_weight(day: WeatherDay) -> float:
    """Share weight of a day: 1 for mild days, bigger for hotter days."""
    return 1.0 + max(0.0, day.max_temp - ALLOC_BASE_TEMP) / ALLOC_TEMP_SCALE


def allocate_daily_budget(tracker: BudgetTracker, remaining_forecast: List[WeatherDay]) -> List[float]:
    """Split the remaining budget across the remaining days, hotter days get more.

    remaining_forecast[0] is today (the first day without an entry yet).
    Returns one allocation (units) per remaining day; they add up to
    tracker.remaining_units. Call it again after every new daily entry and
    the numbers update automatically, because they come from the tracker.
    If the forecast is shorter than days_left, missing days get an average weight.
    """
    days_left = tracker.days_left
    if days_left <= 0:
        return []

    weights = [_day_weight(day) for day in remaining_forecast[:days_left]]
    average = sum(weights) / len(weights) if weights else 1.0
    weights += [average] * (days_left - len(weights))

    share = tracker.remaining_units / sum(weights)
    return [share * w for w in weights]


# ---------------------------------------------------------------------------
# PART 2: USAGEPILOT OPTIMIZER
# ---------------------------------------------------------------------------

@dataclass
class _Item:
    """Working data for one appliance while optimizing (internal)."""
    appliance: Appliance
    category: str           # weather_dependent / essential / flexible
    kw: float               # total power of all units of this appliance, in kW
    hours: float            # hours before any reduction
    min_hours: float        # lowest hours we may reduce to
    new_hours: float = 0.0  # hours after reduction
    required: bool = False  # flexible only: today is one of its must-run days


@dataclass
class PilotResult:
    """Output of UsagePilot.optimize()."""
    plans: List[UsagePlan]      # adjusted plan for every appliance
    predicted_units: float      # one day's usage before any reduction
    adjusted_units: float       # one day's usage after reduction
    daily_allocation: float     # the allowed units for the day
    unachievable: bool          # True if even the minimum plan is over the allocation
    minimum_units: float        # lowest possible usage for the day
    explanation: str            # human-readable summary of what was reduced

    def to_dict(self) -> Dict[str, Any]:
        """Return this result as a plain dictionary."""
        return {
            "plans": [p.to_dict() for p in self.plans],
            "predicted_units": self.predicted_units,
            "adjusted_units": self.adjusted_units,
            "daily_allocation": self.daily_allocation,
            "unachievable": self.unachievable,
            "minimum_units": self.minimum_units,
            "explanation": self.explanation,
        }


def _floor2(value: float) -> float:
    """Round DOWN to 2 decimals, so rounding never pushes usage above the allocation."""
    return math.floor(value * 100 + EPS) / 100


def _reduce_group(items: List[_Item], need: float) -> float:
    """Reduce a group of appliances by up to `need` units; return the units still needed.

    Every appliance in the group is reduced by the same share of what it can
    give up (hours above its minimum), so the cut is spread evenly.
    """
    reducible = sum(i.kw * (i.hours - i.min_hours) for i in items)
    if need <= EPS or reducible <= EPS:
        return max(0.0, need)

    share = min(1.0, need / reducible)  # 1.0 means "reduce everything to its minimum"
    for i in items:
        if share >= 1.0:
            i.new_hours = i.min_hours
        else:
            i.new_hours = max(i.min_hours, _floor2(i.hours - share * (i.hours - i.min_hours)))
    return max(0.0, need - share * reducible)


def _skip_flexible(items: List[_Item], need: float) -> float:
    """Skip whole flexible appliances for today until `need` units are saved.

    Prefers the smallest appliance that covers the remaining need on its own;
    if none can, skips the biggest one and repeats. Returns the units still needed.
    """
    candidates = [i for i in items if i.kw * i.hours > EPS]
    while need > EPS and candidates:
        covering = [i for i in candidates if i.kw * i.hours >= need - EPS]
        if covering:
            chosen = min(covering, key=lambda i: i.kw * i.hours)
        else:
            chosen = max(candidates, key=lambda i: i.kw * i.hours)
        chosen.new_hours = 0.0
        need -= chosen.kw * chosen.hours
        candidates.remove(chosen)
    return max(0.0, need)


def min_runs_per_week(appliance: Appliance) -> int:
    """Minimum days per week a flexible appliance must run (e.g. washing machine: 3)."""
    name = appliance.name.lower()
    for keyword, runs in FLEXIBLE_MIN_RUNS_PER_WEEK.items():
        if keyword in name:
            return runs
    return DEFAULT_FLEXIBLE_MIN_RUNS


def schedule_flexible_runs(household: Household, week_forecast: List[WeatherDay]) -> Dict[str, List[int]]:
    """Choose the must-run days for each flexible appliance: the lowest-cost days.

    A day's cost is the household's predicted units for that day's weather (cool
    days are cheaper). Returns {appliance_name: [day indexes in week_forecast]}.
    If the forecast covers fewer than 7 days, the weekly minimum is scaled down
    (rounded up) to match. Appliances sharing a name share one entry.
    """
    n = len(week_forecast)
    costs = [predict_daily_units(household, day) for day in week_forecast]
    cheapest_first = sorted(range(n), key=lambda i: (costs[i], i))  # ties: earlier day first

    schedule: Dict[str, List[int]] = {}
    for appliance in household.appliances:
        if classify_appliance(appliance) != FLEXIBLE:
            continue
        runs = min(n, -(-min_runs_per_week(appliance) * n // DAYS_PER_WEEK))  # ceiling division
        schedule[appliance.name] = sorted(cheapest_first[:runs])
    return schedule


@dataclass
class WeekPlan:
    """Output of UsagePilot.plan_week()."""
    days: List[PilotResult]                     # one optimized plan per day
    flexible_run_days: Dict[str, List[date]]    # must-run dates for each flexible appliance

    def to_dict(self) -> Dict[str, Any]:
        """Return this week plan as a plain dictionary (dates as ISO text)."""
        return {
            "days": [d.to_dict() for d in self.days],
            "flexible_run_days": {name: [d.isoformat() for d in dates]
                                  for name, dates in self.flexible_run_days.items()},
        }


def _to_time_windows(slot_hours: List[Tuple[str, float]]) -> List[Tuple[int, int]]:
    """Turn per-slot hours into (start_hour, end_hour) windows in whole hours.

    A slot with 0.67 h becomes a 1-hour window at the start of that slot.
    Hours are rounded up to whole hours and never go past the slot's end.
    """
    bounds = {name: (start, end) for name, start, end in SLOTS}
    windows = []
    for name, hours in slot_hours:
        if hours > 0.005:
            start, end = bounds[name]
            windows.append((start, min(end, start + math.ceil(hours - EPS))))
    return windows


class UsagePilot:
    """Reduces one day's appliance hours so usage fits the daily allocation.

    Order of reduction: flexible appliances first (skipped for the day, except on
    their must-run days that keep the weekly minimum), then weather-dependent ones
    (never below their comfort minimum). Essentials are never touched.
    """

    def __init__(self, comfort_min_fraction: Optional[Dict[str, float]] = None) -> None:
        self.comfort_min_fraction = comfort_min_fraction or COMFORT_MIN_FRACTION

    def optimize(
        self,
        household: Household,
        weather: WeatherDay,
        daily_allocation: float,
        required_flexible: Optional[Set[str]] = None,
    ) -> PilotResult:
        """Build adjusted usage plans for one day with the given weather.

        required_flexible: names of flexible appliances that must run today to keep
        their weekly minimum (see schedule_flexible_runs / plan_week). They are never
        skipped. None means weekly minimums are not being tracked, so any flexible
        appliance may be skipped today.
        """
        week_aware = required_flexible is not None
        required_flexible = required_flexible or set()
        items = [self._make_item(a, weather, a.name in required_flexible)
                 for a in household.appliances]
        for item in items:
            item.new_hours = item.hours

        predicted = sum(i.kw * i.hours for i in items)
        minimum = sum(i.kw * i.min_hours for i in items)

        # Reduce in order: flexible first, then weather-dependent. Essentials are skipped.
        need = predicted - daily_allocation
        need = _skip_flexible([i for i in items if i.category == FLEXIBLE and not i.required], need)
        need = _reduce_group([i for i in items if i.category == WEATHER_DEPENDENT], need)
        unachievable = need > EPS

        plans = [self._make_plan(i) for i in items]
        adjusted = sum(i.kw * p.recommended_hours for i, p in zip(items, plans))

        return PilotResult(
            plans=plans,
            predicted_units=predicted,
            adjusted_units=adjusted,
            daily_allocation=daily_allocation,
            unachievable=unachievable,
            minimum_units=minimum,
            explanation=self._explain(items, plans, predicted, adjusted, daily_allocation,
                                      unachievable, minimum, week_aware),
        )

    def plan_week(
        self,
        household: Household,
        week_forecast: List[WeatherDay],
        daily_allocations: List[float],
    ) -> WeekPlan:
        """Optimize each day of a week (up to 7 days), keeping every flexible weekly minimum.

        daily_allocations needs one entry per forecast day, e.g.
        allocate_daily_budget(tracker, forecast)[:7].
        """
        if len(daily_allocations) < len(week_forecast):
            raise ValueError("Need one daily allocation per forecast day")

        schedule = schedule_flexible_runs(household, week_forecast)
        days = []
        for i, weather in enumerate(week_forecast):
            required = {name for name, run_days in schedule.items() if i in run_days}
            days.append(self.optimize(household, weather, daily_allocations[i], required))

        run_dates = {name: [week_forecast[i].date for i in run_days]
                     for name, run_days in schedule.items()}
        return WeekPlan(days, run_dates)

    def plan_today(
        self, tracker: BudgetTracker, household: Household, remaining_forecast: List[WeatherDay]
    ) -> PilotResult:
        """Allocate today's share of the remaining budget, then optimize for today's weather.

        remaining_forecast[0] must be today's weather. Must-run days for flexible
        appliances are chosen over the next 7 days (a rolling week starting today).
        """
        if tracker.days_left <= 0 or not remaining_forecast:
            raise ValueError("Need at least one remaining day and its forecast")
        allocation = allocate_daily_budget(tracker, remaining_forecast)[0]

        week = remaining_forecast[:min(DAYS_PER_WEEK, tracker.days_left)]
        schedule = schedule_flexible_runs(household, week)
        required = {name for name, run_days in schedule.items() if 0 in run_days}
        return self.optimize(household, remaining_forecast[0], allocation, required)

    # --- internal helpers ---------------------------------------------------

    def _make_item(self, appliance: Appliance, weather: WeatherDay, required: bool = False) -> _Item:
        category = classify_appliance(appliance)
        hours = _appliance_hours(appliance, weather)
        if category == FLEXIBLE:
            # Skippable (min 0h) unless today is one of its must-run days
            fraction = 1.0 if required else 0.0
        elif category == WEATHER_DEPENDENT:
            fraction = self.comfort_min_fraction.get(weather_kind(appliance), 1.0)
        else:
            fraction = 1.0  # essentials: minimum equals current hours (no reduction)
        return _Item(
            appliance=appliance,
            category=category,
            kw=appliance.wattage * appliance.quantity / 1000,
            hours=hours,
            min_hours=hours * fraction,
            required=required,
        )

    def _make_plan(self, item: _Item) -> UsagePlan:
        hours = round(item.new_hours, 2)
        slots = split_hours_into_slots(item.appliance, hours)  # [] if not weather-dependent
        return UsagePlan(item.appliance, hours, _to_time_windows(slots))

    def _explain(self, items, plans, predicted, adjusted, allocation, unachievable, minimum,
                 week_aware: bool = False) -> str:
        """Write a human-readable summary of what changed."""
        if predicted <= allocation + EPS:
            return (f"Predicted usage of {predicted:.2f} units is within today's allocation "
                    f"of {allocation:.2f} units. No changes needed.")

        labels = {FLEXIBLE: "flexible", WEATHER_DEPENDENT: "weather-dependent"}
        lines = [f"Predicted usage of {predicted:.2f} units is above today's allocation "
                 f"of {allocation:.2f} units."]
        for item, plan in zip(items, plans):
            name = item.appliance.name
            saved = item.kw * (item.hours - plan.recommended_hours)
            if item.category == FLEXIBLE:
                runs = min_runs_per_week(item.appliance)
                if saved > 0.005:
                    # Flexible appliances are skipped for the whole day, not shortened
                    text = f"- {name} (flexible): skipped today, saves {saved:.2f} units"
                    if week_aware:
                        text += (f" (its weekly minimum of {runs} runs is kept "
                                 f"on the lowest-cost days)")
                    lines.append(text)
                elif item.required and item.hours > 0:
                    lines.append(f"- {name} (flexible): kept today, one of its {runs} "
                                 f"minimum runs this week (today is a low-cost day for it)")
            elif saved > 0.005:
                lines.append(f"- {name} ({labels[item.category]}): "
                             f"{item.hours:.2f}h -> {plan.recommended_hours:.2f}h, "
                             f"saves {saved:.2f} units")

        if unachievable:
            lines.append(f"The allocation cannot be met: even at minimum use the day needs "
                         f"{minimum:.2f} units ({minimum - allocation:.2f} over). "
                         f"Showing the minimum plan.")
        else:
            lines.append(f"Adjusted usage is {adjusted:.2f} units, within the allocation.")

        essentials = [i.appliance.name for i in items if i.category not in labels]
        if essentials:
            lines.append("Essentials left unchanged: " + ", ".join(essentials) + ".")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# TEST CASES (run with: python budget.py)
# ---------------------------------------------------------------------------

def _forecast(days: int, max_temp: float = 30.0, min_temp: float = 20.0,
              humidity: float = 50.0) -> List[WeatherDay]:
    """Build a simple forecast of identical days for the tests."""
    start = date(2026, 11, 1)
    return [WeatherDay(start + timedelta(days=i), max_temp, min_temp, humidity)
            for i in range(days)]


def _tv_household(hours_per_day: float) -> Household:
    """A household whose only appliance is a 1000 W TV: uses `hours_per_day` units a day."""
    return Household(300.0, 4, [Appliance("TV", 1000, 1, "", hours_per_day)])


def _test_predict_flexible_appliance() -> None:
    """2 TVs x 100 W x 5 h = 1 unit a day, so 30 days = 30 units."""
    household = Household(300.0, 4, [Appliance("TV", 100, 2, "", 5)])
    assert math.isclose(predict_monthly_units(household, _forecast(30)), 30.0)


def _test_predict_uses_weather_hours() -> None:
    """On a 40°C day the AC runs 10 h: 1000 W x 10 h = 10 units a day, 3 days = 30."""
    household = Household(300.0, 4, [Appliance("AC", 1000, 1, "", 4)])
    assert math.isclose(predict_monthly_units(household, _forecast(3, max_temp=40.0)), 30.0)


def _test_predicted_units_rise_with_ac_temperature() -> None:
    """A 1000 W AC: predicted daily units differ on 38, 41, 44 and 47°C days."""
    household = Household(300.0, 4, [Appliance("AC", 1000, 1, "", 4)])
    units = [predict_daily_units(household, _forecast(1, max_temp=t)[0]) for t in (38, 41, 44, 47)]
    assert len(set(units)) == 4 and units == sorted(units)
    assert [round(u, 1) for u in units] == [8.6, 10.9, 13.4, 16.0]


def _test_tracker_numbers() -> None:
    """10 days at 10 units against a 300 budget."""
    tracker = BudgetTracker(300.0, [10.0] * 10, days_in_month=30)
    assert math.isclose(tracker.consumed_units, 100.0)
    assert math.isclose(tracker.remaining_units, 200.0)
    assert math.isclose(tracker.percent_used, 100 / 3)
    assert tracker.days_left == 20


def _test_check_safe() -> None:
    """100 used + 20 days x 1 unit = 120 of 300: safe."""
    tracker = BudgetTracker(300.0, [10.0] * 10)
    result = check_budget_exceed(tracker, _tv_household(1.0), _forecast(20))
    assert result.severity == SAFE and not result.will_exceed
    assert result.excess_units == 0.0 and math.isclose(result.predicted_total, 120.0)


def _test_check_warning() -> None:
    """100 used + 20 days x 9.25 units = 285 of 300 (95%): warning, not exceeding."""
    tracker = BudgetTracker(300.0, [10.0] * 10)
    result = check_budget_exceed(tracker, _tv_household(9.25), _forecast(20))
    assert result.severity == WARNING and not result.will_exceed
    assert math.isclose(result.predicted_total, 285.0)


def _test_check_critical() -> None:
    """100 used + 20 days x 11.5 units = 330 of 300: critical, 30 units over."""
    tracker = BudgetTracker(300.0, [10.0] * 10)
    result = check_budget_exceed(tracker, _tv_household(11.5), _forecast(20))
    assert result.severity == CRITICAL and result.will_exceed
    assert math.isclose(result.excess_units, 30.0)


def _test_short_forecast_is_padded() -> None:
    """A 5-day forecast for 20 days left: the other 15 days repeat the average day."""
    tracker = BudgetTracker(300.0, [10.0] * 10)
    result = check_budget_exceed(tracker, _tv_household(2.0), _forecast(5))
    assert math.isclose(result.predicted_total, 100.0 + 20 * 2.0)


def _pilot_household() -> Household:
    """AC (10 units on a 40°C day), Iron (2 units, flexible), Fridge (2.4 units, essential)."""
    return Household(300.0, 4, [
        Appliance("AC", 1000, 1, "", 4),
        Appliance("Iron", 1000, 1, "", 2),
        Appliance("Fridge", 100, 1, "", 24),
    ])


_HOT_DAY = WeatherDay(date(2026, 7, 1), 40.0, 30.0, 50.0)


def _hours(result: PilotResult) -> Dict[str, float]:
    """Map appliance name -> adjusted hours, for easy asserts."""
    return {p.appliance.name: p.recommended_hours for p in result.plans}


def _test_allocation_weights_hot_days() -> None:
    """Shares add up to the remaining budget and hot days get more than mild days."""
    tracker = BudgetTracker(300.0, [10.0] * 10)
    forecast = _forecast(10, max_temp=25.0) + _forecast(10, max_temp=45.0)
    shares = allocate_daily_budget(tracker, forecast)
    assert len(shares) == 20 and math.isclose(sum(shares), 200.0)
    assert shares[-1] > shares[0]


def _test_allocation_recalculates() -> None:
    """After overspending, the same day gets a smaller allocation."""
    tracker = BudgetTracker(300.0, [10.0] * 10)
    forecast = _forecast(20)
    before = allocate_daily_budget(tracker, forecast)[0]   # 200 / 20 = 10
    tracker.add_day(30.0)                                   # overspent by 20 units
    after = allocate_daily_budget(tracker, forecast)[0]    # 170 / 19
    assert math.isclose(before, 10.0) and after < before


def _test_pilot_no_change_within_allocation() -> None:
    """If predicted usage fits the allocation, nothing is reduced."""
    result = UsagePilot().optimize(_pilot_household(), _HOT_DAY, 20.0)
    assert _hours(result) == {"AC": 10.0, "Iron": 2, "Fridge": 24}
    assert not result.unachievable and "No changes" in result.explanation


def _test_pilot_reduces_flexible_first() -> None:
    """Predicted 14.4 vs 13.0 allowed: only the Iron is cut; AC and Fridge are untouched."""
    result = UsagePilot().optimize(_pilot_household(), _HOT_DAY, 13.0)
    hours = _hours(result)
    assert hours["AC"] == 10.0 and hours["Fridge"] == 24
    assert hours["Iron"] < 2
    assert result.adjusted_units <= 13.0 + 1e-6 and not result.unachievable
    assert "Iron" in result.explanation


def _test_pilot_reduces_weather_after_flexible_but_keeps_comfort() -> None:
    """Allowed 8.0: Iron goes to 0, AC is cut but stays at or above half of 10 hours."""
    result = UsagePilot().optimize(_pilot_household(), _HOT_DAY, 8.0)
    hours = _hours(result)
    assert hours["Iron"] == 0 and 5.0 <= hours["AC"] < 10.0 and hours["Fridge"] == 24
    assert result.adjusted_units <= 8.0 + 1e-6 and not result.unachievable


def _test_pilot_unachievable() -> None:
    """Allowed 5.0 but the minimum is 7.4 (AC 5 + Fridge 2.4): flagged unachievable."""
    result = UsagePilot().optimize(_pilot_household(), _HOT_DAY, 5.0)
    hours = _hours(result)
    assert result.unachievable and math.isclose(result.minimum_units, 7.4)
    assert hours["AC"] == 5.0 and hours["Iron"] == 0 and hours["Fridge"] == 24
    assert "cannot be met" in result.explanation


def _test_pilot_plan_today_and_time_slots() -> None:
    """plan_today uses today's share of the budget; AC plans get time windows."""
    tracker = BudgetTracker(300.0, [10.0] * 10)
    result = UsagePilot().plan_today(tracker, _pilot_household(), [_HOT_DAY] * 20)
    ac_plan = result.plans[0]
    assert ac_plan.time_slots and all(0 <= s < e <= 24 for s, e in ac_plan.time_slots)
    assert result.plans[1].time_slots == []   # flexible appliance: no weather slots


def _flex_household() -> Household:
    """Washing Machine (0.5 units, flexible), Iron (2 units, flexible), Fridge (2.4 units, essential)."""
    return Household(300.0, 4, [
        Appliance("Washing Machine", 500, 1, "", 1),
        Appliance("Iron", 1000, 1, "", 2),
        Appliance("Fridge", 100, 1, "", 24),
    ])


def _test_min_runs_per_week_lookup() -> None:
    """Washing machine needs 3 runs a week; unlisted flexible appliances get the default."""
    assert min_runs_per_week(Appliance("Washing Machine", 500, 1, "", 1)) == 3
    assert min_runs_per_week(Appliance("Hair Dryer", 1000, 1, "", 1)) == DEFAULT_FLEXIBLE_MIN_RUNS


def _test_schedule_picks_lowest_cost_days() -> None:
    """With an AC in the house, the cooler days cost less, so the washing machine runs on those."""
    household = Household(300.0, 4, [
        Appliance("AC", 1000, 1, "", 4),
        Appliance("Washing Machine", 500, 1, "", 1),
    ])
    temps = [30, 44, 35, 26, 40, 28, 46]    # cheapest days: 26°C (index 3), 28°C (5), 30°C (0)
    week = [_forecast(1, max_temp=t)[0] for t in temps]
    assert schedule_flexible_runs(household, week) == {"Washing Machine": [0, 3, 5]}


def _test_weekly_minimum_never_violated() -> None:
    """Even with an allocation that only covers the fridge, each flexible appliance
    still runs on exactly its minimum number of days (washing machine 3, iron 2)."""
    household = _flex_household()
    week = _forecast(7)
    plan = UsagePilot().plan_week(household, week, [2.4] * 7)   # fridge uses 2.4 a day

    for name, minimum in (("Washing Machine", 3), ("Iron", 2)):
        ran_on = [day.date for day, result in zip(week, plan.days) if _hours(result)[name] > 0]
        assert len(ran_on) >= minimum
        assert ran_on == plan.flexible_run_days[name]            # ran only on its must-run days
    assert all(_hours(result)["Fridge"] == 24 for result in plan.days)   # essentials untouched


def _test_flexible_can_be_skipped_when_not_required() -> None:
    """On a non-required day the Iron is skipped when needed; the minimum is not touched
    when budget allows (the Washing Machine is only 0.5 units, so it is kept)."""
    result = UsagePilot().optimize(_flex_household(), _forecast(1)[0], 3.0, required_flexible=set())
    hours = _hours(result)
    assert hours["Iron"] == 0 and hours["Washing Machine"] == 1 and not result.unachievable


def _test_explanation_mentions_weekly_minimum() -> None:
    """The text says what was skipped and which appliance was kept for its weekly minimum."""
    result = UsagePilot().optimize(_flex_household(), _forecast(1)[0], 3.0,
                                   required_flexible={"Washing Machine"})
    assert "Iron (flexible): skipped today" in result.explanation
    assert "weekly minimum of 2 runs" in result.explanation
    assert "Washing Machine (flexible): kept today, one of its 3 minimum runs" in result.explanation


if __name__ == "__main__":
    tests = [
        _test_predict_flexible_appliance,
        _test_predict_uses_weather_hours,
        _test_predicted_units_rise_with_ac_temperature,
        _test_tracker_numbers,
        _test_check_safe,
        _test_check_warning,
        _test_check_critical,
        _test_short_forecast_is_padded,
        _test_allocation_weights_hot_days,
        _test_allocation_recalculates,
        _test_pilot_no_change_within_allocation,
        _test_pilot_reduces_flexible_first,
        _test_pilot_reduces_weather_after_flexible_but_keeps_comfort,
        _test_pilot_unachievable,
        _test_pilot_plan_today_and_time_slots,
        _test_min_runs_per_week_lookup,
        _test_schedule_picks_lowest_cost_days,
        _test_weekly_minimum_never_violated,
        _test_flexible_can_be_skipped_when_not_required,
        _test_explanation_mentions_weekly_minimum,
    ]
    for test in tests:
        test()
    print(f"All {len(tests)} budget tests passed.")
