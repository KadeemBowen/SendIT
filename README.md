# SendIt

Delivery and errand service, web-app prototype. Customers book a pickup and drop-off, add tasks for the rider, see a price up front, and pay in cash or with MMG. A rider accepts the job, and the customer then watches the rider on a live map with an ETA. Admins run everything from a dashboard.

## Run it on your computer

Requires Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m app.seed      # demo accounts + a week of test orders
.\run.ps1                               # http://localhost:8000
```

With no `.env` file, data is stored in a local SQLite file (`data/courier.db`). To use Supabase instead, see below.

## Demo data

`python -m app.seed` loads the same test data every time, into whichever database is configured:

| Login (password `demo123`) | What it is |
|---|---|
| `admin@demo.gy` | Admin dashboard |
| `customer@demo.gy` | Customer with no active orders, ready to book |
| `rider@demo.gy`, `rider2@demo.gy` | Approved riders, offline, ready to test with |
| `aaliyah@`, `marcus@`, `shondell@`, `ravi@`, `keisha@demo.gy` | Customers with order history |
| `devon@`, `nadia@demo.gy` | Riders shown online on the admin map |
| `troy@demo.gy` | Rider waiting for approval |

It also creates about 45 orders over the last 7 days at real Georgetown locations, with cash and MMG payments. It includes live items for the dashboard: two orders waiting for a rider, one MMG payment awaiting approval, and one refund due.

- `python -m app.seed --reset` **deletes everything** and reloads the demo data fresh.
- On a server, setting `COURIER_SEED_DEMO=1` loads the demo data automatically the first time it starts with an empty database.

## Use Supabase (Postgres)

1. Create a project at [supabase.com](https://supabase.com). Keep the database password it gives you.
2. In the project, open **Connect** (or Project Settings → Database) and copy the **Session pooler** connection string (URI). It looks like `postgresql://postgres.xxxx:[YOUR-PASSWORD]@aws-0-us-east-1.pooler.supabase.com:5432/postgres`.
3. Copy `.env.example` to `.env`, and set `DATABASE_URL=` to that string with your password filled in. `.env` is git-ignored, so the password never goes to GitHub.
4. Run `.\.venv\Scripts\python -m app.seed` to create the tables and load the demo data into Supabase, then run `.\run.ps1`.

The app creates its own tables on first start. Row Level Security is switched on for every table, so Supabase's public Data API can't read them; only the app, which connects with the database password, can.

## Put it online (test on any phone)

Phones only share GPS with sites served over HTTPS. The easiest way to get HTTPS is to host the app on Render (free) with the database on Supabase:

1. Do the Supabase steps above (steps 1 and 2 are enough).
2. At [render.com](https://render.com), sign in with GitHub and choose **New → Blueprint**, then pick this repo. Render reads `render.yaml`.
3. When asked for `DATABASE_URL`, paste the Supabase Session pooler string.
4. Deploy. You get a URL like `https://sendit-xxxx.onrender.com`. Open it on your phone. If the database is empty, the demo data loads automatically.

A free Render service goes to sleep after 15 minutes without visitors, so the first visit after that takes about a minute to load.

**Quick temporary link from your own computer:** with the app running locally, `cloudflared tunnel --protocol http2 --url http://localhost:8000` prints a temporary `https://….trycloudflare.com` link. It only works while your computer and that command are running.

## What each role can do

- **Customer:** book with pickup, stops, drop-off and tasks; see the price; pay by cash or MMG; track the rider live; cancel before pickup.
- **Rider:** sign up and wait for approval; go online; receive and accept requests; follow the navigation links; mark picked up and delivered; see earnings. Tick **Simulate GPS movement** to test on a computer.
- **Admin** (dashboard):
  - **Overview:** today's numbers, a live map of riders and active orders, and alerts for riders to approve, refunds due and unconfirmed MMG payments.
  - **Orders:** filter and search; assign a rider by hand; cancel; mark paid; record refunds.
  - **Riders and Customers:** approve riders; suspend or reactivate accounts. Click a rider to see all their orders, filter by date period, and see totals (overall, cash collected, paid by MMG).
  - **Pricing:** edit rates live, with a preview of what a trip would cost.
  - **Payments:** every payment, refunds due, and MMG connection status.

Everyone also gets:

- **Notifications:** the bell in the top bar shows order, payment and account updates, live and saved in a list.
- **Profile:** tap your name to choose the theme (System, Light or Dark), turn notifications off, or log out.

To create a real admin account (or promote an existing one): `.\.venv\Scripts\python -m app.make_admin you@example.com "Your Name"`

## Payments and MMG

- **Cash:** recorded as paid when the rider marks the job delivered.
- **MMG:** when a customer books with MMG, a payment request is sent to their MMG number, and the order shows *Awaiting MMG* until MMG confirms. If a payment fails, the customer can retry or switch to cash. If a paid order is cancelled, it's flagged *Refund due* for an admin.

**The app currently runs MMG in test mode** (`COURIER_MMG_MODE=mock`): no money moves. Customers and admins get buttons to simulate approving or declining payments.

To connect the real MMG API, all the work is in one place, `LiveMMG` in [`app/payments.py`](app/payments.py). Its docstring says exactly what each method must do:

1. `request_payment()` calls MMG to charge the customer's wallet, and returns MMG's transaction ID.
2. `parse_callback()` verifies MMG's notification to `POST /api/payments/mmg/callback`, and returns paid or failed.
3. Set `MMG_API_BASE`, `MMG_MERCHANT_ID`, `MMG_API_KEY`, `MMG_CALLBACK_SECRET` and `COURIER_MMG_MODE=live`.

## How it works

| Piece | What it does |
|---|---|
| `app/main.py` | FastAPI server: auth, quotes, orders, payments, rider status, WebSocket live updates |
| `app/admin.py` | Admin dashboard API |
| `app/orders.py` | Order data for the screens, ETAs, and pushing live updates to customers, riders and admins |
| `app/payments.py` | Cash and MMG payments (mock and live providers) |
| `app/notify.py` | In-app notifications: saved per user and pushed live |
| `app/pricing.py` | Price = base fee + per km + per extra stop + per task, rounded up, with a minimum fare. Defaults are in `config.py`; admins can change them on the dashboard. |
| `app/geo.py` | Road distance and route line from OSRM, address search from OpenStreetMap Nominatim. Falls back to straight-line estimates if those are down. |
| `app/db.py` | SQLite or Postgres/Supabase; schema and migrations run automatically at startup |
| `app/seed.py` | Demo accounts and test data |
| `app/static/` | The web app (plain JS, no build step): `customer.js`, `rider.js`, `admin.js`, `notifications.js`, `profile.js`, and shared `map.js` / `ui.js` / `api.js` / `theme.js`. Colors come from the logo and are set at the top of `styles.css`. |
| `render.yaml` | One-click hosting on Render |

Order flow: `requested → accepted → picked_up → delivered`. Customers can cancel while `requested` or `accepted`; admins can cancel any active order. New requests go out live to every approved, online rider, and the first to accept gets the job.

## Not built yet

- The live MMG connection (see above), ratings, and push notifications when the app is closed (notifications currently appear while the app is open)
- Rider documents and ID checks during approval, and rider payouts
- Password reset and changing passwords
- Production readiness: a paid or self-hosted map, routing and geocoding provider (the free public OSM services are for testing only), backups, and monitoring
- Android/iOS: the frontend is a mobile-first web app, so it can be wrapped with Capacitor to reach the app stores, or rebuilt in React Native / Flutter against the same API
