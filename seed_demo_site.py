import os
import sqlite3
import secrets
from pathlib import Path
from datetime import datetime, timezone

try:
    import psycopg2
except Exception:
    psycopg2 = None

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SQLITE_PATH = BASE_DIR / 'leadresponse.sqlite'


def get_database_url():
    value = (os.getenv('DATABASE_URL') or '').strip()
    if value:
        return value
    return f'sqlite:///{DEFAULT_SQLITE_PATH}'


def normalized_database_url():
    url = get_database_url()
    if url.startswith('postgres://'):
        return 'postgresql://' + url[len('postgres://'):]
    return url


def using_postgres():
    return normalized_database_url().startswith('postgresql://')


def sqlite_db_path():
    url = normalized_database_url()
    if url.startswith('sqlite:///'):
        return Path(url.replace('sqlite:///', '', 1))
    return DEFAULT_SQLITE_PATH


def sql(query):
    return query.replace('?', '%s') if using_postgres() else query


def connect():
    if using_postgres():
        if psycopg2 is None:
            raise RuntimeError('psycopg2-binary is required for Postgres seeding.')
        return psycopg2.connect(normalized_database_url())
    path = sqlite_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(path)


conn = connect()
cur = conn.cursor()
cur.execute(sql('''
CREATE TABLE IF NOT EXISTS sites (
    id {} PRIMARY KEY,
    name TEXT NOT NULL,
    domain TEXT,
    connect_token TEXT UNIQUE NOT NULL,
    site_token TEXT UNIQUE,
    site_secret TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    booking_url TEXT,
    widget_enabled INTEGER NOT NULL DEFAULT 1,
    welcome_message TEXT DEFAULT 'Hi, tell us a little about your job and we will get back to you quickly.',
    widget_title TEXT DEFAULT 'LeadResponse',
    widget_button_text TEXT DEFAULT 'LeadResponse',
    widget_cta_label TEXT DEFAULT 'Book a call instead',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
'''.format('BIGSERIAL' if using_postgres() else 'INTEGER')))
now = datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
connect_token = 'connect_' + secrets.token_hex(8)
cur.execute(
    sql('INSERT INTO sites (name, connect_token, booking_url, created_at, updated_at) VALUES (?, ?, ?, ?, ?)'),
    ('Demo Site', connect_token, 'https://leadresponse.co.uk/contact/', now, now)
)
conn.commit()
print(connect_token)
conn.close()
