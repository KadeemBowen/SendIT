import json
import math

from . import db
from .config import PRICING

FIELDS = tuple(PRICING)


def current_rates():
    """Pricing set by an admin, falling back to the defaults in config.py."""
    row = db.query_one("SELECT value FROM settings WHERE key = 'pricing'")
    saved = json.loads(row["value"]) if row else {}
    return {k: saved.get(k, PRICING[k]) for k in FIELDS}


def save_rates(rates):
    db.execute(
        "INSERT INTO settings (key, value) VALUES ('pricing', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (json.dumps({k: rates[k] for k in FIELDS}),),
    )


def quote(distance_km, extra_stops, tasks, rates=None):
    """Return (total, breakdown). Breakdown lines are shown to the customer."""
    p = rates or current_rates()
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
