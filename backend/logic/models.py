"""Data models for WattWise.

These are plain dataclasses. They hold data only: no business logic yet.
Each class has to_dict() so the data can be turned into a dictionary
(and later into JSON) by a UI or backend.
"""

from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Tuple, Any


@dataclass
class Appliance:
    """One electrical appliance in the household."""
    name: str                   # e.g. "Ceiling Fan"
    wattage: float              # power rating in watts (W)
    quantity: int               # how many of this appliance
    category: str               # "weather_dependent", "essential" or "flexible"; "" = decide by name
    avg_hours_per_day: float    # current average daily use in hours

    def to_dict(self) -> Dict[str, Any]:
        """Return this appliance as a plain dictionary."""
        return {
            "name": self.name,
            "wattage": self.wattage,
            "quantity": self.quantity,
            "category": self.category,
            "avg_hours_per_day": self.avg_hours_per_day,
        }


@dataclass
class Household:
    """A household and its monthly electricity budget."""
    monthly_budget_units: float         # allowed monthly usage in kWh (units)
    family_size: int                    # number of people in the home
    appliances: List[Appliance] = field(default_factory=list)  # all appliances

    def to_dict(self) -> Dict[str, Any]:
        """Return this household as a plain dictionary (appliances nested)."""
        return {
            "monthly_budget_units": self.monthly_budget_units,
            "family_size": self.family_size,
            "appliances": [a.to_dict() for a in self.appliances],
        }


@dataclass
class WeatherDay:
    """Weather summary for one day."""
    date: date                  # calendar day
    max_temp: float             # highest temperature in °C
    min_temp: float             # lowest temperature in °C
    humidity: float             # relative humidity in percent (0-100)

    def to_dict(self) -> Dict[str, Any]:
        """Return this weather day as a plain dictionary (date as ISO text)."""
        return {
            "date": self.date.isoformat(),  # e.g. "2026-11-22"
            "max_temp": self.max_temp,
            "min_temp": self.min_temp,
            "humidity": self.humidity,
        }


@dataclass
class UsagePlan:
    """A recommended usage plan for one appliance."""
    appliance: Appliance                        # which appliance this plan is for
    recommended_hours: float                    # suggested hours per day
    # Suggested time windows as (start_hour, end_hour) in 24h format, e.g. (18, 20)
    time_slots: List[Tuple[int, int]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Return this usage plan as a plain dictionary (time slots as lists)."""
        return {
            "appliance": self.appliance.to_dict(),
            "recommended_hours": self.recommended_hours,
            "time_slots": [list(slot) for slot in self.time_slots],
        }
