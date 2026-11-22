"""Advisor (part 1): classify appliances and recommend daily hours from weather.

Pure functions only: plain inputs in, plain outputs out. No UI, no files, no database.
"""

import math
import re
from datetime import date
from typing import Dict, List, Optional, Tuple

from models import Appliance, WeatherDay

# ---------------------------------------------------------------------------
# TUNABLE CONSTANTS (change these to tweak the advice)
# ---------------------------------------------------------------------------

# Temperature (°C) ranges used by the weather-based formulas
# AC curve: 0h at AC_START_TEMP -> AC_KNEE_HOURS at AC_KNEE_TEMP -> AC_MAX_HOURS at AC_PEAK_TEMP
AC_START_TEMP = 26.0            # AC is 0 hours at or below this max temp
AC_KNEE_TEMP = 40.0             # first ramp ends here...
AC_KNEE_HOURS = 10.0            # ...at this many hours
AC_PEAK_TEMP = 47.0             # second ramp ends here (hotter days stay at the max)
AC_MAX_HOURS = 16.0             # hard maximum AC hours per day

FAN_START_TEMP = 28.0           # Fan starts running more above this max temp
FAN_FULL_TEMP = 40.0            # Fan reaches its max hours at this max temp
FAN_MAX_HOURS = 14.0            # Fan max hours per day

COOLER_START_TEMP = 28.0        # Cooler starts running above this max temp
COOLER_FULL_TEMP = 40.0         # Cooler reaches its max hours at this max temp
COOLER_MAX_HOURS = 10.0         # Cooler max hours per day

HEATER_START_TEMP = 18.0        # Heater is 0 hours at or above this min temp
HEATER_FULL_TEMP = 5.0          # Heater reaches its max hours at this min temp
HEATER_MAX_HOURS = 8.0          # Heater max hours per day

GEYSER_START_TEMP = 18.0        # Geyser is 0 hours at or above this min temp
GEYSER_FULL_TEMP = 5.0          # Geyser reaches its max hours at this min temp
GEYSER_MAX_HOURS = 2.0          # Geyser max hours per day

# Humidity (%) settings
HIGH_HUMIDITY = 70.0            # At or above this, humidity effects kick in
FAN_HUMIDITY_BOOST = 1.10       # Fans run 10% more when humid (people feel hotter)
COOLER_HUMIDITY_FACTOR = 0.50   # Coolers work about half as well when humid

# Keyword lists used to classify appliances by name (matched as whole words)
# Weather kinds are checked in this order, so longer phrases come first.
WEATHER_KEYWORDS = [
    ("water heater", "geyser"),
    ("geyser", "geyser"),
    ("heater", "heater"),
    ("air conditioner", "ac"),
    ("air conditioning", "ac"),
    ("ac", "ac"),
    ("fan", "fan"),
    ("cooler", "cooler"),
]
ESSENTIAL_KEYWORDS = [
    "fridge", "refrigerator", "light", "lights", "bulb", "led",
    "router", "modem",
]
# Anything that matches neither list is treated as "flexible".

# Category labels returned by classify_appliance()
WEATHER_DEPENDENT = "weather_dependent"
ESSENTIAL = "essential"
FLEXIBLE = "flexible"
VALID_CLASSES = (WEATHER_DEPENDENT, ESSENTIAL, FLEXIBLE)   # the only non-empty categories allowed

# Day slots as (name, start_hour, end_hour). Each slot is 6 hours long.
SLOTS = [
    ("night", 0, 6),
    ("morning", 6, 12),
    ("afternoon", 12, 18),
    ("evening", 18, 24),
]

# How much each kind of appliance tends to run in each slot (relative weights).
# Higher weight = more of that appliance's hours go into that slot.
# Cooling kinds peak in the hot afternoon/evening; heating kinds peak in the cold morning.
SLOT_WEIGHTS = {
    "ac":     {"night": 0.5, "morning": 1.0, "afternoon": 3.0, "evening": 3.0},
    "fan":    {"night": 1.5, "morning": 1.0, "afternoon": 2.0, "evening": 2.5},
    "cooler": {"night": 1.0, "morning": 1.0, "afternoon": 2.5, "evening": 2.5},
    "heater": {"night": 2.0, "morning": 3.0, "afternoon": 0.5, "evening": 1.5},
    "geyser": {"night": 0.5, "morning": 3.0, "afternoon": 0.5, "evening": 1.0},
}

EPS = 1e-9  # tiny number used to avoid floating-point noise


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def _normalize(name: str) -> str:
    """Lowercase the name and keep only words, padded with spaces.

    Example: "Ceiling-Fan (2)" -> " ceiling fan "
    Padding lets us match whole words only, so "ac" will not match "space".
    """
    words = re.findall(r"[a-z]+", name.lower())
    return " " + " ".join(words) + " "


def _has_word(normalized: str, keyword: str) -> bool:
    """True if the keyword appears as whole word(s) in the normalized name."""
    return f" {keyword} " in normalized


def _ramp(value: float, start: float, full: float) -> float:
    """Return a 0-to-1 fraction moving from start to full, clamped to [0, 1].

    Works for both rising values (AC: 26 -> 40) and falling values
    (heater: 18 -> 5), because it is just linear interpolation.
    """
    fraction = (value - start) / (full - start)
    return max(0.0, min(1.0, fraction))


def _weather_kind(appliance: Appliance) -> Optional[str]:
    """Return the weather kind ("ac", "fan", "cooler", "heater", "geyser") or None."""
    normalized = _normalize(appliance.name)
    for keyword, kind in WEATHER_KEYWORDS:
        if _has_word(normalized, keyword):
            return kind
    return None


# ---------------------------------------------------------------------------
# PUBLIC FUNCTIONS
# ---------------------------------------------------------------------------

def classify_appliance(appliance: Appliance) -> str:
    """Classify an appliance as weather_dependent, essential, or flexible.

    1. If appliance.category is one of those three words (ignoring case and
       surrounding spaces), it is used directly, whatever the name says.
    2. If the category is empty or any other text, the name keywords decide.
       (The API reports unrecognized text as a warning.)
    ValueError is raised only for a category that is not text at all (e.g. a
    number), and for "weather_dependent" on a name that does not say which
    weather curve to use (ac, fan, cooler, heater or geyser).
    """
    raw = appliance.category
    if raw is not None and not isinstance(raw, str):
        raise ValueError(f"Appliance '{appliance.name}': category must be text, "
                         f"got {type(raw).__name__}")
    category = (raw or "").strip().lower()
    if category in VALID_CLASSES:
        if category == WEATHER_DEPENDENT and _weather_kind(appliance) is None:
            raise ValueError(
                f"Appliance '{appliance.name}': category 'weather_dependent' needs a name that "
                f"says which weather curve to use (ac, fan, cooler, heater or geyser)")
        return category

    # Empty or unrecognized category: classify by name
    if _weather_kind(appliance) is not None:
        return WEATHER_DEPENDENT

    normalized = _normalize(appliance.name)
    for keyword in ESSENTIAL_KEYWORDS:
        if _has_word(normalized, keyword):
            return ESSENTIAL

    return FLEXIBLE


def weather_kind(appliance: Appliance) -> Optional[str]:
    """Public version of the kind lookup: "ac", "fan", "cooler", "heater", "geyser" or None."""
    return _weather_kind(appliance)


def weather_hours(appliance: Appliance, weather: WeatherDay) -> float:
    """Recommended daily hours for a weather-dependent appliance on this day.

    Returns 0.0 for appliances whose final class is not weather_dependent
    (for example a fan whose category says "essential").
    """
    if classify_appliance(appliance) != WEATHER_DEPENDENT:
        return 0.0
    kind = _weather_kind(appliance)   # which curve to use, decided by the name

    if kind == "ac":
        # AC depends only on the day's maximum temperature, in two linear steps:
        # 26 -> 40°C gives 0 -> 10h, then 40 -> 47°C keeps rising up to the 16h maximum
        if weather.max_temp <= AC_KNEE_TEMP:
            hours = AC_KNEE_HOURS * _ramp(weather.max_temp, AC_START_TEMP, AC_KNEE_TEMP)
        else:
            hours = AC_KNEE_HOURS + (AC_MAX_HOURS - AC_KNEE_HOURS) * _ramp(
                weather.max_temp, AC_KNEE_TEMP, AC_PEAK_TEMP)

    elif kind == "fan":
        # Fan depends on max temperature, and runs a bit more when humid
        hours = FAN_MAX_HOURS * _ramp(weather.max_temp, FAN_START_TEMP, FAN_FULL_TEMP)
        if weather.humidity >= HIGH_HUMIDITY:
            hours *= FAN_HUMIDITY_BOOST

    elif kind == "cooler":
        # Cooler depends on max temperature, but works poorly when humid
        hours = COOLER_MAX_HOURS * _ramp(weather.max_temp, COOLER_START_TEMP, COOLER_FULL_TEMP)
        if weather.humidity >= HIGH_HUMIDITY:
            hours *= COOLER_HUMIDITY_FACTOR

    elif kind == "heater":
        # Heater depends on how cold the night (minimum temperature) gets
        hours = HEATER_MAX_HOURS * _ramp(weather.min_temp, HEATER_START_TEMP, HEATER_FULL_TEMP)

    elif kind == "geyser":
        # Geyser depends on how cold the night (minimum temperature) gets
        hours = GEYSER_MAX_HOURS * _ramp(weather.min_temp, GEYSER_START_TEMP, GEYSER_FULL_TEMP)

    else:
        return 0.0

    return round(hours, 1)


def recommend_hours(appliances: List[Appliance], weather: WeatherDay) -> Dict[str, float]:
    """Return {appliance_name: recommended_hours} for every appliance.

    - Weather-dependent appliances get hours from the weather formulas.
    - Essential and flexible appliances keep their current average hours for now.
      (Budget-based adjustment of flexible appliances will come in part 2.)

    Note: if two appliances share a name, the later one wins in the dict.
    """
    result: Dict[str, float] = {}
    for appliance in appliances:
        if classify_appliance(appliance) == WEATHER_DEPENDENT:
            result[appliance.name] = weather_hours(appliance, weather)
        else:
            result[appliance.name] = appliance.avg_hours_per_day
    return result


# ---------------------------------------------------------------------------
# PART 2: SLOT SPLITTING
# ---------------------------------------------------------------------------

def _allocate(total: float, weights: Dict[str, float]) -> Dict[str, float]:
    """Split `total` hours across slots in proportion to `weights`.

    No slot may get more than its length (6h). If a slot would overflow,
    it is filled to its full length and the rest is shared among the others.
    """
    capacity = {name: float(end - start) for name, start, end in SLOTS}
    if total > sum(capacity.values()) + EPS:
        raise ValueError(f"Cannot fit {total} hours into a 24-hour day")

    alloc = {name: 0.0 for name, _, _ in SLOTS}
    remaining = total
    open_slots = [name for name in capacity if weights.get(name, 0) > 0]

    while remaining > EPS:
        if not open_slots:
            raise ValueError("No slots left to hold the remaining hours")
        weight_sum = sum(weights[s] for s in open_slots)
        room = {s: capacity[s] - alloc[s] for s in open_slots}

        # Which slots would get more than they have room for?
        overflow = [s for s in open_slots if remaining * weights[s] / weight_sum > room[s]]

        if not overflow:
            # Everything fits: give each open slot its proportional share
            for s in open_slots:
                alloc[s] += remaining * weights[s] / weight_sum
            remaining = 0.0
        else:
            # Fill the overflowing slots completely, then share the rest again
            for s in overflow:
                alloc[s] = capacity[s]
                remaining -= room[s]
            open_slots = [s for s in open_slots if s not in overflow]

    return alloc


def split_into_slots(appliance: Appliance, weather: WeatherDay) -> List[Tuple[str, float]]:
    """Split a weather-dependent appliance's recommended hours across day slots.

    Returns [(slot_name, hours), ...] for all four slots, in time order.
    The hours add up to weather_hours(appliance, weather), and no slot
    exceeds its length. Non-weather-dependent appliances return [].
    """
    return split_hours_into_slots(appliance, weather_hours(appliance, weather))


def split_hours_into_slots(appliance: Appliance, hours: float) -> List[Tuple[str, float]]:
    """Like split_into_slots, but for any number of hours you choose.

    Useful after the hours were changed (for example, reduced to fit a budget).
    Appliances whose final class is not weather_dependent return [].
    """
    if classify_appliance(appliance) != WEATHER_DEPENDENT:
        return []
    kind = _weather_kind(appliance)

    total = hours
    alloc = _allocate(total, SLOT_WEIGHTS[kind])

    # Round to 2 decimals, then push any rounding difference into one slot
    # so the total still matches exactly (at 2 decimals).
    result = {name: round(alloc[name], 2) for name in alloc}
    diff = round(total - sum(result.values()), 2)
    if diff != 0:
        capacity = {name: float(end - start) for name, start, end in SLOTS}
        # Choose the slot that has the most hours and still has room for the change
        candidates = [s for s in result if 0 <= result[s] + diff <= capacity[s]]
        best = max(candidates, key=lambda s: result[s])
        result[best] = round(result[best] + diff, 2)

    return [(name, result[name]) for name, _, _ in SLOTS]


# ---------------------------------------------------------------------------
# TEST CASES (run with: python advisor.py)
# ---------------------------------------------------------------------------

def _test_classify_appliance() -> None:
    """Keywords map each appliance to the right class."""
    assert classify_appliance(Appliance("Ceiling Fan", 75, 2, "", 8)) == WEATHER_DEPENDENT
    assert classify_appliance(Appliance("Fridge", 150, 1, "", 24)) == ESSENTIAL
    assert classify_appliance(Appliance("Washing Machine", 500, 1, "", 1)) == FLEXIBLE


def _test_category_overrides_name() -> None:
    """A valid category wins over the name keywords (ignoring case and spaces)."""
    assert classify_appliance(Appliance("Ceiling Fan", 75, 2, "essential", 8)) == ESSENTIAL
    assert classify_appliance(Appliance("Fridge", 150, 1, "flexible", 24)) == FLEXIBLE
    assert classify_appliance(Appliance("Iron", 1000, 1, " Essential ", 1)) == ESSENTIAL
    assert classify_appliance(Appliance("AC", 1500, 1, "flexible", 8)) == FLEXIBLE
    assert classify_appliance(Appliance("Window AC", 1500, 1, "weather_dependent", 8)) == WEATHER_DEPENDENT

    # "weather_dependent" on a name with no known weather curve is rejected, not silently 0 hours
    try:
        classify_appliance(Appliance("Water Pump", 750, 1, "weather_dependent", 2))
    except ValueError as error:
        assert "weather curve" in str(error)
    else:
        raise AssertionError("expected ValueError")


def _test_overridden_appliance_gets_no_weather_hours_or_slots() -> None:
    """A fan marked essential is not weather-dependent: no weather hours, no time slots."""
    fan = Appliance("Ceiling Fan", 75, 1, "essential", 10)
    hot = WeatherDay(date(2026, 5, 1), 42.0, 30.0, 50.0)
    assert weather_hours(fan, hot) == 0.0
    assert split_into_slots(fan, hot) == []
    assert split_hours_into_slots(fan, 5.0) == []
    assert recommend_hours([fan], hot) == {"Ceiling Fan": 10}   # keeps its average hours


def _test_name_fallback_when_category_missing() -> None:
    """With an empty (or whitespace-only) category, the name keywords decide as before."""
    assert classify_appliance(Appliance("Desert Cooler", 200, 1, "", 8)) == WEATHER_DEPENDENT
    assert classify_appliance(Appliance("Wi-Fi Router", 10, 1, "  ", 24)) == ESSENTIAL
    assert classify_appliance(Appliance("TV", 100, 1, "", 4)) == FLEXIBLE


def _test_unrecognized_category_falls_back_to_name() -> None:
    """Unknown text such as "cooling" no longer raises: the name decides.
    A category that is not text at all (a number) still raises."""
    assert classify_appliance(Appliance("Ceiling Fan", 75, 2, "cooling", 8)) == WEATHER_DEPENDENT
    assert classify_appliance(Appliance("Fridge", 150, 1, "kitchen", 24)) == ESSENTIAL
    assert classify_appliance(Appliance("TV", 100, 1, "whatever", 4)) == FLEXIBLE
    try:
        classify_appliance(Appliance("TV", 100, 1, 5, 4))
    except ValueError as error:
        assert "must be text" in str(error)
    else:
        raise AssertionError("expected ValueError")


def _test_ac_temperature_scaling() -> None:
    """AC: 0h at 26°C or below, 10h at 40°C, then rising to a 16h max at 47°C."""
    ac = Appliance("AC", 1500, 1, "", 8)

    def ac_hours(max_temp: float) -> float:
        return weather_hours(ac, WeatherDay(date(2026, 11, 22), max_temp, 28.0, 50.0))

    assert ac_hours(25.0) == 0.0 and ac_hours(26.0) == 0.0
    assert ac_hours(38.0) == 8.6                    # 10 * 12/14
    assert ac_hours(40.0) == AC_KNEE_HOURS          # 10h: end of the first ramp
    assert ac_hours(41.0) == 10.9                   # 10 + 6 * 1/7
    assert ac_hours(44.0) == 13.4                   # 10 + 6 * 4/7
    assert ac_hours(47.0) == AC_MAX_HOURS           # 16h
    assert ac_hours(50.0) == AC_MAX_HOURS           # hard max: never above 16h
    assert ac_hours(38.0) < ac_hours(41.0) < ac_hours(44.0) < ac_hours(47.0)


def _test_heater_cold_scaling() -> None:
    """Heater is 0 hours at a min of 18°C and max hours at 5°C."""
    heater = Appliance("Room Heater", 2000, 1, "", 4)
    mild_night = WeatherDay(date(2026, 1, 10), 22.0, 18.0, 60.0)
    freezing_night = WeatherDay(date(2026, 1, 10), 14.0, 5.0, 60.0)
    assert weather_hours(heater, mild_night) == 0.0
    assert weather_hours(heater, freezing_night) == HEATER_MAX_HOURS


def _test_cooler_less_effective_when_humid() -> None:
    """A cooler gets fewer recommended hours on a humid day than a dry day."""
    cooler = Appliance("Desert Cooler", 200, 1, "", 8)
    dry_day = WeatherDay(date(2026, 4, 15), 35.0, 25.0, 40.0)
    humid_day = WeatherDay(date(2026, 4, 15), 35.0, 25.0, 85.0)
    assert weather_hours(cooler, humid_day) < weather_hours(cooler, dry_day)


def _test_recommend_hours_dict() -> None:
    """recommend_hours returns every appliance, and keeps essentials unchanged."""
    appliances = [
        Appliance("AC", 1500, 1, "", 8),
        Appliance("Fridge", 150, 1, "", 24),
        Appliance("TV", 100, 1, "", 4),
    ]
    weather = WeatherDay(date(2026, 5, 1), 41.0, 30.0, 55.0)
    result = recommend_hours(appliances, weather)
    assert set(result.keys()) == {"AC", "Fridge", "TV"}
    assert result["Fridge"] == 24  # essentials keep their current hours


def _test_slot_totals_match_recommended_hours() -> None:
    """Slot hours add up to the recommended daily hours, for every kind."""
    hot = WeatherDay(date(2026, 7, 1), 42.0, 30.0, 50.0)
    cold = WeatherDay(date(2026, 1, 10), 12.0, 5.0, 60.0)
    cases = [
        (Appliance("AC", 1500, 1, "", 8), hot),
        (Appliance("Ceiling Fan", 75, 2, "", 8), hot),
        (Appliance("Desert Cooler", 200, 1, "", 8), hot),
        (Appliance("Room Heater", 2000, 1, "", 4), cold),
        (Appliance("Geyser", 3000, 1, "", 1), cold),
    ]
    for appliance, weather in cases:
        slots = split_into_slots(appliance, weather)
        total = sum(hours for _, hours in slots)
        assert math.isclose(total, weather_hours(appliance, weather), abs_tol=0.01), appliance.name


def _test_slots_never_exceed_length() -> None:
    """Every slot gets between 0 and its length (6 hours)."""
    weather = WeatherDay(date(2026, 1, 10), 12.0, 5.0, 60.0)
    geyser = Appliance("Geyser", 3000, 1, "", 1)
    heater = Appliance("Room Heater", 2000, 1, "", 4)
    for appliance in (geyser, heater):
        for name, hours in split_into_slots(appliance, weather):
            assert 0.0 <= hours <= 6.0, (appliance.name, name, hours)


def _test_hot_day_ac_peaks_afternoon_evening() -> None:
    """On a hot day, AC runs mostly in the afternoon and evening."""
    ac = Appliance("AC", 1500, 1, "", 8)
    hot = WeatherDay(date(2026, 7, 1), 42.0, 30.0, 50.0)
    hours = dict(split_into_slots(ac, hot))
    assert hours["afternoon"] + hours["evening"] > hours["night"] + hours["morning"]


def _test_cold_day_heater_peaks_morning() -> None:
    """On a cold day, the heater runs most in the morning."""
    heater = Appliance("Room Heater", 2000, 1, "", 4)
    cold = WeatherDay(date(2026, 1, 10), 12.0, 5.0, 60.0)
    hours = dict(split_into_slots(heater, cold))
    assert hours["morning"] == max(hours.values())


def _test_non_weather_appliance_has_no_slots() -> None:
    """Essential and flexible appliances are not split by weather."""
    fridge = Appliance("Fridge", 150, 1, "", 24)
    assert split_into_slots(fridge, WeatherDay(date(2026, 1, 10), 12.0, 5.0, 60.0)) == []


def _print_slots(title: str, appliance: Appliance, weather: WeatherDay) -> None:
    """Print a readable slot breakdown for the demo."""
    print(f"{title}: {appliance.name}, max {weather.max_temp}°C, min {weather.min_temp}°C")
    print(f"  Recommended hours: {weather_hours(appliance, weather)}")
    for name, hours in split_into_slots(appliance, weather):
        print(f"  {name:<10} {hours:>5.2f} h")
    print()


if __name__ == "__main__":
    tests = [
        _test_classify_appliance,
        _test_category_overrides_name,
        _test_overridden_appliance_gets_no_weather_hours_or_slots,
        _test_name_fallback_when_category_missing,
        _test_unrecognized_category_falls_back_to_name,
        _test_ac_temperature_scaling,
        _test_heater_cold_scaling,
        _test_cooler_less_effective_when_humid,
        _test_recommend_hours_dict,
        _test_slot_totals_match_recommended_hours,
        _test_slots_never_exceed_length,
        _test_hot_day_ac_peaks_afternoon_evening,
        _test_cold_day_heater_peaks_morning,
        _test_non_weather_appliance_has_no_slots,
    ]
    for test in tests:
        test()
    print(f"All {len(tests)} advisor tests passed.\n")

    # Demo: slot breakdown on a 42°C day and a 5°C day
    _print_slots("Hot day", Appliance("AC", 1500, 1, "", 8),
                 WeatherDay(date(2026, 7, 1), 42.0, 30.0, 50.0))
    _print_slots("Cold day", Appliance("Room Heater", 2000, 1, "", 4),
                 WeatherDay(date(2026, 1, 10), 12.0, 5.0, 60.0))
