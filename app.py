from flask import Flask, request, jsonify, g
import sqlite3
import secrets
from pathlib import Path
from datetime import datetime, timezone

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / 'leadresponse.sqlite'

app = Flask(__name__)
app.config['JSON_SORT_KEYS'] = False


def db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    conn = g.pop('db', None)
    if conn is not None:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.executescript('''
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
    );

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
    );

    CREATE TABLE IF NOT EXISTS lead_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        site_id INTEGER NOT NULL,
        lead_id INTEGER,
        event_type TEXT NOT NULL,
        payload_json TEXT,
        created_at TEXT NOT NULL,
        FOREIGN KEY(site_id) REFERENCES sites(id),
        FOREIGN KEY(lead_id) REFERENCES leads(id)
    );
    ''')
    conn.commit()
    conn.close()


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')


def row_to_dict(row):
    return {key: row[key] for key in row.keys()}


def get_site_by_token(site_token):
    return db().execute('SELECT * FROM sites WHERE site_token = ?', (site_token,)).fetchone()


@app.after_request
def add_cors(resp):
    resp.headers['Access-Control-Allow-Origin'] = '*'
    resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
    return resp


@app.route('/api/v1/sites/connect', methods=['POST'])
def connect_site():
    payload = request.get_json(silent=True) or {}
    connect_token = (payload.get('connect_token') or '').strip()
    domain = (payload.get('domain') or '').strip()

    if not connect_token:
        return jsonify({'error': 'Missing connect token.'}), 400

    conn = db()
    site = conn.execute('SELECT * FROM sites WHERE connect_token = ?', (connect_token,)).fetchone()
    if not site:
        return jsonify({'error': 'Invalid connect token.'}), 404

    site_token = site['site_token'] or f"site_{secrets.token_hex(12)}"
    site_secret = site['site_secret'] or f"secret_{secrets.token_hex(24)}"
    updated_at = now_iso()

    conn.execute(
        'UPDATE sites SET domain = ?, site_token = ?, site_secret = ?, status = ?, updated_at = ? WHERE id = ?',
        (domain, site_token, site_secret, 'connected', updated_at, site['id'])
    )
    conn.commit()

    return jsonify({
        'site_id': site['id'],
        'site_token': site_token,
        'site_secret': site_secret,
        'status': 'connected'
    })


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

    return jsonify({
        'brand_name': 'LeadResponse',
        'primary_color': '#2575fc',
        'widget_enabled': bool(site['widget_enabled']),
        'welcome_message': site['welcome_message'],
        'booking_url': site['booking_url'] or ''
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
    cur = conn.cursor()
    cur.execute(
        'INSERT INTO leads (site_id, source, first_name, email, phone, message, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
        (
            site['id'],
            source,
            (lead.get('first_name') or '').strip(),
            (lead.get('email') or '').strip(),
            (lead.get('phone') or '').strip(),
            (lead.get('message') or '').strip(),
            'new',
            created_at,
        )
    )
    lead_id = cur.lastrowid
    cur.execute(
        'INSERT INTO lead_events (site_id, lead_id, event_type, payload_json, created_at) VALUES (?, ?, ?, ?, ?)',
        (site['id'], lead_id, payload.get('event_type') or 'lead_created', str(payload), created_at)
    )
    conn.commit()

    return jsonify({
        'success': True,
        'lead_id': lead_id,
        'status': 'new',
        'next_action': {
            'type': 'booking_prompt' if site['booking_url'] else 'message',
            'booking_url': site['booking_url'] or '',
            'message': 'Lead captured successfully.'
        }
    })


@app.route('/api/v1/leads', methods=['GET'])
def list_leads():
    site_token = (request.args.get('site_token') or '').strip()
    site = get_site_by_token(site_token)
    if not site:
        return jsonify({'error': 'Invalid site token.'}), 404

    rows = db().execute('SELECT * FROM leads WHERE site_id = ? ORDER BY id DESC LIMIT 100', (site['id'],)).fetchall()
    return jsonify({'items': [row_to_dict(r) for r in rows]})


@app.route('/api/v1/leads/<int:lead_id>', methods=['GET'])
def get_lead(lead_id):
    row = db().execute('SELECT * FROM leads WHERE id = ?', (lead_id,)).fetchone()
    if not row:
        return jsonify({'error': 'Lead not found.'}), 404
    return jsonify(row_to_dict(row))

init_db()

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
