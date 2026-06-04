from flask import Flask, request, jsonify, g, render_template_string, redirect, url_for
import sqlite3
import secrets
import json
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


def get_default_site():
    return db().execute(
        "SELECT * FROM sites WHERE status = 'connected' ORDER BY id ASC LIMIT 1"
    ).fetchone() or db().execute('SELECT * FROM sites ORDER BY id ASC LIMIT 1').fetchone()


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
        data = json.loads(payload_json.replace("'", '"'))
        return data if isinstance(data, dict) else {'raw': data}
    except Exception:
        return {'raw': payload_json}


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
      --radius-sm: 12px;
      --max: 1180px;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: linear-gradient(180deg, #f8fbff 0%, #f4f7fb 100%);
      color: var(--ink);
    }
    a { color: var(--blue); text-decoration: none; }
    .shell {
      max-width: var(--max);
      margin: 0 auto;
      padding: 28px 20px 40px;
    }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 20px;
      margin-bottom: 22px;
      flex-wrap: wrap;
    }
    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 800;
      letter-spacing: -0.02em;
      color: var(--ink);
    }
    .brand-mark {
      width: 42px;
      height: 42px;
      border-radius: 14px;
      background: linear-gradient(135deg, var(--blue) 0%, #5ea1ff 100%);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      color: #fff;
      box-shadow: 0 10px 24px rgba(37, 117, 252, 0.28);
      font-size: 18px;
      font-weight: 900;
    }
    .nav {
      display: flex;
      align-items: center;
      gap: 10px;
      flex-wrap: wrap;
    }
    .nav a {
      background: #fff;
      border: 1px solid var(--line);
      color: var(--ink);
      padding: 10px 14px;
      border-radius: 999px;
      font-weight: 600;
      transition: .18s ease;
    }
    .nav a:hover, .nav a.active {
      transform: translateY(-1px);
      border-color: rgba(37,117,252,.35);
      background: var(--blue-soft);
      color: var(--blue-dark);
    }
    .hero {
      background: linear-gradient(135deg, #17233a 0%, #233a68 100%);
      color: #fff;
      border-radius: 26px;
      padding: 28px;
      box-shadow: var(--shadow);
      position: relative;
      overflow: hidden;
      margin-bottom: 24px;
    }
    .hero:before {
      content: '';
      position: absolute;
      inset: auto -60px -60px auto;
      width: 220px;
      height: 220px;
      background: radial-gradient(circle, rgba(94,161,255,.35), rgba(94,161,255,0));
    }
    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      background: rgba(255,255,255,.1);
      border: 1px solid rgba(255,255,255,.14);
      color: #dbe7ff;
      border-radius: 999px;
      padding: 8px 12px;
      font-size: 13px;
      font-weight: 700;
      margin-bottom: 14px;
    }
    h1, h2, h3 { margin: 0 0 10px; letter-spacing: -0.03em; }
    h1 { font-size: clamp(30px, 4vw, 44px); line-height: 1.05; }
    h2 { font-size: clamp(24px, 3vw, 32px); }
    h3 { font-size: 18px; }
    p { margin: 0; color: var(--muted); line-height: 1.65; }
    .hero p { color: rgba(255,255,255,.84); max-width: 760px; }
    .grid { display: grid; gap: 18px; }
    .grid.stats { grid-template-columns: repeat(4, minmax(0, 1fr)); margin-top: 22px; }
    .stat {
      background: rgba(255,255,255,.08);
      border: 1px solid rgba(255,255,255,.10);
      border-radius: 18px;
      padding: 18px;
      transition: .18s ease;
    }
    .stat:hover { transform: translateY(-2px); background: rgba(255,255,255,.11); }
    .stat .label { color: rgba(255,255,255,.75); font-size: 13px; font-weight: 700; }
    .stat .value { color: #fff; font-size: 28px; font-weight: 800; margin-top: 8px; }
    .stack { display: grid; gap: 18px; }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      box-shadow: var(--shadow);
      padding: 22px;
    }
    .panel-header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 14px;
      margin-bottom: 18px;
      flex-wrap: wrap;
    }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 14px;
    }
    .pill {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 9px 12px;
      border-radius: 999px;
      font-size: 13px;
      font-weight: 700;
      background: var(--blue-soft);
      color: var(--blue-dark);
      border: 1px solid rgba(37,117,252,.16);
    }
    .pill.neutral { background: #f6f8fb; color: #41506d; border-color: var(--line); }
    .pill.success { background: #ebfff4; color: #09814a; border-color: #ccefdc; }
    .kpis {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
    }
    .kpi {
      padding: 16px;
      border-radius: 16px;
      background: #f9fbff;
      border: 1px solid var(--line);
      transition: .18s ease;
    }
    .kpi:hover { transform: translateY(-2px); box-shadow: 0 8px 20px rgba(17,36,77,.06); }
    .kpi small { display:block; font-size: 12px; color: var(--muted); font-weight: 700; text-transform: uppercase; letter-spacing: .05em; }
    .kpi strong { display:block; font-size: 24px; margin-top: 7px; }
    table {
      width: 100%;
      border-collapse: collapse;
    }
    thead th {
      text-align: left;
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: .05em;
      color: var(--muted);
      padding: 12px 10px;
      border-bottom: 1px solid var(--line);
    }
    tbody td {
      padding: 14px 10px;
      border-bottom: 1px solid #edf2f8;
      vertical-align: top;
      color: #24314b;
    }
    tbody tr {
      transition: .16s ease;
    }
    tbody tr:hover {
      background: #fafcff;
    }
    .lead-name {
      font-weight: 800;
      color: var(--ink);
      margin-bottom: 4px;
    }
    .muted { color: var(--muted); }
    .badge {
      display: inline-flex;
      align-items: center;
      padding: 6px 10px;
      border-radius: 999px;
      font-weight: 700;
      font-size: 12px;
      line-height: 1;
      border: 1px solid transparent;
      text-transform: capitalize;
    }
    .badge-new { background: #ebfff4; color: #0c8b51; border-color: #ccefdc; }
    .badge-open { background: #fff5df; color: #996600; border-color: #f5dfb0; }
    .badge-won { background: #eaf2ff; color: var(--blue-dark); border-color: #d5e3ff; }
    .badge-lost { background: #fff0f0; color: #b33a3a; border-color: #f4d1d1; }
    .action {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 10px 14px;
      border-radius: 12px;
      border: 1px solid rgba(37,117,252,.18);
      background: var(--blue-soft);
      color: var(--blue-dark);
      font-weight: 700;
      transition: .16s ease;
    }
    .action:hover { transform: translateY(-1px); background: #dce9ff; }
    .empty {
      border: 2px dashed var(--line);
      border-radius: 18px;
      padding: 28px;
      background: #fbfdff;
      text-align: center;
    }
    .detail-grid {
      display: grid;
      grid-template-columns: 1.15fr .85fr;
      gap: 18px;
    }
    .info-list {
      display: grid;
      gap: 12px;
    }
    .info-item {
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 14px;
      background: #fbfdff;
    }
    .info-item .label {
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .05em;
      font-weight: 800;
      margin-bottom: 7px;
    }
    .message-box, pre {
      background: #f8fbff;
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 18px;
      color: #22304b;
      white-space: pre-wrap;
      word-break: break-word;
    }
    pre { margin: 0; font-size: 13px; line-height: 1.55; overflow: auto; }
    .footer-note {
      margin-top: 18px;
      font-size: 13px;
      color: var(--muted);
      text-align: center;
    }
    @media (max-width: 960px) {
      .grid.stats, .kpis, .detail-grid { grid-template-columns: 1fr; }
    }
    @media (max-width: 720px) {
      .shell { padding: 18px 14px 28px; }
      .hero, .panel { padding: 18px; }
      table, thead, tbody, th, td, tr { display: block; }
      thead { display: none; }
      tbody tr {
        border: 1px solid var(--line);
        border-radius: 16px;
        padding: 10px;
        margin-bottom: 12px;
        background: #fff;
      }
      tbody td {
        border: 0;
        padding: 7px 6px;
      }
      tbody td:before {
        content: attr(data-label);
        display: block;
        color: var(--muted);
        font-size: 11px;
        text-transform: uppercase;
        letter-spacing: .05em;
        font-weight: 800;
        margin-bottom: 6px;
      }
    }
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
    <div class="footer-note">LeadResponse v0.2 test dashboard · built for inbox and lead detail review</div>
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
    site = get_site_by_token(site_token) if site_token else get_default_site()

    if site and site['site_token'] and not site_token:
        return redirect(url_for('dashboard', site_token=site['site_token']))

    sites = db().execute('SELECT * FROM sites ORDER BY id ASC').fetchall()

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

    lead_rows = db().execute('SELECT * FROM leads WHERE site_id = ? ORDER BY id DESC LIMIT 100', (site['id'],)).fetchall()
    total_leads = db().execute('SELECT COUNT(*) AS c FROM leads WHERE site_id = ?', (site['id'],)).fetchone()['c']
    new_leads = db().execute("SELECT COUNT(*) AS c FROM leads WHERE site_id = ? AND status = 'new'", (site['id'],)).fetchone()['c']
    today_leads = db().execute("SELECT COUNT(*) AS c FROM leads WHERE site_id = ? AND substr(created_at,1,10) = substr(?,1,10)", (site['id'], now_iso())).fetchone()['c']
    latest = lead_rows[0] if lead_rows else None

    body = render_template_string('''
    <section class="hero">
      <div class="eyebrow">Lead inbox · Site #{{ site['id'] }}</div>
      <h1>{{ site['name'] or 'Lead Inbox' }}</h1>
      <p>Review new submissions, open individual lead records, and confirm that your WordPress plugin is sending data into the SaaS backend correctly.</p>
      <div class="grid stats">
        <div class="stat"><div class="label">Total Leads</div><div class="value">{{ total_leads }}</div></div>
        <div class="stat"><div class="label">New</div><div class="value">{{ new_leads }}</div></div>
        <div class="stat"><div class="label">Today</div><div class="value">{{ today_leads }}</div></div>
        <div class="stat"><div class="label">Widget</div><div class="value">{{ 'On' if site['widget_enabled'] else 'Off' }}</div></div>
      </div>
    </section>

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
        </div>
      </section>

      <section class="panel">
        <div class="panel-header">
          <div>
            <h2>Lead inbox</h2>
            <p>Latest 100 leads received for this connected site.</p>
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
              <th>Contact</th>
              <th>Message</th>
              <th>Status</th>
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
              </td>
              <td data-label="Contact">
                <div>{{ lead['email'] or '—' }}</div>
                <div class="muted">{{ lead['phone'] or '—' }}</div>
              </td>
              <td data-label="Message">{{ (lead['message'] or '—')[:120] }}{% if lead['message'] and lead['message']|length > 120 %}…{% endif %}</td>
              <td data-label="Status"><span class="badge badge-{{ lead['status'] if lead['status'] in ['new', 'open', 'won', 'lost'] else 'open' }}">{{ lead['status'] }}</span></td>
              <td data-label="Received">{{ fmt_dt(lead['created_at']) }}</td>
              <td data-label="Open"><a class="action" href="{{ url_for('lead_detail_page', lead_id=lead['id'], site_token=site['site_token']) }}">View</a></td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
        {% else %}
        <div class="empty">
          <h3>No leads yet</h3>
          <p>Submit a test lead through the widget and it will appear here.</p>
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
    ''', site=site, lead_rows=lead_rows, total_leads=total_leads, new_leads=new_leads, today_leads=today_leads, latest=latest, sites=sites, fmt_dt=fmt_dt)

    return render_page(body, title='LeadResponse Lead Inbox', current_site=site)


@app.route('/dashboard/leads/<int:lead_id>')
def lead_detail_page(lead_id):
    site_token = (request.args.get('site_token') or '').strip()
    current_site = get_site_by_token(site_token) if site_token else get_default_site()

    lead = db().execute('SELECT * FROM leads WHERE id = ?', (lead_id,)).fetchone()
    if not lead:
        body = render_template_string('''
        <section class="panel">
          <h2>Lead not found</h2>
          <p>The lead you requested does not exist.</p>
          <div class="meta" style="margin-top:14px;"><a class="action" href="{{ back_url }}">Back to inbox</a></div>
        </section>
        ''', back_url=url_for('dashboard', site_token=current_site['site_token']) if current_site and current_site['site_token'] else url_for('dashboard'))
        return render_page(body, title='Lead not found', current_site=current_site)

    site = db().execute('SELECT * FROM sites WHERE id = ?', (lead['site_id'],)).fetchone()
    events = db().execute('SELECT * FROM lead_events WHERE lead_id = ? ORDER BY id DESC', (lead_id,)).fetchall()

    body = render_template_string('''
    <section class="hero">
      <div class="eyebrow">Lead detail · #{{ lead['id'] }}</div>
      <h1>{{ lead['first_name'] or 'Unknown lead' }}</h1>
      <p>Review the captured contact details, original message, linked site record and raw event payload for testing and development.</p>
      <div class="meta">
        <span class="pill">Status: {{ lead['status'] }}</span>
        <span class="pill neutral">Source: {{ lead['source'] }}</span>
        <span class="pill neutral">Received: {{ fmt_dt(lead['created_at']) }}</span>
      </div>
    </section>

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
          <div class="info-item"><div class="label">Message</div><div class="message-box">{{ lead['message'] or '—' }}</div></div>
        </div>
      </section>

      <div class="stack">
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
    ''', lead=lead, site=site, current_site=current_site, events=events, fmt_dt=fmt_dt, parse_payload=parse_payload)

    return render_page(body, title=f"Lead #{lead['id']} · LeadResponse", current_site=site or current_site)


@app.route('/seed-demo')
def seed_demo():
    token = 'connect_demo_12345'
    conn = db()
    site = conn.execute('SELECT * FROM sites WHERE connect_token = ?', (token,)).fetchone()
    if not site:
        now = now_iso()
        conn.execute(
            'INSERT INTO sites (name, connect_token, booking_url, created_at, updated_at) VALUES (?, ?, ?, ?, ?)',
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
        (site['id'], lead_id, payload.get('event_type') or 'lead_created', json.dumps(payload), created_at)
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


@app.route('/health')
def health():
    return jsonify({'ok': True, 'time': now_iso()})


init_db()


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
