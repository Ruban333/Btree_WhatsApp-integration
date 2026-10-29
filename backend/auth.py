"""User accounts, roles and permissions.

Every signed-in person is a row in `users`. Admins can do everything; other users get a set of
page permissions (presets per role, adjustable per user). Permissions are enforced on the API,
not only hidden in the UI.
"""
import hashlib
import hmac
import json
import os
import re
import secrets
import time

import db

# Permission id -> label shown in the Users page. Order is the order of the sidebar.
PERMS = {
    "dashboard": "Dashboard",
    "chats": "Live Chat (own chats)",
    "chats_all": "Live Chat: see & take over chats handled by others",
    "leads": "Contacts (tags, lists, segments)",
    "contacts_bulk": "Contacts: import, export & bulk edit",
    "numbers": "Number Checker",
    "templates": "Templates",
    "broadcasts": "Campaigns",
    "courses": "Courses",
    "sessions": "Sessions (link / unlink WhatsApp)",
    "webhooks": "Webhooks",
    "tester": "Message Tester",
    "bot": "Chatbot menu",
    "ai": "AI Agent settings",
}
ROLES = {
    "admin": {"label": "Admin", "perms": list(PERMS), "about": "Full control, including users and roles."},
    "manager": {"label": "Manager", "perms": ["dashboard", "chats", "chats_all", "leads", "numbers", "templates", "broadcasts", "courses", "bot", "contacts_bulk"],
                "about": "Runs campaigns and content. Cannot manage WhatsApp connections or users."},
    "agent": {"label": "Agent", "perms": ["dashboard", "chats", "leads"],
              "about": "Handles conversations and leads."},
}
SESSION_DAYS = 7


# ---------- passwords ----------

def hash_password(pw: str) -> str:
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
    return f"pbkdf2${salt}${h}"


def check_password(pw: str, stored: str) -> bool:
    try:
        _, salt, h = stored.split("$")
    except ValueError:
        return False
    return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex(), h)


def validate_password(pw: str) -> str:
    return "" if len(pw) >= 8 else "Password must be at least 8 characters"


USERNAME_RE = re.compile(r"[A-Za-z0-9._-]{3,32}")


# ---------- sessions (signed cookies) ----------

def _secret() -> bytes:
    row = db.one("SELECT value FROM settings WHERE key='auth_secret'")
    if not row:
        db.run("INSERT OR IGNORE INTO settings(key,value) VALUES('auth_secret',?)", (secrets.token_hex(32),))
        row = db.one("SELECT value FROM settings WHERE key='auth_secret'")
    return row["value"].encode()


def _sig(uid: int, exp: str, pw_hash: str) -> str:
    # Includes the tail of the password hash, so changing a password signs that user out everywhere.
    return hmac.new(_secret(), f"{uid}.{exp}.{pw_hash[-16:]}".encode(), hashlib.sha256).hexdigest()


def make_token(user: dict) -> str:
    exp = str(int(time.time()) + SESSION_DAYS * 86400)
    return f"{user['id']}.{exp}.{_sig(user['id'], exp, user['password_hash'])}"


def user_from_token(token: str | None) -> dict | None:
    if not token or token.count(".") != 2:
        return None
    uid, exp, sig = token.split(".")
    if not (uid.isdigit() and exp.isdigit()) or int(exp) < time.time():
        return None
    u = db.one("SELECT * FROM users WHERE id=? AND active=1", (int(uid),))
    if not u or not hmac.compare_digest(sig, _sig(u["id"], exp, u["password_hash"])):
        return None
    return u


# ---------- permissions ----------

def perms_of(user: dict) -> list[str]:
    if user["role"] == "admin":
        return list(PERMS) + ["users"]
    granted = set(json.loads(user["perms"] or "[]"))
    return [p for p in PERMS if p in granted]  # "users" is admin-only, never grantable


def public_user(u: dict) -> dict:
    return {"id": u["id"], "name": u["name"], "username": u["username"], "role": u["role"],
            "perms": perms_of(u), "active": bool(u["active"]), "last_login": u["last_login"], "created_at": u["created_at"]}


def required_perm(method: str, path: str) -> str | None:
    """Permission an API call needs. None = any signed-in user. Unknown paths are admin-only."""
    p = path
    if p in ("/api/config", "/api/whatsapp/status") or p.startswith("/api/auth/"):
        return None
    if p.startswith("/api/users"):
        return "users"
    if p.startswith("/api/ai/"):
        return "ai"
    if p.startswith("/api/bot/"):
        return "bot"
    if p in ("/api/contacts/bulk", "/api/contacts/import", "/api/contacts/export"):
        return "contacts_bulk"
    if method == "GET" and p in ("/api/tags", "/api/lists", "/api/segments"):
        return None  # names only: campaigns pick audiences from them, contacts and chats show them
    if p.startswith(("/api/contacts", "/api/tags", "/api/lists", "/api/segments")):
        # the chat's profile panel reads one contact's profile and edits its tags, attributes and notes
        if re.fullmatch(r"/api/contacts/\d+(/profile|/attrs|/tags|/lists)?", p):
            return "chats" if method == "GET" and p.endswith("/profile") else "leads"
        return "leads"
    if p.startswith("/api/livechat"):
        return "chats"
    if re.fullmatch(r"/api/leads/\d+/callback-done", p):
        return "chats"
    if p in ("/api/stats", "/api/analytics"):
        return "dashboard"
    if p == "/api/conversations" or re.fullmatch(r"/api/leads/\d+/(messages|send|bot/\w+|intervene|takeover|release|resolve|reopen)", p) \
            or re.fullmatch(r"/api/templates/\d+/send/\d+", p) or p == "/api/mock/incoming":
        return "chats"
    if p.startswith("/api/leads"):
        return "leads"
    if p.startswith("/api/templates"):
        return None if method == "GET" else "templates"  # chats and broadcasts need the template list
    if p.startswith("/api/broadcasts"):
        return "broadcasts"
    if p.startswith("/api/courses"):
        return None if method == "GET" else "courses"
    if p == "/api/openwa/check":
        return "numbers"
    if p == "/api/openwa/send-test":
        return "tester"
    if p.startswith("/api/openwa/webhooks"):
        return "webhooks"
    if p == "/api/openwa/sessions" and method == "GET":
        return None  # status overview on the dashboard
    if p.startswith("/api/openwa") or p == "/api/whatsapp/test":
        return "sessions"
    return "users"


# ---------- first run ----------

def needs_setup() -> bool:
    return not db.one("SELECT 1 FROM users LIMIT 1")


def bootstrap():
    """First start after upgrading: turn the old shared ADMIN_PASSWORD into an 'admin' account."""
    pw = os.getenv("ADMIN_PASSWORD", "")
    if needs_setup() and pw:
        db.run("INSERT INTO users(name,username,password_hash,role,perms) VALUES(?,?,?,?,?)",
               ("Administrator", "admin", hash_password(pw), "admin", json.dumps(list(PERMS))))
