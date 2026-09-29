import json
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import admin, config, db, geo, payments, pricing
from .auth import hash_password, new_token, verify_password
from .deps import bearer_token, current_user, customer_user, now, public_user, rider_user
from .hub import hub
from .orders import announce_new, get_order, order_view, push_order, rider_active_job, withdraw

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app):
    db.init()
    yield


app = FastAPI(title=config.APP_NAME, lifespan=lifespan)
app.include_router(admin.router)


# ---------- request models ----------

class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    email: str = Field(min_length=3, max_length=120)
    phone: str = Field(min_length=5, max_length=30)
    password: str = Field(min_length=6, max_length=200)
    role: Literal["customer", "rider"] = "customer"
    vehicle: str | None = Field(default=None, max_length=80)
    plate: str | None = Field(default=None, max_length=20)


class LoginIn(BaseModel):
    email: str
    password: str


class Place(BaseModel):
    address: str = Field(min_length=1, max_length=300)
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class TripIn(BaseModel):
    pickup: Place
    dropoff: Place
    stops: list[Place] = Field(default_factory=list, max_length=5)
    tasks: list[str] = Field(default_factory=list, max_length=10)


class OrderIn(TripIn):
    recipient_name: str | None = Field(default=None, max_length=80)
    recipient_phone: str | None = Field(default=None, max_length=30)
    notes: str | None = Field(default=None, max_length=500)
    payment_method: Literal["cash", "mmg"] = "cash"
    mmg_number: str | None = Field(default=None, max_length=30)


class PaymentMethodIn(BaseModel):
    method: Literal["cash", "mmg"]
    mmg_number: str | None = Field(default=None, max_length=30)


class SimulateIn(BaseModel):
    outcome: Literal["paid", "failed"]


class StatusIn(BaseModel):
    status: Literal["picked_up", "delivered"]


class OnlineIn(BaseModel):
    online: bool


class LocationIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


# ---------- helpers ----------

def start_session(user_id):
    token = new_token()
    db.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)", (token, user_id, now()))
    return token


def clean_tasks(tasks):
    return [t.strip()[:200] for t in tasks if t and t.strip()]


def clean_mmg_number(number):
    digits = "".join(c for c in (number or "") if c.isdigit())
    if len(digits) < 7:
        raise HTTPException(422, "Enter the MMG number that will pay")
    return digits


def trip_points(trip):
    return [(trip.pickup.lat, trip.pickup.lng), *[(s.lat, s.lng) for s in trip.stops], (trip.dropoff.lat, trip.dropoff.lng)]


async def price_trip(trip):
    tasks = clean_tasks(trip.tasks)
    route = await run_in_threadpool(geo.route, trip_points(trip))
    total, breakdown = pricing.quote(route["distance_km"], len(trip.stops), len(tasks))
    return {
        "distance_km": round(route["distance_km"], 2),
        "duration_min": round(route["duration_min"], 1),
        "route": route["geometry"],
        "route_source": route["source"],
        "price": total,
        "currency": config.CURRENCY,
        "breakdown": breakdown,
        "tasks": tasks,
    }


def own_order(order_id, user):
    row = get_order(order_id)
    if row["customer_id"] != user["id"]:
        raise HTTPException(404, "Order not found")
    return row


def require_approved(rider):
    if not rider["approved"]:
        raise HTTPException(403, "Your rider account is waiting for approval")


async def save_rider_location(rider, lat, lng):
    db.execute(
        """INSERT INTO rider_locations (rider_id, lat, lng, updated_at) VALUES (?, ?, ?, ?)
           ON CONFLICT(rider_id) DO UPDATE SET lat = excluded.lat, lng = excluded.lng, updated_at = excluded.updated_at""",
        (rider["id"], lat, lng, now()),
    )
    job = rider_active_job(rider["id"])
    if job:
        await push_order(job["id"])


# ---------- routes: config & auth ----------

@app.get("/api/config")
def get_config():
    return {
        "app_name": config.APP_NAME,
        "currency": config.CURRENCY,
        "map_center": config.MAP_CENTER,
        "pricing": pricing.current_rates(),
        "mmg_mode": payments.provider().mode,
        "demo": config.DEMO_MODE,
    }


@app.post("/api/auth/register")
def register(body: RegisterIn):
    email = body.email.strip().lower()
    if db.query_one("SELECT id FROM users WHERE email = ?", (email,)):
        raise HTTPException(409, "An account with this email already exists")
    if body.role == "rider" and not (body.vehicle and body.vehicle.strip()):
        raise HTTPException(422, "Riders must describe their vehicle")
    user_id, _ = db.execute(
        """INSERT INTO users (name, email, phone, role, password_hash, vehicle, plate, approved, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (body.name.strip(), email, body.phone.strip(), body.role, hash_password(body.password),
         (body.vehicle or "").strip() or None, (body.plate or "").strip().upper() or None,
         int(body.role != "rider"), now()),
    )
    user = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    return {"token": start_session(user_id), "user": public_user(user)}


@app.post("/api/auth/login")
def login(body: LoginIn):
    user = db.query_one("SELECT * FROM users WHERE email = ?", (body.email.strip().lower(),))
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Wrong email or password")
    if not user["active"]:
        raise HTTPException(403, "This account has been suspended. Please contact us.")
    return {"token": start_session(user["id"]), "user": public_user(user)}


@app.post("/api/auth/logout")
def logout(authorization: str | None = Header(default=None), user=Depends(current_user)):
    db.execute("DELETE FROM sessions WHERE token = ?", (bearer_token(authorization),))
    return {"ok": True}


@app.get("/api/me")
def me(user=Depends(current_user)):
    return public_user(user)


# ---------- routes: places & pricing ----------

@app.get("/api/geocode")
def geocode(q: str = Query(min_length=3, max_length=200), user=Depends(current_user)):
    try:
        return geo.search(q.strip())
    except Exception:
        raise HTTPException(502, "Address search is unavailable right now - pick the spot on the map instead")


@app.get("/api/reverse")
def reverse_geocode(lat: float, lng: float, user=Depends(current_user)):
    try:
        return {"address": geo.reverse(round(lat, 5), round(lng, 5))}
    except Exception:
        return {"address": None}


@app.post("/api/quote")
async def get_quote(body: TripIn, user=Depends(current_user)):
    return await price_trip(body)


# ---------- routes: orders ----------

@app.post("/api/orders")
async def create_order(body: OrderIn, user=Depends(customer_user)):
    mmg_number = clean_mmg_number(body.mmg_number) if body.payment_method == "mmg" else None
    q = await price_trip(body)
    order_id, _ = db.execute(
        """INSERT INTO orders (customer_id, status, pickup_address, pickup_lat, pickup_lng,
               dropoff_address, dropoff_lat, dropoff_lng, stops_json, tasks_json,
               recipient_name, recipient_phone, notes, distance_km, duration_min,
               route_json, route_source, price, currency, breakdown_json,
               payment_method, mmg_number, created_at)
           VALUES (?, 'requested', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user["id"], body.pickup.address, body.pickup.lat, body.pickup.lng,
         body.dropoff.address, body.dropoff.lat, body.dropoff.lng,
         json.dumps([s.model_dump() for s in body.stops]), json.dumps(q["tasks"]),
         (body.recipient_name or "").strip() or None, (body.recipient_phone or "").strip() or None,
         (body.notes or "").strip() or None, q["distance_km"], q["duration_min"],
         json.dumps(q["route"]), q["route_source"], q["price"], q["currency"], json.dumps(q["breakdown"]),
         body.payment_method, mmg_number, now()),
    )
    if body.payment_method == "mmg":
        await run_in_threadpool(payments.start_mmg_payment, get_order(order_id))
    await announce_new(order_id)
    return order_view(get_order(order_id))


@app.get("/api/orders")
def my_orders(user=Depends(current_user)):
    column = "rider_id" if user["role"] == "rider" else "customer_id"
    rows = db.query(f"SELECT * FROM orders WHERE {column} = ? ORDER BY id DESC LIMIT 50", (user["id"],))
    return [order_view(r) for r in rows]


@app.get("/api/orders/open")
def open_orders(user=Depends(rider_user)):
    rows = db.query("SELECT * FROM orders WHERE status = 'requested' ORDER BY id DESC LIMIT 30")
    return [order_view(r, hide_customer_contact=True) for r in rows]


@app.get("/api/orders/{order_id}")
def order_detail(order_id: int, user=Depends(current_user)):
    row = get_order(order_id)
    if user["id"] in (row["customer_id"], row["rider_id"]) or user["role"] == "admin":
        return order_view(row)
    if user["role"] == "rider" and row["status"] == "requested":
        return order_view(row, hide_customer_contact=True)
    raise HTTPException(404, "Order not found")


@app.post("/api/orders/{order_id}/accept")
async def accept_order(order_id: int, user=Depends(rider_user)):
    require_approved(user)
    if rider_active_job(user["id"]):
        raise HTTPException(409, "Finish your current job first")
    _, changed = db.execute(
        "UPDATE orders SET rider_id = ?, status = 'accepted', accepted_at = ? WHERE id = ? AND status = 'requested'",
        (user["id"], now(), order_id),
    )
    if not changed:
        raise HTTPException(409, "This request was already taken or cancelled")
    await withdraw(order_id, except_rider=user["id"])
    return await push_order(order_id)


@app.post("/api/orders/{order_id}/status")
async def update_status(order_id: int, body: StatusIn, user=Depends(rider_user)):
    row = get_order(order_id)
    if row["rider_id"] != user["id"]:
        raise HTTPException(404, "Order not found")
    allowed = {"picked_up": "accepted", "delivered": "picked_up"}
    if row["status"] != allowed[body.status]:
        raise HTTPException(409, f"Can't mark as {body.status.replace('_', ' ')} from {row['status'].replace('_', ' ')}")
    db.execute(f"UPDATE orders SET status = ?, {body.status}_at = ? WHERE id = ?", (body.status, now(), order_id))
    if body.status == "delivered" and row["payment_method"] == "cash":
        payments.record_cash(row)
    return await push_order(order_id)


@app.post("/api/orders/{order_id}/cancel")
async def cancel_order(order_id: int, user=Depends(customer_user)):
    own_order(order_id, user)
    _, changed = db.execute(
        """UPDATE orders SET status = 'cancelled', cancelled_at = ?, cancelled_by = 'customer'
           WHERE id = ? AND status IN ('requested', 'accepted')""",
        (now(), order_id),
    )
    if not changed:
        raise HTTPException(409, "This delivery can no longer be cancelled")
    payments.on_cancel(get_order(order_id))
    await withdraw(order_id)
    return await push_order(order_id)


# ---------- routes: payments ----------

@app.post("/api/orders/{order_id}/payment")
async def change_payment(order_id: int, body: PaymentMethodIn, user=Depends(customer_user)):
    """Retry an MMG payment, or switch between MMG and cash, before the order is paid."""
    row = own_order(order_id, user)
    if row["status"] in ("delivered", "cancelled") or row["payment_status"] == "paid":
        raise HTTPException(409, "Payment for this delivery can't be changed")
    db.execute_all([
        ("UPDATE payments SET status = 'void', updated_at = ? WHERE order_id = ? AND status = 'pending'", (now(), order_id)),
        ("UPDATE orders SET payment_method = ?, payment_status = 'unpaid', mmg_number = ? WHERE id = ?",
         (body.method, clean_mmg_number(body.mmg_number) if body.method == "mmg" else None, order_id)),
    ])
    if body.method == "mmg":
        await run_in_threadpool(payments.start_mmg_payment, get_order(order_id))
    return await push_order(order_id)


@app.post("/api/payments/{payment_id}/simulate")
async def simulate_payment(payment_id: int, body: SimulateIn, user=Depends(current_user)):
    """Mock MMG only: pretend the customer approved or declined the payment."""
    if payments.provider().mode != "mock":
        raise HTTPException(404, "Not found")
    payment = db.query_one("SELECT * FROM payments WHERE id = ?", (payment_id,))
    if not payment:
        raise HTTPException(404, "Payment not found")
    if user["role"] != "admin":
        own_order(payment["order_id"], user)
    order_id = payments.apply_result(
        payment_id, body.outcome, {"simulated": True},
        "Paid with MMG (simulated)" if body.outcome == "paid" else "Declined in MMG (simulated)",
    )
    if not order_id:
        raise HTTPException(409, "This payment is no longer pending")
    return await push_order(order_id)


@app.post("/api/payments/mmg/callback")
async def mmg_callback(request: Request):
    """MMG calls this when a customer pays or declines. Verified inside LiveMMG.parse_callback."""
    try:
        ref, status, raw = payments.provider().parse_callback(dict(request.headers), await request.body())
    except payments.PaymentError as err:
        raise HTTPException(400, str(err))
    payment = payments.payment_by_ref("mmg", ref)
    if not payment:
        raise HTTPException(404, "Unknown payment")
    order_id = payments.apply_result(payment["id"], status, raw)
    if order_id:
        await push_order(order_id)
    return {"ok": True}


# ---------- routes: rider ----------

@app.post("/api/rider/online")
def set_online(body: OnlineIn, user=Depends(rider_user)):
    if body.online:
        require_approved(user)
    db.execute("UPDATE users SET is_online = ? WHERE id = ?", (int(body.online), user["id"]))
    return {"online": body.online}


@app.post("/api/rider/location")
async def post_location(body: LocationIn, user=Depends(rider_user)):
    await save_rider_location(user, body.lat, body.lng)
    return {"ok": True}


# ---------- live updates ----------

@app.websocket("/ws")
async def websocket(ws: WebSocket, token: str = Query(default="")):
    user = db.user_for_token(token)
    if not user:
        await ws.close(code=4401)
        return
    await ws.accept()
    hub.add(user["id"], ws)
    try:
        while True:
            try:
                msg = json.loads(await ws.receive_text())
            except ValueError:
                continue
            if msg.get("type") == "location" and user["role"] == "rider":
                try:
                    loc = LocationIn(lat=msg.get("lat"), lng=msg.get("lng"))
                except ValueError:
                    continue
                await save_rider_location(user, loc.lat, loc.lng)
            elif msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        hub.remove(user["id"], ws)


# ---------- web app ----------

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")
