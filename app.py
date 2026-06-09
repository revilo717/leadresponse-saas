from flask import Flask, request, jsonify, g, render_template_string, redirect, url_for
import os
import sqlite3
import secrets
import json
import imaplib
import email
import re
from pathlib import Path
from datetime import datetime, timezone, timedelta
import hashlib
from urllib.parse import urlparse
import smtplib
import ssl
import threading
from email.message import EmailMessage
from email.utils import parseaddr, make_msgid, parsedate_to_datetime
from email.header import decode_header, make_header

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
REPLY_HANDLING_VALUES = {'lead_inbox', 'client_email'}
OUTBOUND_THREAD_EVENT_TYPES = (
    'manual_email_sent',
    'auto_ack_sent',
    'follow_up_1_sent',
    'follow_up_2_sent',
    'follow_up_3_sent',
)

WIDGET_TEXT_DEFAULTS = {
    'widget_title': 'LeadResponse',
    'widget_button_text': 'LeadResponse',
    'widget_button_color': '#2575fc',
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
    ('widget_button_color', 'Launcher button colour', 'single'),
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


WHITE_LABEL_DEFAULTS = {
    'white_label_enabled': 0,
    'brand_display_name': 'LeadResponse',
    'portal_subdomain': 'go',
    'portal_domain_status': 'not_started',
    'email_subdomain': 'em',
    'email_domain_status': 'not_started',
    'dns_last_checked_at': '',
    'email_provider': 'mailgun',
    'email_from_localpart': 'hello',
    'reply_to_email': '',
    'reply_handling_mode': 'lead_inbox',
    'delivery_mode': 'platform_domain',
    'sender_name': '',
    'sender_email': '',
    'transport_mode': 'platform_smtp',
    'smtp_host': '',
    'smtp_port': '587',
    'smtp_username': '',
    'smtp_password': '',
    'smtp_use_ssl': 0,
    'smtp_use_tls': 1,
    'last_reply_sync_at': '',
    'last_reply_sync_count': 0,
    'last_reply_sync_error': '',
}

EMAIL_TEMPLATE_DEFAULTS = {
    'use_global_email_templates': 1,
    'ack_subject_template': 'Thanks {{first_name}} — we received your enquiry',
    'ack_body_template': 'Hi {{first_name}},\n\nThanks for contacting {{site_name}}. We have received your enquiry and the details below have been captured:\n\nService: {{service_type}}\nPostcode: {{postcode}}\nUrgency: {{urgency}}\n\nWe will review this and follow up with the next step shortly.\n\n{{booking_line}}\n\nRegards,\n{{site_name}}',
    'followup_1_subject_template': '{{first_name}}, just checking on your enquiry',
    'followup_1_body_template': 'Hi {{first_name}},\n\n{{follow_up_intro}}\n\nService: {{service_type}}\nPostcode: {{postcode}}\nUrgency: {{urgency}}\n\n{{booking_line}}\n\n{{reply_prompt}}\n\nRegards,\n{{site_name}}',
    'followup_2_subject_template': 'Quick follow-up on your enquiry',
    'followup_2_body_template': 'Hi {{first_name}},\n\n{{follow_up_intro}}\n\nService: {{service_type}}\nPostcode: {{postcode}}\nUrgency: {{urgency}}\n\n{{booking_line}}\n\n{{reply_prompt}}\n\nRegards,\n{{site_name}}',
    'followup_3_subject_template': 'Final follow-up before we close your enquiry',
    'followup_3_body_template': 'Hi {{first_name}},\n\n{{follow_up_intro}}\n\nService: {{service_type}}\nPostcode: {{postcode}}\nUrgency: {{urgency}}\n\n{{booking_line}}\n\n{{reply_prompt}}\n\nRegards,\n{{site_name}}',
}

WHITE_LABEL_STATUS_VALUES = {'not_started', 'pending', 'verified', 'failed'}


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
        'widget_button_color': "widget_button_color TEXT DEFAULT '#2575fc'",
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
        'white_label_enabled': "white_label_enabled INTEGER DEFAULT 0",
        'brand_display_name': "brand_display_name TEXT DEFAULT 'LeadResponse'",
        'portal_subdomain': "portal_subdomain TEXT DEFAULT 'go'",
        'portal_domain_status': "portal_domain_status TEXT DEFAULT 'not_started'",
        'email_subdomain': "email_subdomain TEXT DEFAULT 'em'",
        'email_domain_status': "email_domain_status TEXT DEFAULT 'not_started'",
        'dns_last_checked_at': "dns_last_checked_at TEXT",
        'email_provider': "email_provider TEXT DEFAULT 'mailgun'",
        'email_from_localpart': "email_from_localpart TEXT DEFAULT 'hello'",
        'reply_to_email': "reply_to_email TEXT",
        'reply_handling_mode': "reply_handling_mode TEXT DEFAULT 'lead_inbox'",
        'delivery_mode': "delivery_mode TEXT DEFAULT 'platform_domain'",
        'sender_name': "sender_name TEXT",
        'sender_email': "sender_email TEXT",
        'transport_mode': "transport_mode TEXT DEFAULT 'platform_smtp'",
        'smtp_host': "smtp_host TEXT",
        'smtp_port': "smtp_port INTEGER DEFAULT 587",
        'smtp_username': "smtp_username TEXT",
        'smtp_password': "smtp_password TEXT",
        'smtp_use_ssl': "smtp_use_ssl INTEGER DEFAULT 0",
        'smtp_use_tls': "smtp_use_tls INTEGER DEFAULT 1",
        'last_reply_sync_at': "last_reply_sync_at TEXT",
        'last_reply_sync_count': "last_reply_sync_count INTEGER DEFAULT 0",
        'last_reply_sync_error': "last_reply_sync_error TEXT DEFAULT ''",
        'use_global_email_templates': "use_global_email_templates INTEGER DEFAULT 1",
        'ack_subject_template': "ack_subject_template TEXT",
        'ack_body_template': "ack_body_template TEXT",
        'followup_1_subject_template': "followup_1_subject_template TEXT",
        'followup_1_body_template': "followup_1_body_template TEXT",
        'followup_2_subject_template': "followup_2_subject_template TEXT",
        'followup_2_body_template': "followup_2_body_template TEXT",
        'followup_3_subject_template': "followup_3_subject_template TEXT",
        'followup_3_body_template': "followup_3_body_template TEXT",
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


def log_mail_event(event_name, payload):
    try:
        print(f"[{now_iso()}] {event_name} {json.dumps(payload, sort_keys=True)}", flush=True)
    except Exception:
        print(f"[{now_iso()}] {event_name} {payload}", flush=True)


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



def normalize_white_label_status(value, default='not_started'):
    value = (value or '').strip().lower()
    return value if value in WHITE_LABEL_STATUS_VALUES else default



def parse_site_hostname(value):
    raw = (value or '').strip()
    if not raw:
        return ''
    candidate = raw if '://' in raw else f'https://{raw}'
    try:
        return (urlparse(candidate).hostname or '').lower()
    except Exception:
        return ''


def coerce_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return int(value) != 0
    value = str(value).strip().lower()
    if value in ('1', 'true', 'yes', 'on', 'y'):
        return True
    if value in ('0', 'false', 'no', 'off', 'n', ''):
        return False
    return default


def clean_subdomain_label(value, default):
    raw = (value or '').strip().lower()
    cleaned = ''.join(ch for ch in raw if ch.isalnum() or ch == '-').strip('-')
    return cleaned or default


def clean_localpart(value, default):
    raw = (value or '').strip().lower()
    cleaned = ''.join(ch for ch in raw if ch.isalnum() or ch in ('-', '_', '.')).strip('._-')
    return cleaned or default


def clean_email_value(value):
    raw = (value or '').strip()
    if not raw:
        return ''
    if ' ' in raw or '@' not in raw:
        return ''
    return raw


def clean_reply_handling_mode(value, default='lead_inbox'):
    raw = (value or '').strip().lower()
    return raw if raw in REPLY_HANDLING_VALUES else default


def clean_email_provider(value, default='mailgun'):
    allowed = {'mailgun', 'sendgrid', 'postmark', 'custom'}
    raw = (value or '').strip().lower()
    return raw if raw in allowed else default


def email_provider_options():
    return {
        'mailgun': 'Mailgun-style DNS pack',
        'sendgrid': 'SendGrid (use provider-issued values)',
        'postmark': 'Postmark (use provider-issued values)',
        'custom': 'Custom provider / my own provider',
    }


def build_email_dns_records(settings):
    provider = settings.get('email_provider') or 'mailgun'
    subdomain = settings.get('email_subdomain') or 'em'
    branded_sender = settings.get('branded_sender_email') or '[localpart]@[email-subdomain].[client-domain]'
    tracking_host = f"email.{subdomain}"

    if provider == 'mailgun':
        return [
            {
                'host': subdomain,
                'type': 'TXT',
                'value': 'v=spf1 include:mailgun.org ~all',
                'priority': '',
                'required': True,
                'notes': 'SPF for the branded email subdomain.',
            },
            {
                'host': f'smtp._domainkey.{subdomain}',
                'type': 'TXT',
                'value': 'provider-issued-dkim-public-key',
                'priority': '',
                'required': True,
                'notes': 'Replace with the DKIM key issued for this domain in your Mailgun account.',
            },
            {
                'host': tracking_host,
                'type': 'CNAME',
                'value': 'mailgun.org',
                'priority': '',
                'required': True,
                'notes': 'Tracking domain for branded click/open tracking.',
            },
            {
                'host': subdomain,
                'type': 'MX',
                'value': 'mxa.mailgun.org',
                'priority': '10',
                'required': True,
                'notes': 'Inbound routing / verification support.',
            },
            {
                'host': subdomain,
                'type': 'MX',
                'value': 'mxb.mailgun.org',
                'priority': '10',
                'required': True,
                'notes': 'Inbound routing / verification support.',
            },
            {
                'host': f'_dmarc.{subdomain}',
                'type': 'TXT',
                'value': 'v=DMARC1; p=none;',
                'priority': '',
                'required': False,
                'notes': 'Recommended starting DMARC policy while you test branded sending.',
            },
        ]

    if provider == 'sendgrid':
        return [
            {
                'host': subdomain,
                'type': 'CNAME',
                'value': 'provider-issued-return-path.sendgrid.net',
                'priority': '',
                'required': True,
                'notes': 'Use the exact return-path host provided by SendGrid domain authentication.',
            },
            {
                'host': f's1._domainkey.{subdomain}',
                'type': 'CNAME',
                'value': 'provider-issued-dkim-1.sendgrid.net',
                'priority': '',
                'required': True,
                'notes': 'Use the first DKIM CNAME exactly as issued by SendGrid.',
            },
            {
                'host': f's2._domainkey.{subdomain}',
                'type': 'CNAME',
                'value': 'provider-issued-dkim-2.sendgrid.net',
                'priority': '',
                'required': True,
                'notes': 'Use the second DKIM CNAME exactly as issued by SendGrid.',
            },
            {
                'host': f'_dmarc.{subdomain}',
                'type': 'TXT',
                'value': 'v=DMARC1; p=none;',
                'priority': '',
                'required': False,
                'notes': 'Recommended starting DMARC policy while you test branded sending.',
            },
        ]

    if provider == 'postmark':
        return [
            {
                'host': subdomain,
                'type': 'TXT',
                'value': 'provider-issued-spf-or-domain-verification-value',
                'priority': '',
                'required': True,
                'notes': 'Use the domain verification TXT/SPF value issued by Postmark for this sending domain.',
            },
            {
                'host': f'pm-bounces.{subdomain}',
                'type': 'CNAME',
                'value': 'pm.mtasv.net',
                'priority': '',
                'required': True,
                'notes': 'Return-path / bounce domain commonly used with Postmark branded sending.',
            },
            {
                'host': f'{subdomain}._domainkey',
                'type': 'TXT',
                'value': 'provider-issued-dkim-public-key',
                'priority': '',
                'required': True,
                'notes': 'Replace with the DKIM value issued by Postmark for this sending domain.',
            },
            {
                'host': f'_dmarc.{subdomain}',
                'type': 'TXT',
                'value': 'v=DMARC1; p=none;',
                'priority': '',
                'required': False,
                'notes': 'Recommended starting DMARC policy while you test branded sending.',
            },
        ]

    return [
        {
            'host': subdomain,
            'type': 'TXT',
            'value': 'provider-issued-spf-or-domain-verification-value',
            'priority': '',
            'required': True,
            'notes': 'Add the exact SPF or verification TXT record issued by your provider.',
        },
        {
            'host': f'{subdomain}._domainkey',
            'type': 'TXT or CNAME',
            'value': 'provider-issued-dkim-value',
            'priority': '',
            'required': True,
            'notes': 'Add the DKIM value issued by your provider.',
        },
        {
            'host': f'track.{subdomain}',
            'type': 'CNAME',
            'value': 'provider-issued-tracking-domain',
            'priority': '',
            'required': False,
            'notes': 'Optional branded tracking / click domain if your provider supports it.',
        },
        {
            'host': f'_dmarc.{subdomain}',
            'type': 'TXT',
            'value': 'v=DMARC1; p=none;',
            'priority': '',
            'required': False,
            'notes': 'Recommended starting DMARC policy while you test branded sending.',
        },
    ]


def build_email_dns_steps(settings):
    provider = settings.get('email_provider') or 'mailgun'
    branded_sender = settings.get('branded_sender_email') or '[localpart]@[email-subdomain].[client-domain]'
    email_domain = settings.get('email_domain') or '[email-subdomain].[client-domain]'
    if provider == 'mailgun':
        return [
            f'Create the branded sending subdomain {email_domain} inside Mailgun or your equivalent sending provider account.',
            'Add the DNS pack below at your DNS host. Replace the DKIM placeholder with the live key issued for this domain.',
            f'Once the provider marks the domain as verified, LeadResponse can switch branded sending to {branded_sender}.',
            'Until verification is complete, LeadResponse stays in platform fallback mode automatically.',
        ]
    if provider in ('sendgrid', 'postmark'):
        return [
            f'Select {settings.get("email_provider_label") or provider.title()} in your provider account and start branded domain authentication for {email_domain}.',
            'Use the record structure below as your checklist and replace every placeholder value with the exact values issued by the provider.',
            f'Once the provider marks the domain as verified, LeadResponse can switch branded sending to {branded_sender}.',
            'Until verification is complete, LeadResponse stays in platform fallback mode automatically.',
        ]
    return [
        f'Use your own provider to authenticate the sending subdomain {email_domain}.',
        'Add the provider-issued SPF / verification, DKIM, and optional tracking records shown in the table below.',
        f'Once verified, LeadResponse can switch branded sending to {branded_sender}.',
        'Until verification is complete, LeadResponse stays in platform fallback mode automatically.',
    ]


def get_white_label_settings(site):
    site_data = row_to_dict(site) or {}
    settings = {}
    for key, default in WHITE_LABEL_DEFAULTS.items():
        value = site_data.get(key)
        settings[key] = value if value not in (None, '') else default

    settings['site_id'] = site_data.get('id')
    settings['site_name'] = site_data.get('name') or 'LeadResponse'
    settings['white_label_enabled'] = coerce_bool(site_data.get('white_label_enabled'), False)
    settings['portal_domain_status'] = normalize_white_label_status(site_data.get('portal_domain_status'))
    settings['email_domain_status'] = normalize_white_label_status(site_data.get('email_domain_status'))
    settings['brand_display_name'] = (site_data.get('brand_display_name') or site_data.get('name') or 'LeadResponse').strip() or 'LeadResponse'
    settings['site_domain'] = parse_site_hostname(site_data.get('domain') or '')
    settings['portal_subdomain'] = clean_subdomain_label(site_data.get('portal_subdomain') or 'go', 'go')
    settings['email_subdomain'] = clean_subdomain_label(site_data.get('email_subdomain') or 'em', 'em')
    settings['email_provider'] = clean_email_provider(site_data.get('email_provider') or settings.get('email_provider') or 'mailgun', 'mailgun')
    settings['email_from_localpart'] = clean_localpart(site_data.get('email_from_localpart') or 'hello', 'hello')
    settings['reply_to_email'] = clean_email_value(site_data.get('reply_to_email') or '')
    settings['reply_handling_mode'] = clean_reply_handling_mode(site_data.get('reply_handling_mode') or 'lead_inbox', 'lead_inbox')
    requested_mode = (site_data.get('delivery_mode') or settings.get('delivery_mode') or 'platform_domain').strip() or 'platform_domain'
    settings['sender_name'] = (site_data.get('sender_name') or '').strip()
    settings['sender_email'] = clean_email_value(site_data.get('sender_email') or '')
    requested_transport = (site_data.get('transport_mode') or settings.get('transport_mode') or 'platform_smtp').strip() or 'platform_smtp'
    if requested_transport not in ('site_smtp', 'platform_smtp'):
        requested_transport = 'platform_smtp'
    settings['requested_transport_mode'] = requested_transport
    settings['smtp_host'] = (site_data.get('smtp_host') or '').strip()
    settings['smtp_port'] = str(site_data.get('smtp_port') or '587').strip() or '587'
    settings['smtp_username'] = (site_data.get('smtp_username') or '').strip()
    settings['smtp_password'] = site_data.get('smtp_password') or ''
    settings['smtp_password_saved'] = bool(site_data.get('smtp_password'))
    settings['smtp_use_ssl'] = 1 if coerce_bool(site_data.get('smtp_use_ssl'), False) else 0
    settings['smtp_use_tls'] = 1 if coerce_bool(site_data.get('smtp_use_tls'), True) else 0
    settings['dns_last_checked_at'] = (site_data.get('dns_last_checked_at') or '').strip()
    settings['portal_domain'] = f"{settings['portal_subdomain']}.{settings['site_domain']}" if settings['site_domain'] else ''
    settings['email_domain'] = f"{settings['email_subdomain']}.{settings['site_domain']}" if settings['site_domain'] else ''
    settings['portal_cname_target'] = (os.getenv('WHITE_LABEL_PORTAL_TARGET') or 'leadresponse-saas.onrender.com').strip()
    settings['platform_from_email'] = (os.getenv('MAIL_FROM') or os.getenv('SMTP_USERNAME') or 'no-reply@leadresponse.local').strip()
    settings['platform_from_name'] = (os.getenv('MAIL_FROM_NAME') or 'LeadResponse').strip()
    settings['branded_sender_email'] = f"{settings['email_from_localpart']}@{settings['email_domain']}" if settings['email_domain'] else ''
    branded_ready = settings['white_label_enabled'] and settings['email_domain_status'] == 'verified' and bool(settings['branded_sender_email'])
    personal_ready = bool(settings['sender_email'])
    if requested_mode == 'personal_mailbox' and personal_ready:
        settings['delivery_mode'] = 'personal_mailbox'
        settings['active_from_email'] = settings['sender_email']
        settings['active_from_name'] = settings['sender_name'] or settings['brand_display_name'] or settings['platform_from_name']
    elif requested_mode == 'branded_domain' and branded_ready:
        settings['delivery_mode'] = 'branded_domain'
        settings['active_from_email'] = settings['branded_sender_email']
        settings['active_from_name'] = settings['brand_display_name'] or settings['platform_from_name']
    else:
        settings['delivery_mode'] = 'platform_domain'
        settings['active_from_email'] = settings['platform_from_email']
        settings['active_from_name'] = settings['platform_from_name']
    settings['mail_from_name'] = settings['active_from_name']
    site_smtp_ready = bool(settings['smtp_host'] and settings['smtp_username'] and settings['smtp_password'])
    if settings['requested_transport_mode'] == 'site_smtp' and site_smtp_ready:
        settings['transport_mode'] = 'site_smtp'
        settings['transport_summary'] = 'Using client SMTP credentials for outbound email.'
    elif settings['requested_transport_mode'] == 'site_smtp':
        settings['transport_mode'] = 'platform_smtp'
        settings['transport_summary'] = 'Client SMTP selected but incomplete. Falling back to LeadResponse platform SMTP until host, username, and password are saved.'
    else:
        settings['transport_mode'] = 'platform_smtp'
        settings['transport_summary'] = 'Using LeadResponse platform SMTP fallback.'
    settings['portal_dns_record'] = {
        'host': settings['portal_subdomain'],
        'type': 'CNAME',
        'value': settings['portal_cname_target'],
        'priority': '',
        'required': True,
        'notes': 'Portal / branded access record for the client-facing subdomain.',
    }
    provider_options = email_provider_options()
    settings['provider_options'] = provider_options
    settings['email_provider_label'] = provider_options.get(settings['email_provider'], 'Custom provider / my own provider')
    for key, default in EMAIL_TEMPLATE_DEFAULTS.items():
        if key == 'use_global_email_templates':
            settings[key] = 1 if coerce_bool(site_data.get(key), True) else 0
        else:
            settings[key] = normalize_template_text(site_data.get(key), default)
    settings['email_template_defaults'] = dict(EMAIL_TEMPLATE_DEFAULTS)
    settings['email_dns_records'] = build_email_dns_records(settings)
    settings['email_dns_steps'] = build_email_dns_steps(settings)
    settings['dns_pack_summary'] = 'Email branding normally needs a DNS pack (TXT / CNAME / MX / DMARC), not just one CNAME.'
    settings['activation_summary'] = 'LeadResponse stays in platform fallback mode until the branded email domain is verified.'
    return settings

def resolve_mail_profile(site):
    white_label = get_white_label_settings(site)
    reply_mode = clean_reply_handling_mode(white_label.get('reply_handling_mode') or 'lead_inbox', 'lead_inbox')
    reply_to_email = white_label.get('reply_to_email') or ''
    if reply_mode != 'client_email':
        reply_to_email = ''
    return {
        'from_email': white_label.get('active_from_email') or (os.getenv('MAIL_FROM') or os.getenv('SMTP_USERNAME') or 'no-reply@leadresponse.local').strip(),
        'from_name': white_label.get('active_from_name') or (os.getenv('MAIL_FROM_NAME') or 'LeadResponse').strip(),
        'reply_to_email': reply_to_email,
        'reply_handling_mode': reply_mode,
        'delivery_mode': white_label.get('delivery_mode') or 'platform_domain',
        'branded_sender_email': white_label.get('branded_sender_email') or '',
        'platform_from_email': white_label.get('platform_from_email') or '',
        'transport_mode': white_label.get('transport_mode') or 'platform_smtp',
        'requested_transport_mode': white_label.get('requested_transport_mode') or 'platform_smtp',
        'transport_summary': white_label.get('transport_summary') or '',
        'smtp_host': white_label.get('smtp_host') or '',
        'smtp_port': white_label.get('smtp_port') or '587',
        'smtp_username': white_label.get('smtp_username') or '',
        'smtp_password': white_label.get('smtp_password') or '',
        'smtp_use_ssl': 1 if coerce_bool(white_label.get('smtp_use_ssl'), False) else 0,
        'smtp_use_tls': 1 if coerce_bool(white_label.get('smtp_use_tls'), True) else 0,
    }


def public_white_label_settings(site):
    settings = get_white_label_settings(site)
    settings['smtp_password'] = ''
    settings['smtp_password_saved'] = bool(settings.get('smtp_password_saved'))
    settings['imap_configured'] = imap_is_configured()
    return settings


def normalize_template_text(value, default):
    text = str(value if value not in (None, '') else default).replace('\r\n', '\n').replace('\r', '\n').strip()
    return text or default


def template_context(site, lead, step_number=None):
    site_data = row_to_dict(site) or {}
    lead_data = row_to_dict(lead) or {}
    booking_url = (site_data.get('booking_url') or '').strip()
    context = {
        'first_name': (lead_data.get('first_name') or 'there').strip() or 'there',
        'site_name': (site_data.get('brand_display_name') or site_data.get('name') or 'LeadResponse').strip() or 'LeadResponse',
        'service_type': (lead_data.get('service_type') or '').strip() or 'Not provided',
        'postcode': (lead_data.get('postcode') or '').strip() or 'Not provided',
        'urgency': (lead_data.get('urgency') or '').strip() or 'Not provided',
        'booking_url': booking_url,
        'booking_line': '',
        'follow_up_intro': '',
        'reply_prompt': 'Reply to this email if you would like us to help.',
    }
    if step_number in (1, 2, 3):
        intros = {
            1: 'We wanted to follow up to make sure you still need help with this job.',
            2: 'We are following up again in case you still want to move this forward.',
            3: 'This is our final automated follow-up before we mark the enquiry as no response.',
        }
        context['follow_up_intro'] = intros.get(step_number, 'We are following up on your enquiry.')
        if booking_url:
            context['booking_line'] = f'If you are ready, book the next step here: {booking_url}'
    elif booking_url:
        context['booking_line'] = f'If you would prefer, you can book the next step now: {booking_url}'
    return context


def render_template_text(template, context):
    text = str(template or '')
    def repl(match):
        key = (match.group(1) or '').strip()
        return str(context.get(key, ''))
    rendered = re.sub(r'{{\s*([a-z0-9_]+)\s*}}', repl, text, flags=re.IGNORECASE)
    rendered = re.sub(r'\n{3,}', '\n\n', rendered)
    rendered = re.sub(r'[ \t]+\n', '\n', rendered)
    return rendered.strip()


def effective_email_template(settings, key):
    default = EMAIL_TEMPLATE_DEFAULTS.get(key, '')
    if key == 'use_global_email_templates':
        return 1 if coerce_bool(settings.get(key), True) else 0
    if coerce_bool(settings.get('use_global_email_templates'), True):
        return default
    return normalize_template_text(settings.get(key), default)


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


def reply_handling_label(value):
    mapping = {
        'lead_inbox': 'Lead inbox threading',
        'client_email': 'Client email reply-to',
    }
    return mapping.get(clean_reply_handling_mode(value), 'Lead inbox threading')


def decode_email_header_value(value):
    raw = value or ''
    try:
        return str(make_header(decode_header(raw))).strip()
    except Exception:
        return raw.strip()


def extract_email_address(value):
    return (parseaddr(value or '')[1] or '').strip().lower()


def strip_html_to_text(value):
    text = re.sub(r'(?is)<(script|style)[^>]*>.*?</\1>', ' ', value or '')
    text = re.sub(r'(?i)<br\\s*/?>', '\\n', text)
    text = re.sub(r'(?i)</p\\s*>', '\\n\\n', text)
    text = re.sub(r'(?i)</div\\s*>', '\\n', text)
    text = re.sub(r'(?is)<blockquote[^>]*>.*?</blockquote>', ' ', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = text.replace('&nbsp;', ' ')
    return text


def normalize_email_text(value):
    text = (value or '').replace('\r\n', '\n').replace('\r', '\n')
    text = text.replace('\u00a0', ' ')
    text = re.sub(r'[ \t]+\n', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def trim_reply_disclaimer_and_history(value):
    text = normalize_email_text(value)
    if not text:
        return ''
    lines = text.split('\n')
    kept = []
    meaningful_seen = 0
    quote_markers = [
        r'^on .+ wrote:$',
        r'^from:\s',
        r'^sent:\s',
        r'^subject:\s',
        r'^to:\s',
        r'^cc:\s',
        r'^-----original message-----$',
        r'^_{5,}$',
        r'^-{5,}$',
    ]
    disclaimer_markers = [
        'this electronic mail transmission is intended',
        'this email and any files transmitted',
        'this message and any attachments',
        'may contain private and confidential information',
        'please return the original to us immediately',
        'no liability for any loss or damage',
        'virus checking',
    ]
    signature_markers = [
        r'^(regards|kind regards|best regards|many thanks|thanks|cheers|sincerely)[,!\.]?$',
    ]
    for line in lines:
        stripped = line.strip()
        lowered = stripped.lower()
        if meaningful_seen > 0 and any(re.match(pattern, lowered, flags=re.IGNORECASE) for pattern in quote_markers):
            break
        if meaningful_seen > 0 and any(marker in lowered for marker in disclaimer_markers):
            break
        if meaningful_seen > 0 and any(re.match(pattern, stripped, flags=re.IGNORECASE) for pattern in signature_markers):
            break
        kept.append(line.rstrip())
        if stripped:
            meaningful_seen += 1
    return normalize_email_text('\n'.join(kept))


def clean_inbound_reply_text(value):
    cleaned = trim_reply_disclaimer_and_history(value)
    if not cleaned:
        return ''
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', cleaned) if p.strip()]
    if not paragraphs:
        return ''
    return re.sub(r'\s+', ' ', paragraphs[0]).strip()


def reply_preview_text(value, limit=220):
    text = clean_inbound_reply_text(value)
    if not text:
        text = normalize_email_text(value)
        if text:
            text = re.sub(r'\s+', ' ', text).strip()
    if len(text) > limit:
        text = text[:limit] + '…'
    return text


def extract_message_text(message):
    text_chunks = []
    html_chunks = []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        disposition = (part.get('Content-Disposition') or '').lower()
        if 'attachment' in disposition:
            continue
        content_type = (part.get_content_type() or '').lower()
        if content_type not in ('text/plain', 'text/html'):
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or 'utf-8'
        try:
            decoded = payload.decode(charset, errors='replace')
        except Exception:
            decoded = payload.decode('utf-8', errors='replace')
        decoded = decoded.strip()
        if not decoded:
            continue
        if content_type == 'text/plain':
            text_chunks.append(decoded)
        else:
            html_chunks.append(strip_html_to_text(decoded))
    joined = '\n\n'.join(text_chunks or html_chunks)
    return normalize_email_text(joined)


def imap_is_configured():
    return bool((os.getenv('IMAP_HOST') or '').strip() and (os.getenv('IMAP_USERNAME') or '').strip() and (os.getenv('IMAP_PASSWORD') or ''))


def notify_client_of_inbound_reply(site_data, lead, payload):
    client_email = clean_email_value((site_data.get('reply_to_email') or '').strip())
    platform_mailbox = ((os.getenv('MAIL_FROM') or os.getenv('SMTP_USERNAME') or '').strip()).lower()
    if not client_email or client_email.lower() == platform_mailbox:
        return None

    lead_name = (lead.get('first_name') or payload.get('from_email') or 'Customer').strip() or 'Customer'
    subject = f"New customer reply from {lead_name}"
    body = [
        f"A customer has replied to lead #{lead.get('id')}.",
        '',
        f"From: {payload.get('from_name') or payload.get('from_email') or 'Unknown sender'}",
        f"Email: {payload.get('from_email') or 'Unknown email'}",
        f"Subject: {payload.get('subject') or 'No subject'}",
        '',
        payload.get('body_text_clean') or payload.get('body_text') or 'No message body extracted.',
        '',
        'This reply has also been saved to the lead timeline in LeadResponse.',
    ]
    notify_site = dict(site_data)
    notify_site['reply_handling_mode'] = 'lead_inbox'
    notify_site['reply_to_email'] = ''
    return send_email_message(notify_site, client_email, subject, '\n'.join(body))


def inbound_reply_fingerprint(sender_email, subject, body_text, sent_at):
    normalized = '|'.join([
        (sender_email or '').strip().lower(),
        re.sub(r'\s+', ' ', (subject or '').strip().lower()),
        re.sub(r'\s+', ' ', (body_text or '').strip().lower())[:500],
        (sent_at or '').strip().lower(),
    ])
    return hashlib.sha1(normalized.encode('utf-8', errors='ignore')).hexdigest()


def recent_imap_search_keys(lookback_days=7):
    try:
        days = int(lookback_days or 7)
    except Exception:
        days = 7
    days = max(1, min(days, 30))
    since_dt = datetime.now(timezone.utc) - timedelta(days=days)
    since_token = since_dt.strftime('%d-%b-%Y')
    return [
        ['SINCE', since_token],
        ['ALL'],
    ]


def fetch_recent_imap_message_numbers(mailbox, lookback_days=7):
    for keys in recent_imap_search_keys(lookback_days):
        try:
            typ, data = mailbox.search(None, *keys)
        except Exception:
            continue
        if typ == 'OK' and data and data[0]:
            return data[0].split()
    return []


def normalize_message_id_token(value):
    token = decode_email_header_value(value or '').strip().lower()
    if not token:
        return ''
    if token.startswith('<') and token.endswith('>'):
        token = token[1:-1].strip()
    return token


def extract_header_message_ids(value):
    raw = decode_email_header_value(value or '')
    if not raw:
        return []
    matches = re.findall(r'<[^>]+>', raw)
    if matches:
        tokens = [normalize_message_id_token(match) for match in matches]
    else:
        tokens = [normalize_message_id_token(part) for part in re.split(r'[\s,]+', raw) if '@' in part]
    return [token for token in tokens if token]


def normalize_thread_subject(value):
    text = re.sub(r'^(re|fw|fwd)\s*:\s*', '', (value or '').strip(), flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip().lower()


def parse_email_datetime(value):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(str(value))
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return parse_iso_ts(value)


def iso_from_dt(dt, fallback=''):
    if dt is None:
        return fallback
    try:
        return dt.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')
    except Exception:
        return fallback


def inbound_event_created_at(sent_at):
    dt = parse_email_datetime(sent_at)
    return iso_from_dt(dt, now_iso())


def event_payload_dict(event):
    if not isinstance(event, dict):
        return {}
    payload = event.get('payload')
    if isinstance(payload, dict):
        return payload
    payload_json = event.get('payload_json')
    if not payload_json:
        return {}
    try:
        return json.loads(payload_json)
    except Exception:
        return {}


def event_occurred_dt(event):
    if not isinstance(event, dict):
        return parse_iso_ts('1970-01-01T00:00:00Z')
    payload = event_payload_dict(event)
    dt = None
    if (event.get('event_type') or '') == 'customer_reply_received':
        dt = parse_email_datetime(payload.get('sent_at') or payload.get('reply_occurred_at') or '')
    if dt is None:
        dt = parse_iso_ts(event.get('created_at'))
    return dt or parse_iso_ts('1970-01-01T00:00:00Z')


def event_occurred_at_iso(event):
    return iso_from_dt(event_occurred_dt(event), (event or {}).get('created_at') or '')


def sort_events_newest_first(events):
    return sorted(
        [event for event in (events or []) if isinstance(event, dict)],
        key=lambda event: (event_occurred_dt(event), int(event.get('id') or 0)),
        reverse=True,
    )


def latest_lead_event(lead_id):
    rows = db().execute(
        sql('SELECT * FROM lead_events WHERE lead_id = ? ORDER BY id DESC LIMIT 200'),
        (lead_id,)
    ).fetchall()
    events = sort_events_newest_first([row_to_dict(row) for row in rows])
    return events[0] if events else {}


def outbound_message_ids_from_payload(payload):
    payload = payload or {}
    values = []
    for key in ('message_id', 'outbound_message_id'):
        token = normalize_message_id_token(payload.get(key) or '')
        if token:
            values.append(token)
    return values


def recent_outbound_thread_events(conn, site_id, recipient_email=None, limit=800):
    rows = conn.execute(
        sql('SELECT * FROM lead_events WHERE site_id = ? ORDER BY id DESC LIMIT ?'),
        (site_id, int(limit))
    ).fetchall()
    events = []
    recipient_email = (recipient_email or '').strip().lower()
    for row in rows:
        event = row_to_dict(row)
        if event.get('event_type') not in OUTBOUND_THREAD_EVENT_TYPES:
            continue
        payload = parse_payload(event.get('payload_json'))
        if recipient_email and (payload.get('to') or '').strip().lower() != recipient_email:
            continue
        event['payload'] = payload
        events.append(event)
    return events


def find_lead_by_thread_headers(conn, site_id, sender_email, in_reply_to, references):
    reference_ids = set(extract_header_message_ids(in_reply_to) + extract_header_message_ids(references))
    if not reference_ids:
        return None, 'no_thread_headers'

    thread_matches = []
    for event in recent_outbound_thread_events(conn, site_id, None):
        outbound_ids = set(outbound_message_ids_from_payload(event.get('payload')))
        if outbound_ids and reference_ids.intersection(outbound_ids):
            lead_id = int(event.get('lead_id') or 0)
            if lead_id and lead_id not in thread_matches:
                thread_matches.append(lead_id)

    if len(thread_matches) != 1:
        return None, 'ambiguous_thread_match' if thread_matches else 'no_thread_header_match'

    lead_row = conn.execute(
        sql('SELECT * FROM leads WHERE id = ? AND site_id = ? LIMIT 1'),
        (thread_matches[0], site_id)
    ).fetchone()
    if lead_row:
        return lead_row, 'thread_headers'
    return None, 'thread_header_lead_not_found'


def find_lead_by_safe_fallback(conn, site_id, sender_email, subject, sent_at):
    outbound_events = recent_outbound_thread_events(conn, site_id, sender_email)
    unique_lead_ids = []
    for event in outbound_events:
        lead_id = int(event.get('lead_id') or 0)
        if lead_id and lead_id not in unique_lead_ids:
            unique_lead_ids.append(lead_id)
    if len(unique_lead_ids) != 1:
        return None, 'ambiguous_sender_email'

    lead_id = unique_lead_ids[0]
    lead_row = conn.execute(
        sql('SELECT * FROM leads WHERE id = ? AND site_id = ? LIMIT 1'),
        (lead_id, site_id)
    ).fetchone()
    if not lead_row:
        return None, 'lead_not_found'

    latest_outbound = None
    for event in outbound_events:
        if int(event.get('lead_id') or 0) == lead_id:
            latest_outbound = event
            break
    if not latest_outbound:
        return None, 'no_outbound_context'

    outbound_dt = parse_iso_ts(latest_outbound.get('created_at'))
    inbound_dt = parse_email_datetime(sent_at)
    if outbound_dt and datetime.now(timezone.utc) - outbound_dt > timedelta(days=30):
        return None, 'outbound_context_too_old'
    if inbound_dt and outbound_dt and inbound_dt + timedelta(minutes=2) < outbound_dt:
        return None, 'reply_older_than_latest_outbound'

    inbound_subject = normalize_thread_subject(subject)
    outbound_subject = normalize_thread_subject((latest_outbound.get('payload') or {}).get('subject') or '')
    if inbound_subject and outbound_subject and inbound_subject != outbound_subject:
        return None, 'subject_mismatch_without_thread_headers'

    return lead_row, 'single_recent_outbound_fallback'


def resolve_inbound_reply_lead(conn, site_id, sender_email, subject, in_reply_to, references, sent_at):
    lead_row, matched_via = find_lead_by_thread_headers(conn, site_id, sender_email, in_reply_to, references)
    if lead_row:
        return lead_row, matched_via
    fallback_row, fallback_reason = find_lead_by_safe_fallback(conn, site_id, sender_email, subject, sent_at)
    if fallback_row:
        return fallback_row, fallback_reason
    if matched_via:
        return None, matched_via
    return None, fallback_reason


def save_reply_sync_status(site_id, checked_at, replies_ingested=0, error=''):
    if not site_id:
        return
    conn = db()
    conn.execute(
        sql('UPDATE sites SET last_reply_sync_at = ?, last_reply_sync_count = ?, last_reply_sync_error = ?, updated_at = ? WHERE id = ?'),
        (checked_at, int(replies_ingested or 0), (error or '').strip(), checked_at, site_id)
    )
    conn.commit()


def poll_inbound_replies_for_site(site):
    site_data = row_to_dict(site) or {}
    checked_at = now_iso()
    result = {
        'success': True,
        'imap_configured': imap_is_configured(),
        'replies_ingested': 0,
        'last_checked_at': checked_at,
        'message': 'Reply sync completed.',
        'error': '',
    }
    if not site_data.get('id'):
        result['success'] = False
        result['message'] = 'Missing site for reply sync.'
        result['error'] = 'Missing site for reply sync.'
        return result
    if not result['imap_configured']:
        result['message'] = 'IMAP is not configured for inbound reply capture.'
        result['error'] = 'IMAP is not configured for inbound reply capture.'
        save_reply_sync_status(site_data['id'], checked_at, 0, result['error'])
        return result

    host = (os.getenv('IMAP_HOST') or '').strip()
    port = int((os.getenv('IMAP_PORT') or '993').strip())
    username = (os.getenv('IMAP_USERNAME') or '').strip()
    password = os.getenv('IMAP_PASSWORD') or ''
    folder = (os.getenv('IMAP_FOLDER') or 'INBOX').strip() or 'INBOX'
    use_ssl = env_flag('IMAP_USE_SSL', True)
    use_tls = env_flag('IMAP_USE_TLS', not use_ssl)
    lookback_days = int((os.getenv('IMAP_LOOKBACK_DAYS') or '7').strip() or '7')
    processed = 0

    try:
        if use_ssl:
            mailbox = imaplib.IMAP4_SSL(host, port)
        else:
            mailbox = imaplib.IMAP4(host, port)
            if use_tls and hasattr(mailbox, 'starttls'):
                mailbox.starttls(ssl_context=ssl.create_default_context())
        mailbox.login(username, password)
        mailbox.select(folder)
        message_nums = fetch_recent_imap_message_numbers(mailbox, lookback_days)

        for num in message_nums:
            typ, fetched = mailbox.fetch(num, '(BODY.PEEK[])')
            if typ != 'OK' or not fetched:
                continue
            raw_bytes = None
            for item in fetched:
                if isinstance(item, tuple) and len(item) > 1 and item[1]:
                    raw_bytes = item[1]
                    break
            if not raw_bytes:
                continue

            message = email.message_from_bytes(raw_bytes)
            sender_email = extract_email_address(message.get('From'))
            if not sender_email:
                continue

            message_id = decode_email_header_value(message.get('Message-ID') or '').strip()
            subject = decode_email_header_value(message.get('Subject') or '').strip()
            body_text_raw = extract_message_text(message)
            body_text_clean = clean_inbound_reply_text(body_text_raw)
            body_text_preview = reply_preview_text(body_text_clean or body_text_raw)
            body_text = body_text_clean or body_text_preview or body_text_raw
            sent_at = decode_email_header_value(message.get('Date') or '').strip()
            from_name = decode_email_header_value(parseaddr(message.get('From') or '')[0])
            in_reply_to = decode_email_header_value(message.get('In-Reply-To') or '').strip()
            references = decode_email_header_value(message.get('References') or '').strip()
            reply_fingerprint = inbound_reply_fingerprint(sender_email, subject, body_text_raw, sent_at)

            conn = connect_db()
            try:
                duplicate = None
                if message_id:
                    duplicate = conn.execute(
                        sql("SELECT id FROM lead_events WHERE event_type = ? AND payload_json LIKE ? LIMIT 1"),
                        ('customer_reply_received', f'%{message_id}%')
                    ).fetchone()
                if not duplicate and reply_fingerprint:
                    duplicate = conn.execute(
                        sql("SELECT id FROM lead_events WHERE event_type = ? AND payload_json LIKE ? LIMIT 1"),
                        ('customer_reply_received', f'%{reply_fingerprint}%')
                    ).fetchone()
                if duplicate:
                    continue

                lead_row, matched_via = resolve_inbound_reply_lead(
                    conn,
                    site_data['id'],
                    sender_email,
                    subject,
                    in_reply_to,
                    references,
                    sent_at,
                )
                if not lead_row:
                    log_mail_event('INBOUND_REPLY_UNMATCHED', {
                        'site_id': site_data['id'],
                        'from_email': sender_email,
                        'subject': subject,
                        'folder': folder,
                        'in_reply_to': in_reply_to,
                        'references': references,
                        'unmatched_reason': matched_via or 'unknown',
                    })
                    continue

                lead = row_to_dict(lead_row)
                reply_event_at = inbound_event_created_at(sent_at)
                payload = {
                    'from_name': from_name,
                    'from_email': sender_email,
                    'subject': subject,
                    'body_text': body_text,
                    'body_text_clean': body_text_clean,
                    'body_text_raw': body_text_raw,
                    'reply_preview_clean': body_text_preview,
                    'message_id': message_id,
                    'reply_fingerprint': reply_fingerprint,
                    'sent_at': sent_at,
                    'reply_occurred_at': reply_event_at,
                    'reply_via': 'imap',
                    'imap_folder': folder,
                    'in_reply_to': in_reply_to,
                    'references': references,
                    'matched_via': matched_via,
                }
                create_lead_event(conn, site_data['id'], lead['id'], 'customer_reply_received', payload, reply_event_at)
                existing_notes = (lead.get('notes') or '').strip()
                snippet = (body_text_preview or body_text_clean or body_text or '').strip()
                if len(snippet) > 700:
                    snippet = snippet[:700] + '…'
                note_entry = f"[{now_iso()}] Customer reply from {sender_email}: {snippet or 'No body extracted.'}"
                new_notes = f"{existing_notes}\n\n{note_entry}".strip() if existing_notes else note_entry
                update_lead_status(conn, lead['id'], 'open', new_notes)
                conn.commit()
                processed += 1
                log_mail_event('INBOUND_REPLY_MATCHED', {
                    'site_id': site_data['id'],
                    'lead_id': lead['id'],
                    'from_email': sender_email,
                    'subject': subject,
                    'folder': folder,
                    'matched_via': matched_via,
                })
                notification = notify_client_of_inbound_reply(site_data, lead, payload)
                if notification:
                    log_mail_event('INBOUND_REPLY_CLIENT_NOTIFIED', {
                        'site_id': site_data['id'],
                        'lead_id': lead['id'],
                        'client_email': site_data.get('reply_to_email') or '',
                        'ok': notification.get('ok'),
                        'mode': notification.get('mode'),
                    })
            finally:
                conn.close()

        mailbox.logout()
        result['replies_ingested'] = processed
        result['message'] = 'Reply sync completed.' if processed == 0 else f'Reply sync completed. {processed} new repl' + ('y was' if processed == 1 else 'ies were') + ' captured.'
        save_reply_sync_status(site_data['id'], checked_at, processed, '')
    except Exception as exc:
        result['message'] = 'Reply sync failed.'
        result['error'] = str(exc)
        log_mail_event('INBOUND_REPLY_POLL_ERROR', {
            'site_id': site_data.get('id'),
            'error': str(exc),
            'host': host,
            'port': port,
            'folder': folder,
            'lookback_days': lookback_days,
        })
        save_reply_sync_status(site_data['id'], checked_at, 0, str(exc))

    return result


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
    rows = db().execute(
        sql('SELECT * FROM lead_events WHERE lead_id = ? AND event_type = ? ORDER BY id DESC LIMIT 200'),
        (lead_id, event_type)
    ).fetchall()
    events = sort_events_newest_first([row_to_dict(row) for row in rows])
    return events[0] if events else {}


def update_lead_status(conn, lead_id, status=None, notes=None):
    if status is not None and notes is not None:
        conn.execute(sql('UPDATE leads SET status = ?, notes = ? WHERE id = ?'), (status, notes, lead_id))
    elif status is not None:
        conn.execute(sql('UPDATE leads SET status = ? WHERE id = ?'), (status, lead_id))
    elif notes is not None:
        conn.execute(sql('UPDATE leads SET notes = ? WHERE id = ?'), (notes, lead_id))


def send_email_message(site, to_email, subject, body_text):
    to_email = (to_email or '').strip()
    if not to_email:
        return {'ok': False, 'mode': 'skipped', 'error': 'Missing recipient email.'}

    profile = resolve_mail_profile(site)
    if profile.get('transport_mode') == 'site_smtp':
        host = (profile.get('smtp_host') or '').strip()
        port = int((str(profile.get('smtp_port') or '587')).strip())
        username = (profile.get('smtp_username') or '').strip()
        password = profile.get('smtp_password') or ''
        use_ssl = bool(profile.get('smtp_use_ssl'))
        use_tls = bool(profile.get('smtp_use_tls')) if not use_ssl else False
    else:
        host = (os.getenv('SMTP_HOST') or '').strip()
        port = int((os.getenv('SMTP_PORT') or '587').strip())
        username = (os.getenv('SMTP_USERNAME') or '').strip()
        password = os.getenv('SMTP_PASSWORD') or ''
        use_ssl = env_flag('SMTP_USE_SSL', False)
        use_tls = env_flag('SMTP_USE_TLS', not use_ssl)
    from_email = (profile.get('from_email') or os.getenv('MAIL_FROM') or username or 'no-reply@leadresponse.local').strip()
    from_name = (profile.get('from_name') or os.getenv('MAIL_FROM_NAME') or 'LeadResponse').strip()
    reply_to_email = (profile.get('reply_to_email') or '').strip()
    message_id = make_msgid()

    if not host:
        result = {
            'ok': True,
            'mode': 'simulation',
            'delivery_mode': profile.get('delivery_mode') or 'platform_domain',
            'to': to_email,
            'subject': subject,
            'from_email': from_email,
            'from_name': from_name,
            'reply_to_email': reply_to_email,
            'transport_mode': profile.get('transport_mode') or 'platform_smtp',
            'message_id': message_id,
        }
        log_mail_event('SMTP_SIMULATION', result)
        return result

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = f'{from_name} <{from_email}>' if from_name else from_email
    msg['To'] = to_email
    msg['Message-ID'] = message_id
    if reply_to_email:
        msg['Reply-To'] = reply_to_email
    msg.set_content(body_text)

    smtp_timeout = int((os.getenv('SMTP_TIMEOUT_SECONDS') or '10').strip())

    log_mail_event('SMTP_SEND_START', {
        'host': host,
        'port': port,
        'use_ssl': use_ssl,
        'use_tls': use_tls,
        'timeout_seconds': smtp_timeout,
        'to': to_email,
        'from_email': from_email,
        'from_name': from_name,
        'reply_to_email': reply_to_email,
        'delivery_mode': profile.get('delivery_mode') or 'platform_domain',
        'transport_mode': profile.get('transport_mode') or 'platform_smtp',
        'subject': subject,
        'message_id': message_id,
    })

    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=smtp_timeout, context=ssl.create_default_context())
        else:
            server = smtplib.SMTP(host, port, timeout=smtp_timeout)
        with server:
            if not use_ssl and use_tls:
                server.starttls(context=ssl.create_default_context())
            if username:
                server.login(username, password)
            server.send_message(msg)
        result = {
            'ok': True,
            'mode': 'smtp',
            'delivery_mode': profile.get('delivery_mode') or 'platform_domain',
            'to': to_email,
            'subject': subject,
            'from_email': from_email,
            'from_name': from_name,
            'reply_to_email': reply_to_email,
            'transport_mode': profile.get('transport_mode') or 'platform_smtp',
            'message_id': message_id,
        }
        log_mail_event('SMTP_SEND_SUCCESS', result)
        return result
    except Exception as exc:
        result = {
            'ok': False,
            'mode': 'error',
            'delivery_mode': profile.get('delivery_mode') or 'platform_domain',
            'to': to_email,
            'subject': subject,
            'from_email': from_email,
            'from_name': from_name,
            'reply_to_email': reply_to_email,
            'transport_mode': profile.get('transport_mode') or 'platform_smtp',
            'message_id': message_id,
            'error': str(exc),
        }
        log_mail_event('SMTP_SEND_ERROR', result)
        return result


def persist_mail_event_async(site_id, lead_id, event_type, payload):
    conn = connect_db()
    try:
        create_lead_event(conn, site_id, lead_id, event_type, payload, now_iso())
        conn.commit()
    finally:
        conn.close()


def send_ack_email_async(site_data, lead, site_id, lead_id):
    try:
        recipient = (lead.get('email') or '').strip()
        if not recipient:
            payload = {'ok': False, 'mode': 'skipped', 'error': 'Missing recipient email.'}
            log_mail_event('AUTO_ACK_FAILED', {'site_id': site_id, 'lead_id': lead_id, **payload})
            persist_mail_event_async(site_id, lead_id, 'auto_ack_failed', payload)
            return

        log_mail_event('AUTO_ACK_START', {'site_id': site_id, 'lead_id': lead_id, 'to': recipient})
        subject, body = build_ack_email(site_data, lead)
        mail_result = send_email_message(site_data, recipient, subject, body)
        event_payload = {**mail_result, 'subject': subject, 'body_text': body}
        event_type = 'auto_ack_sent' if mail_result.get('ok') else 'auto_ack_failed'
        log_mail_event('AUTO_ACK_RESULT', {'site_id': site_id, 'lead_id': lead_id, 'event_type': event_type, **event_payload})
        persist_mail_event_async(site_id, lead_id, event_type, event_payload)

        if site_data.get('booking_url') and mail_result.get('ok'):
            persist_mail_event_async(site_id, lead_id, 'booking_link_sent', {
                'booking_url': site_data.get('booking_url') or '',
                'mode': mail_result.get('mode'),
                'delivery_mode': mail_result.get('delivery_mode') or 'platform_domain',
            })
    except Exception as exc:
        payload = {'ok': False, 'mode': 'error', 'error': str(exc)}
        log_mail_event('AUTO_ACK_FAILED', {'site_id': site_id, 'lead_id': lead_id, **payload})
        persist_mail_event_async(site_id, lead_id, 'auto_ack_failed', payload)




def send_manual_lead_email(site_data, lead, subject, body_text, status_after_send='open'):
    lead = dict(lead or {})
    recipient = (lead.get('email') or '').strip()
    if not recipient:
        return {'ok': False, 'mode': 'skipped', 'error': 'Lead has no email address.'}

    subject = (subject or '').strip()
    body_text = (body_text or '').strip()
    if not subject or not body_text:
        return {'ok': False, 'mode': 'skipped', 'error': 'Subject and body are required.'}

    mail_result = send_email_message(site_data, recipient, subject, body_text)
    payload = {
        'to': recipient,
        'subject': subject,
        'body_text': body_text,
        'from_email': mail_result.get('from_email') or '',
        'from_name': mail_result.get('from_name') or '',
        'reply_to_email': mail_result.get('reply_to_email') or '',
        'mode': mail_result.get('mode') or '',
        'delivery_mode': mail_result.get('delivery_mode') or 'platform_domain',
        'message_id': mail_result.get('message_id') or '',
        'outbound_message_id': mail_result.get('message_id') or '',
    }
    if mail_result.get('error'):
        payload['error'] = mail_result.get('error')

    conn = db()
    event_type = 'manual_email_sent' if mail_result.get('ok') else 'manual_email_failed'
    create_lead_event(conn, site_data['id'], lead['id'], event_type, payload, now_iso())
    if mail_result.get('ok'):
        next_status = safe_status(status_after_send or 'open')
        current_status = safe_status(lead.get('status') or 'new')
        notes_value = lead.get('notes') if isinstance(lead.get('notes'), str) else ''
        if next_status != current_status:
            update_lead_status(conn, lead['id'], next_status, notes_value)
            create_lead_event(conn, site_data['id'], lead['id'], 'lead_updated', {'status': next_status, 'notes': notes_value}, now_iso())
    conn.commit()
    refreshed = db().execute(sql('SELECT * FROM leads WHERE id = ?'), (lead['id'],)).fetchone()
    return {'ok': bool(mail_result.get('ok')), 'mail_result': mail_result, 'item': enrich_lead_for_inbox(row_to_dict(refreshed) or lead)}

def build_ack_email(site, lead):
    settings = get_white_label_settings(site)
    context = template_context(settings, lead)
    subject = render_template_text(effective_email_template(settings, 'ack_subject_template'), context)
    body = render_template_text(effective_email_template(settings, 'ack_body_template'), context)
    return subject or render_template_text(EMAIL_TEMPLATE_DEFAULTS['ack_subject_template'], context), body or render_template_text(EMAIL_TEMPLATE_DEFAULTS['ack_body_template'], context)


def build_follow_up_email(site, lead, step_number):
    settings = get_white_label_settings(site)
    context = template_context(settings, lead, step_number)
    subject_key = f'followup_{step_number}_subject_template'
    body_key = f'followup_{step_number}_body_template'
    fallback_subject = EMAIL_TEMPLATE_DEFAULTS.get(subject_key, 'LeadResponse follow-up')
    fallback_body = EMAIL_TEMPLATE_DEFAULTS.get(body_key, EMAIL_TEMPLATE_DEFAULTS['followup_1_body_template'])
    subject = render_template_text(effective_email_template(settings, subject_key), context)
    body = render_template_text(effective_email_template(settings, body_key), context)
    return subject or render_template_text(fallback_subject, context), body or render_template_text(fallback_body, context)


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
    <div class="footer-note">LeadResponse v0.8.3 test dashboard · Render Postgres ready</div>
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
    reply_saved = (request.args.get('reply_saved') or '') == '1'
    current_reply_mode = clean_reply_handling_mode((row_to_dict(site) or {}).get('reply_handling_mode') or 'lead_inbox', 'lead_inbox')

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
    {% if reply_saved %}
      <div class="notice-success">Reply handling updated successfully.</div>
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
            <h2>Reply handling</h2>
            <p>Choose whether customer replies go straight to the client email or stay in the LeadResponse mailbox and get attached to the matching lead.</p>
          </div>
        </div>
        <div class="meta">
          <span class="pill neutral">Mode: {{ reply_handling_label(current_reply_mode) }}</span>
          <span class="pill neutral">Client reply email: {{ site['reply_to_email'] or 'Not set' }}</span>
          <span class="pill neutral">IMAP ingest: {{ 'Enabled' if imap_configured else 'Not configured' }}</span>
        </div>
        <form method="post" action="{{ url_for('save_reply_routing', site_id=site['id']) }}?site_token={{ site['site_token'] }}" class="admin-form" style="margin-top:18px;">
          <label>
            Reply handling mode
            <select name="reply_handling_mode">
              <option value="lead_inbox" {% if current_reply_mode == 'lead_inbox' %}selected{% endif %}>Lead inbox threading (recommended)</option>
              <option value="client_email" {% if current_reply_mode == 'client_email' %}selected{% endif %}>Direct to client email</option>
            </select>
          </label>
          <label>
            Client reply email
            <input type="email" name="reply_to_email" value="{{ site['reply_to_email'] or '' }}" placeholder="owner@example.com">
          </label>
          <button type="submit">Save reply handling</button>
        </form>
        <p class="muted" style="margin-top:12px;">Lead inbox threading keeps the platform mailbox as the reply destination and pulls inbound replies into lead events using IMAP. Direct to client email sets Reply-To so replies bypass the platform and go straight to the client inbox.</p>
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
    ''', site=site, lead_rows=lead_rows, total_leads=total_leads, new_leads=new_leads, open_leads=open_leads, won_leads=won_leads, lost_leads=lost_leads, latest=latest, sites=sites, fmt_dt=fmt_dt, label_urgency=label_urgency, status_filter=status_filter, widget_settings=widget_settings, widget_fields=WIDGET_SETTINGS_FIELDS, widget_saved=widget_saved, reply_saved=reply_saved, current_reply_mode=current_reply_mode, reply_handling_label=reply_handling_label, imap_configured=imap_is_configured())

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


@app.route('/dashboard/sites/<int:site_id>/reply-routing/save', methods=['POST'])
def save_reply_routing(site_id):
    site_token = (request.args.get('site_token') or '').strip()
    site = get_site_by_token(site_token) if site_token else None
    if not site or site['id'] != site_id:
        site = db().execute(sql('SELECT * FROM sites WHERE id = ?'), (site_id,)).fetchone()
    if not site:
        return redirect(url_for('dashboard'))

    reply_to_email = clean_email_value(request.form.get('reply_to_email') or '')
    reply_handling_mode = clean_reply_handling_mode(request.form.get('reply_handling_mode') or 'lead_inbox', 'lead_inbox')
    if reply_handling_mode == 'client_email' and not reply_to_email:
        reply_handling_mode = 'lead_inbox'

    updated_at = now_iso()
    conn = db()
    conn.execute(
        sql('UPDATE sites SET reply_to_email = ?, reply_handling_mode = ?, updated_at = ? WHERE id = ?'),
        (reply_to_email, reply_handling_mode, updated_at, site_id)
    )
    conn.execute(
        sql('INSERT INTO lead_events (site_id, lead_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)'),
        (site_id, None, 'reply_routing_updated', json.dumps({'reply_to_email': reply_to_email, 'reply_handling_mode': reply_handling_mode}), updated_at)
    )
    conn.commit()

    refreshed = db().execute(sql('SELECT * FROM sites WHERE id = ?'), (site_id,)).fetchone()
    refreshed_token = refreshed['site_token'] if refreshed and refreshed['site_token'] else site_token
    return redirect(url_for('dashboard', site_token=refreshed_token, reply_saved='1'))


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



def create_connected_site_from_template(conn, template_site, requested_domain, updated_at):
    template = row_to_dict(template_site) or {}
    name = (template.get('name') or 'LeadResponse Site').strip() or 'LeadResponse Site'
    booking_url = template.get('booking_url') or ''
    connect_token = f"connect_{secrets.token_hex(8)}"
    site_token = f"site_{secrets.token_hex(12)}"
    site_secret = f"secret_{secrets.token_hex(24)}"

    if get_db_backend() == 'postgres':
        cur = conn.cursor()
        cur.execute(
            sql('INSERT INTO sites (name, domain, connect_token, site_token, site_secret, status, booking_url, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING id'),
            (name, requested_domain, connect_token, site_token, site_secret, 'connected', booking_url, updated_at, updated_at)
        )
        inserted = cur.fetchone()
        new_id = inserted['id'] if isinstance(inserted, dict) else inserted[0]
    else:
        cur = conn.cursor()
        cur.execute(
            sql('INSERT INTO sites (name, domain, connect_token, site_token, site_secret, status, booking_url, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)'),
            (name, requested_domain, connect_token, site_token, site_secret, 'connected', booking_url, updated_at, updated_at)
        )
        new_id = cur.lastrowid

    create_lead_event(conn, new_id, None, 'site_cloned_from_connect_template', {
        'template_site_id': template.get('id'),
        'requested_domain': requested_domain,
    }, updated_at)
    return {'id': new_id, 'site_token': site_token, 'site_secret': site_secret, 'status': 'connected'}


@app.route('/api/v1/sites/connect', methods=['POST'])
def connect_site():
    payload = request.get_json(silent=True) or {}
    connect_token = (payload.get('connect_token') or '').strip()
    domain = (payload.get('domain') or '').strip()
    requested_domain = parse_site_hostname(domain)

    if not connect_token:
        return jsonify({'error': 'Missing connect token.'}), 400

    conn = db()
    site = conn.execute(sql('SELECT * FROM sites WHERE connect_token = ?'), (connect_token,)).fetchone()
    if not site:
        return jsonify({'error': 'Invalid connect token.'}), 404

    site_data = row_to_dict(site)
    existing_domain = parse_site_hostname(site_data.get('domain') or '')
    updated_at = now_iso()

    if site_data.get('site_token') and requested_domain and existing_domain and existing_domain != requested_domain:
        connected_clone = create_connected_site_from_template(conn, site_data, domain, updated_at)
        conn.commit()
        return jsonify(connected_clone)

    site_token = site_data.get('site_token') or f"site_{secrets.token_hex(12)}"
    site_secret = site_data.get('site_secret') or f"secret_{secrets.token_hex(24)}"

    conn.execute(
        sql('UPDATE sites SET domain = ?, site_token = ?, site_secret = ?, status = ?, updated_at = ? WHERE id = ?'),
        (domain, site_token, site_secret, 'connected', updated_at, site_data['id'])
    )
    conn.commit()

    return jsonify({'site_id': site_data['id'], 'site_token': site_token, 'site_secret': site_secret, 'status': 'connected'})

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
        'primary_color': settings.get('widget_button_color') or '#2575fc',
        **settings
    })



@app.route('/api/v1/sites/white-label-config', methods=['GET'])
def white_label_config():
    site_token = (request.args.get('site_token') or '').strip()
    site = get_site_by_token(site_token)
    if not site:
        return jsonify({'error': 'Site not found.'}), 404
    return jsonify(public_white_label_settings(site))


@app.route('/api/v1/sites/white-label-status', methods=['GET'])
def white_label_status():
    site_token = (request.args.get('site_token') or '').strip()
    site = get_site_by_token(site_token)
    if not site:
        return jsonify({'error': 'Site not found.'}), 404
    settings = get_white_label_settings(site)
    return jsonify({
        'white_label_enabled': settings['white_label_enabled'],
        'portal_domain_status': settings['portal_domain_status'],
        'email_domain_status': settings['email_domain_status'],
        'dns_last_checked_at': settings.get('dns_last_checked_at') or '',
        'delivery_mode': settings.get('delivery_mode') or 'platform_domain',
        'transport_mode': settings.get('transport_mode') or 'platform_smtp',
        'requested_transport_mode': settings.get('requested_transport_mode') or 'platform_smtp',
        'transport_summary': settings.get('transport_summary') or '',
        'active_from_email': settings.get('active_from_email') or '',
        'branded_sender_email': settings.get('branded_sender_email') or '',
        'smtp_host': settings.get('smtp_host') or '',
        'smtp_username': settings.get('smtp_username') or '',
        'smtp_password_saved': bool(settings.get('smtp_password_saved')),
    })



@app.route('/api/v1/sites/white-label-settings/update', methods=['POST'])
def update_white_label_settings_api():
    payload = request.get_json(silent=True) or {}
    site_token = (payload.get('site_token') or '').strip()
    site_secret = (payload.get('site_secret') or '').strip()
    site = get_site_by_token(site_token)
    if not site:
        return jsonify({'error': 'Invalid site token.'}), 404
    if not site_secret or site_secret != site['site_secret']:
        return jsonify({'error': 'Invalid site secret.'}), 403

    existing = get_white_label_settings(site)
    white_label_enabled = 1 if coerce_bool(payload.get('white_label_enabled'), existing.get('white_label_enabled', False)) else 0
    brand_display_name = (payload.get('brand_display_name') or existing.get('brand_display_name') or 'LeadResponse').strip() or 'LeadResponse'
    portal_subdomain = clean_subdomain_label(payload.get('portal_subdomain') or existing.get('portal_subdomain') or 'go', 'go')
    email_subdomain = clean_subdomain_label(payload.get('email_subdomain') or existing.get('email_subdomain') or 'em', 'em')
    email_provider = clean_email_provider(payload.get('email_provider') or existing.get('email_provider') or 'mailgun', 'mailgun')
    email_from_localpart = clean_localpart(payload.get('email_from_localpart') or existing.get('email_from_localpart') or 'hello', 'hello')
    reply_to_email = clean_email_value(payload.get('reply_to_email') or '')
    reply_handling_mode = clean_reply_handling_mode(payload.get('reply_handling_mode') or existing.get('reply_handling_mode') or 'lead_inbox', 'lead_inbox')
    delivery_mode = (payload.get('delivery_mode') or existing.get('delivery_mode') or 'platform_domain').strip() or 'platform_domain'
    if delivery_mode not in ('personal_mailbox', 'branded_domain', 'platform_domain'):
        delivery_mode = 'platform_domain'
    sender_name = (payload.get('sender_name') or existing.get('sender_name') or '').strip()
    sender_email = clean_email_value(payload.get('sender_email') or existing.get('sender_email') or '')
    transport_mode = (payload.get('transport_mode') or existing.get('requested_transport_mode') or existing.get('transport_mode') or 'platform_smtp').strip() or 'platform_smtp'
    if transport_mode not in ('site_smtp', 'platform_smtp'):
        transport_mode = 'platform_smtp'
    smtp_host = (payload.get('smtp_host') or existing.get('smtp_host') or '').strip()
    smtp_port = int(payload.get('smtp_port') or existing.get('smtp_port') or 587)
    smtp_username = (payload.get('smtp_username') or existing.get('smtp_username') or '').strip()
    incoming_password = payload.get('smtp_password')
    smtp_password = existing.get('smtp_password') or ''
    if incoming_password is not None and str(incoming_password) != '':
        smtp_password = str(incoming_password)
    smtp_use_ssl = 1 if coerce_bool(payload.get('smtp_use_ssl'), existing.get('smtp_use_ssl', False)) else 0
    smtp_use_tls = 1 if coerce_bool(payload.get('smtp_use_tls'), existing.get('smtp_use_tls', True)) else 0
    portal_domain_status = normalize_white_label_status(payload.get('portal_domain_status'), existing.get('portal_domain_status') or 'not_started')
    email_domain_status = normalize_white_label_status(payload.get('email_domain_status'), existing.get('email_domain_status') or 'not_started')
    dns_last_checked_at = (payload.get('dns_last_checked_at') or existing.get('dns_last_checked_at') or '').strip()
    use_global_email_templates = 1 if coerce_bool(payload.get('use_global_email_templates'), existing.get('use_global_email_templates', True)) else 0
    ack_subject_template = normalize_template_text(payload.get('ack_subject_template') if 'ack_subject_template' in payload else existing.get('ack_subject_template'), EMAIL_TEMPLATE_DEFAULTS['ack_subject_template'])
    ack_body_template = normalize_template_text(payload.get('ack_body_template') if 'ack_body_template' in payload else existing.get('ack_body_template'), EMAIL_TEMPLATE_DEFAULTS['ack_body_template'])
    followup_1_subject_template = normalize_template_text(payload.get('followup_1_subject_template') if 'followup_1_subject_template' in payload else existing.get('followup_1_subject_template'), EMAIL_TEMPLATE_DEFAULTS['followup_1_subject_template'])
    followup_1_body_template = normalize_template_text(payload.get('followup_1_body_template') if 'followup_1_body_template' in payload else existing.get('followup_1_body_template'), EMAIL_TEMPLATE_DEFAULTS['followup_1_body_template'])
    followup_2_subject_template = normalize_template_text(payload.get('followup_2_subject_template') if 'followup_2_subject_template' in payload else existing.get('followup_2_subject_template'), EMAIL_TEMPLATE_DEFAULTS['followup_2_subject_template'])
    followup_2_body_template = normalize_template_text(payload.get('followup_2_body_template') if 'followup_2_body_template' in payload else existing.get('followup_2_body_template'), EMAIL_TEMPLATE_DEFAULTS['followup_2_body_template'])
    followup_3_subject_template = normalize_template_text(payload.get('followup_3_subject_template') if 'followup_3_subject_template' in payload else existing.get('followup_3_subject_template'), EMAIL_TEMPLATE_DEFAULTS['followup_3_subject_template'])
    followup_3_body_template = normalize_template_text(payload.get('followup_3_body_template') if 'followup_3_body_template' in payload else existing.get('followup_3_body_template'), EMAIL_TEMPLATE_DEFAULTS['followup_3_body_template'])
    updated_at = now_iso()

    conn = db()
    conn.execute(
        sql('UPDATE sites SET white_label_enabled = ?, brand_display_name = ?, portal_subdomain = ?, portal_domain_status = ?, email_subdomain = ?, email_domain_status = ?, dns_last_checked_at = ?, email_provider = ?, email_from_localpart = ?, reply_to_email = ?, reply_handling_mode = ?, delivery_mode = ?, sender_name = ?, sender_email = ?, transport_mode = ?, smtp_host = ?, smtp_port = ?, smtp_username = ?, smtp_password = ?, smtp_use_ssl = ?, smtp_use_tls = ?, use_global_email_templates = ?, ack_subject_template = ?, ack_body_template = ?, followup_1_subject_template = ?, followup_1_body_template = ?, followup_2_subject_template = ?, followup_2_body_template = ?, followup_3_subject_template = ?, followup_3_body_template = ?, updated_at = ? WHERE id = ?'),
        (white_label_enabled, brand_display_name, portal_subdomain, portal_domain_status, email_subdomain, email_domain_status, dns_last_checked_at, email_provider, email_from_localpart, reply_to_email, reply_handling_mode, delivery_mode, sender_name, sender_email, transport_mode, smtp_host, smtp_port, smtp_username, smtp_password, smtp_use_ssl, smtp_use_tls, use_global_email_templates, ack_subject_template, ack_body_template, followup_1_subject_template, followup_1_body_template, followup_2_subject_template, followup_2_body_template, followup_3_subject_template, followup_3_body_template, updated_at, site['id'])
    )
    create_lead_event(conn, site['id'], None, 'white_label_settings_updated', {
        'white_label_enabled': bool(white_label_enabled),
        'brand_display_name': brand_display_name,
        'portal_subdomain': portal_subdomain,
        'portal_domain_status': portal_domain_status,
        'email_subdomain': email_subdomain,
        'email_domain_status': email_domain_status,
        'email_provider': email_provider,
        'email_from_localpart': email_from_localpart,
        'reply_to_email': reply_to_email,
        'reply_handling_mode': reply_handling_mode,
        'delivery_mode': delivery_mode,
        'sender_name': sender_name,
        'sender_email': sender_email,
        'transport_mode': transport_mode,
        'smtp_host': smtp_host,
        'smtp_port': smtp_port,
        'smtp_username': smtp_username,
        'smtp_password_saved': bool(smtp_password),
        'smtp_use_ssl': bool(smtp_use_ssl),
        'smtp_use_tls': bool(smtp_use_tls),
        'dns_last_checked_at': dns_last_checked_at,
        'use_global_email_templates': bool(use_global_email_templates),
        'email_template_overrides_active': not bool(use_global_email_templates),
    }, updated_at)
    conn.commit()

    refreshed = db().execute(sql('SELECT * FROM sites WHERE id = ?'), (site['id'],)).fetchone()
    return jsonify({'success': True, 'item': public_white_label_settings(refreshed)})

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
        queued_payload = {
            'to': (lead.get('email') or '').strip(),
            'delivery_mode': resolve_mail_profile(site_data).get('delivery_mode') or 'platform_domain',
        }
        log_mail_event('AUTO_ACK_QUEUED', {'site_id': site['id'], 'lead_id': lead_id, **queued_payload})
        create_lead_event(conn, site['id'], lead_id, 'auto_ack_queued', queued_payload, now_iso())
        threading.Thread(
            target=send_ack_email_async,
            args=(site_data, dict(lead), site['id'], lead_id),
            daemon=True,
        ).start()
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



def summarize_timeline_payload(payload):
    payload = payload or {}
    if not isinstance(payload, dict):
        return str(payload)
    for key in ('reply_preview_clean', 'body_text_clean', 'body_text', 'message', 'subject', 'notes', 'error'):
        value = (payload.get(key) or '').strip() if isinstance(payload.get(key), str) else payload.get(key)
        if isinstance(value, str) and value:
            return value
    status = (payload.get('status') or '').strip() if isinstance(payload.get('status'), str) else ''
    if status:
        return f"Status changed to {status}."
    return ''


def enrich_lead_for_inbox(lead):
    lead = dict(lead or {})
    lead_id = lead.get('id')
    if not lead_id:
        lead['reply_count'] = 0
        lead['latest_reply_at'] = ''
        lead['latest_reply_preview'] = ''
        lead['latest_event_type'] = ''
        lead['latest_event_at'] = lead.get('created_at') or ''
        lead['latest_activity_at'] = lead.get('created_at') or ''
        return lead

    reply_count = 0
    latest_reply = latest_event_for_lead(lead_id, 'customer_reply_received') or {}
    try:
        reply_count_row = db().execute(
            sql('SELECT COUNT(*) AS reply_count FROM lead_events WHERE lead_id = ? AND event_type = ?'),
            (lead_id, 'customer_reply_received')
        ).fetchone()
        if reply_count_row is not None:
            reply_count = int((row_to_dict(reply_count_row).get('reply_count') or 0))
    except Exception:
        reply_count = 0

    latest_event_dict = latest_lead_event(lead_id) or {}
    latest_reply_payload = parse_payload(latest_reply.get('payload_json')) if latest_reply else {}
    latest_reply_preview = summarize_timeline_payload(latest_reply_payload)
    if len(latest_reply_preview) > 220:
        latest_reply_preview = latest_reply_preview[:220] + '…'

    latest_reply_at = event_occurred_at_iso(latest_reply) if latest_reply else ''
    latest_event_at = event_occurred_at_iso(latest_event_dict) if latest_event_dict else (lead.get('created_at') or '')

    lead['reply_count'] = reply_count
    lead['latest_reply_at'] = latest_reply_at
    lead['latest_reply_preview'] = latest_reply_preview
    lead['latest_event_type'] = latest_event_dict.get('event_type') or ''
    lead['latest_event_at'] = latest_event_at
    lead['latest_activity_at'] = latest_reply_at or latest_event_at or lead.get('created_at') or ''
    return lead


def lead_timeline_for_api(lead_id, limit=50):
    rows = db().execute(
        sql('SELECT * FROM lead_events WHERE lead_id = ? ORDER BY id DESC LIMIT ?'),
        (lead_id, int(limit) * 4)
    ).fetchall()
    events = []
    for row in rows:
        event = row_to_dict(row)
        event['payload'] = parse_payload(event.get('payload_json'))
        events.append(event)
    return sort_events_newest_first(events)[:int(limit)]


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
    items = [enrich_lead_for_inbox(row_to_dict(r)) for r in rows]
    return jsonify({'items': items})


@app.route('/api/v1/leads/<int:lead_id>', methods=['GET'])
def get_lead(lead_id):
    site_token = (request.args.get('site_token') or '').strip()
    include_events = (request.args.get('include_events') or '').strip().lower() in ('1', 'true', 'yes', 'on')

    params = [lead_id]
    query = 'SELECT * FROM leads WHERE id = ?'
    if site_token:
        site = get_site_by_token(site_token)
        if not site:
            return jsonify({'error': 'Invalid site token.'}), 404
        query += ' AND site_id = ?'
        params.append(site['id'])

    row = db().execute(sql(query), params).fetchone()
    if not row:
        return jsonify({'error': 'Lead not found.'}), 404

    lead = enrich_lead_for_inbox(row_to_dict(row))
    if not include_events:
        return jsonify(lead)

    events = lead_timeline_for_api(lead_id, 60)
    return jsonify({
        'item': lead,
        'events': events,
    })


@app.route('/api/v1/leads/<int:lead_id>/delete', methods=['POST'])
def delete_lead_api(lead_id):
    payload = request.get_json(silent=True) or {}
    site_token = (payload.get('site_token') or '').strip()
    site = get_site_by_token(site_token)
    if not site:
        return jsonify({'error': 'Invalid site token.'}), 404

    lead = db().execute(sql('SELECT * FROM leads WHERE id = ? AND site_id = ?'), (lead_id, site['id'])).fetchone()
    if not lead:
        return jsonify({'error': 'Lead not found.'}), 404

    conn = db()
    conn.execute(sql('DELETE FROM lead_events WHERE lead_id = ? AND site_id = ?'), (lead_id, site['id']))
    conn.execute(sql('DELETE FROM leads WHERE id = ? AND site_id = ?'), (lead_id, site['id']))
    conn.commit()
    return jsonify({'success': True, 'deleted_lead_id': lead_id})


@app.route('/api/v1/leads/<int:lead_id>/send-email', methods=['POST'])
def send_manual_lead_email_api(lead_id):
    payload = request.get_json(silent=True) or {}
    site_token = (payload.get('site_token') or '').strip()
    site = get_site_by_token(site_token)
    if not site:
        return jsonify({'error': 'Invalid site token.'}), 404

    lead_row = db().execute(sql('SELECT * FROM leads WHERE id = ? AND site_id = ?'), (lead_id, site['id'])).fetchone()
    if not lead_row:
        return jsonify({'error': 'Lead not found.'}), 404

    result = send_manual_lead_email(row_to_dict(site), row_to_dict(lead_row), payload.get('subject') or '', payload.get('body_text') or '', payload.get('status_after_send') or 'open')
    if not result.get('ok'):
        return jsonify({'error': result.get('mail_result', {}).get('error') or result.get('error') or 'Manual email failed.', 'item': result.get('item') or {}}), 400

    return jsonify({'success': True, 'item': result.get('item') or {}, 'mail_result': result.get('mail_result') or {}})


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


@app.route('/api/v1/replies/check', methods=['POST'])
def check_replies_api():
    payload = request.get_json(silent=True) or {}
    site_token = (payload.get('site_token') or '').strip()
    site_secret = (payload.get('site_secret') or '').strip()

    site = get_site_by_token(site_token)
    if not site:
        return jsonify({'error': 'Invalid site token.'}), 404
    if not site_secret or site_secret != site['site_secret']:
        return jsonify({'error': 'Invalid site secret.'}), 403

    sync_result = poll_inbound_replies_for_site(row_to_dict(site))
    refreshed = db().execute(sql('SELECT * FROM sites WHERE id = ?'), (site['id'],)).fetchone()
    public = public_white_label_settings(refreshed)
    return jsonify({
        'success': bool(sync_result.get('success')),
        'imap_configured': bool(sync_result.get('imap_configured')),
        'replies_ingested': int(sync_result.get('replies_ingested') or 0),
        'last_checked_at': sync_result.get('last_checked_at') or now_iso(),
        'message': sync_result.get('message') or 'Reply sync completed.',
        'error': sync_result.get('error') or '',
        'item': public,
    })


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
    replies_ingested = 0
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
        result = send_email_message(site_data, (lead.get('email') or '').strip(), subject, body)
        event_payload = {**result, 'subject': subject, 'body_text': body, 'step_number': step_to_send}
        create_lead_event(conn, site['id'], lead['id'], f'follow_up_{step_to_send}_sent', event_payload, now_iso())
        follow_ups_sent += 1

        if step_to_send == 1 and site['booking_url']:
            update_lead_status(conn, lead['id'], 'booking_sent')
        if step_to_send == 3:
            update_lead_status(conn, lead['id'], 'no_response')
            create_lead_event(conn, site['id'], lead['id'], 'lead_marked_no_response', {'reason': 'final_follow_up_sent'}, now_iso())
            no_response_marked += 1

    conn.commit()
    reply_sync = poll_inbound_replies_for_site(site_data)
    return jsonify({
        'success': True,
        'processed': processed,
        'follow_ups_sent': follow_ups_sent,
        'no_response_marked': no_response_marked,
        'replies_ingested': int(reply_sync.get('replies_ingested') or 0),
        'imap_configured': bool(reply_sync.get('imap_configured')),
        'last_checked_at': reply_sync.get('last_checked_at') or now_iso(),
        'message': 'Website follow-up engine run completed.'
    })


@app.route('/health')
def health():
    return jsonify({'ok': True, 'time': now_iso(), 'db_backend': get_db_backend(), 'mail_mode': 'smtp' if (os.getenv('SMTP_HOST') or '').strip() else 'simulation'})


init_db()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
