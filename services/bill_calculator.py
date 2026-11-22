"""Configurable bill estimator — supports a flat rate or slab tariff, free units and electricity duty.

  billable units = max(0, units - free_units)          (simple deduction; scheme eligibility rules are NOT modelled)
  energy charge  = slab sum, or billable units x flat tariff
  bill           = (energy + fixed) x (1 + duty%) + other
Always an estimate: real bills may differ through taxes, surcharges, subsidies and meter-reading dates.
"""
import json


def _slabs(h):
    s = h.get("tariff_slabs")
    if isinstance(s, str):
        try:
            s = json.loads(s)
        except ValueError:
            s = None
    return s or []


def energy_charge(units, tariff, slabs):
    if not slabs:
        return units * float(tariff)
    total, prev = 0.0, 0.0
    for upto, rate in slabs:
        top = units if upto is None else min(units, upto)
        if top > prev:
            total += (top - prev) * rate
        prev = upto if upto is not None else prev
        if upto is None or units <= upto:
            break
    return total


def estimate_bill(units, tariff, fixed_charges=0.0, other_charges=0.0, slabs=None, duty_percent=0.0, free_units=0.0, symbol="₹", free_mode="deduct"):
    """free_mode "deduct": free units come off the consumption first and the rest is priced from the first slab.
    "cover": the first free_units units are free where they sit, so units above them keep their own slab rate
    (Punjab 0-300 free, 301+ at the upper slab rate)."""
    units = max(0.0, float(units))
    free = float(free_units or 0)
    billable = max(0.0, units - free)
    if free_mode == "cover":
        energy = energy_charge(units, tariff, slabs) - energy_charge(min(units, free), tariff, slabs)
    else:
        energy = energy_charge(billable, tariff, slabs)
    subtotal = energy + float(fixed_charges)
    duty = subtotal * float(duty_percent or 0) / 100.0
    total = subtotal + duty + float(other_charges)
    return {
        "units": round(units, 2), "billable_units": round(billable, 2), "tariff": float(tariff),
        "slab_tariff": bool(slabs), "energy_charge": round(energy, 2), "fixed_charges": round(float(fixed_charges), 2),
        "duty_percent": float(duty_percent or 0), "duty": round(duty, 2), "other_charges": round(float(other_charges), 2),
        "total": round(total, 2), "currency_symbol": symbol, "is_estimate": True,
        "disclaimer": ("Estimate based on " + ("your slab tariff" if slabs else "a flat per-unit tariff") +
                       ". Actual bills may include taxes, surcharges, subsidies and billing-date differences — check your utility bill."),
    }


def bill_for(h, units):
    """Bill estimate using the household's own (or region-applied) pricing settings."""
    return estimate_bill(units, h["tariff"], h["fixed_charges"], h["other_charges"], _slabs(h),
                         h.get("duty_percent") or 0, h.get("free_units") or 0, h.get("currency_symbol") or "",
                         h.get("free_units_mode") or "deduct")


def marginal_rate(h, units):
    """Cost of the next unit at the current consumption level (includes slab step and duty)."""
    a, b = bill_for(h, units)["total"], bill_for(h, units + 1)["total"]
    return max(0.0, b - a)
