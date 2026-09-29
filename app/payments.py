"""Payments.

Cash: recorded as paid when the rider marks the job delivered.
MMG:  a payment request goes to the customer's MMG wallet when they book; MMG then tells us
      (via callback) whether it was paid. Until the real API is wired into LiveMMG, the app runs
      MockMMG, where the customer or an admin taps "simulate" to approve or fail the payment.

Order payment_status: unpaid -> pending -> paid | failed; after cancelling: refund_due -> refunded, or void.
"""
import json
import secrets
from datetime import datetime, timezone

from . import config, db


class PaymentError(Exception):
    pass


class MockMMG:
    mode = "mock"
    configured = True

    def request_payment(self, payment, order):
        """Ask the customer's wallet to pay. Returns (provider_ref, message for the customer)."""
        ref = f"MOCK-{payment['id']}-{secrets.token_hex(3).upper()}"
        return ref, f"Payment request sent to MMG {order['mmg_number']}. Approve it in MMG to pay."

    def parse_callback(self, headers, body):
        raise PaymentError("Mock mode has no callbacks - use the simulate button instead")


class LiveMMG:
    """The real MMG merchant API. TODO: fill in from MMG's merchant/API documentation.

    request_payment(payment, order):
        Call MMG to charge `payment["amount"]` (GYD) to wallet `order["mmg_number"]`, using our
        reference `payment["id"]`. Return (MMG's transaction id, message to show the customer).
        If MMG uses a hosted checkout page rather than a push-to-phone request, put the URL in
        the message for now (or extend this to return a URL the app can open).
        Raise PaymentError with a customer-friendly message if MMG rejects the request.

    parse_callback(headers, body):
        MMG calls POST /api/payments/mmg/callback when the customer pays or declines.
        FIRST verify the request really came from MMG (signature / shared secret, per their docs,
        using MMG_CALLBACK_SECRET). Then return (MMG transaction id, "paid" | "failed", payload dict).

    Credentials come from environment variables: MMG_API_BASE, MMG_MERCHANT_ID, MMG_API_KEY,
    MMG_CALLBACK_SECRET. Switch on with COURIER_MMG_MODE=live.
    """
    mode = "live"

    def __init__(self):
        self.configured = all((config.MMG_API_BASE, config.MMG_MERCHANT_ID, config.MMG_API_KEY))

    def request_payment(self, payment, order):
        raise PaymentError("MMG payments aren't connected yet - please choose cash")

    def parse_callback(self, headers, body):
        raise PaymentError("MMG callback handling isn't implemented yet")


def provider():
    return LiveMMG() if config.MMG_MODE == "live" else MockMMG()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _new_payment(order, provider_name, status, message=None):
    payment_id, _ = db.execute(
        """INSERT INTO payments (order_id, provider, amount, currency, status, message, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (order["id"], provider_name, order["price"], order["currency"], status, message, _now(), _now()),
    )
    return payment_id


def start_mmg_payment(order):
    """Send an MMG payment request for an order and record it as pending (or failed)."""
    payment_id = _new_payment(order, "mmg", "pending")
    payment = db.query_one("SELECT * FROM payments WHERE id = ?", (payment_id,))
    try:
        ref, message = provider().request_payment(payment, order)
    except PaymentError as err:
        db.execute_all([
            ("UPDATE payments SET status = 'failed', message = ?, updated_at = ? WHERE id = ?", (str(err), _now(), payment_id)),
            ("UPDATE orders SET payment_status = 'failed' WHERE id = ?", (order["id"],)),
        ])
        return
    db.execute_all([
        ("UPDATE payments SET provider_ref = ?, message = ?, updated_at = ? WHERE id = ?", (ref, message, _now(), payment_id)),
        ("UPDATE orders SET payment_status = 'pending' WHERE id = ?", (order["id"],)),
    ])


def apply_result(payment_id, status, raw=None, message=None):
    """Record MMG's answer for a pending payment. Returns the order id, or None if nothing changed."""
    payment = db.query_one("SELECT * FROM payments WHERE id = ?", (payment_id,))
    if not payment or payment["status"] != "pending" or status not in ("paid", "failed"):
        return None
    order = db.query_one("SELECT status FROM orders WHERE id = ?", (payment["order_id"],))
    order_status = status
    if order["status"] == "cancelled":
        # Paid after the order was cancelled: the money has to go back.
        order_status = "refund_due" if status == "paid" else "void"
    db.execute_all([
        ("UPDATE payments SET status = ?, message = COALESCE(?, message), raw_json = ?, updated_at = ? WHERE id = ?",
         (status, message, json.dumps(raw) if raw else None, _now(), payment_id)),
        ("UPDATE orders SET payment_status = ? WHERE id = ?", (order_status, payment["order_id"])),
    ])
    return payment["order_id"]


def payment_by_ref(provider_name, ref):
    return db.query_one("SELECT * FROM payments WHERE provider = ? AND provider_ref = ?", (provider_name, ref))


def latest_payment(order_id):
    return db.query_one("SELECT * FROM payments WHERE order_id = ? ORDER BY id DESC LIMIT 1", (order_id,))


def record_cash(order):
    _new_payment(order, "cash", "paid", "Collected by rider")
    db.execute("UPDATE orders SET payment_status = 'paid' WHERE id = ?", (order["id"],))


def mark_paid_manually(order, note):
    _new_payment(order, "manual", "paid", note)
    db.execute("UPDATE orders SET payment_status = 'paid' WHERE id = ?", (order["id"],))


def on_cancel(order):
    """Settle payment state after an order is cancelled."""
    if order["payment_status"] == "paid":
        db.execute("UPDATE orders SET payment_status = 'refund_due' WHERE id = ?", (order["id"],))
        return
    db.execute_all([
        ("UPDATE payments SET status = 'void', updated_at = ? WHERE order_id = ? AND status = 'pending'", (_now(), order["id"])),
        ("UPDATE orders SET payment_status = 'void' WHERE id = ?", (order["id"],)),
    ])


def mark_refunded(order):
    db.execute_all([
        ("UPDATE payments SET status = 'refunded', updated_at = ? WHERE order_id = ? AND status = 'paid'", (_now(), order["id"])),
        ("UPDATE orders SET payment_status = 'refunded' WHERE id = ?", (order["id"],)),
    ])
