import psycopg2
import os

database_url = os.environ.get('DATABASE_URL')

if database_url:
    print("Connecting to Railway database using DATABASE_URL...")
    conn = psycopg2.connect(database_url, sslmode='require')
else:
    print("Connecting to local database...")
    conn = psycopg2.connect(
        host='localhost',
        user='postgres',
        password='Audi_123',
        dbname='crop_advisory',
        port=5432
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
    cur.execute(f'SELECT MAX("{column}") FROM {table}')
    max_id = cur.fetchone()[0]
    if max_id is not None:
        print(f'Updating {seq} to {max_id}')
        cur.execute(f"SELECT setval('{seq}', {max_id})")

cur.close()
conn.close()
print('All sequences updated!')
