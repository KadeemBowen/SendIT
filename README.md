# SendIT

Delivery and errand service, web-app prototype. Customers book a pickup and drop-off, add tasks for the rider, see a price up front, and pay in cash or with MMG. A rider accepts the job, and the customer then watches the rider on a live map with an ETA. Admins run everything from a dashboard.

## Run it

Requires Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m app.seed      # demo accounts (password: demo123)
.\run.ps1                               # http://localhost:8000
```

Demo accounts: `customer@demo.gy`, `rider@demo.gy`, `rider2@demo.gy`, `admin@demo.gy`.

To create a real admin account (or promote an existing one): `.\.venv\Scripts\python -m app.make_admin you@example.com "Your Name"`

**Trying several roles on one computer:** logins are stored per browser origin, so use `http://localhost:8000` for one role, `http://127.0.0.1:8000` for another, and a private window for a third. On the rider screen, tick **Simulate GPS movement** so the rider "drives" without real GPS.

**Testing on phones:** browsers only share GPS over HTTPS. Put the app behind an HTTPS tunnel, for example `cloudflared tunnel --url http://localhost:8000`, and open the URL it prints on each phone.

## What each role can do

- **Customer:** book with pickup, stops, drop-off and tasks; see the price; pay by cash or MMG; track the rider live; cancel before pickup.
- **Rider:** sign up and wait for approval; go online; receive and accept requests; follow the navigation links; mark picked up and delivered; see earnings.
- **Admin** (dashboard):
  - **Overview:** today's numbers, a live map of riders and active orders, and alerts for riders to approve, refunds due and unconfirmed MMG payments.
  - **Orders:** filter and search; assign a rider by hand; cancel; mark paid; record refunds.
  - **Riders and Customers:** approve riders; suspend or reactivate accounts.
  - **Pricing:** edit rates live, with a preview of what a trip would cost.
  - **Payments:** every payment, refunds due, and MMG connection status.

## Payments and MMG

- **Cash:** recorded as paid when the rider marks the job delivered.
- **MMG:** when a customer books with MMG, a payment request is sent to their MMG number, and the order shows *Awaiting MMG* until MMG confirms. If a payment fails, the customer can retry or switch to cash. If a paid order is cancelled, it's flagged *Refund due* for an admin.

**The app currently runs MMG in test mode** (`COURIER_MMG_MODE=mock`): no money moves. Customers and admins get buttons to simulate approving or declining payments.

To connect the real MMG API, all the work is in one place, `LiveMMG` in [`app/payments.py`](app/payments.py). Its docstring says exactly what each method must do:

1. `request_payment()` calls MMG to charge the customer's wallet, and returns MMG's transaction ID.
2. `parse_callback()` verifies MMG's notification to `POST /api/payments/mmg/callback`, and returns paid or failed.
3. Set `MMG_API_BASE`, `MMG_MERCHANT_ID`, `MMG_API_KEY`, `MMG_CALLBACK_SECRET` and `COURIER_MMG_MODE=live`.

Nothing else in the app needs to change: the order flow, retries, refunds and the dashboard already handle MMG payment states.

## How it works

| Piece | What it does |
|---|---|
| `app/main.py` | FastAPI server: auth, quotes, orders, payments, rider status, WebSocket live updates |
| `app/admin.py` | Admin dashboard API |
| `app/orders.py` | Order data for the screens, ETAs, and pushing live updates to customers, riders and admins |
| `app/payments.py` | Cash and MMG payments (mock and live providers) |
| `app/pricing.py` | Price = base fee + per km + per extra stop + per task, rounded up, with a minimum fare. Defaults are in `config.py`; admins can change them on the dashboard. |
| `app/geo.py` | Road distance and route line from OSRM, address search from OpenStreetMap Nominatim. Falls back to straight-line estimates if those are down. |
| `app/db.py` | SQLite schema and migrations (they run automatically at startup) |
| `app/static/` | The web app (plain JS, no build step): `customer.js`, `rider.js`, `admin.js`, and shared `map.js` / `ui.js` / `api.js` |

Order flow: `requested → accepted → picked_up → delivered`. Customers can cancel while `requested` or `accepted`; admins can cancel any active order. New requests go out live to every approved, online rider, and the first to accept gets the job.

## Not built yet

- The live MMG connection (see above), ratings, and push notifications when the app is closed
- Rider documents and ID checks during approval, and rider payouts
- Production hosting: Postgres instead of SQLite, HTTPS, and a paid or self-hosted map, routing and geocoding provider (the free public OSM services are for testing only)
- Android/iOS: the frontend is a mobile-first web app, so it can be wrapped with Capacitor to reach the app stores, or rebuilt in React Native / Flutter against the same API
