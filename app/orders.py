"""Order serialization, ETAs, and live-update fan-out."""
import json

from fastapi import HTTPException

from . import config, db, geo, payments
from .hub import hub

ACTIVE_STATUSES = ("requested", "accepted", "picked_up")


def compute_eta(o, loc):
    if not loc:
        return None
    here = (loc["lat"], loc["lng"])
    pickup = (o["pickup_lat"], o["pickup_lng"])
    dropoff = (o["dropoff_lat"], o["dropoff_lng"])
    stops = [(s["lat"], s["lng"]) for s in o["stops"]]
    if o["status"] == "accepted":
        to_pickup = geo.minutes_for_km(geo.estimated_road_km([here, pickup]))
        delivery = to_pickup + config.PICKUP_HANDLING_MIN + o["duration_min"] + len(o["tasks"]) * config.TASK_MIN
        return {"next": "pickup", "next_min": round(to_pickup), "delivery_min": round(delivery)}
    if o["status"] == "picked_up":
        remaining = geo.minutes_for_km(geo.estimated_road_km([here, *stops, dropoff])) + len(stops) * config.STOP_MIN
        return {"next": "dropoff", "next_min": round(remaining), "delivery_min": round(remaining)}
    return None


def order_view(o, hide_customer_contact=False):
    o = dict(o)
    o["stops"] = json.loads(o.pop("stops_json"))
    o["tasks"] = json.loads(o.pop("tasks_json"))
    o["route"] = json.loads(o.pop("route_json"))
    o["breakdown"] = json.loads(o.pop("breakdown_json"))

    customer = db.query_one("SELECT name, phone FROM users WHERE id = ?", (o["customer_id"],))
    o["customer"] = {"name": customer["name"], "phone": None if hide_customer_contact else customer["phone"]}
    if hide_customer_contact:
        o["mmg_number"] = None

    payment = payments.latest_payment(o["id"]) if o["payment_method"] == "mmg" else None
    o["payment"] = payment and {k: payment[k] for k in ("id", "status", "message", "provider_ref")}

    o["rider"] = o["rider_location"] = o["eta"] = None
    if o["rider_id"]:
        o["rider"] = db.query_one("SELECT id, name, phone, vehicle, plate FROM users WHERE id = ?", (o["rider_id"],))
        if o["status"] in ACTIVE_STATUSES:
            o["rider_location"] = db.query_one(
                "SELECT lat, lng, updated_at FROM rider_locations WHERE rider_id = ?", (o["rider_id"],)
            )
            o["eta"] = compute_eta(o, o["rider_location"])
    return o


def get_order(order_id):
    row = db.query_one("SELECT * FROM orders WHERE id = ?", (order_id,))
    if not row:
        raise HTTPException(404, "Order not found")
    return row


def online_rider_ids():
    return [r["id"] for r in db.query(
        "SELECT id FROM users WHERE role = 'rider' AND is_online = 1 AND approved = 1 AND active = 1"
    )]


def admin_ids():
    return [r["id"] for r in db.query("SELECT id FROM users WHERE role = 'admin' AND active = 1")]


def rider_active_job(rider_id):
    return db.query_one(
        "SELECT id FROM orders WHERE rider_id = ? AND status IN ('accepted', 'picked_up')", (rider_id,)
    )


async def push_order(order_id):
    """Send the latest state of an order to its customer, its rider and all admins."""
    row = get_order(order_id)
    view = order_view(row)
    targets = [row["customer_id"], *admin_ids()] + ([row["rider_id"]] if row["rider_id"] else [])
    await hub.send_many(targets, {"type": "order", "order": view})
    return view


async def announce_new(order_id):
    """Offer a new request to online riders (and show it to admins)."""
    row = get_order(order_id)
    await hub.send_many(online_rider_ids(), {"type": "order_new", "order": order_view(row, hide_customer_contact=True)})
    await hub.send_many(admin_ids(), {"type": "order", "order": order_view(row)})


async def withdraw(order_id, except_rider=None):
    """Remove a request from riders' open lists."""
    await hub.send_many([r for r in online_rider_ids() if r != except_rider], {"type": "order_gone", "id": order_id})
