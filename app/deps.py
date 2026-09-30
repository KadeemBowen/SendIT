"""Shared helpers: timestamps and the logged-in user."""
from datetime import datetime, timezone

from fastapi import Depends, Header, HTTPException

from . import db


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def bearer_token(authorization):
    return authorization[7:] if authorization and authorization.startswith("Bearer ") else None


def current_user(authorization: str | None = Header(default=None)):
    user = db.user_for_token(bearer_token(authorization))
    if not user:
        raise HTTPException(401, "Please log in")
    return user


def _role(role, message):
    def check(user=Depends(current_user)):
        if user["role"] != role:
            raise HTTPException(403, message)
        return user
    return check


customer_user = _role("customer", "Customers only")
rider_user = _role("rider", "Riders only")
admin_user = _role("admin", "Admins only")


def public_user(u):
    return {k: u[k] for k in ("id", "name", "email", "phone", "role", "vehicle", "plate")} | {
        "is_online": bool(u["is_online"]),
        "approved": bool(u["approved"]),
        "active": bool(u["active"]),
        "theme": u["theme"],
        "notifications": bool(u["notify_enabled"]),
    }
