import sqlite3
from pathlib import Path

import os
DB_PATH = Path(os.getenv("DB_PATH") or Path(__file__).parent / "data.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS courses (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  fee TEXT DEFAULT '',
  duration TEXT DEFAULT '',
  batch_timing TEXT DEFAULT '',
  syllabus_url TEXT DEFAULT '',
  description TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS leads (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  phone TEXT NOT NULL UNIQUE,
  email TEXT DEFAULT '',
  course TEXT DEFAULT '',
  source TEXT DEFAULT 'manual',
  status TEXT DEFAULT 'new',
  consent INTEGER DEFAULT 0,
  bot_paused INTEGER DEFAULT 0,
  last_inbound_at TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS templates (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE,
  category TEXT DEFAULT 'MARKETING',
  language TEXT DEFAULT 'en',
  body TEXT NOT NULL,
  buttons TEXT DEFAULT '[]',
  status TEXT DEFAULT 'DRAFT',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS broadcasts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  template_id INTEGER NOT NULL,
  audience TEXT DEFAULT '{}',
  scheduled_at TEXT,
  status TEXT DEFAULT 'scheduled',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  lead_id INTEGER NOT NULL,
  direction TEXT NOT NULL,
  kind TEXT DEFAULT 'text',
  body TEXT DEFAULT '',
  template_id INTEGER,
  broadcast_id INTEGER,
  wamid TEXT,
  status TEXT DEFAULT 'sent',
  error TEXT DEFAULT '',
  sender TEXT DEFAULT '',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'agent',
  perms TEXT NOT NULL DEFAULT '[]',
  active INTEGER NOT NULL DEFAULT 1,
  last_login TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tags (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  color TEXT DEFAULT '#1a5bec'
);
CREATE TABLE IF NOT EXISTS lead_tags (
  lead_id INTEGER NOT NULL, tag_id INTEGER NOT NULL,
  PRIMARY KEY (lead_id, tag_id)
);
CREATE TABLE IF NOT EXISTS lists (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS list_members (
  list_id INTEGER NOT NULL, lead_id INTEGER NOT NULL,
  PRIMARY KEY (list_id, lead_id)
);
CREATE TABLE IF NOT EXISTS segments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  rules TEXT NOT NULL DEFAULT '{"match":"all","conditions":[]}',
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS lead_attrs (
  lead_id INTEGER NOT NULL, key TEXT NOT NULL, value TEXT DEFAULT '',
  PRIMARY KEY (lead_id, key)
);
CREATE INDEX IF NOT EXISTS idx_lead_tags_tag ON lead_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_list_members_lead ON list_members(lead_id);
CREATE INDEX IF NOT EXISTS idx_msg_lead ON messages(lead_id);
CREATE INDEX IF NOT EXISTS idx_msg_wamid ON messages(wamid);
"""

SEED_COURSES = [
    ("Full Stack Development", "Rs. 35,000", "5 months", "Weekdays 9-11 AM / 6-8 PM",
     "https://example.com/syllabus/full-stack.pdf", "MERN stack with live projects and placement support."),
    ("Python & Data Science", "Rs. 30,000", "4 months", "Weekends 10 AM-2 PM",
     "https://example.com/syllabus/python-ds.pdf", "Python, pandas, ML basics and capstone project."),
    ("Cloud & DevOps (AWS)", "Rs. 32,000", "3 months", "Weekdays 7-9 PM",
     "https://example.com/syllabus/devops.pdf", "AWS, Docker, Kubernetes and CI/CD pipelines."),
]

SEED_TEMPLATES = [
    ("welcome_enquiry", "UTILITY", "Hi {{name}}, thanks for your interest in {{course}} at BTree Systems! "
     "Duration: {{duration}}, Fee: {{fee}}. Syllabus: {{syllabus_url}}\nReply FEES, TIMINGS or COUNSELLOR to know more.", "[]"),
    ("demo_class_invite", "MARKETING", "Hi {{name}}, join our free demo class for {{course}} this Saturday! "
     "Seats are limited.", '["Reserve my seat","Not interested"]'),
]

DEMO_BUTTONS = (
    '[{"text": "Reserve my seat", "reply": "Great, {{name}}! Your seat for the {{course}} demo is reserved. '
    'We will share the joining link a day before.", "status": "interested", "handover": false},'
    ' {"text": "Not interested", "reply": "No problem, {{name}}. Reply COUNSELLOR any time if you would like to talk to us.",'
    ' "status": "lost", "handover": false}]')


def connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init():
    with connect() as c:
        c.executescript(SCHEMA)
        cols = [r["name"] for r in c.execute("PRAGMA table_info(leads)")]
        if "wa_chat_id" not in cols:
            c.execute("ALTER TABLE leads ADD COLUMN wa_chat_id TEXT DEFAULT ''")
        if "needs_callback" not in cols:  # set by the AI agent: the lead was promised a counsellor call
            c.execute("ALTER TABLE leads ADD COLUMN needs_callback INTEGER DEFAULT 0")
            c.execute("ALTER TABLE leads ADD COLUMN callback_at TEXT")
        bcols = [r["name"] for r in c.execute("PRAGMA table_info(broadcasts)")]
        if "audience_total" not in bcols:  # campaigns: audience size when sending started, who created it, when it ran
            c.execute("ALTER TABLE broadcasts ADD COLUMN audience_total INTEGER DEFAULT 0")
            c.execute("ALTER TABLE broadcasts ADD COLUMN created_by TEXT DEFAULT ''")
            c.execute("ALTER TABLE broadcasts ADD COLUMN started_at TEXT")
            c.execute("ALTER TABLE broadcasts ADD COLUMN finished_at TEXT")
        if "requested_at" not in cols:  # live chat stages: when the contact asked for a person (Requesting), who closed it
            c.execute("ALTER TABLE leads ADD COLUMN requested_at TEXT")
            c.execute("ALTER TABLE leads ADD COLUMN closed_by INTEGER")
            c.execute("ALTER TABLE leads ADD COLUMN closed_at TEXT")
            c.execute("ALTER TABLE leads ADD COLUMN handled_by INTEGER")   # last team member who intervened
            # Chats the bot had already handed over (bot paused, nobody on it) are "requesting" from now on.
            if "intervened_by" in cols:
                c.execute("""UPDATE leads SET requested_at=COALESCE(last_inbound_at, created_at)
                             WHERE bot_paused=1 AND intervened_by IS NULL AND COALESCE(chat_status,'open')!='resolved'""")
        if "intervened_by" not in cols:  # live chat: which team member took over this conversation
            c.execute("ALTER TABLE leads ADD COLUMN intervened_by INTEGER")
            c.execute("ALTER TABLE leads ADD COLUMN intervened_at TEXT")
            c.execute("ALTER TABLE leads ADD COLUMN unread INTEGER DEFAULT 0")
            c.execute("ALTER TABLE leads ADD COLUMN chat_status TEXT DEFAULT 'open'")   # open | resolved
            c.execute("ALTER TABLE leads ADD COLUMN unresolved INTEGER DEFAULT 0")      # questions the bot could not answer
            c.execute("ALTER TABLE leads ADD COLUMN notes TEXT DEFAULT ''")
            c.execute("ALTER TABLE leads ADD COLUMN opted_out INTEGER DEFAULT 0")       # sticky: set by STOP, cleared by START or the toggle
        if "bot_node" not in cols:  # where the lead is in the menu chatbot
            c.execute("ALTER TABLE leads ADD COLUMN bot_node TEXT DEFAULT ''")
            c.execute("ALTER TABLE leads ADD COLUMN bot_invalid INTEGER DEFAULT 0")
        if not c.execute("SELECT 1 FROM courses LIMIT 1").fetchone():
            c.executemany(
                "INSERT INTO courses(name,fee,duration,batch_timing,syllabus_url,description) VALUES(?,?,?,?,?,?)",
                SEED_COURSES)
        if not c.execute("SELECT 1 FROM templates LIMIT 1").fetchone():
            c.executemany(
                "INSERT INTO templates(name,category,body,buttons,status) VALUES(?,?,?,?,'APPROVED')",
                SEED_TEMPLATES)
        # The sample template's buttons were plain labels; give them example replies once.
        c.execute("UPDATE templates SET buttons=? WHERE name='demo_class_invite' AND buttons=?",
                  (DEMO_BUTTONS, '["Reserve my seat","Not interested"]'))


def q(sql, args=()):
    with connect() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def one(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def run(sql, args=()):
    with connect() as c:
        return c.execute(sql, args).lastrowid
