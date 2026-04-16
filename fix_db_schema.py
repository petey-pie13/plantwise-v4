"""
One-time schema fix for diagnosis_sessions table.
Run: python fix_db_schema.py
"""
from dotenv import load_dotenv
import os, mysql.connector

load_dotenv()
conn = mysql.connector.connect(
    host=os.getenv('DB_HOST', 'localhost'),
    user=os.getenv('DB_USER'),
    password=os.getenv('DB_PASSWORD'),
    database=os.getenv('DB_NAME', 'crop_advisory'),
    port=int(os.getenv('DB_PORT', 3306)),
)
cur = conn.cursor()

print("Fixing diagnosis_sessions schema...")

# 1. Make session_ref nullable (was NOT NULL, causing all inserts to fail)
cur.execute("""
    ALTER TABLE diagnosis_sessions
    MODIFY COLUMN session_ref VARCHAR(64) NULL DEFAULT NULL
""")
print("  session_ref -> nullable OK")

# 2. Expand method ENUM to include all three diagnosis methods
cur.execute("""
    ALTER TABLE diagnosis_sessions
    MODIFY COLUMN method ENUM(
        'symptom',
        'image',
        'hybrid',
        'knowledge_base',
        'image_analysis'
    ) NOT NULL DEFAULT 'knowledge_base'
""")
print("  method ENUM expanded OK")

conn.commit()

# Verify
cur.execute("SHOW COLUMNS FROM diagnosis_sessions")
print("\nFinal diagnosis_sessions schema:")
for row in cur.fetchall():
    print(f"  {row[0]:25s} | {row[1]:50s} | null={row[2]}")

conn.close()
print("\nDone! Restart Flask for changes to take effect.")
