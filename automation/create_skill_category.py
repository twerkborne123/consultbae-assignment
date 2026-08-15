import sqlite3

DB_PATH = r"C:\Users\admin\consultbae-assignment\db\consultbae.db"

conn = sqlite3.connect(DB_PATH)

conn.execute("""
CREATE TABLE IF NOT EXISTS skill_category (
    person_id INTEGER PRIMARY KEY,
    category TEXT NOT NULL,
    reasoning TEXT,
    tagged_at TEXT NOT NULL,
    FOREIGN KEY (person_id) REFERENCES person(person_id)
);
""")

conn.commit()
conn.close()

print("skill_category table created successfully.")