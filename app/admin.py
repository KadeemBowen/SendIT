"""Admin dashboard API: overview, orders, riders, customers, pricing, payments."""
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from . import config, db, payments, pricing
from .deps import admin_user, now, public_user
from .hub import hub
from .orders import ACTIVE_STATUSES, get_order, order_view, push_order, rider_active_job, withdraw

router = APIRouter(prefix="/api/admin", dependencies=[Depends(admin_user)])


class AssignIn(BaseModel):
    rider_id: int


class CancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=200)


class UserUpdateIn(BaseModel):
    approved: bool | None = None
    active: bool | None = None


class RatesIn(BaseModel):
    base_fee: float = Field(ge=0, le=1_000_000)
    per_km: float = Field(ge=0, le=1_000_000)
    per_extra_stop: float = Field(ge=0, le=1_000_000)
    per_task: float = Field(ge=0, le=1_000_000)
    minimum: float = Field(ge=0, le=1_000_000)
    round_to: float = Field(ge=1, le=100_000)


class PreviewIn(BaseModel):
    rates: RatesIn
    distance_km: float = Field(ge=0, le=1000)
    stops: int = Field(ge=0, le=20)
    tasks: int = Field(ge=0, le=20)


def start_of_today_utc():
    local = timezone(timedelta(hours=config.UTC_OFFSET_HOURS))
    midnight = datetime.now(local).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.astimezone(timezone.utc).isoformat(timespec="seconds")


def count(sql, args=()):
    return db.query_one(sql, args)["n"]


# ---------- overview ----------

@router.get("/overview")
def overview():
    today = start_of_today_utc()
    stats = {
        "orders_today": count("SELECT COUNT(*) n FROM orders WHERE created_at >= ?", (today,)),
        "delivered_today": count("SELECT COUNT(*) n FROM orders WHERE delivered_at >= ?", (today,)),
        "cancelled_today": count("SELECT COUNT(*) n FROM orders WHERE cancelled_at >= ?", (today,)),
        "revenue_today": db.query_one(
            "SELECT COALESCE(SUM(price), 0) n FROM orders WHERE status = 'delivered' AND delivered_at >= ?", (today,)
        )["n"],
        "waiting": count("SELECT COUNT(*) n FROM orders WHERE status = 'requested'"),
        "in_progress": count("SELECT COUNT(*) n FROM orders WHERE status IN ('accepted', 'picked_up')"),
        "riders_online": count("SELECT COUNT(*) n FROM users WHERE role = 'rider' AND is_online = 1 AND active = 1"),
        "riders_pending": count("SELECT COUNT(*) n FROM users WHERE role = 'rider' AND approved = 0 AND active = 1"),
        "refunds_due": count("SELECT COUNT(*) n FROM orders WHERE payment_status = 'refund_due'"),
        "mmg_pending": count("SELECT COUNT(*) n FROM orders WHERE payment_status = 'pending' AND status != 'cancelled'"),
    }
    riders = db.query(
        """SELECT u.id, u.name, u.phone, u.vehicle, u.plate, l.lat, l.lng, l.updated_at,
                  (SELECT o.id FROM orders o WHERE o.rider_id = u.id AND o.status IN ('accepted', 'picked_up')) AS job_id
           FROM users u LEFT JOIN rider_locations l ON l.rider_id = u.id
           WHERE u.role = 'rider' AND u.is_online = 1 AND u.active = 1 ORDER BY u.name"""
    )
    active = db.query(
        f"SELECT * FROM orders WHERE status IN ({','.join('?' * len(ACTIVE_STATUSES))}) ORDER BY id DESC",
        ACTIVE_STATUSES,
    )
    return {"stats": stats, "riders": riders, "active_orders": [order_view(o) for o in active], "currency": config.CURRENCY}


# ---------- orders ----------

ORDER_FILTERS = {
    "all": ("1 = 1", ()),
    "active": ("o.status IN ('requested', 'accepted', 'picked_up')", ()),
    "requested": ("o.status = 'requested'", ()),
    "delivered": ("o.status = 'delivered'", ()),
    "cancelled": ("o.status = 'cancelled'", ()),
    "payment_issues": ("o.payment_status IN ('failed', 'refund_due') OR (o.payment_status = 'pending' AND o.status != 'cancelled')", ()),
}


@router.get("/orders")
def list_orders(status: str = "all", q: str = "", limit: int = 50, offset: int = 0):
    where, args = ORDER_FILTERS.get(status, ORDER_FILTERS["all"])
    where, args = f"({where})", list(args)
    q = q.strip()
    if q:
        if q.lstrip("#").isdigit():
            where += " AND o.id = ?"
            args.append(int(q.lstrip("#")))
        else:
            like = f"%{q}%"
            where += """ AND (o.pickup_address LIKE ? OR o.dropoff_address LIKE ? OR c.name LIKE ?
                          OR c.phone LIKE ? OR r.name LIKE ?)"""
            args += [like] * 5
    base = f"FROM orders o JOIN users c ON c.id = o.customer_id LEFT JOIN users r ON r.id = o.rider_id WHERE {where}"
    total = count(f"SELECT COUNT(*) n {base}", args)
    rows = db.query(f"SELECT o.* {base} ORDER BY o.id DESC LIMIT ? OFFSET ?", [*args, min(limit, 200), max(offset, 0)])
    return {"total": total, "orders": [order_view(r) for r in rows]}


@router.post("/orders/{order_id}/cancel")
async def cancel(order_id: int, body: CancelIn):
    _, changed = db.execute(
        """UPDATE orders SET status = 'cancelled', cancelled_at = ?, cancelled_by = ?
           WHERE id = ? AND status IN ('requested', 'accepted', 'picked_up')""",
        (now(), f"admin: {body.reason.strip()}" if body.reason and body.reason.strip() else "admin", order_id),
    )
    if not changed:
        raise HTTPException(409, "Only active orders can be cancelled")
    payments.on_cancel(get_order(order_id))
    await withdraw(order_id)
    return await push_order(order_id)


@router.post("/orders/{order_id}/assign")
async def assign(order_id: int, body: AssignIn):
    rider = db.query_one("SELECT * FROM users WHERE id = ? AND role = 'rider'", (body.rider_id,))
    if not rider or not rider["active"] or not rider["approved"]:
        raise HTTPException(422, "Pick an approved, active rider")
    if rider_active_job(rider["id"]):
        raise HTTPException(409, f"{rider['name']} is already on a job")
    _, changed = db.execute(
        "UPDATE orders SET rider_id = ?, status = 'accepted', accepted_at = ? WHERE id = ? AND status = 'requested'",
        (rider["id"], now(), order_id),
    )
    if not changed:
        raise HTTPException(409, "Only orders still waiting for a rider can be assigned")
    await withdraw(order_id)
    return await push_order(order_id)


@router.post("/orders/{order_id}/mark-paid")
async def mark_paid(order_id: int):
    row = get_order(order_id)
    if row["payment_status"] in ("paid", "refund_due", "refunded"):
        raise HTTPException(409, "This order is already paid")
    if row["status"] == "cancelled":
        raise HTTPException(409, "This order was cancelled")
    payments.mark_paid_manually(row, "Marked paid by admin")
    return await push_order(order_id)


@router.post("/orders/{order_id}/mark-refunded")
async def mark_refunded(order_id: int):
    row = get_order(order_id)
    if row["payment_status"] != "refund_due":
        raise HTTPException(409, "No refund is due on this order")
    payments.mark_refunded(row)
    return await push_order(order_id)


@router.get("/riders/available")
def available_riders():
    return db.query(
        """SELECT id, name, is_online FROM users u
           WHERE role = 'rider' AND approved = 1 AND active = 1
             AND NOT EXISTS (SELECT 1 FROM orders o WHERE o.rider_id = u.id AND o.status IN ('accepted', 'picked_up'))
           ORDER BY is_online DESC, name"""
    )


# ---------- people ----------

@router.get("/users")
def list_users(role: Literal["rider", "customer"], q: str = ""):
    like = f"%{q.strip()}%"
    if role == "rider":
        sql = """SELECT u.*, l.updated_at AS last_seen,
                        (SELECT COUNT(*) FROM orders o WHERE o.rider_id = u.id AND o.status = 'delivered') AS jobs,
                        (SELECT COALESCE(SUM(price), 0) FROM orders o WHERE o.rider_id = u.id AND o.status = 'delivered') AS earned
                 FROM users u LEFT JOIN rider_locations l ON l.rider_id = u.id"""
    else:
        sql = """SELECT u.*,
                        (SELECT COUNT(*) FROM orders o WHERE o.customer_id = u.id) AS jobs,
                        (SELECT COALESCE(SUM(price), 0) FROM orders o WHERE o.customer_id = u.id AND o.status = 'delivered') AS earned
                 FROM users u"""
    rows = db.query(
        sql + """ WHERE u.role = ? AND (u.name LIKE ? OR u.email LIKE ? OR u.phone LIKE ? OR COALESCE(u.plate, '') LIKE ?)
                 ORDER BY u.approved ASC, u.id DESC LIMIT 200""",
        (role, like, like, like, like),
    )
    return [public_user(r) | {"jobs": r["jobs"], "total": r["earned"], "created_at": r["created_at"],
                              "last_seen": r.get("last_seen")} for r in rows]


@router.patch("/users/{user_id}")
async def update_user(user_id: int, body: UserUpdateIn):
    user = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if not user or user["role"] == "admin":
        raise HTTPException(404, "User not found")
    if body.approved is not None:
        if user["role"] != "rider":
            raise HTTPException(422, "Only riders need approval")
        db.execute("UPDATE users SET approved = ?, is_online = is_online AND ? WHERE id = ?",
                   (int(body.approved), int(body.approved), user_id))
    if body.active is not None:
        if not body.active and user["role"] == "rider" and rider_active_job(user_id):
            raise HTTPException(409, "This rider is on a job - reassign or finish it first")
        db.execute("UPDATE users SET active = ?, is_online = is_online AND ? WHERE id = ?",
                   (int(body.active), int(body.active), user_id))
        if not body.active:
            db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
            await hub.disconnect(user_id)
    user = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    if body.approved is not None:
        await hub.send(user_id, {"type": "account", "user": public_user(user)})
    return public_user(user)


# ---------- pricing ----------

@router.get("/pricing")
def get_pricing():
    return {"rates": pricing.current_rates(), "defaults": config.PRICING, "currency": config.CURRENCY}


@router.put("/pricing")
def put_pricing(body: RatesIn):
    pricing.save_rates(body.model_dump())
    return {"rates": pricing.current_rates()}


@router.post("/pricing/preview")
def preview_pricing(body: PreviewIn):
    total, breakdown = pricing.quote(body.distance_km, body.stops, body.tasks, rates=body.rates.model_dump())
    return {"price": total, "breakdown": breakdown}


# ---------- payments ----------

@router.get("/payments")
def list_payments():
    rows = db.query(
        """SELECT p.*, o.status AS order_status, o.payment_status AS order_payment_status, o.mmg_number, c.name AS customer
           FROM payments p JOIN orders o ON o.id = p.order_id JOIN users c ON c.id = o.customer_id
           ORDER BY p.id DESC LIMIT 200"""
    )
    provider = payments.provider()
    return {
        "payments": [{k: v for k, v in r.items() if k != "raw_json"} for r in rows],
        "mmg": {"mode": provider.mode, "configured": provider.configured},
        "currency": config.CURRENCY,
    }
