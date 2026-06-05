from flask import Flask, request, jsonify, g, render_template_string, redirect, url_for
import os
import sqlite3
import secrets
import json
from pathlib import Path
from datetime import datetime, timezone
import smtplib
import ssl
from email.message import EmailMessage

try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except Exception:
    psycopg2 = None
    RealDictCursor = None

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_SQLITE_PATH = BASE_DIR / 'leadresponse.sqlite'

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False

ALLOWED_STATUSES = ['new', 'acknowledged', 'qualified', 'booking_sent', 'open', 'booked', 'won', 'lost', 'no_response']

WIDGET_TEXT_DEFAULTS = {
    'widget_title': 'LeadResponse',
    'widget_button_text': 'LeadResponse',
    'welcome_message': 'Hi, tell us a little about your job and we will get back to you quickly.',
    'widget_label_service': 'What do you need help with',
    'widget_placeholder_service': 'e.g. Boiler repair',
    'widget_label_postcode': 'Your postcode',
    'widget_placeholder_postcode': 'e.g. SW1A 1AA',
    'widget_label_urgency': 'How urgent is it',
    'widget_placeholder_urgency': 'Select urgency',
    'widget_label_first_name': 'First name',
    'widget_placeholder_first_name': 'Your first name',
    'widget_label_email': 'Email address',
    'widget_placeholder_email': 'you@example.com',
    'widget_label_phone': 'Phone number',
    'widget_placeholder_phone': 'Phone number',
    'widget_label_message': 'Job details',
    'widget_placeholder_message': 'Tell us what you need',
    'widget_next_text': 'Next',
    'widget_back_text': 'Back',
    'widget_submit_text': 'Send',
    'widget_success_title': 'Thanks — your enquiry has been sent.',
    'widget_success_message': 'We have captured your details and qualification answers.',
    'widget_cta_label': 'Book a call instead',
}

WIDGET_SETTINGS_FIELDS = [
    ('widget_title', 'Widget title', 'single'),
    ('widget_button_text', 'Launcher button text', 'single'),
    ('welcome_message', 'Intro copy', 'multi'),
    ('booking_url', 'CTA booking URL', 'single'),
    ('widget_cta_label', 'CTA label', 'single'),
    ('widget_label_service', 'Service label', 'single'),
    ('widget_placeholder_service', 'Service placeholder', 'single'),
    ('widget_label_postcode', 'Postcode label', 'single'),
    ('widget_placeholder_postcode', 'Postcode placeholder', 'single'),
    ('widget_label_urgency', 'Urgency label', 'single'),
    ('widget_placeholder_urgency', 'Urgency placeholder', 'single'),
    ('widget_label_first_name', 'First name label', 'single'),
    ('widget_placeholder_first_name', 'First name placeholder', 'single'),
    ('widget_label_email', 'Email label', 'single'),
    ('widget_placeholder_email', 'Email placeholder', 'single'),
    ('widget_label_phone', 'Phone label', 'single'),
    ('widget_placeholder_phone', 'Phone placeholder', 'single'),
    ('widget_label_message', 'Message label', 'single'),
    ('widget_placeholder_message', 'Message placeholder', 'single'),
    ('widget_next_text', 'Next button text', 'single'),
    ('widget_back_text', 'Back button text', 'single'),
    ('widget_submit_text', 'Submit button text', 'single'),
    ('widget_success_title', 'Success title', 'single'),
    ('widget_success_message', 'Success copy', 'multi'),
]


class DBConnection:
    def __init__(self, conn, backend):
        self.conn = conn
        self.backend = backend

    def execute(self, query, params=()):
        cursor = self.cursor()
        cursor.execute(query, params)
        return cursor

    def cursor(self):
        if self.backend == 'postgres':
            return self.conn.cursor(cursor_factory=RealDictCursor)
        return self.conn.cursor()

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()


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


def get_db_backend():
    return 'postgres' if normalized_database_url().startswith('postgresql://') else 'sqlite'


def sqlite_db_path():
    url = normalized_database_url()
    if url.startswith('sqlite:///'):
        return Path(url.replace('sqlite:///', '', 1))
    return DEFAULT_SQLITE_PATH


def sql(query):
    return query.replace('?', '%s') if get_db_backend() == 'postgres' else query


def connect_db():
    backend = get_db_backend()
    if backend == 'postgres':
        if psycopg2 is None:
            raise RuntimeError('psycopg2-binary is required when DATABASE_URL points to Postgres.')
        conn = psycopg2.connect(normalized_database_url())
        return DBConnection(conn, backend)

    db_path = sqlite_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return DBConnection(conn, backend)


def db():
    if 'db' not in g:
        g.db = connect_db()
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    conn = g.pop('db', None)
    if conn is not None:
        conn.close()


def ensure_column(conn, table_name, column_name, column_sql):
    if get_db_backend() == 'postgres':
        conn.execute(f'ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {column_sql}')
        return

    existing = {row[1] for row in conn.execute(f'PRAGMA table_info({table_name})').fetchall()}
    if column_name not in existing:
        conn.execute(f'ALTER TABLE {table_name} ADD COLUMN {column_sql}')


def init_db():
    conn = connect_db()

    if get_db_backend() == 'postgres':
        conn.execute('''
        CREATE TABLE IF NOT EXISTS sites (
            id BIGSERIAL PRIMARY KEY,
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
        conn.execute('''
        CREATE TABLE IF NOT EXISTS leads (
            id BIGSERIAL PRIMARY KEY,
            site_id BIGINT NOT NULL REFERENCES sites(id),
            source TEXT NOT NULL,
            first_name TEXT,
            email TEXT,
            phone TEXT,
            message TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL
        )
        ''')
        conn.execute('''
        CREATE TABLE IF NOT EXISTS lead_events (
            id BIGSERIAL PRIMARY KEY,
            site_id BIGINT NOT NULL REFERENCES sites(id),
            lead_id BIGINT REFERENCES leads(id),
            event_type TEXT NOT NULL,
            payload_json TEXT,
            created_at TEXT NOT NULL
        )
        ''')
    else:
        conn.execute('''
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
        conn.execute('''
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL,
            source TEXT NOT NULL,
            first_name TEXT,
            email TEXT,
            phone TEXT,
            message TEXT,
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL,
            FOREIGN KEY(site_id) REFERENCES sites(id)
        )
        ''')
        conn.execute('''
        CREATE TABLE IF NOT EXISTS lead_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL,
            lead_id INTEGER,
            event_type TEXT NOT NULL,
            payload_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(site_id) REFERENCES sites(id),
            FOREIGN KEY(lead_id) REFERENCES leads(id)
        )
        ''')

    ensure_column(conn, 'leads', 'service_type', 'service_type TEXT')
    ensure_column(conn, 'leads', 'postcode', 'postcode TEXT')
    ensure_column(conn, 'leads', 'urgency', 'urgency TEXT')
    ensure_column(conn, 'leads', 'notes', "notes TEXT DEFAULT ''")

    site_columns = {
        'widget_title': "widget_title TEXT DEFAULT 'LeadResponse'",
        'widget_button_text': "widget_button_text TEXT DEFAULT 'LeadResponse'",
        'widget_label_service': "widget_label_service TEXT DEFAULT 'What do you need help with'",
        'widget_placeholder_service': "widget_placeholder_service TEXT DEFAULT 'e.g. Boiler repair'",
        'widget_label_postcode': "widget_label_postcode TEXT DEFAULT 'Your postcode'",
        'widget_placeholder_postcode': "widget_placeholder_postcode TEXT DEFAULT 'e.g. SW1A 1AA'",
        'widget_label_urgency': "widget_label_urgency TEXT DEFAULT 'How urgent is it'",
        'widget_placeholder_urgency': "widget_placeholder_urgency TEXT DEFAULT 'Select urgency'",
        'widget_label_first_name': "widget_label_first_name TEXT DEFAULT 'First name'",
        'widget_placeholder_first_name': "widget_placeholder_first_name TEXT DEFAULT 'Your first name'",
        'widget_label_email': "widget_label_email TEXT DEFAULT 'Email address'",
        'widget_placeholder_email': "widget_placeholder_email TEXT DEFAULT 'you@example.com'",
        'widget_label_phone': "widget_label_phone TEXT DEFAULT 'Phone number'",
        'widget_placeholder_phone': "widget_placeholder_phone TEXT DEFAULT 'Phone number'",
        'widget_label_message': "widget_label_message TEXT DEFAULT 'Job details'",
        'widget_placeholder_message': "widget_placeholder_message TEXT DEFAULT 'Tell us what you need'",
        'widget_next_text': "widget_next_text TEXT DEFAULT 'Next'",
        'widget_back_text': "widget_back_text TEXT DEFAULT 'Back'",
        'widget_submit_text': "widget_submit_text TEXT DEFAULT 'Send'",
        'widget_success_title': "widget_success_title TEXT DEFAULT 'Thanks — your enquiry has been sent.'",
        'widget_success_message': "widget_success_message TEXT DEFAULT 'We have captured your details and qualification answers.'",
        'widget_cta_label': "widget_cta_label TEXT DEFAULT 'Book a call instead'",
        'auto_ack_enabled': "auto_ack_enabled INTEGER DEFAULT 1",
        'follow_up_enabled': "follow_up_enabled INTEGER DEFAULT 1",
        'followup_1_hours': "followup_1_hours INTEGER DEFAULT 2",
        'followup_2_hours': "followup_2_hours INTEGER DEFAULT 24",
        'followup_3_hours': "followup_3_hours INTEGER DEFAULT 72",
    }
    for column_name, column_sql in site_columns.items():
        ensure_column(conn, 'sites', column_name, column_sql)

    conn.commit()
    conn.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def row_to_dict(row):
    if row is None:
        return None
    if isinstance(row, dict):
        return dict(row)
    try:
        return {key: row[key] for key in row.keys()}
    except Exception:
        return dict(row)


def get_site_by_token(site_token):
    return db().execute(sql('SELECT * FROM sites WHERE site_token = ?'), (site_token,)).fetchone()


def get_default_site():
    return db().execute(
        sql("SELECT * FROM sites WHERE status = 'connected' ORDER BY id ASC LIMIT 1")
    ).fetchone() or db().execute(sql('SELECT * FROM sites ORDER BY id ASC LIMIT 1')).fetchone()


def get_widget_settings(site):
    site_data = row_to_dict(site) or {}
    settings = {}
    for key, default in WIDGET_TEXT_DEFAULTS.items():
        value = site_data.get(key)
        settings[key] = value if value not in (None, '') else default
    settings['booking_url'] = site_data.get('booking_url') or ''
    settings['widget_enabled'] = bool(site_data.get('widget_enabled', 1))
    return settings


def fmt_dt(value):
    if not value:
        return '—'
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt.strftime('%d %b %Y, %H:%M UTC')
    except Exception:
        return value


def parse_payload(payload_json):
    if not payload_json:
        return {}
    try:
        return json.loads(payload_json)
    except Exception:
        return {'raw': payload_json}


def label_urgency(value):
    mapping = {
        'asap': 'ASAP',
        'this_week': 'This week',
        'planning': 'Just planning'
    }
    return mapping.get((value or '').strip(), value or '—')


def safe_status(value):
    value = (value or '').strip().lower()
    return value if value in ALLOWED_STATUSES else 'new'


def env_flag(name, default=False):
    value = (os.getenv(name) or '').strip().lower()
    if value == '':
        return default
    return value in ('1', 'true', 'yes', 'on')


def parse_iso_ts(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except Exception:
        return None


def hours_since(value):
    dt = parse_iso_ts(value)
    if dt is None:
        return 0
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 3600.0)


def site_int(site, key, default):
    try:
        value = row_to_dict(site).get(key)
    except Exception:
        value = None
    try:
        return int(value) if value is not None else int(default)
    except Exception:
        return int(default)


def create_lead_event(conn, site_id, lead_id, event_type, payload, created_at=None):
    created_at = created_at or now_iso()
    conn.execute(
        sql('INSERT INTO lead_events (site_id, lead_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)'),
        (site_id, lead_id, event_type, json.dumps(payload), created_at)
    )


def latest_event_for_lead(lead_id, event_type):
    row = db().execute(
        sql('SELECT * FROM lead_events WHERE lead_id = ? AND event_type = ? ORDER BY id DESC LIMIT 1'),
        (lead_id, event_type)
    ).fetchone()
    return row_to_dict(row)


def update_lead_status(conn, lead_id, status=None, notes=None):
    if status is not None and notes is not None:
        conn.execute(sql('UPDATE leads SET status = ?, notes = ? WHERE id = ?'), (status, notes, lead_id))
    elif status is not None:
        conn.execute(sql('UPDATE leads SET status = ? WHERE id = ?'), (status, lead_id))
    elif notes is not None:
        conn.execute(sql('UPDATE leads SET notes = ? WHERE id = ?'), (notes, lead_id))


def send_email_message(to_email, subject, body_text):
    to_email = (to_email or '').strip()
    if not to_email:
        return {'ok': False, 'mode': 'skipped', 'error': 'Missing recipient email.'}

    host = (os.getenv('SMTP_HOST') or '').strip()
    port = int((os.getenv('SMTP_PORT') or '587').strip())
    username = (os.getenv('SMTP_USERNAME') or '').strip()
    password = os.getenv('SMTP_PASSWORD') or ''
    from_email = (os.getenv('MAIL_FROM') or username or 'no-reply@leadresponse.local').strip()
    from_name = (os.getenv('MAIL_FROM_NAME') or 'LeadResponse').strip()

    if not host:
        return {'ok': True, 'mode': 'simulation', 'to': to_email, 'subject': subject}

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = f'{from_name} <{from_email}>' if from_name else from_email
    msg['To'] = to_email
    msg.set_content(body_text)

    use_ssl = env_flag('SMTP_USE_SSL', False)
    use_tls = env_flag('SMTP_USE_TLS', not use_ssl)

    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=20, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=20)
        with server:
            if not use_ssl and use_tls:
                server.starttls(context=ssl.create_default_context())
            if username:
                server.login(username, password)
            server.send_message(msg)
        return {'ok': True, 'mode': 'smtp', 'to': to_email, 'subject': subject}
    except Exception as exc:
        return {'ok': False, 'mode': 'error', 'to': to_email, 'subject': subject, 'error': str(exc)}


def build_ack_email(site, lead):
    site_name = (site.get('name') or 'LeadResponse') if isinstance(site, dict) else (site['name'] or 'LeadResponse')
    lead_name = (lead.get('first_name') or 'there').strip() or 'there'
    booking_url = (site.get('booking_url') or '') if isinstance(site, dict) else (site['booking_url'] or '')
    subject = f'Thanks {lead_name} — we received your enquiry'
    body = [
        f'Hi {lead_name},',
        '',
        f'Thanks for contacting {site_name}. We have received your enquiry and the details below have been captured:',
        '',
        f"Service: {(lead.get('service_type') or '').strip() or 'Not provided'}",
        f"Postcode: {(lead.get('postcode') or '').strip() or 'Not provided'}",
        f"Urgency: {(lead.get('urgency') or '').strip() or 'Not provided'}",
        '',
        'We will review this and follow up with the next step shortly.'
    ]
    if booking_url:
        body.extend(['', f'If you would prefer, you can book the next step now: {booking_url}'])
    body.extend(['', 'Regards,', site_name])
    return subject, '\n'.join(body)


def build_follow_up_email(site, lead, step_number):
    site_name = (site.get('name') or 'LeadResponse') if isinstance(site, dict) else (site['name'] or 'LeadResponse')
    lead_name = (lead.get('first_name') or 'there').strip() or 'there'
    booking_url = (site.get('booking_url') or '') if isinstance(site, dict) else (site['booking_url'] or '')
    subjects = {
        1: f'{lead_name}, just checking on your enquiry',
        2: f'Quick follow-up on your enquiry',
        3: f'Final follow-up before we close your enquiry',
    }
    intros = {
        1: 'We wanted to follow up to make sure you still need help with this job.',
        2: 'We are following up again in case you still want to move this forward.',
        3: 'This is our final automated follow-up before we mark the enquiry as no response.',
    }
    body = [
        f'Hi {lead_name},',
        '',
        intros.get(step_number, 'We are following up on your enquiry.'),
        '',
        f"Service: {(lead.get('service_type') or '').strip() or 'Not provided'}",
        f"Postcode: {(lead.get('postcode') or '').strip() or 'Not provided'}",
        f"Urgency: {(lead.get('urgency') or '').strip() or 'Not provided'}",
    ]
    if booking_url:
        body.extend(['', f'If you are ready, book the next step here: {booking_url}'])
    body.extend(['', 'Reply to this email if you would like us to help.', '', 'Regards,', site_name])
    return subjects.get(step_number, 'LeadResponse follow-up'), '\n'.join(body)


BASE_HTML = '''
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ title }}</title>
  <style>
    :root {
      --bg: #f5f8fc;
      --panel: #ffffff;
      --ink: #17233a;
      --muted: #62708a;
      --line: #dfe7f3;
      --blue: #2575fc;
      --blue-dark: #1b5ed1;
      --blue-soft: #eaf2ff;
      --green: #14b86a;
      --amber: #ffb020;
      --red: #e05555;
      --shadow: 0 12px 32px rgba(17, 36, 77, 0.08);
      --radius: 18px;
      --max: 1180px;
    }
    * { box-sizing: border-box; }
    body { margin:0; font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:linear-gradient(180deg,#f8fbff 0%,#f4f7fb 100%); color:var(--ink); }
    a { color:var(--blue); text-decoration:none; }
    .shell { max-width:var(--max); margin:0 auto; padding:28px 20px 40px; }
    .topbar { display:flex; align-items:center; justify-content:space-between; gap:20px; margin-bottom:22px; flex-wrap:wrap; }
    .brand { display:flex; align-items:center; gap:12px; font-weight:800; letter-spacing:-0.02em; color:var(--ink); }
    .brand-mark { width:42px; height:42px; border-radius:14px; background:linear-gradient(135deg,var(--blue) 0%,#5ea1ff 100%); display:inline-flex; align-items:center; justify-content:center; color:#fff; box-shadow:0 10px 24px rgba(37,117,252,0.28); font-size:18px; font-weight:900; }
    .nav { display:flex; align-items:center; gap:10px; flex-wrap:wrap; }
    .nav a { background:#fff; border:1px solid var(--line); color:var(--ink); padding:10px 14px; border-radius:999px; font-weight:600; transition:.18s ease; }
    .nav a:hover,.nav a.active { transform:translateY(-1px); border-color:rgba(37,117,252,.35); background:var(--blue-soft); color:var(--blue-dark); }
    .hero { background:linear-gradient(135deg,#17233a 0%,#233a68 100%); color:#fff; border-radius:26px; padding:28px; box-shadow:var(--shadow); position:relative; overflow:hidden; margin-bottom:24px; }
    .hero:before { content:''; position:absolute; inset:auto -60px -60px auto; width:220px; height:220px; background:radial-gradient(circle,rgba(94,161,255,.35),rgba(94,161,255,0)); }
    .eyebrow { display:inline-flex; align-items:center; gap:8px; background:rgba(255,255,255,.1); border:1px solid rgba(255,255,255,.14); color:#dbe7ff; border-radius:999px; padding:8px 12px; font-size:13px; font-weight:700; margin-bottom:14px; }
    h1,h2,h3 { margin:0 0 10px; letter-spacing:-0.03em; }
    h1 { font-size:clamp(30px,4vw,44px); line-height:1.05; }
    h2 { font-size:clamp(24px,3vw,32px); }
    h3 { font-size:18px; }
    p { margin:0; color:var(--muted); line-height:1.65; }
    .hero p { color:rgba(255,255,255,.84); max-width:760px; }
    .grid { display:grid; gap:18px; }
    .grid.stats { grid-template-columns:repeat(4,minmax(0,1fr)); margin-top:22px; }
    .stat { background:rgba(255,255,255,.08); border:1px solid rgba(255,255,255,.10); border-radius:18px; padding:18px; transition:.18s ease; }
    .stat:hover { transform:translateY(-2px); background:rgba(255,255,255,.11); }
    .stat .label { color:rgba(255,255,255,.75); font-size:13px; font-weight:700; }
    .stat .value { color:#fff; font-size:28px; font-weight:800; margin-top:8px; }
    .stack { display:grid; gap:18px; }
    .panel { background:var(--panel); border:1px solid var(--line); border-radius:var(--radius); box-shadow:var(--shadow); padding:22px; }
    .panel-header { display:flex; align-items:flex-start; justify-content:space-between; gap:14px; margin-bottom:18px; flex-wrap:wrap; }
    .meta, .filters { display:flex; flex-wrap:wrap; gap:10px; margin-top:14px; }
    .pill { display:inline-flex; align-items:center; gap:8px; padding:9px 12px; border-radius:999px; font-size:13px; font-weight:700; background:var(--blue-soft); color:var(--blue-dark); border:1px solid rgba(37,117,252,.16); }
    .pill.neutral { background:#f6f8fb; color:#41506d; border-color:var(--line); }
    .pill.success { background:#ebfff4; color:#09814a; border-color:#ccefdc; }
    .pill.filter-active { background:#1b5ed1; color:#fff; border-color:#1b5ed1; }
    .kpis { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }
    .kpi { padding:16px; border-radius:16px; background:#f9fbff; border:1px solid var(--line); transition:.18s ease; }
    .kpi:hover { transform:translateY(-2px); box-shadow:0 8px 20px rgba(17,36,77,.06); }
    .kpi small { display:block; font-size:12px; color:var(--muted); font-weight:700; text-transform:uppercase; letter-spacing:.05em; }
    .kpi strong { display:block; font-size:24px; margin-top:7px; }
    table { width:100%; border-collapse:collapse; }
    thead th { text-align:left; font-size:12px; text-transform:uppercase; letter-spacing:.05em; color:var(--muted); padding:12px 10px; border-bottom:1px solid var(--line); }
    tbody td { padding:14px 10px; border-bottom:1px solid #edf2f8; vertical-align:top; color:#24314b; }
    tbody tr { transition:.16s ease; }
    tbody tr:hover { background:#fafcff; }
    .lead-name { font-weight:800; color:var(--ink); margin-bottom:4px; }
    .muted { color:var(--muted); }
    .badge { display:inline-flex; align-items:center; padding:6px 10px; border-radius:999px; font-weight:700; font-size:12px; line-height:1; border:1px solid transparent; text-transform:capitalize; }
    .badge-new { background:#ebfff4; color:#0c8b51; border-color:#ccefdc; }
    .badge-open { background:#fff5df; color:#996600; border-color:#f5dfb0; }
    .badge-won { background:#eaf2ff; color:var(--blue-dark); border-color:#d5e3ff; }
    .badge-lost { background:#fff0f0; color:#b33a3a; border-color:#f4d1d1; }
    .badge-acknowledged { background:#eef6ff; color:#2456c7; border-color:#d6e4ff; }
    .badge-qualified { background:#f0fdf8; color:#0a8f5b; border-color:#cdeedd; }
    .badge-booking_sent { background:#f5f0ff; color:#6e45c7; border-color:#e3d8ff; }
    .badge-booked { background:#e8fff5; color:#0b8b4d; border-color:#c9efd9; }
    .badge-no_response { background:#f7f7f8; color:#5b6678; border-color:#dde2ea; }
    .action { display:inline-flex; align-items:center; justify-content:center; padding:10px 14px; border-radius:12px; border:1px solid rgba(37,117,252,.18); background:var(--blue-soft); color:var(--blue-dark); font-weight:700; transition:.16s ease; }
    .action:hover { transform:translateY(-1px); background:#dce9ff; }
    .empty { border:2px dashed var(--line); border-radius:18px; padding:28px; background:#fbfdff; text-align:center; }
    .detail-grid { display:grid; grid-template-columns:1.05fr .95fr; gap:18px; }
    .info-list { display:grid; gap:12px; }
    .info-item { border:1px solid var(--line); border-radius:14px; padding:14px; background:#fbfdff; }
    .info-item .label { font-size:12px; color:var(--muted); text-transform:uppercase; letter-spacing:.05em; font-weight:800; margin-bottom:7px; }
    .message-box, pre, textarea, select { font:inherit; }
    .message-box, pre { background:#f8fbff; border:1px solid var(--line); border-radius:16px; padding:18px; color:#22304b; white-space:pre-wrap; word-break:break-word; }
    pre { margin:0; font-size:13px; line-height:1.55; overflow:auto; }
    .admin-form label { display:block; margin:0 0 12px; font-size:13px; font-weight:700; color:#33425d; }
    .admin-form select, .admin-form textarea { width:100%; padding:12px 13px; border:1px solid #dbe5f0; border-radius:12px; background:#fff; }
    .admin-form textarea { min-height:140px; resize:vertical; }
    .admin-form button { border:0; border-radius:12px; padding:12px 16px; font-weight:700; cursor:pointer; background:#2575fc; color:#fff; }
    .notice-success { background:#ebfff4; border:1px solid #ccefdc; color:#0c8b51; padding:12px 14px; border-radius:14px; margin-bottom:14px; font-weight:700; }
    .footer-note { margin-top:18px; font-size:13px; color:var(--muted); text-align:center; }
    @media (max-width:960px){ .grid.stats,.kpis,.detail-grid{grid-template-columns:1fr;} }
    @media (max-width:720px){ .shell{padding:18px 14px 28px;} .hero,.panel{padding:18px;} table,thead,tbody,th,td,tr{display:block;} thead{display:none;} tbody tr{border:1px solid var(--line); border-radius:16px; padding:10px; margin-bottom:12px; background:#fff;} tbody td{border:0; padding:7px 6px;} tbody td:before{content:attr(data-label); display:block; color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.05em; font-weight:800; margin-bottom:6px;} }
  </style>
</head>
<body>
  <div class="shell">
    <div class="topbar">
      <div class="brand"><span class="brand-mark">LR</span> LeadResponse SaaS</div>
      <div class="nav">
        <a href="{{ dashboard_url }}" class="{% if active == 'dashboard' %}active{% endif %}">Lead Inbox</a>
        {% if current_site %}<a href="{{ api_url }}">JSON API</a>{% endif %}
      </div>
    </div>
    {{ body|safe }}
    <div class="footer-note">LeadResponse v0.7.0 test dashboard · Render Postgres ready</div>
  </div>
</body>
</html>
'''


def render_page(body, title='LeadResponse Dashboard', active='dashboard', current_site=None):
    dashboard_url = url_for('dashboard')
    api_url = url_for('list_leads') + (f'?site_token={current_site["site_token"]}' if current_site and current_site['site_token'] else '')
    return render_template_string(
        BASE_HTML,
        title=title,
        body=body,
        active=active,
        current_site=current_site,
        dashboard_url=dashboard_url + (f'?site_token={current_site["site_token"]}' if current_site and current_site['site_token'] else ''),
        api_url=api_url,
    )


@app.after_request
def add_cors(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return resp


@app.route('/')
def home():
    site = get_default_site()
    if site and site['site_token']:
        return redirect(url_for('dashboard', site_token=site['site_token']))
    return redirect(url_for('dashboard'))


@app.route('/dashboard')
def dashboard():
    site_token = (request.args.get('site_token') or '').strip()
    status_filter = (request.args.get('status') or 'all').strip().lower()
    site = get_site_by_token(site_token) if site_token else get_default_site()

    if site and site['site_token'] and not site_token:
        return redirect(url_for('dashboard', site_token=site['site_token']))

    sites = db().execute(sql('SELECT * FROM sites ORDER BY id ASC')).fetchall()

    if not site:
        body = render_template_string('''
        <section class="hero">
          <div class="eyebrow">Dashboard not connected yet</div>
          <h1>No connected site found</h1>
          <p>Create or connect a site first, then reload this dashboard using its site token.</p>
        </section>
        <section class="panel">
          <h2>Available site records</h2>
          <div class="empty" style="margin-top:16px;">
            {% if sites %}
              <p>Sites exist in the database but none are fully connected yet.</p>
              <div class="meta" style="justify-content:center; margin-top:14px;">
                {% for s in sites %}
                  <span class="pill neutral">#{{ s['id'] }} {{ s['name'] }} · {{ s['status'] }}</span>
                {% endfor %}
              </div>
            {% else %}
              <p>No site records found yet. Use the plugin connect flow first.</p>
            {% endif %}
          </div>
        </section>
        ''', sites=sites)
        return render_page(body, current_site=None)

    params = [site['id']]
    query = 'SELECT * FROM leads WHERE site_id = ?'
    if status_filter in ALLOWED_STATUSES:
        query += ' AND status = ?'
        params.append(status_filter)
    query += ' ORDER BY id DESC LIMIT 100'
    lead_rows = db().execute(sql(query), params).fetchall()

    total_leads = db().execute(sql('SELECT COUNT(*) AS c FROM leads WHERE site_id = ?'), (site['id'],)).fetchone()['c']
    new_leads = db().execute(sql("SELECT COUNT(*) AS c FROM leads WHERE site_id = ? AND status = 'new'"), (site['id'],)).fetchone()['c']
    open_leads = db().execute(sql("SELECT COUNT(*) AS c FROM leads WHERE site_id = ? AND status = 'open'"), (site['id'],)).fetchone()['c']
    won_leads = db().execute(sql("SELECT COUNT(*) AS c FROM leads WHERE site_id = ? AND status = 'won'"), (site['id'],)).fetchone()['c']
    lost_leads = db().execute(sql("SELECT COUNT(*) AS c FROM leads WHERE site_id = ? AND status = 'lost'"), (site['id'],)).fetchone()['c']
    latest = lead_rows[0] if lead_rows else None
    widget_settings = get_widget_settings(site)
    widget_saved = (request.args.get('widget_saved') or '') == '1'

    body = render_template_string('''
    <section class="hero">
      <div class="eyebrow">Lead inbox · Site #{{ site['id'] }}</div>
      <h1>{{ site['name'] or 'Lead Inbox' }}</h1>
      <p>Review new submissions, update statuses, add notes, and confirm that your WordPress plugin is sending data into the SaaS backend correctly.</p>
      <div class="grid stats">
        <div class="stat"><div class="label">Total Leads</div><div class="value">{{ total_leads }}</div></div>
        <div class="stat"><div class="label">New</div><div class="value">{{ new_leads }}</div></div>
        <div class="stat"><div class="label">Open</div><div class="value">{{ open_leads }}</div></div>
        <div class="stat"><div class="label">Won</div><div class="value">{{ won_leads }}</div></div>
      </div>
    </section>

    {% if widget_saved %}
      <div class="notice-success">Widget settings updated successfully.</div>
    {% endif %}

    <div class="stack">
      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>Connected site</h2>
            <p>Use this block to confirm the current site token, booking URL, domain and live status.</p>
          </div>
        </div>
        <div class="meta">
          <span class="pill">Status: {{ site['status'] }}</span>
          <span class="pill neutral">Domain: {{ site['domain'] or 'Not provided' }}</span>
          <span class="pill neutral">Site token: {{ site['site_token'] or 'Missing' }}</span>
          <span class="pill neutral">Booking URL: {{ site['booking_url'] or 'Not set' }}</span>
          <span class="pill neutral">Lost: {{ lost_leads }}</span>
        </div>
      </section>

      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>Lead inbox</h2>
            <p>Latest 100 leads received for this connected site.</p>
            <div class="filters">
              <a class="pill {% if status_filter == 'all' %}filter-active{% else %}neutral{% endif %}" href="{{ url_for('dashboard', site_token=site['site_token'], status='all') }}">All</a>
              <a class="pill {% if status_filter == 'new' %}filter-active{% else %}neutral{% endif %}" href="{{ url_for('dashboard', site_token=site['site_token'], status='new') }}">New</a>
              <a class="pill {% if status_filter == 'open' %}filter-active{% else %}neutral{% endif %}" href="{{ url_for('dashboard', site_token=site['site_token'], status='open') }}">Open</a>
              <a class="pill {% if status_filter == 'won' %}filter-active{% else %}neutral{% endif %}" href="{{ url_for('dashboard', site_token=site['site_token'], status='won') }}">Won</a>
              <a class="pill {% if status_filter == 'lost' %}filter-active{% else %}neutral{% endif %}" href="{{ url_for('dashboard', site_token=site['site_token'], status='lost') }}">Lost</a>
            </div>
          </div>
          {% if latest %}
          <a class="action" href="{{ url_for('lead_detail_page', lead_id=latest['id'], site_token=site['site_token']) }}">Open latest lead</a>
          {% endif %}
        </div>

        {% if lead_rows %}
        <table>
          <thead>
            <tr>
              <th>Lead</th>
              <th>Qualification</th>
              <th>Message</th>
              <th>Status</th>
              <th>Notes</th>
              <th>Received</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {% for lead in lead_rows %}
            <tr>
              <td data-label="Lead">
                <div class="lead-name">{{ lead['first_name'] or 'Unknown lead' }}</div>
                <div class="muted">#{{ lead['id'] }} · {{ lead['source'] }}</div>
                <div class="muted">{{ lead['email'] or '—' }}</div>
                <div class="muted">{{ lead['phone'] or '—' }}</div>
              </td>
              <td data-label="Qualification">
                <div><strong>Service:</strong> {{ lead['service_type'] or '—' }}</div>
                <div class="muted"><strong>Postcode:</strong> {{ lead['postcode'] or '—' }}</div>
                <div class="muted"><strong>Urgency:</strong> {{ label_urgency(lead['urgency']) }}</div>
              </td>
              <td data-label="Message">{{ (lead['message'] or '—')[:100] }}{% if lead['message'] and lead['message']|length > 100 %}…{% endif %}</td>
              <td data-label="Status"><span class="badge badge-{{ lead['status'] if lead['status'] in ['new', 'acknowledged', 'qualified', 'booking_sent', 'open', 'booked', 'won', 'lost', 'no_response'] else 'open' }}">{{ lead['status'].replace('_', ' ') }}</span></td>
              <td data-label="Notes">{{ (lead['notes'] or '—')[:70] }}{% if lead['notes'] and lead['notes']|length > 70 %}…{% endif %}</td>
              <td data-label="Received">{{ fmt_dt(lead['created_at']) }}</td>
              <td data-label="Open"><a class="action" href="{{ url_for('lead_detail_page', lead_id=lead['id'], site_token=site['site_token']) }}">View</a></td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
        {% else %}
        <div class="empty">
          <h3>No leads in this filter</h3>
          <p>Try a different status filter or submit a new lead through the widget.</p>
        </div>
        {% endif %}
      </section>

      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>Other site records</h2>
            <p>Quick switch between sites stored in the same database.</p>
          </div>
        </div>
        <div class="meta">
          {% for s in sites %}
            {% if s['site_token'] %}
              <a class="pill {% if s['id'] == site['id'] %}success{% else %}neutral{% endif %}" href="{{ url_for('dashboard', site_token=s['site_token']) }}">#{{ s['id'] }} {{ s['name'] }}</a>
            {% else %}
              <span class="pill neutral">#{{ s['id'] }} {{ s['name'] }} · no token</span>
            {% endif %}
          {% endfor %}
        </div>
      </section>
    </div>
    ''', site=site, lead_rows=lead_rows, total_leads=total_leads, new_leads=new_leads, open_leads=open_leads, won_leads=won_leads, lost_leads=lost_leads, latest=latest, sites=sites, fmt_dt=fmt_dt, label_urgency=label_urgency, status_filter=status_filter, widget_settings=widget_settings, widget_fields=WIDGET_SETTINGS_FIELDS, widget_saved=widget_saved)

    return render_page(body, title='LeadResponse Lead Inbox', current_site=site)


@app.route('/dashboard/leads/<int:lead_id>')
def lead_detail_page(lead_id):
    site_token = (request.args.get('site_token') or '').strip()
    current_site = get_site_by_token(site_token) if site_token else get_default_site()

    lead = db().execute(sql('SELECT * FROM leads WHERE id = ?'), (lead_id,)).fetchone()
    if not lead:
        body = render_template_string('''
        <section class="panel">
          <h2>Lead not found</h2>
          <p>The lead you requested does not exist.</p>
          <div class="meta" style="margin-top:14px;"><a class="action" href="{{ back_url }}">Back to inbox</a></div>
        </section>
        ''', back_url=url_for('dashboard', site_token=current_site['site_token']) if current_site and current_site['site_token'] else url_for('dashboard'))
        return render_page(body, title='Lead not found', current_site=current_site)

    site = db().execute(sql('SELECT * FROM sites WHERE id = ?'), (lead['site_id'],)).fetchone()
    events = db().execute(sql('SELECT * FROM lead_events WHERE lead_id = ? ORDER BY id DESC'), (lead_id,)).fetchall()
    updated_notice = (request.args.get('updated') or '') == '1'

    body = render_template_string('''
    <section class="hero">
      <div class="eyebrow">Lead detail · #{{ lead['id'] }}</div>
      <h1>{{ lead['first_name'] or 'Unknown lead' }}</h1>
      <p>Review captured contact details, qualification answers, notes and the raw event payload for development testing and admin handling.</p>
      <div class="meta">
        <span class="pill">Status: {{ lead['status'] }}</span>
        <span class="pill neutral">Source: {{ lead['source'] }}</span>
        <span class="pill neutral">Received: {{ fmt_dt(lead['created_at']) }}</span>
      </div>
    </section>

    {% if updated_notice %}
      <div class="notice-success">Lead updated successfully.</div>
    {% endif %}

    <div class="detail-grid">
      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>Lead details</h2>
            <p>Main contact information captured by the widget.</p>
          </div>
          <a class="action" href="{{ url_for('dashboard', site_token=(site['site_token'] if site else current_site['site_token'] if current_site else '')) }}">Back to inbox</a>
        </div>
        <div class="info-list">
          <div class="info-item"><div class="label">First name</div><div>{{ lead['first_name'] or '—' }}</div></div>
          <div class="info-item"><div class="label">Email</div><div>{{ lead['email'] or '—' }}</div></div>
          <div class="info-item"><div class="label">Phone</div><div>{{ lead['phone'] or '—' }}</div></div>
          <div class="info-item"><div class="label">Service type</div><div>{{ lead['service_type'] or '—' }}</div></div>
          <div class="info-item"><div class="label">Postcode</div><div>{{ lead['postcode'] or '—' }}</div></div>
          <div class="info-item"><div class="label">Urgency</div><div>{{ label_urgency(lead['urgency']) }}</div></div>
          <div class="info-item"><div class="label">Message</div><div class="message-box">{{ lead['message'] or '—' }}</div></div>
        </div>
      </section>

      <div class="stack">
        <section class="panel">
          <div class="panel-header">
            <div>
              <h2>Lead admin</h2>
              <p>Update the lead status and add internal notes.</p>
            </div>
          </div>
          <form method="post" action="{{ url_for('lead_update_page', lead_id=lead['id']) }}?site_token={{ site['site_token'] if site else current_site['site_token'] if current_site else '' }}" class="admin-form">
            <label>
              Status
              <select name="status">
                {% for value in ['new', 'acknowledged', 'qualified', 'booking_sent', 'open', 'booked', 'won', 'lost', 'no_response'] %}
                  <option value="{{ value }}" {% if lead['status'] == value %}selected{% endif %}>{{ value.title() }}</option>
                {% endfor %}
              </select>
            </label>
            <label>
              Notes
              <textarea name="notes" placeholder="Add internal notes for this lead...">{{ lead['notes'] or '' }}</textarea>
            </label>
            <button type="submit">Save lead</button>
          </form>
        </section>

        <section class="panel">
          <div class="panel-header">
            <div>
              <h2>Lead context</h2>
              <p>Which site record this lead belongs to.</p>
            </div>
          </div>
          <div class="kpis">
            <div class="kpi"><small>Site ID</small><strong>{{ site['id'] if site else '—' }}</strong></div>
            <div class="kpi"><small>Site Name</small><strong style="font-size:18px;">{{ site['name'] if site else '—' }}</strong></div>
            <div class="kpi"><small>Site Status</small><strong style="font-size:18px;">{{ site['status'] if site else '—' }}</strong></div>
          </div>
          <div class="meta" style="margin-top:14px;">
            <span class="pill neutral">Domain: {{ site['domain'] if site and site['domain'] else 'Not provided' }}</span>
            <span class="pill neutral">Booking URL: {{ site['booking_url'] if site and site['booking_url'] else 'Not set' }}</span>
          </div>
        </section>

        <section class="panel">
          <div class="panel-header">
            <div>
              <h2>Lead events</h2>
              <p>Development log for this lead.</p>
            </div>
          </div>
          {% if events %}
            {% for event in events %}
              <div class="info-item" style="margin-bottom:12px;">
                <div class="label">{{ event['event_type'] }} · {{ fmt_dt(event['created_at']) }}</div>
                <pre>{{ parse_payload(event['payload_json']) | tojson(indent=2) }}</pre>
              </div>
            {% endfor %}
          {% else %}
            <div class="empty"><p>No events recorded for this lead yet.</p></div>
          {% endif %}
        </section>
      </div>
    </div>
    ''', lead=lead, site=site, current_site=current_site, events=events, fmt_dt=fmt_dt, parse_payload=parse_payload, label_urgency=label_urgency, updated_notice=updated_notice)

    return render_page(body, title=f"Lead #{lead['id']} · LeadResponse", current_site=site or current_site)


@app.route('/dashboard/leads/<int:lead_id>/save', methods=['POST'])
def lead_update_page(lead_id):
    site_token = (request.args.get('site_token') or '').strip()
    site = get_site_by_token(site_token)
    if not site:
        return redirect(url_for('dashboard'))

    lead = db().execute(sql('SELECT * FROM leads WHERE id = ? AND site_id = ?'), (lead_id, site['id'])).fetchone()
    if not lead:
        return redirect(url_for('dashboard', site_token=site['site_token']))

    status = safe_status(request.form.get('status'))
    notes = (request.form.get('notes') or '').strip()
    updated_at = now_iso()

    conn = db()
    conn.execute(sql('UPDATE leads SET status = ?, notes = ? WHERE id = ?'), (status, notes, lead_id))
    conn.execute(
        sql('INSERT INTO lead_events (site_id, lead_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)'),
        (site['id'], lead_id, 'lead_updated', json.dumps({'status': status, 'notes': notes}), updated_at)
    )
    conn.commit()

    return redirect(url_for('lead_detail_page', lead_id=lead_id, site_token=site['site_token'], updated='1'))


@app.route('/dashboard/sites/<int:site_id>/widget-settings/save', methods=['POST'])
def save_widget_settings(site_id):
    site_token = (request.args.get('site_token') or '').strip()
    site = get_site_by_token(site_token) if site_token else None
    if not site or site['id'] != site_id:
        site = db().execute(sql('SELECT * FROM sites WHERE id = ?'), (site_id,)).fetchone()
    if not site:
        return redirect(url_for('dashboard'))

    site_data = row_to_dict(site) or {}
    values = []
    assignments = []
    for key in WIDGET_TEXT_DEFAULTS.keys():
        assignments.append(f"{key} = ?")
        values.append((request.form.get(key) or '').strip() or WIDGET_TEXT_DEFAULTS[key])

    booking_url = (request.form.get('booking_url') or '').strip()
    updated_at = now_iso()
    values.extend([booking_url, updated_at, site_id])

    conn = db()
    conn.execute(
        sql(f"UPDATE sites SET {', '.join(assignments)}, booking_url = ?, updated_at = ? WHERE id = ?"),
        values
    )
    conn.execute(
        sql('INSERT INTO lead_events (site_id, lead_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)'),
        (site_id, None, 'widget_settings_updated', json.dumps({'updated_fields': list(WIDGET_TEXT_DEFAULTS.keys()) + ['booking_url']}), updated_at)
    )
    conn.commit()

    refreshed = db().execute(sql('SELECT * FROM sites WHERE id = ?'), (site_id,)).fetchone()
    refreshed_token = refreshed['site_token'] if refreshed and refreshed['site_token'] else site_token
    return redirect(url_for('dashboard', site_token=refreshed_token, widget_saved='1'))


@app.route('/api/v1/sites/widget-settings/update', methods=['POST'])
def update_widget_settings_api():
    payload = request.get_json(silent=True) or {}
    site_token = (payload.get('site_token') or '').strip()
    site_secret = (payload.get('site_secret') or '').strip()

    site = get_site_by_token(site_token)
    if not site:
        return jsonify({'error': 'Invalid site token.'}), 404

    if not site_secret or site_secret != site['site_secret']:
        return jsonify({'error': 'Invalid site secret.'}), 403

    updated_at = now_iso()
    update_values = []
    assignments = []
    for key, default in WIDGET_TEXT_DEFAULTS.items():
        assignments.append(f"{key} = ?")
        update_values.append((payload.get(key) or '').strip() or default)

    booking_url = (payload.get('booking_url') or '').strip()
    update_values.extend([booking_url, updated_at, site['id']])

    conn = db()
    conn.execute(
        sql(f"UPDATE sites SET {', '.join(assignments)}, booking_url = ?, updated_at = ? WHERE id = ?"),
        update_values
    )
    conn.execute(
        sql('INSERT INTO lead_events (site_id, lead_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)'),
        (site['id'], None, 'widget_settings_updated', json.dumps({'updated_fields': list(WIDGET_TEXT_DEFAULTS.keys()) + ['booking_url']}), updated_at)
    )
    conn.commit()

    updated_site = db().execute(sql('SELECT * FROM sites WHERE id = ?'), (site['id'],)).fetchone()
    return jsonify({'success': True, 'item': get_widget_settings(updated_site)})


@app.route('/seed-demo')
def seed_demo():
    token = 'connect_demo_12345'
    conn = db()
    site = conn.execute(sql('SELECT * FROM sites WHERE connect_token = ?'), (token,)).fetchone()
    if not site:
        now = now_iso()
        conn.execute(
            sql('INSERT INTO sites (name, connect_token, booking_url, created_at, updated_at) VALUES (?, ?, ?, ?, ?)'),
            ('Demo Site', token, 'https://leadresponse.co.uk/contact/', now, now)
        )
        conn.commit()
    return jsonify({'connect_token': token})


@app.route('/api/v1/sites/connect', methods=['POST'])
def connect_site():
    payload = request.get_json(silent=True) or {}
    connect_token = (payload.get('connect_token') or '').strip()
    domain = (payload.get('domain') or '').strip()

    if not connect_token:
        return jsonify({'error': 'Missing connect token.'}), 400

    conn = db()
    site = conn.execute(sql('SELECT * FROM sites WHERE connect_token = ?'), (connect_token,)).fetchone()
    if not site:
        return jsonify({'error': 'Invalid connect token.'}), 404

    site_token = site['site_token'] or f"site_{secrets.token_hex(12)}"
    site_secret = site['site_secret'] or f"secret_{secrets.token_hex(24)}"
    updated_at = now_iso()

    conn.execute(
        sql('UPDATE sites SET domain = ?, site_token = ?, site_secret = ?, status = ?, updated_at = ? WHERE id = ?'),
        (domain, site_token, site_secret, 'connected', updated_at, site['id'])
    )
    conn.commit()

    return jsonify({'site_id': site['id'], 'site_token': site_token, 'site_secret': site_secret, 'status': 'connected'})


@app.route('/api/v1/sites/verify', methods=['POST'])
def verify_site():
    payload = request.get_json(silent=True) or {}
    site_token = (payload.get('site_token') or '').strip()
    if not site_token:
        return jsonify({'connected': False}), 400

    site = get_site_by_token(site_token)
    if not site:
        return jsonify({'connected': False}), 404

    return jsonify({'connected': True, 'site_id': site['id'], 'status': site['status']})


@app.route('/api/v1/widget/config', methods=['GET'])
def widget_config():
    site_token = (request.args.get('site_token') or '').strip()
    site = get_site_by_token(site_token)
    if not site:
        return jsonify({'error': 'Site not found.'}), 404

    settings = get_widget_settings(site)
    return jsonify({
        'brand_name': settings['widget_title'],
        'primary_color': '#2575fc',
        **settings
    })


@app.route('/api/v1/lead-events', methods=['POST'])
def lead_events():
    payload = request.get_json(silent=True) or {}
    site_token = (payload.get('site_token') or '').strip()
    site_secret = (payload.get('site_secret') or '').strip()
    source = (payload.get('source') or 'widget').strip()
    lead = payload.get('lead') or {}

    site = get_site_by_token(site_token)
    if not site:
        return jsonify({'error': 'Invalid site token.'}), 404

    if not site_secret or site_secret != site['site_secret']:
        return jsonify({'error': 'Invalid site secret.'}), 403

    created_at = now_iso()
    conn = db()
    lead_values = (
        site['id'], source,
        (lead.get('first_name') or '').strip(),
        (lead.get('email') or '').strip(),
        (lead.get('phone') or '').strip(),
        (lead.get('service_type') or '').strip(),
        (lead.get('postcode') or '').strip(),
        (lead.get('urgency') or '').strip(),
        (lead.get('message') or '').strip(),
        'new',
        '',
        created_at,
    )
    if get_db_backend() == 'postgres':
        cur = conn.cursor()
        cur.execute(
            sql('INSERT INTO leads (site_id, source, first_name, email, phone, service_type, postcode, urgency, message, status, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id'),
            lead_values,
        )
        inserted = cur.fetchone()
        lead_id = inserted['id'] if isinstance(inserted, dict) else inserted[0]
    else:
        cur = conn.cursor()
        cur.execute(
            sql('INSERT INTO leads (site_id, source, first_name, email, phone, service_type, postcode, urgency, message, status, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)'),
            lead_values,
        )
        lead_id = cur.lastrowid

    create_lead_event(conn, site['id'], lead_id, payload.get('event_type') or 'lead_created', payload, created_at)

    next_status = 'booking_sent' if site['booking_url'] else 'acknowledged'
    next_message = 'Thanks — we have received your enquiry. Check your email for the next step.'

    if site_int(site, 'auto_ack_enabled', 1) and (lead.get('email') or '').strip():
        site_data = row_to_dict(site)
        subject, body = build_ack_email(site_data, lead)
        mail_result = send_email_message((lead.get('email') or '').strip(), subject, body)
        create_lead_event(conn, site['id'], lead_id, 'auto_ack_sent', mail_result, now_iso())
        if site['booking_url']:
            create_lead_event(conn, site['id'], lead_id, 'booking_link_sent', {'booking_url': site['booking_url'], 'mode': mail_result.get('mode')}, now_iso())
    else:
        next_message = 'Thanks — we have received your enquiry and will follow up shortly.'

    update_lead_status(conn, lead_id, next_status)
    conn.commit()

    return jsonify({
        'success': True,
        'lead_id': lead_id,
        'status': next_status,
        'next_action': {
            'type': 'booking_prompt' if site['booking_url'] else 'message',
            'booking_url': site['booking_url'] or '',
            'message': next_message
        }
    })


@app.route('/api/v1/leads', methods=['GET'])
def list_leads():
    site_token = (request.args.get('site_token') or '').strip()
    status_filter = (request.args.get('status') or 'all').strip().lower()
    site = get_site_by_token(site_token)
    if not site:
        return jsonify({'error': 'Invalid site token.'}), 404

    params = [site['id']]
    query = 'SELECT * FROM leads WHERE site_id = ?'
    if status_filter in ALLOWED_STATUSES:
        query += ' AND status = ?'
        params.append(status_filter)
    query += ' ORDER BY id DESC LIMIT 100'
    rows = db().execute(sql(query), params).fetchall()
    return jsonify({'items': [row_to_dict(r) for r in rows]})


@app.route('/api/v1/leads/<int:lead_id>', methods=['GET'])
def get_lead(lead_id):
    row = db().execute(sql('SELECT * FROM leads WHERE id = ?'), (lead_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Lead not found.'}), 404
    return jsonify(row_to_dict(row))


@app.route('/api/v1/leads/<int:lead_id>/update', methods=['POST'])
def update_lead_api(lead_id):
    payload = request.get_json(silent=True) or {}
    site_token = (payload.get('site_token') or '').strip()
    site = get_site_by_token(site_token)
    if not site:
        return jsonify({'error': 'Invalid site token.'}), 404

    lead = db().execute(sql('SELECT * FROM leads WHERE id = ? AND site_id = ?'), (lead_id, site['id'])).fetchone()
    if not lead:
        return jsonify({'error': 'Lead not found.'}), 404

    status = safe_status(payload.get('status') or lead['status'])
    notes = (payload.get('notes') or '').strip()
    updated_at = now_iso()

    conn = db()
    conn.execute(sql('UPDATE leads SET status = ?, notes = ? WHERE id = ?'), (status, notes, lead_id))
    conn.execute(
        sql('INSERT INTO lead_events (site_id, lead_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)'),
        (site['id'], lead_id, 'lead_updated', json.dumps({'status': status, 'notes': notes}), updated_at)
    )
    conn.commit()

    updated = db().execute(sql('SELECT * FROM leads WHERE id = ?'), (lead_id,)).fetchone()
    return jsonify({'success': True, 'item': row_to_dict(updated)})


@app.route('/api/v1/automation/run', methods=['POST'])
def run_automation_api():
    payload = request.get_json(silent=True) or {}
    site_token = (payload.get('site_token') or '').strip()
    site_secret = (payload.get('site_secret') or '').strip()

    site = get_site_by_token(site_token)
    if not site:
        return jsonify({'error': 'Invalid site token.'}), 404

    if not site_secret or site_secret != site['site_secret']:
        return jsonify({'error': 'Invalid site secret.'}), 403

    site_data = row_to_dict(site)
    if not site_int(site, 'follow_up_enabled', 1):
        return jsonify({'success': True, 'processed': 0, 'follow_ups_sent': 0, 'no_response_marked': 0, 'message': 'Follow-up engine is disabled for this site.'})

    thresholds = {
        1: site_int(site, 'followup_1_hours', 2),
        2: site_int(site, 'followup_2_hours', 24),
        3: site_int(site, 'followup_3_hours', 72),
    }
    eligible_statuses = ['new', 'acknowledged', 'qualified', 'booking_sent', 'open']
    rows = db().execute(
        sql('SELECT * FROM leads WHERE site_id = ? ORDER BY id DESC LIMIT 200'),
        (site['id'],)
    ).fetchall()

    processed = 0
    follow_ups_sent = 0
    no_response_marked = 0
    conn = db()

    for row in rows:
        lead = row_to_dict(row)
        if (lead.get('status') or 'new') not in eligible_statuses:
            continue
        if not (lead.get('email') or '').strip():
            continue

        processed += 1
        elapsed = hours_since(lead.get('created_at'))
        f1 = latest_event_for_lead(lead['id'], 'follow_up_1_sent')
        f2 = latest_event_for_lead(lead['id'], 'follow_up_2_sent')
        f3 = latest_event_for_lead(lead['id'], 'follow_up_3_sent')

        step_to_send = None
        if not f1 and elapsed >= thresholds[1]:
            step_to_send = 1
        elif f1 and not f2 and elapsed >= thresholds[2]:
            step_to_send = 2
        elif f2 and not f3 and elapsed >= thresholds[3]:
            step_to_send = 3

        if step_to_send is None:
            continue

        subject, body = build_follow_up_email(site_data, lead, step_to_send)
        result = send_email_message((lead.get('email') or '').strip(), subject, body)
        create_lead_event(conn, site['id'], lead['id'], f'follow_up_{step_to_send}_sent', result, now_iso())
        follow_ups_sent += 1

        if step_to_send == 1 and site['booking_url']:
            update_lead_status(conn, lead['id'], 'booking_sent')
        if step_to_send == 3:
            update_lead_status(conn, lead['id'], 'no_response')
            create_lead_event(conn, site['id'], lead['id'], 'lead_marked_no_response', {'reason': 'final_follow_up_sent'}, now_iso())
            no_response_marked += 1

    conn.commit()
    return jsonify({
        'success': True,
        'processed': processed,
        'follow_ups_sent': follow_ups_sent,
        'no_response_marked': no_response_marked,
        'message': 'Website follow-up engine run completed.'
    })


@app.route('/health')
def health():
    return jsonify({'ok': True, 'time': now_iso(), 'db_backend': get_db_backend(), 'mail_mode': 'smtp' if (os.getenv('SMTP_HOST') or '').strip() else 'simulation'})


init_db()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
