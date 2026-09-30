"""In-app notifications: saved per user and pushed live to any open app."""
from . import db
from .deps import now
from .hub import hub
from .orders import admin_ids


async def notify(user_ids, title, body="", order_id=None):
    """Notify users who have notifications turned on."""
    ids = sorted({u for u in user_ids if u})
    if not ids:
        return
    marks = ",".join("?" * len(ids))
    wanted = db.query(f"SELECT id FROM users WHERE id IN ({marks}) AND notify_enabled = 1 AND active = 1", ids)
    for row in wanted:
        created = now()
        nid = db.insert(
            "INSERT INTO notifications (user_id, title, body, order_id, created_at) VALUES (?, ?, ?, ?, ?)",
            (row["id"], title, body, order_id, created),
        )
        await hub.send(row["id"], {"type": "notification", "notification": {
            "id": nid, "title": title, "body": body, "order_id": order_id, "created_at": created, "read_at": None,
        }})


async def notify_admins(title, body="", order_id=None):
    await notify(admin_ids(), title, body, order_id)


async def payment_changed(order):
    """Tell the customer (and rider) the outcome of an MMG payment. `order` is an order_view."""
    oid = order["id"]
    if order["payment_status"] == "paid":
        await notify([order["customer_id"]], "Payment received", f"Your MMG payment for order #{oid} went through.", oid)
        await notify([order["rider_id"]], "Order paid with MMG", f"Order #{oid} is paid - don't collect cash.", oid)
    elif order["payment_status"] == "failed":
        await notify([order["customer_id"]], "MMG payment failed",
                     f"Order #{oid}: try again or pay the rider in cash.", oid)
    elif order["payment_status"] == "refund_due":
        await notify_admins("Refund due", f"Order #{oid} was cancelled after an MMG payment.", oid)


def list_for(user_id):
    items = db.query(
        "SELECT id, title, body, order_id, created_at, read_at FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 30",
        (user_id,),
    )
    unread = db.query_one("SELECT COUNT(*) n FROM notifications WHERE user_id = ? AND read_at IS NULL", (user_id,))["n"]
    return {"items": items, "unread": unread}


def mark_all_read(user_id):
    db.execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL", (now(), user_id))
