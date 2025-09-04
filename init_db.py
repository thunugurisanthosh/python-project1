import sqlite3

DB = "finance.db"

conn = sqlite3.connect(DB)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT,
    category TEXT,
    amount REAL,
    type TEXT,
    notes TEXT
);
""")

conn.commit()
conn.close()

print("✅ Database and table created successfully!")
