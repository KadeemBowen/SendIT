import math

from .config import PRICING


def quote(distance_km, extra_stops, tasks):
    """Return (total, breakdown). Breakdown lines are shown to the customer."""
    p = PRICING
    lines = [
        ("Base fee", p["base_fee"]),
        (f"Distance ({distance_km:.1f} km)", distance_km * p["per_km"]),
    ]
    if extra_stops:
        lines.append((f"Extra stops ({extra_stops})", extra_stops * p["per_extra_stop"]))
    if tasks:
        lines.append((f"Tasks ({tasks})", tasks * p["per_task"]))

    subtotal = sum(amount for _, amount in lines)
    total = math.ceil(subtotal / p["round_to"]) * p["round_to"]
    if total < p["minimum"]:
        lines.append(("Minimum fare adjustment", p["minimum"] - total))
        total = p["minimum"]
    return total, [{"label": label, "amount": round(amount)} for label, amount in lines]
