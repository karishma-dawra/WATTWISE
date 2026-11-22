"""Single source of truth for appliance energy.

    predicted units (kWh) = rated kW x quantity x duty cycle x operating hours

Every calculation (appliance table, daily demand, recommendation plan, dashboard totals) goes through these
functions, so an appliance's number is identical wherever it is shown.
Duty cycles come from weather_appliance_rules.json (duty_cycle).
"""
from services.weather_rules_engine import kind_of, load_rules


def duty_factor(a):
    duty = load_rules().get("duty_cycle", {})
    return float(duty.get(kind_of(a), duty.get("default", 1.0)))


def effective_kw(a):
    """Average power while the appliance is operating: rated kW x quantity x duty cycle."""
    return a["wattage"] * a["quantity"] / 1000.0 * duty_factor(a)


def predicted_kwh(a, hours):
    """Predicted units for `hours` of operation."""
    return effective_kw(a) * float(hours)
