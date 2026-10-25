"""
seed_admin.py — Run ONCE to insert the first admin user with a hashed password.
Then delete or keep this file (it won't re-insert if the username already exists).

Usage:  python seed_admin.py

The script reads DB credentials from the .env file (or environment variables).
Set ADMIN_PASSWORD in your environment to use a custom password; otherwise
you will be prompted securely.
"""

import getpass
import os
import sys

import psycopg2
from dotenv import load_dotenv
from werkzeug.security import generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

DB_CONFIG = {
    'host':     os.getenv('DB_HOST', 'localhost'),
    'user':     os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', ''),
    'dbname':   os.getenv('DB_NAME', 'crop_advisory'),
    'port':     int(os.getenv('DB_PORT', 5432))
}

ADMIN_USERNAME = os.getenv('ADMIN_USERNAME', 'admin')
ADMIN_EMAIL    = os.getenv('ADMIN_EMAIL', 'admin@plantwise.com')
ADMIN_ROLE     = os.getenv('ADMIN_ROLE', 'superadmin')


def seed():
    # Prompt for password securely if not supplied via environment
    plain_password = os.getenv('ADMIN_PASSWORD', '')
    if not plain_password:
        plain_password = getpass.getpass(f"Enter password for admin user '{ADMIN_USERNAME}': ")
    if not plain_password:
        print("[ERROR] Password cannot be empty.")
        sys.exit(1)

    hashed = generate_password_hash(plain_password)

    try:
        conn = psycopg2.connect(**DB_CONFIG)
        cur  = conn.cursor()

        cur.execute(
            "SELECT id FROM admin_users WHERE username = %s OR email = %s",
            (ADMIN_USERNAME, ADMIN_EMAIL)
        )
        existing = cur.fetchone()
        if existing:
            cur.execute("""
                UPDATE admin_users
                SET username = %s, email = %s, password_hash = %s, role = %s, is_active = 1
                WHERE id = %s
            """, (ADMIN_USERNAME, ADMIN_EMAIL, hashed, ADMIN_ROLE, existing[0]))
        else:
            cur.execute("""
                INSERT INTO admin_users (username, email, password_hash, role, is_active)
                VALUES (%s, %s, %s, %s, 1)
            """, (ADMIN_USERNAME, ADMIN_EMAIL, hashed, ADMIN_ROLE))

        conn.commit()
        print(f"[OK] Admin user '{ADMIN_USERNAME}' seeded successfully.")
        print(f"     Email : {ADMIN_EMAIL}")
        print(f"     Role  : {ADMIN_ROLE}")
        print(f"     Hash  : {hashed[:30]}... (truncated)")

    except psycopg2.Error as e:
        print(f"[ERROR] Database error: {e}")
        sys.exit(1)
    finally:
        if 'cur' in dir():
            cur.close()
        if 'conn' in dir():
            conn.close()


if __name__ == '__main__':
    seed()
