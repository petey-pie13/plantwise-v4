import os
import psycopg2
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, '.env'))

database_url = os.environ.get('DATABASE_URL')

if database_url:
    print("Connecting to database using DATABASE_URL...")
    conn = psycopg2.connect(database_url, sslmode='require')
else:
    print("Connecting to local database...")
    conn = psycopg2.connect(
        host=os.getenv('DB_HOST', 'localhost'),
        user=os.getenv('DB_USER', 'postgres'),
        password=os.getenv('DB_PASSWORD', ''),
        dbname=os.getenv('DB_NAME', 'crop_advisory'),
        port=int(os.getenv('DB_PORT', 5432))
    )
conn.autocommit = True
cur = conn.cursor()

# Get all tables and columns that use sequences
cur.execute('''
    SELECT 
        t.oid::regclass AS table_name,
        a.attname AS column_name,
        s.relname AS sequence_name
    FROM pg_class AS t
    JOIN pg_attribute AS a ON a.attrelid = t.oid
    JOIN pg_depend AS d ON d.refobjid = t.oid AND d.refobjsubid = a.attnum
    JOIN pg_class AS s ON s.oid = d.objid
    WHERE t.relkind = 'r' 
    AND s.relkind = 'S'
    AND d.deptype = 'a'
''')

sequences = cur.fetchall()

for table, column, seq in sequences:
    cur.execute(psycopg2.sql.SQL('SELECT MAX({}) FROM {}').format(psycopg2.sql.Identifier(column), psycopg2.sql.Identifier(str(table))))
    max_id = cur.fetchone()[0]
    if max_id is not None:
        print(f'Updating {seq} to {max_id}')
        cur.execute("SELECT setval(%s, %s)", (seq, max_id))

cur.close()
conn.close()
print('All sequences updated!')
