"""Demo accounts and a week of realistic test data.

    python -m app.seed            add demo accounts; add demo orders if there are no orders yet
    python -m app.seed --reset    DELETE ALL DATA, then load the demo data fresh

Every demo account's password is demo123. Works with SQLite and Postgres/Supabase.
"""
import argparse
import json
import random
from datetime import datetime, timedelta, timezone

from . import config, db, geo, pricing
from .auth import hash_password

# (name, email, phone, role, vehicle, plate, approved)
DEMO_USERS = [
    ("Demo Admin", "admin@demo.gy", "592-600-0000", "admin", None, None, True),
    ("Demo Customer", "customer@demo.gy", "592-600-0001", "customer", None, None, True),
    ("Aaliyah Persaud", "aaliyah@demo.gy", "592-611-1001", "customer", None, None, True),
    ("Marcus Williams", "marcus@demo.gy", "592-611-1002", "customer", None, None, True),
    ("Shondell Jones", "shondell@demo.gy", "592-611-1003", "customer", None, None, True),
    ("Ravi Singh", "ravi@demo.gy", "592-611-1004", "customer", None, None, True),
    ("Keisha Adams", "keisha@demo.gy", "592-611-1005", "customer", None, None, True),
    ("Demo Rider", "rider@demo.gy", "592-600-0002", "rider", "Red Honda motorbike", "CJ 1234", True),
    ("Second Rider", "rider2@demo.gy", "592-600-0003", "rider", "Blue Yamaha scooter", "CK 5678", True),
    ("Devon Charles", "devon@demo.gy", "592-622-2001", "rider", "Black Suzuki motorbike", "CL 2468", True),
    ("Nadia Khan", "nadia@demo.gy", "592-622-2002", "rider", "White Honda scooter", "CM 1357", True),
    ("Troy Benjamin", "troy@demo.gy", "592-622-2003", "rider", "Silver bicycle", None, False),
]

# Riders shown online on the admin map (not the ones you log in as, so testing starts clean).
ONLINE_RIDERS = {"devon@demo.gy": (6.8121, -58.1502), "nadia@demo.gy": (6.8203, -58.1395)}

PLACES = [
    ("Stabroek Market, Water Street", 6.8065, -58.1628),
    ("Bourda Market, Regent Street", 6.8105, -58.1535),
    ("Giftland Mall, Turkeyen", 6.8150, -58.1140),
    ("Amazonia Mall, Providence", 6.7560, -58.1840),
    ("Sheriff Street, Campbellville", 6.8190, -58.1400),
    ("Kitty Market, Kitty", 6.8250, -58.1430),
    ("University of Guyana, Turkeyen", 6.8148, -58.1165),
    ("Main Street, Cummingsburg", 6.8110, -58.1640),
    ("Lamaha Gardens", 6.8255, -58.1370),
    ("Eccles, East Bank Demerara", 6.7830, -58.1600),
    ("Georgetown Public Hospital", 6.8095, -58.1590),
    ("Regent Street, Lacytown", 6.8070, -58.1570),
    ("Queenstown", 6.8150, -58.1560),
    ("Bel Air Park", 6.8225, -58.1330),
    ("Camp Street, Georgetown", 6.8085, -58.1600),
    ("Vlissengen Road", 6.8130, -58.1480),
]

TASKS = [
    "Collect a package", "Buy items for me", "Pay a bill", "Drop off documents", "Wait and bring back",
    "Pick up lunch order", "Buy 2 bags of rice", "Collect prescription", "Pay GPL bill", "Deliver birthday cake",
]
NOTES = [None, None, None, "Call when outside", "Green gate, ring the bell", "Leave with security", "Fragile - handle with care"]

LOCAL = timezone(timedelta(hours=config.UTC_OFFSET_HOURS))


def iso(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def reset():
    db.execute_all([(f"DELETE FROM {t}", ()) for t in ("payments", "rider_locations", "orders", "sessions", "settings", "users")])
    print("deleted all data")


def add_users():
    ids = {}
    for name, email, phone, role, vehicle, plate, approved in DEMO_USERS:
        existing = db.query_one("SELECT id FROM users WHERE email = ?", (email,))
        if existing:
            ids[email] = existing["id"]
            continue
        ids[email] = db.insert(
            """INSERT INTO users (name, email, phone, role, password_hash, vehicle, plate, approved, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, email, phone, role, hash_password("demo123"), vehicle, plate, int(approved),
             iso(datetime.now(LOCAL) - timedelta(days=14))),
        )
        print(f"created  {email:22} {role}")
    return ids


def add_orders(ids):
    rnd = random.Random(592)
    rates = pricing.current_rates()
    customers = [ids[u[1]] for u in DEMO_USERS if u[3] == "customer"]
    riders = [ids[u[1]] for u in DEMO_USERS if u[3] == "rider" and u[6]]
    now = datetime.now(LOCAL)
    count = 0

    for days_ago in range(6, -1, -1):
        day = (now - timedelta(days=days_ago)).replace(hour=0, minute=0, second=0, microsecond=0)
        latest_hour = min(19, now.hour - 1) if days_ago == 0 else 19
        for _ in range(rnd.randint(5, 9) if days_ago else max(0, min(5, latest_hour - 7))):
            created = day + timedelta(hours=rnd.randint(8, max(8, latest_hour)), minutes=rnd.randint(0, 59))
            if created >= now - timedelta(minutes=20):
                continue
            pickup, dropoff = rnd.sample(PLACES, 2)
            stops = [dict(zip(("address", "lat", "lng"), p)) for p in rnd.sample(PLACES, 1) if rnd.random() < 0.2
                     and p not in (pickup, dropoff)]
            tasks = rnd.sample(TASKS, rnd.choice([0, 1, 1, 1, 2]))
            points = [pickup[1:], *[(s["lat"], s["lng"]) for s in stops], dropoff[1:]]
            km = geo.estimated_road_km(points)
            duration = geo.minutes_for_km(km)
            price, breakdown = pricing.quote(km, len(stops), len(tasks), rates)
            method = "mmg" if rnd.random() < 0.4 else "cash"
            cancelled = rnd.random() < 0.13
            customer = rnd.choice(customers)
            rider = None if cancelled else rnd.choice(riders)
            accepted = created + timedelta(minutes=rnd.randint(1, 8))
            picked = accepted + timedelta(minutes=rnd.randint(8, 22))
            delivered = picked + timedelta(minutes=duration + len(tasks) * 8 + rnd.randint(3, 12))
            cancelled_at = created + timedelta(minutes=rnd.randint(2, 10))
            payment_status = ("void" if method == "cash" else rnd.choice(["refunded", "void"])) if cancelled else "paid"

            order_id = db.insert(
                """INSERT INTO orders (customer_id, rider_id, status, pickup_address, pickup_lat, pickup_lng,
                       dropoff_address, dropoff_lat, dropoff_lng, stops_json, tasks_json, notes,
                       distance_km, duration_min, route_json, route_source, price, currency, breakdown_json,
                       created_at, accepted_at, picked_up_at, delivered_at, cancelled_at, cancelled_by,
                       payment_method, payment_status, mmg_number)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (customer, rider, "cancelled" if cancelled else "delivered",
                 pickup[0], pickup[1], pickup[2], dropoff[0], dropoff[1], dropoff[2],
                 json.dumps(stops), json.dumps(tasks), rnd.choice(NOTES),
                 round(km, 2), round(duration, 1), json.dumps([list(p) for p in points]), "estimate",
                 price, config.CURRENCY, json.dumps(breakdown), iso(created),
                 None if cancelled else iso(accepted), None if cancelled else iso(picked),
                 None if cancelled else iso(delivered), iso(cancelled_at) if cancelled else None,
                 "customer" if cancelled else None, method, payment_status,
                 f"592611{rnd.randint(1000, 9999)}" if method == "mmg" else None),
            )
            add_payment(order_id, method, payment_status, price, created, delivered if not cancelled else cancelled_at)
            count += 1

    # Live items for the dashboard: one order waiting for a rider, one MMG payment waiting, one refund due.
    waiting = [
        (ids["aaliyah@demo.gy"], PLACES[1], PLACES[4], ["Buy items for me"], "cash", "unpaid"),
        (ids["marcus@demo.gy"], PLACES[2], PLACES[8], ["Collect a package"], "mmg", "pending"),
    ]
    for customer, pickup, dropoff, tasks, method, payment_status in waiting:
        km = geo.estimated_road_km([pickup[1:], dropoff[1:]])
        price, breakdown = pricing.quote(km, 0, len(tasks), rates)
        created = now - timedelta(minutes=rnd.randint(3, 9))
        order_id = db.insert(
            """INSERT INTO orders (customer_id, status, pickup_address, pickup_lat, pickup_lng,
                   dropoff_address, dropoff_lat, dropoff_lng, tasks_json, distance_km, duration_min,
                   route_json, route_source, price, currency, breakdown_json, created_at,
                   payment_method, payment_status, mmg_number)
               VALUES (?, 'requested', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'estimate', ?, ?, ?, ?, ?, ?, ?)""",
            (customer, pickup[0], pickup[1], pickup[2], dropoff[0], dropoff[1], dropoff[2], json.dumps(tasks),
             round(km, 2), round(geo.minutes_for_km(km), 1), json.dumps([list(pickup[1:]), list(dropoff[1:])]),
             price, config.CURRENCY, json.dumps(breakdown), iso(created), method, payment_status,
             "5926111002" if method == "mmg" else None),
        )
        add_payment(order_id, method, payment_status, price, created, created)
        count += 1

    refund = db.query_one(
        "SELECT id FROM orders WHERE status = 'cancelled' AND payment_method = 'mmg' ORDER BY id DESC LIMIT 1"
    )
    if refund:
        db.execute_all([
            ("UPDATE orders SET payment_status = 'refund_due' WHERE id = ?", (refund["id"],)),
            ("UPDATE payments SET status = 'paid', message = 'Paid with MMG' WHERE order_id = ?", (refund["id"],)),
        ])
    print(f"created  {count} orders over the last 7 days")


def add_payment(order_id, method, payment_status, amount, created, updated):
    if method == "cash":
        if payment_status != "paid":
            return
        status, message, ref = "paid", "Collected by rider", None
    else:
        status = {"paid": "paid", "refunded": "refunded", "void": "void", "pending": "pending"}.get(payment_status)
        if not status:
            return
        ref = f"MOCK-DEMO-{order_id}"
        message = {"paid": "Paid with MMG", "refunded": "Refunded", "void": "Cancelled before payment",
                   "pending": "Payment request sent to MMG 5926111002. Approve it in MMG to pay."}[status]
    db.insert(
        """INSERT INTO payments (order_id, provider, amount, currency, status, provider_ref, message, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (order_id, method, amount, config.CURRENCY, status, ref, message, iso(created), iso(updated)),
    )


def add_rider_positions(ids):
    for email, (lat, lng) in ONLINE_RIDERS.items():
        db.execute_all([
            ("UPDATE users SET is_online = 1 WHERE id = ?", (ids[email],)),
            ("""INSERT INTO rider_locations (rider_id, lat, lng, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(rider_id) DO UPDATE SET lat = excluded.lat, lng = excluded.lng, updated_at = excluded.updated_at""",
             (ids[email], lat, lng, iso(datetime.now(LOCAL)))),
        ])


def load_demo(reset_first=False):
    print("database:", "Postgres" if db.IS_POSTGRES else f"SQLite ({config.DB_PATH})")
    if reset_first:
        reset()
    ids = add_users()
    if db.query_one("SELECT COUNT(*) n FROM orders")["n"] == 0:
        add_orders(ids)
        add_rider_positions(ids)
    else:
        print("orders already exist - skipped demo orders (use --reset for a fresh demo dataset)")
    print("all demo passwords: demo123")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="delete ALL existing data first")
    args = parser.parse_args()
    db.init()
    load_demo(args.reset)


if __name__ == "__main__":
    main()
