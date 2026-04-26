"""
PlantWise V4 — MySQL to PostgreSQL Migration Script
Run: python migrate_to_postgres.py

What this does:
  1. Reads your MySQL export SQL file
  2. Converts MySQL-specific syntax to PostgreSQL syntax
  3. Imports it into your local PostgreSQL crop_advisory database
"""

import re
import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import psycopg2
import os
from dotenv import load_dotenv

# ─── CONFIG ───────────────────────────────────────────────────────────────────
MYSQL_EXPORT_FILE = r"C:\Users\user\OneDrive\Desktop\mysql_export.sql"

PG_CONFIG = {
    "host":     "localhost",
    "port":     5432,
    "user":     "postgres",
    "password": "Audi_123",   # ← YOUR postgres password from Step 1
    "dbname":   "crop_advisory",
}
# ──────────────────────────────────────────────────────────────────────────────


def convert_mysql_to_postgres(sql: str) -> str:
    """Apply all necessary MySQL → PostgreSQL syntax conversions."""

    # 1. Remove MySQL-only statements
    sql = re.sub(r'\/\*.*?\*\/;?\n?', '', sql, flags=re.DOTALL)  # strip block comments
    sql = re.sub(r'--.*\n', '\n', sql)                            # strip line comments
    sql = re.sub(r'SET\s+SQL_MODE.*?;\n?', '', sql)
    sql = re.sub(r'SET\s+time_zone.*?;\n?', '', sql)
    sql = re.sub(r'SET\s+character_set.*?;\n?', '', sql)
    sql = re.sub(r'SET\s+NAMES.*?;\n?', '', sql)
    sql = re.sub(r'SET\s+FOREIGN_KEY_CHECKS.*?;\n?', '', sql)
    sql = re.sub(r'SET\s+UNIQUE_CHECKS.*?;\n?', '', sql)
    sql = re.sub(r'SET\s+AUTOCOMMIT.*?;\n?', '', sql)
    sql = re.sub(r'START TRANSACTION;\n?', '', sql)
    sql = re.sub(r'COMMIT;\n?', '', sql)
    sql = re.sub(r'LOCK TABLES.*?;\n?', '', sql)
    sql = re.sub(r'UNLOCK TABLES;\n?', '', sql)
    sql = re.sub(r'DROP TABLE IF EXISTS.*?;\n?', '', sql)

    # 2. Data types — handle 'unsigned' modifier first
    sql = re.sub(r'\bint\s+unsigned\s+NOT NULL\s+AUTO_INCREMENT', 'SERIAL NOT NULL', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bint\s+unsigned\s+AUTO_INCREMENT', 'SERIAL', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\b(int|smallint|tinyint|bigint)\s+unsigned\b', lambda m: {'int': 'INTEGER', 'smallint': 'SMALLINT', 'tinyint': 'SMALLINT', 'bigint': 'BIGINT'}[m.group(1).lower()], sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bINT\s+NOT NULL\s+AUTO_INCREMENT', 'SERIAL NOT NULL', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bINT\s+AUTO_INCREMENT', 'SERIAL', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bBIGINT\s+AUTO_INCREMENT', 'BIGSERIAL', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bTINYINT\(1\)', 'SMALLINT', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bTINYINT\b', 'SMALLINT', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bMEDIUMINT\b', 'INTEGER', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bDOUBLE\b', 'DOUBLE PRECISION', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bLONGTEXT\b', 'TEXT', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bMEDIUMTEXT\b', 'TEXT', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bTINYTEXT\b', 'TEXT', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bDATETIME\b', 'TIMESTAMP', sql, flags=re.IGNORECASE)
    sql = re.sub(r'\bVARCHAR\((\d+)\)\s+CHARACTER SET\s+\w+', r'VARCHAR(\1)', sql, flags=re.IGNORECASE)

    # 3. ENUM types -> VARCHAR (safest conversion)
    sql = re.sub(r"ENUM\([^)]+\)", "VARCHAR(50)", sql, flags=re.IGNORECASE)

    # 4. SET type -> TEXT (store as comma-separated string)
    sql = re.sub(r"SET\([^)]+\)", "TEXT", sql, flags=re.IGNORECASE)

    # 5. MySQL table options (ENGINE, CHARSET, etc.)
    # Pattern: ) ENGINE=InnoDB...; -> );
    sql = re.sub(r'\)\s*\n\s*ENGINE\s*=\s*\w+[^;]*;', ');', sql, flags=re.IGNORECASE)
    sql = re.sub(r'DEFAULT\s+CHARSET\s*=\s*\w+', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'COLLATE\s*=?\s*[\w_]+', '', sql, flags=re.IGNORECASE)
    sql = re.sub(r'ROW_FORMAT\s*=\s*\w+', '', sql, flags=re.IGNORECASE)

    # 6. Backtick identifiers -> double-quote (PostgreSQL standard)
    sql = re.sub(r'`(\w+)`', r'"\1"', sql)

    # 7. MySQL boolean literals and defaults
    sql = sql.replace(" b'1'", " TRUE").replace(" b'0'", " FALSE")
    # Fix BOOLEAN column DEFAULTs: DEFAULT '1' -> DEFAULT TRUE, DEFAULT '0' -> DEFAULT FALSE
    sql = re.sub(r'(BOOLEAN\s+(?:NOT NULL\s+)?DEFAULT\s+)\'1\'', r'\1TRUE', sql, flags=re.IGNORECASE)
    sql = re.sub(r'(BOOLEAN\s+(?:NOT NULL\s+)?DEFAULT\s+)\'0\'', r'\1FALSE', sql, flags=re.IGNORECASE)

    # 7b. MySQL escaped apostrophes -> PostgreSQL double single-quotes
    sql = sql.replace("\\'", "''")

    # 8. ON UPDATE CURRENT_TIMESTAMP (not supported same way in PG)
    sql = re.sub(r'ON UPDATE CURRENT_TIMESTAMP', '', sql, flags=re.IGNORECASE)

    # 9. Remove KEY / INDEX declarations inside CREATE TABLE
    sql = re.sub(r',\s*\n\s*KEY\s+"?\w+"?\s*\([^)]+\)', '', sql)
    sql = re.sub(r',\s*\n\s*INDEX\s+"?\w+"?\s*\([^)]+\)', '', sql)

    # 9b. Remove ALL FOREIGN KEY constraints (loop handles multiple FKs per table)
    # Note: after KEY/UNIQUE KEY removal, CONSTRAINT lines may not have a leading comma
    while re.search(r'\n\s*CONSTRAINT\s+"?\w+"?\s*FOREIGN KEY', sql, flags=re.IGNORECASE):
        # Remove any trailing comma from the previous line + the CONSTRAINT FK line
        sql = re.sub(r',?\s*\n\s*CONSTRAINT\s+"?\w+"?\s*FOREIGN KEY.+', '', sql, flags=re.IGNORECASE)
    while re.search(r'\n\s*FOREIGN KEY\s', sql, flags=re.IGNORECASE):
        sql = re.sub(r',?\s*\n\s*FOREIGN KEY.+', '', sql, flags=re.IGNORECASE)
    # Strip inline REFERENCES + any trailing ON DELETE/UPDATE actions
    sql = re.sub(r'\s+REFERENCES\s+"?\w+"?\s*\([^)]+\)(\s+ON\s+(DELETE|UPDATE)\s+\w+(\s+\w+)?)*', '', sql, flags=re.IGNORECASE)
    # Strip any remaining dangling ON DELETE / ON UPDATE tokens after closing paren
    sql = re.sub(r'\)\s+(ON\s+(DELETE|UPDATE)\s+\w+(\s+\w+)?\s*)+', ')', sql, flags=re.IGNORECASE)

    # 10. UNIQUE KEY -> UNIQUE (columns) constraint -- handles single and multi-column
    sql = re.sub(r'\bUNIQUE KEY\s+"?\w+"?\s*\(([^)]+)\)', r'UNIQUE (\1)', sql, flags=re.IGNORECASE)

    # 11. NOW() stays the same in PostgreSQL

    return sql.strip()


def run_migration():
    print("=" * 60)
    print("  PlantWise V4 -- MySQL -> PostgreSQL Migration")
    print("=" * 60)

    # Read the MySQL export
    print(f"\n[1/4] Reading MySQL export from:\n      {MYSQL_EXPORT_FILE}")
    if not os.path.exists(MYSQL_EXPORT_FILE):
        print(f"  ✕ File not found! Run mysqldump first (Step 2).")
        return

    # Detect encoding — PowerShell's > operator saves as UTF-16 on Windows
    with open(MYSQL_EXPORT_FILE, 'rb') as f:
        bom = f.read(2)
    if bom in (b'\xff\xfe', b'\xfe\xff'):
        enc = 'utf-16'
        print("      Detected UTF-16 encoding (PowerShell default)")
    else:
        enc = 'utf-8'
        print("      Detected UTF-8 encoding")
    with open(MYSQL_EXPORT_FILE, 'r', encoding=enc, errors='replace') as f:
        raw_sql = f.read()
    print(f"      Read {len(raw_sql):,} characters OK")

    # Convert
    print("\n[2/4] Converting MySQL syntax → PostgreSQL...")
    pg_sql = convert_mysql_to_postgres(raw_sql)

    # Save converted SQL for inspection
    out_path = MYSQL_EXPORT_FILE.replace("mysql_export.sql", "postgres_import.sql")
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(pg_sql)
    print(f"      Converted SQL saved to:\n      {out_path}")
    print("      ↑ Open this in VS Code to review before importing!")

    # Connect to PostgreSQL
    print(f"\n[3/4] Connecting to PostgreSQL ({PG_CONFIG['host']}:{PG_CONFIG['port']})...")
    try:
        conn = psycopg2.connect(**PG_CONFIG)
        conn.autocommit = False
        cur = conn.cursor()
        print("      Connected OK")
    except Exception as e:
        print(f"  ✕ Connection failed: {e}")
        print("      Check your PG_CONFIG password and that PostgreSQL is running.")
        return

    # Execute
    print("\n[4/4] Importing into PostgreSQL...")
    try:
        # Quote-aware SQL splitter — splits on ';' only outside single-quoted strings
        def split_sql(sql_text):
            stmts = []
            current = []
            in_quote = False
            i = 0
            while i < len(sql_text):
                ch = sql_text[i]
                if ch == "'" and (i == 0 or sql_text[i-1] != '\\'):
                    in_quote = not in_quote
                if ch == ';' and not in_quote:
                    stmt = ''.join(current).strip()
                    if stmt:
                        stmts.append(stmt)
                    current = []
                else:
                    current.append(ch)
                i += 1
            stmt = ''.join(current).strip()
            if stmt:
                stmts.append(stmt)
            return stmts

        statements = split_sql(pg_sql)

        # Disable FK checks so tables can import in any order
        cur.execute("SET session_replication_role = replica")

        ok = 0
        skipped = 0
        for i, stmt in enumerate(statements):
            try:
                cur.execute(stmt)
                ok += 1
            except Exception as e:
                conn.rollback()
                cur = conn.cursor()
                cur.execute("SET session_replication_role = replica")
                print(f"  ⚠  Statement {i+1} skipped: {str(e)[:120]}")
                print(f"     SQL: {stmt[:80]}...")
                skipped += 1
                # Continue with the rest
                cur = conn.cursor()

        conn.commit()
        print(f"\n  ✅ Import complete!")
        print(f"     Statements run:     {ok}")
        print(f"     Statements skipped: {skipped}")

    except Exception as e:
        conn.rollback()
        print(f"  ✕ Fatal error: {e}")
    finally:
        cur.close()
        conn.close()

    print("\n  Next: Open pgAdmin 4 and browse your tables to verify data.")
    print("=" * 60)


if __name__ == "__main__":
    run_migration()
