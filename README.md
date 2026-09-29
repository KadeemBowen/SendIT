# SendIT

Delivery and errand service, web-app prototype. Customers book a pickup and drop-off, add tasks for the rider, and see a price up front. A rider accepts the job, and the customer then watches the rider on a live map with an ETA.

## Run it

Requires Python 3.11+.

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m app.seed      # demo accounts (password: demo123)
.\run.ps1                               # http://localhost:8000
```

Demo accounts: `customer@demo.gy`, `rider@demo.gy`, `rider2@demo.gy`.

**Trying both sides on one computer:** logins are stored per browser origin, so open the customer at `http://localhost:8000` and the rider at `http://127.0.0.1:8000` (or use a private window). On the rider screen, tick **Simulate GPS movement** so the rider "drives" without real GPS.

**Testing on phones:** browsers only share GPS over HTTPS. Put the app behind an HTTPS tunnel, for example `cloudflared tunnel --url http://localhost:8000`, and open the URL it prints on each phone.

## How it works

| Piece | What it does |
|---|---|
| `app/main.py` | FastAPI server: auth, quotes, orders, rider status, WebSocket live updates |
| `app/pricing.py` + `app/config.py` | Price = base fee + per km + per extra stop + per task, rounded up, with a minimum fare. **All rates live in `config.py`.** |
| `app/geo.py` | Road distance and route line from OSRM, address search from OpenStreetMap Nominatim. Falls back to straight-line estimates if those are down. |
| `app/static/` | The web app (plain JS, no build step): `customer.js`, `rider.js`, and shared `map.js` / `ui.js` / `api.js` |
| `data/courier.db` | SQLite database, created on first run |

Order flow: `requested → accepted → picked_up → delivered` (customers can cancel while `requested` or `accepted`). New requests go out live to every online rider, and the first rider to accept gets the job. While a job is active, each location update from the rider pushes a fresh ETA to the customer.

## Not built yet

- Payments (cash on delivery for now), ratings, and push notifications when the app is closed
- An admin dashboard (riders, orders, pricing), plus rider vetting and approval
- Production hosting: Postgres instead of SQLite, HTTPS, and a paid or self-hosted map, routing and geocoding provider (the free public OSM services are for testing only)
- Android/iOS: the frontend is a mobile-first web app, so it can be wrapped with Capacitor to reach the app stores, or rebuilt in React Native / Flutter against the same API
