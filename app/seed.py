"""Create demo accounts: python -m app.seed"""
from datetime import datetime, timezone

from . import db
from .auth import hash_password

DEMO_USERS = [
    ("Demo Customer", "customer@demo.gy", "592-600-0001", "customer", None, None),
    ("Demo Rider", "rider@demo.gy", "592-600-0002", "rider", "Red Honda motorbike", "CJ 1234"),
    ("Second Rider", "rider2@demo.gy", "592-600-0003", "rider", "Blue Yamaha scooter", "CK 5678"),
    ("Demo Admin", "admin@demo.gy", "592-600-0000", "admin", None, None),
]


def main():
    db.init()
    for name, email, phone, role, vehicle, plate in DEMO_USERS:
        if db.query_one("SELECT id FROM users WHERE email = ?", (email,)):
            print(f"exists   {email}")
            continue
        db.execute(
            """INSERT INTO users (name, email, phone, role, password_hash, vehicle, plate, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, email, phone, role, hash_password("demo123"), vehicle, plate,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        print(f"created  {email}  (password: demo123)")


if __name__ == "__main__":
    main()
