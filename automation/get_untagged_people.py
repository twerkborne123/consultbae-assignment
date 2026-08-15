import sqlite3
import json

DB_PATH = r"C:\Users\admin\consultbae-assignment\db\consultbae.db"

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

rows = conn.execute("""
    SELECT
        p.person_id,
        p.canonical_name,
        p.city,
        GROUP_CONCAT(DISTINCT s.skill) AS skills
    FROM person p
    LEFT JOIN skill s
        ON p.person_id = s.person_id
    LEFT JOIN skill_category sc
        ON p.person_id = sc.person_id
    WHERE sc.person_id IS NULL
    GROUP BY p.person_id
    LIMIT 10;
""").fetchall()

conn.close()

print(json.dumps([dict(row) for row in rows]))