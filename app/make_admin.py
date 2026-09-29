"""Create an admin account, or promote an existing account to admin.

    python -m app.make_admin you@example.com "Your Name" 592-600-0000
"""
import argparse
import getpass
from datetime import datetime, timezone

from . import db
from .auth import hash_password


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("email")
    parser.add_argument("name", nargs="?", default="Admin")
    parser.add_argument("phone", nargs="?", default="-")
    args = parser.parse_args()
    db.init()

    email = args.email.strip().lower()
    user = db.query_one("SELECT * FROM users WHERE email = ?", (email,))
    if user:
        if user["role"] == "rider" and db.query_one(
            "SELECT id FROM orders WHERE rider_id = ? AND status IN ('accepted', 'picked_up')", (user["id"],)
        ):
            raise SystemExit("That rider is on a job right now - try again after it is finished.")
        db.execute("UPDATE users SET role = 'admin', approved = 1, active = 1, is_online = 0 WHERE id = ?", (user["id"],))
        print(f"{email} is now an admin")
        return

    password = getpass.getpass("Password for the new admin (min 8 characters): ")
    if len(password) < 8:
        raise SystemExit("Password too short")
    db.execute(
        """INSERT INTO users (name, email, phone, role, password_hash, created_at)
           VALUES (?, ?, ?, 'admin', ?, ?)""",
        (args.name, email, args.phone, hash_password(password), datetime.now(timezone.utc).isoformat(timespec="seconds")),
    )
    print(f"Created admin {email}")


if __name__ == "__main__":
    main()
