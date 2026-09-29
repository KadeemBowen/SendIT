"""Order serialization, ETAs, and live-update fan-out."""
import json

from fastapi import HTTPException

from . import config, db, geo
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


# An order plus its customer, rider, rider location and latest payment, in one query.
ORDER_SELECT = """
    SELECT o.*,
           c.name AS c_name, c.phone AS c_phone,
           r.name AS r_name, r.phone AS r_phone, r.vehicle AS r_vehicle, r.plate AS r_plate,
           l.lat AS l_lat, l.lng AS l_lng, l.updated_at AS l_updated_at,
           p.id AS p_id, p.status AS p_status, p.message AS p_message, p.provider_ref AS p_provider_ref
    FROM orders o
    JOIN users c ON c.id = o.customer_id
    LEFT JOIN users r ON r.id = o.rider_id
    LEFT JOIN rider_locations l ON l.rider_id = o.rider_id
    LEFT JOIN payments p ON p.id = (SELECT MAX(id) FROM payments WHERE order_id = o.id)
"""
JOINED_COLUMNS = (
    "c_name", "c_phone", "r_name", "r_phone", "r_vehicle", "r_plate",
    "l_lat", "l_lng", "l_updated_at", "p_id", "p_status", "p_message", "p_provider_ref",
)


def fetch_orders(where="1 = 1", args=(), tail="ORDER BY o.id DESC"):
    """Orders (with ORDER_SELECT's joined columns) matching a WHERE clause on aliases o/c/r."""
    return db.query(f"{ORDER_SELECT} WHERE {where} {tail}", args)


def get_order(order_id):
    rows = fetch_orders("o.id = ?", (order_id,), "")
    if not rows:
        raise HTTPException(404, "Order not found")
    return rows[0]


def order_view(row, hide_customer_contact=False):
    o = {k: v for k, v in row.items() if k not in JOINED_COLUMNS}
    o["stops"] = json.loads(o.pop("stops_json"))
    o["tasks"] = json.loads(o.pop("tasks_json"))
    o["route"] = json.loads(o.pop("route_json"))
    o["breakdown"] = json.loads(o.pop("breakdown_json"))

    o["customer"] = {"name": row["c_name"], "phone": None if hide_customer_contact else row["c_phone"]}
    if hide_customer_contact:
        o["mmg_number"] = None

    o["payment"] = None
    if o["payment_method"] == "mmg" and row["p_id"]:
        o["payment"] = {"id": row["p_id"], "status": row["p_status"], "message": row["p_message"],
                        "provider_ref": row["p_provider_ref"]}

    o["rider"] = o["rider_location"] = o["eta"] = None
    if o["rider_id"]:
        o["rider"] = {"id": o["rider_id"], "name": row["r_name"], "phone": row["r_phone"],
                      "vehicle": row["r_vehicle"], "plate": row["r_plate"]}
        if o["status"] in ACTIVE_STATUSES and row["l_lat"] is not None:
            o["rider_location"] = {"lat": row["l_lat"], "lng": row["l_lng"], "updated_at": row["l_updated_at"]}
            o["eta"] = compute_eta(o, o["rider_location"])
    return o


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
