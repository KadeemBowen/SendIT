"""Business settings. Tweak these while testing - no other code changes needed."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")  # secrets like DATABASE_URL live in .env (never committed)

APP_NAME = os.environ.get("COURIER_APP_NAME", "SendIT")

# Database: Postgres/Supabase if DATABASE_URL is set, otherwise a local SQLite file.
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()
DB_PATH = Path(os.environ.get("COURIER_DB", ROOT / "data" / "courier.db"))

CURRENCY = "GYD"
UTC_OFFSET_HOURS = -4             # Guyana time, used for "today" on the admin dashboard
MAP_CENTER = (6.8013, -58.1551)  # Georgetown
GEOCODE_COUNTRY = "gy"            # limit address search to Guyana ("" = worldwide)

# Pricing: base + distance + extra stops + tasks, rounded up, never below the minimum.
# These are the defaults; admins can change them live on the dashboard (stored in the database).
PRICING = {
    "base_fee": 500,
    "per_km": 150,
    "per_extra_stop": 300,   # stops between pickup and drop-off
    "per_task": 250,         # each thing the rider has to do (buy, collect, pay, wait...)
    "minimum": 800,
    "round_to": 100,
}

# Time estimates
AVG_SPEED_KMH = 25        # city riding speed used for live ETAs
ROAD_FACTOR = 1.3         # straight-line km -> road km when no routing service is available
PICKUP_HANDLING_MIN = 5   # time spent at pickup
TASK_MIN = 10             # extra time per task
STOP_MIN = 5              # extra time per intermediate stop

# Free public routing (OSRM) for road distance + route line. Falls back to estimates if it fails.
USE_OSRM = os.environ.get("COURIER_USE_OSRM", "1") == "1"
HTTP_USER_AGENT = os.environ.get("COURIER_USER_AGENT", f"{APP_NAME}-prototype/0.1")

# MMG (Mobile Money Guyana) payments.
#   "mock": simulated payments for testing - customers/admins get a button to approve or fail them.
#   "live": the real MMG API (fill in app/payments.py:LiveMMG once you have MMG's merchant API docs).
MMG_MODE = os.environ.get("COURIER_MMG_MODE", "mock")
MMG_API_BASE = os.environ.get("MMG_API_BASE", "")
MMG_MERCHANT_ID = os.environ.get("MMG_MERCHANT_ID", "")
MMG_API_KEY = os.environ.get("MMG_API_KEY", "")
MMG_CALLBACK_SECRET = os.environ.get("MMG_CALLBACK_SECRET", "")

# Load the demo accounts and a week of test orders on startup when the database has no users yet.
SEED_DEMO_IF_EMPTY = os.environ.get("COURIER_SEED_DEMO", "0") == "1"

# Shows demo login hints on the sign-in screen.
DEMO_MODE = os.environ.get("COURIER_DEMO", "1") == "1"
