import sqlite3
from pathlib import Path
from datetime import datetime, timezone
import secrets

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'leadresponse.sqlite'

conn = sqlite3.connect(DB_PATH)
cur = conn.cursor()
cur.execute('''
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    domain TEXT,
    connect_token TEXT UNIQUE NOT NULL,
    site_token TEXT UNIQUE,
    site_secret TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    booking_url TEXT,
    widget_enabled INTEGER NOT NULL DEFAULT 1,
    welcome_message TEXT DEFAULT 'Hi, tell us a little about your job and we will get back to you quickly.',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
''')
now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
connect_token = 'connect_' + secrets.token_hex(8)
cur.execute(
    'INSERT INTO sites (name, connect_token, booking_url, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
    ('Demo Site', connect_token, 'https://leadresponse.co.uk/contact/', now, now)
)
conn.commit()
print(connect_token)
conn.close()
