import json
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db, geo, pricing
from .auth import hash_password, new_token, verify_password
from .hub import hub

STATIC = Path(__file__).parent / "static"
ACTIVE_STATUSES = ("requested", "accepted", "picked_up")


@asynccontextmanager
async def lifespan(_app):
    db.init()
    yield


app = FastAPI(title=config.APP_NAME, lifespan=lifespan)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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


class StatusIn(BaseModel):
    status: Literal["picked_up", "delivered"]


class OnlineIn(BaseModel):
    online: bool


class LocationIn(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


# ---------- auth helpers ----------

def current_user(authorization: str | None = Header(default=None)):
    token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
    user = db.user_for_token(token)
    if not user:
        raise HTTPException(401, "Please log in")
    return user


def customer_user(user=Depends(current_user)):
    if user["role"] != "customer":
        raise HTTPException(403, "Customers only")
    return user


def rider_user(user=Depends(current_user)):
    if user["role"] != "rider":
        raise HTTPException(403, "Riders only")
    return user


def public_user(u):
    return {k: u[k] for k in ("id", "name", "email", "phone", "role", "vehicle", "plate")} | {
        "is_online": bool(u["is_online"])
    }


def start_session(user_id):
    token = new_token()
    db.execute("INSERT INTO sessions (token, user_id, created_at) VALUES (?, ?, ?)", (token, user_id, now()))
    return token


# ---------- order helpers ----------

def clean_tasks(tasks):
    return [t.strip()[:200] for t in tasks if t and t.strip()]


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
    return [r["id"] for r in db.query("SELECT id FROM users WHERE role = 'rider' AND is_online = 1")]


async def push_order(order_id):
    """Send the latest state of an order to its customer and rider."""
    row = get_order(order_id)
    view = order_view(row)
    targets = [row["customer_id"]] + ([row["rider_id"]] if row["rider_id"] else [])
    await hub.send_many(targets, {"type": "order", "order": view})
    return view


async def save_rider_location(rider, lat, lng):
    db.execute(
        """INSERT INTO rider_locations (rider_id, lat, lng, updated_at) VALUES (?, ?, ?, ?)
           ON CONFLICT(rider_id) DO UPDATE SET lat = excluded.lat, lng = excluded.lng, updated_at = excluded.updated_at""",
        (rider["id"], lat, lng, now()),
    )
    job = db.query_one(
        "SELECT id FROM orders WHERE rider_id = ? AND status IN ('accepted', 'picked_up')", (rider["id"],)
    )
    if job:
        await push_order(job["id"])


# ---------- routes: config & auth ----------

@app.get("/api/config")
def get_config():
    return {
        "app_name": config.APP_NAME,
        "currency": config.CURRENCY,
        "map_center": config.MAP_CENTER,
        "pricing": config.PRICING,
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
        """INSERT INTO users (name, email, phone, role, password_hash, vehicle, plate, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (body.name.strip(), email, body.phone.strip(), body.role, hash_password(body.password),
         (body.vehicle or "").strip() or None, (body.plate or "").strip().upper() or None, now()),
    )
    user = db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))
    return {"token": start_session(user_id), "user": public_user(user)}


@app.post("/api/auth/login")
def login(body: LoginIn):
    user = db.query_one("SELECT * FROM users WHERE email = ?", (body.email.strip().lower(),))
    if not user or not verify_password(body.password, user["password_hash"]):
        raise HTTPException(401, "Wrong email or password")
    return {"token": start_session(user["id"]), "user": public_user(user)}


@app.post("/api/auth/logout")
def logout(authorization: str | None = Header(default=None), user=Depends(current_user)):
    db.execute("DELETE FROM sessions WHERE token = ?", (authorization[7:],))
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
    q = await price_trip(body)
    order_id, _ = db.execute(
        """INSERT INTO orders (customer_id, status, pickup_address, pickup_lat, pickup_lng,
               dropoff_address, dropoff_lat, dropoff_lng, stops_json, tasks_json,
               recipient_name, recipient_phone, notes, distance_km, duration_min,
               route_json, route_source, price, currency, breakdown_json, created_at)
           VALUES (?, 'requested', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user["id"], body.pickup.address, body.pickup.lat, body.pickup.lng,
         body.dropoff.address, body.dropoff.lat, body.dropoff.lng,
         json.dumps([s.model_dump() for s in body.stops]), json.dumps(q["tasks"]),
         (body.recipient_name or "").strip() or None, (body.recipient_phone or "").strip() or None,
         (body.notes or "").strip() or None, q["distance_km"], q["duration_min"],
         json.dumps(q["route"]), q["route_source"], q["price"], q["currency"], json.dumps(q["breakdown"]), now()),
    )
    row = get_order(order_id)
    await hub.send_many(online_rider_ids(), {"type": "order_new", "order": order_view(row, hide_customer_contact=True)})
    return order_view(row)


@app.get("/api/orders")
def my_orders(user=Depends(current_user)):
    column = "customer_id" if user["role"] == "customer" else "rider_id"
    rows = db.query(f"SELECT * FROM orders WHERE {column} = ? ORDER BY id DESC LIMIT 50", (user["id"],))
    return [order_view(r) for r in rows]


@app.get("/api/orders/open")
def open_orders(user=Depends(rider_user)):
    rows = db.query("SELECT * FROM orders WHERE status = 'requested' ORDER BY id DESC LIMIT 30")
    return [order_view(r, hide_customer_contact=True) for r in rows]


@app.get("/api/orders/{order_id}")
def order_detail(order_id: int, user=Depends(current_user)):
    row = get_order(order_id)
    if user["id"] in (row["customer_id"], row["rider_id"]):
        return order_view(row)
    if user["role"] == "rider" and row["status"] == "requested":
        return order_view(row, hide_customer_contact=True)
    raise HTTPException(404, "Order not found")


@app.post("/api/orders/{order_id}/accept")
async def accept_order(order_id: int, user=Depends(rider_user)):
    if db.query_one("SELECT id FROM orders WHERE rider_id = ? AND status IN ('accepted', 'picked_up')", (user["id"],)):
        raise HTTPException(409, "Finish your current job first")
    _, changed = db.execute(
        "UPDATE orders SET rider_id = ?, status = 'accepted', accepted_at = ? WHERE id = ? AND status = 'requested'",
        (user["id"], now(), order_id),
    )
    if not changed:
        raise HTTPException(409, "This request was already taken or cancelled")
    await hub.send_many([r for r in online_rider_ids() if r != user["id"]], {"type": "order_gone", "id": order_id})
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
    return await push_order(order_id)


@app.post("/api/orders/{order_id}/cancel")
async def cancel_order(order_id: int, user=Depends(customer_user)):
    row = get_order(order_id)
    if row["customer_id"] != user["id"]:
        raise HTTPException(404, "Order not found")
    _, changed = db.execute(
        "UPDATE orders SET status = 'cancelled', cancelled_at = ? WHERE id = ? AND status IN ('requested', 'accepted')",
        (now(), order_id),
    )
    if not changed:
        raise HTTPException(409, "This delivery can no longer be cancelled")
    await hub.send_many(online_rider_ids(), {"type": "order_gone", "id": order_id})
    return await push_order(order_id)


# ---------- routes: rider ----------

@app.post("/api/rider/online")
def set_online(body: OnlineIn, user=Depends(rider_user)):
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
