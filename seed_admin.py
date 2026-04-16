"""
seed_admin.py — Run ONCE to insert the first admin user with a hashed password.
Then delete or keep this file (it won't re-insert if the username already exists).

Usage:  python seed_admin.py
"""

from werkzeug.security import generate_password_hash
import mysql.connector
import os
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    'host':     os.getenv('DB_HOST', 'localhost'),
    'user':     os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', 'Audi_123'),
    'database': os.getenv('DB_NAME', 'crop_advisory'),
    'port':     int(os.getenv('DB_PORT', 3306))
}

ADMIN = {
    'username': 'admin',
    'email':    'admin@plantwise.com',
    'password': 'plantwise2024',   # Change before going live
    'role':     'superadmin'
}

def seed():
    conn = mysql.connector.connect(**DB_CONFIG)
    cur  = conn.cursor()

    hashed = generate_password_hash(ADMIN['password'])

    cur.execute("""
        INSERT INTO admin_users (username, email, password_hash, role)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            password_hash = VALUES(password_hash),
            role          = VALUES(role)
    """, (ADMIN['username'], ADMIN['email'], hashed, ADMIN['role']))

    conn.commit()
    print(f"[OK] Admin user '{ADMIN['username']}' seeded successfully.")
    print(f"     Email : {ADMIN['email']}")
    print(f"     Role  : {ADMIN['role']}")
    print(f"     Hash  : {hashed[:30]}... (truncated)")

    cur.close()
    conn.close()

if __name__ == '__main__':
    seed()
