import asyncio
import hashlib
import hmac
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import ai_agent
import auth
import bot_flow
import campaigns
import contacts
import db
import livechat
import whatsapp as wa

ROOT = Path(__file__).resolve().parent.parent
FRONTEND = ROOT / "frontend"


def _load_env():
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


_load_env()


class _QuietPolling(logging.Filter):
    """Keeps the terminal readable: drops successful background refreshes (dashboard, chat, status) from the
    access log. Every change (POST/PUT/DELETE) and every error is still logged."""
    ROUTINE = re.compile(r"/api/(stats|analytics|config|conversations|templates|courses|whatsapp/status|auth/me"
                         r"|openwa/sessions|broadcasts|leads/\d+/messages)(\?.*)?$")

    def filter(self, record):
        args = record.args if isinstance(record.args, tuple) else ()
        if len(args) >= 5 and args[1] == "GET" and isinstance(args[4], int) and args[4] < 400:
            return not self.ROUTINE.match(str(args[2]))
        return True


logging.getLogger("uvicorn.access").addFilter(_QuietPolling())


def now() -> datetime:
    return datetime.now(timezone.utc)


def ts(d: datetime) -> str:
    return d.strftime("%Y-%m-%d %H:%M:%S")


def parse_ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)


# ---------- helpers ----------

def lead_values(lead: dict) -> dict:
    """Variable values available to templates for a lead."""
    course = db.one("SELECT * FROM courses WHERE name=?", (lead["course"],)) or {}
    attrs = {r["key"]: r["value"] for r in db.q("SELECT key, value FROM lead_attrs WHERE lead_id=?", (lead["id"],))}
    return {**attrs, "name": lead["name"], "phone": lead["phone"], "email": lead["email"],
            "course": lead["course"], "fee": course.get("fee", ""),
            "duration": course.get("duration", ""), "batch_timing": course.get("batch_timing", ""),
            "syllabus_url": course.get("syllabus_url", ""),
            "institute": os.getenv("INSTITUTE_NAME", "BTree Systems")}


def log_message(lead_id, direction, kind, body, status="sent", wamid=None, template_id=None,
                broadcast_id=None, error="", sender=""):
    return db.run(
        "INSERT INTO messages(lead_id,direction,kind,body,status,wamid,template_id,broadcast_id,error,sender,created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (lead_id, direction, kind, body, status, wamid, template_id, broadcast_id, error, sender, ts(now())))


LEAD_STATUSES = ("new", "contacted", "interested", "enrolled", "lost")
KEYCAPS = ["1.", "2.", "3."]   # plain text numbering for template options


def norm_buttons(raw) -> list[dict]:
    """Template buttons as dicts. Older templates stored plain labels."""
    items = json.loads(raw) if isinstance(raw, str) else (raw or [])
    out = []
    for b in items[:3]:
        b = {"text": b} if isinstance(b, str) else dict(b)
        text = str(b.get("text", "")).strip()
        if text:
            out.append({"text": text, "reply": str(b.get("reply", "")),
                        "status": b.get("status") if b.get("status") in LEAD_STATUSES else "",
                        "handover": bool(b.get("handover"))})
    return out


def body_with_options(tpl: dict) -> str:
    """Without Meta's native buttons (gateway/mock), list the options so the lead can reply 1, 2, 3 or the label."""
    buttons = norm_buttons(tpl["buttons"])
    if not buttons or wa.mode() == "cloud":
        return tpl["body"]
    base = option_link_base()
    if base:  # tap an option, then Send: the lead's reply is the option text, which match_button() recognises
        return bot_flow._with_options(tpl["body"], [b["text"] for b in buttons], base)
    opts = "\n".join(f"{KEYCAPS[i]} {b['text']}" for i, b in enumerate(buttons))
    return f"{tpl['body']}\n\n{opts}\n\nReply with the number or the option."


def send_template_to_lead(lead: dict, tpl: dict, broadcast_id=None, sender="system", extra=None):
    """Sends a template. Enforces opt-in and approval. Returns (ok, error)."""
    if not lead["consent"]:
        err = "No opt-in consent"
    elif tpl["status"] != "APPROVED":
        err = f"Template not approved ({tpl['status']})"
    else:
        err = ""
    values = {**lead_values(lead), **(extra or {})}
    body = body_with_options(tpl)
    text = wa.render(body, values)
    if err:
        log_message(lead["id"], "out", "template", text, "failed", None, tpl["id"], broadcast_id, err, sender)
        return False, err
    try:
        wamid = wa.send_template(lead["phone"], tpl["name"], tpl["language"], body if wa.mode() != "cloud" else tpl["body"],
                                 values, lead.get("wa_chat_id") or "")
    except Exception as e:  # network / API errors are recorded, not raised
        log_message(lead["id"], "out", "template", text, "failed", None, tpl["id"], broadcast_id, str(e), sender)
        return False, str(e)
    log_message(lead["id"], "out", "template", text, "sent", wamid, tpl["id"], broadcast_id, "", sender)
    return True, ""


def window_open(lead: dict) -> bool:
    if wa.mode() == "openwa":  # the 24-hour rule is a Cloud API concept
        return True
    if not lead["last_inbound_at"]:
        return False
    return now() - parse_ts(lead["last_inbound_at"]) < timedelta(hours=24)


def send_text_to_lead(lead: dict, text: str, sender: str):
    if not window_open(lead):
        raise HTTPException(409, "24-hour window closed. Send an approved template instead.")
    try:
        wamid = wa.send_text(lead["phone"], text, lead.get("wa_chat_id") or "")
    except Exception as e:
        log_message(lead["id"], "out", "text", text, "failed", None, error=str(e), sender=sender)
        raise HTTPException(502, str(e))
    log_message(lead["id"], "out", "text", text, "sent", wamid, sender=sender)


def bot_reply(lead: dict, text: str, first: bool = False):
    """Keyword FAQ bot answering from the courses table. Hook for an LLM later."""
    t = text.lower()
    course = db.one("SELECT * FROM courses WHERE name=?", (lead["course"],))
    inst = os.getenv("INSTITUTE_NAME", "BTree Systems")
    reply = None
    if any(k in t for k in ("counsellor", "human", "agent", "call me")):
        livechat.request_human(lead["id"], "the contact asked for a counsellor")
        reply = "Sure! A counsellor will contact you shortly. Thank you for your patience."
    elif first and course:
        reply = (f"Hi! Thanks for contacting {inst}. {course['name']}: {course['duration']}, fee {course['fee']}, "
                 f"batches {course['batch_timing']}. Syllabus: {course['syllabus_url']}\n"
                 "Reply FEES, TIMINGS, SYLLABUS or COUNSELLOR to know more.")
    elif first:
        names = ", ".join(c["name"] for c in db.q("SELECT name FROM courses"))
        reply = f"Hi! Welcome to {inst}. We offer: {names}. Tell us which course you're interested in, or type COUNSELLOR to talk to a person."
    elif course and any(k in t for k in ("fee", "price", "cost")):
        reply = f"{course['name']} fee: {course['fee']}."
    elif course and any(k in t for k in ("timing", "batch", "schedule")):
        reply = f"{course['name']} batches: {course['batch_timing']}."
    elif course and any(k in t for k in ("duration", "long")):
        reply = f"{course['name']} duration: {course['duration']}."
    elif course and "syllabus" in t:
        reply = f"Syllabus for {course['name']}: {course['syllabus_url']}"
    elif any(k in t for k in ("hi", "hello", "hey", "course")):
        names = ", ".join(c["name"] for c in db.q("SELECT name FROM courses"))
        reply = f"Welcome to {os.getenv('INSTITUTE_NAME', 'BTree Systems')}! Courses: {names}. Ask about FEES, TIMINGS, DURATION or SYLLABUS, or type COUNSELLOR."
    if reply:
        lead = db.one("SELECT * FROM leads WHERE id=?", (lead["id"],))
        try:
            send_text_to_lead(lead, reply, "bot")
        except HTTPException:
            pass


_tasks: set = set()


def handle_inbound(phone: str, text: str, wamid: str | None = None, name: str = "", chat_id: str = ""):
    if wamid and db.one("SELECT 1 FROM messages WHERE wamid=? AND direction='in'", (wamid,)):
        return  # webhook re-delivery of a message we already handled
    phone = wa.normalize_phone(phone)
    lead = db.one("SELECT * FROM leads WHERE phone=?", (phone,))
    if not lead and chat_id:  # a privacy-id (@lid) contact we have already seen
        lead = db.one("SELECT * FROM leads WHERE wa_chat_id=?", (chat_id,))
    if lead and chat_id and lead.get("wa_chat_id") != chat_id:
        db.run("UPDATE leads SET wa_chat_id=? WHERE id=?", (chat_id, lead["id"]))
    # Course the person asked about (the website widget pre-fills the course name).
    asked = next((c["name"] for c in db.q("SELECT name FROM courses") if c["name"].lower() in text.lower()), "")
    if not lead:
        # Messaging the business first is a user-initiated contact; treat as consent to reply.
        source = "web_widget" if "(via website)" in text.lower() else "whatsapp_inbound"
        lid = db.run("INSERT INTO leads(name,phone,course,source,consent,wa_chat_id) VALUES(?,?,?,?,1,?)",
                     (name or phone, phone, asked, source, chat_id))
        lead = db.one("SELECT * FROM leads WHERE id=?", (lid,))
    elif asked and not lead["course"]:
        db.run("UPDATE leads SET course=? WHERE id=?", (asked, lead["id"]))
    first = not db.one("SELECT 1 FROM messages WHERE lead_id=? AND direction='in'", (lead["id"],))
    # Writing to us counts as consent, unless the contact opted out (STOP) or a team member switched opt-in off.
    db.run("UPDATE leads SET last_inbound_at=?, unread=COALESCE(unread,0)+1, consent=CASE WHEN opted_out=1 THEN consent ELSE 1 END WHERE id=?",
           (ts(now()), lead["id"]))
    if lead.get("chat_status") == "resolved":  # a new message reopens a resolved chat, and the bot looks after it again
        db.run("UPDATE leads SET chat_status='open', bot_paused=0, bot_node='', bot_invalid=0 WHERE id=?", (lead["id"],))
        livechat.log_event(lead["id"], "Chat reopened: new message from the contact")
    lead = db.one("SELECT * FROM leads WHERE id=?", (lead["id"],))
    word = _plain(text)
    if word in ("stop", "unsubscribe", "opt out", "optout"):
        db.run("UPDATE leads SET consent=0, opted_out=1 WHERE id=?", (lead["id"],))
        log_message(lead["id"], "in", "text", text, "received", wamid)
        livechat.log_event(lead["id"], "Contact opted out (replied STOP)")
        _agent_send(lead["id"], f"You will no longer receive messages from {os.getenv('INSTITUTE_NAME', 'BTree Systems')}. "
                                "Reply START any time to opt in again.", "system")
        return
    if word == "start" and lead.get("opted_out"):
        db.run("UPDATE leads SET consent=1, opted_out=0 WHERE id=?", (lead["id"],))
        lead = db.one("SELECT * FROM leads WHERE id=?", (lead["id"],))
        log_message(lead["id"], "in", "text", text, "received", wamid)
        livechat.log_event(lead["id"], "Contact opted back in (replied START)")
        _agent_send(lead["id"], "Thanks, you are subscribed again. Reply Hi to see the options.", "system")
        return
    choice = match_button(lead, text)
    if choice:
        # The lead answered a template option: run that button's configured response, not the keyword bot.
        log_message(lead["id"], "in", "button", choice["text"], "received", wamid)
        run_button(lead, choice)
        return
    log_message(lead["id"], "in", "text", text, "received", wamid)
    if not lead["bot_paused"]:
        # Order: AI agent (if switched on) -> menu chatbot (if switched on) -> old keyword bot.
        send = lambda body, sender: _agent_send(lead["id"], body, sender)
        if not (ai_agent.handle(lead, send, lead_values(lead)) or flow_reply(lead, text, send)):
            bot_reply(lead, text, first)


def option_link_base(flow: dict | None = None) -> str | None:
    """Tap-to-reply links ("pick one"), or None to fall back to numbered options (no number known, Cloud API, or switched off)."""
    flow = flow or bot_flow.load()
    if flow.get("option_style") == "numbers" or wa.mode() == "cloud":
        return None
    n = wa.own_number()
    return f"https://wa.me/{n}?text=" if n else None


def flow_reply(lead: dict, text: str, send) -> bool:
    """Menu chatbot turn. Returns False when the menu chatbot is switched off."""
    flow = bot_flow.load()
    if not flow.get("enabled"):
        return False
    state = lead.get("bot_node") or ""
    prev = db.one("SELECT created_at FROM messages WHERE lead_id=? AND direction='in' ORDER BY id DESC LIMIT 1 OFFSET 1",
                  (lead["id"],))
    if state and prev and now() - parse_ts(prev["created_at"]) > timedelta(hours=12):
        state = ""  # back after a long gap: start again from the welcome menu
    res = bot_flow.step(flow, state, lead.get("bot_invalid") or 0, text, lead_values(lead),
                        db.q("SELECT * FROM courses ORDER BY name"), option_link_base(flow))
    for m in res["messages"]:
        send(m, "menu-bot")
    db.run("UPDATE leads SET bot_node=?, bot_invalid=?, unresolved=COALESCE(unresolved,0)+? WHERE id=?",
           (res["state"], res["invalid"], 1 if res["invalid"] > (lead.get("bot_invalid") or 0) else 0, lead["id"]))
    if res["started"]:  # the welcome says a counsellor will contact them
        db.run("UPDATE leads SET needs_callback=1, callback_at=? WHERE id=?", (ts(now()), lead["id"]))
    if res["handover"]:
        livechat.request_human(lead["id"], "the contact asked for a counsellor" if res.get("reason") != "invalid"
                               else "the chatbot could not understand the replies")
    return True


def _agent_send(lead_id: int, body: str, sender: str):
    try:
        send_text_to_lead(db.one("SELECT * FROM leads WHERE id=?", (lead_id,)), body, sender)
    except HTTPException:
        pass  # the failure is already logged on the message


def _plain(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", s.lower()).split())


def match_button(lead: dict, text: str) -> dict | None:
    """The option the lead picked from the last template they were sent (within 7 days), if the text is one."""
    last = db.one("SELECT template_id, created_at FROM messages WHERE lead_id=? AND direction='out' AND kind='template'"
                  " AND template_id IS NOT NULL AND status!='failed' ORDER BY id DESC LIMIT 1", (lead["id"],))
    if not last or now() - parse_ts(last["created_at"]) > timedelta(days=7):
        return None
    tpl = db.one("SELECT buttons FROM templates WHERE id=?", (last["template_id"],))
    buttons = norm_buttons(tpl["buttons"]) if tpl else []
    t = _plain(text)
    for i, b in enumerate(buttons, 1):
        if t in (str(i), _plain(b["text"])):
            return b
    return None


def run_button(lead: dict, b: dict):
    if b["status"]:
        db.run("UPDATE leads SET status=? WHERE id=?", (b["status"], lead["id"]))
    if b["handover"]:
        livechat.request_human(lead["id"], f"the contact tapped “{b['text']}”")
    reply = wa.render(b["reply"], lead_values(lead)).strip()
    if b["handover"] and not reply:
        reply = "Thanks! A counsellor will contact you shortly."
    if reply:
        try:
            send_text_to_lead(db.one("SELECT * FROM leads WHERE id=?", (lead["id"],)), reply, "auto-reply")
        except HTTPException:
            pass


# ---------- background worker ----------

def run_broadcast(b: dict):
    """Sends a campaign. Safe to call again for the same campaign: contacts who already got a message are skipped,
    so Resume continues where it stopped and Retry only re-sends what failed. Stops when paused or cancelled."""
    tpl = db.one("SELECT * FROM templates WHERE id=?", (b["template_id"],))
    db.run("UPDATE broadcasts SET status='running', started_at=COALESCE(started_at, ?) WHERE id=?", (ts(now()), b["id"]))
    people = contacts.campaign_audience(json.loads(b["audience"] or "{}"))
    db.run("UPDATE broadcasts SET audience_total=? WHERE id=?",
           (max(len(people), b.get("audience_total") or 0), b["id"]))
    done = {r["lead_id"] for r in db.q("SELECT DISTINCT lead_id FROM messages WHERE broadcast_id=? AND status!='failed'", (b["id"],))}
    for lead in people:
        if lead["id"] in done:
            continue
        if db.one("SELECT status FROM broadcasts WHERE id=?", (b["id"],))["status"] != "running":
            return  # paused or cancelled while sending
        ok, _ = send_template_to_lead(db.one("SELECT * FROM leads WHERE id=?", (lead["id"],)), tpl,
                                      broadcast_id=b["id"], sender="campaign")
        if ok:
            wa.pace()
    db.run("UPDATE broadcasts SET status='completed', finished_at=? WHERE id=? AND status='running'", (ts(now()), b["id"]))


def advance_mock_statuses():
    """In mock mode, simulate delivery/read receipts so the dashboard has data."""
    for m in db.q("SELECT id,status,created_at FROM messages WHERE wamid LIKE 'mock.%' AND direction='out'"
                  " AND status IN ('sent','delivered')"):
        age = (now() - parse_ts(m["created_at"])).total_seconds()
        if m["status"] == "sent" and age > 2:
            db.run("UPDATE messages SET status='delivered' WHERE id=?", (m["id"],))
        elif m["status"] == "delivered" and age > 6 and int(hashlib.md5(str(m["id"]).encode()).hexdigest(), 16) % 10 < 6:
            db.run("UPDATE messages SET status='read' WHERE id=?", (m["id"],))


_last_wake = 0.0


def keep_openwa_alive():
    """If the linked session dropped (OpenWA restart, engine crash), start it again. Needs no new QR scan.
    If the gateway itself is unreachable, its WSL distro has probably stopped: wake it (Docker then restarts it)."""
    global _last_wake
    try:
        status = wa.ow_status().get("status")
    except wa.GatewayDown:
        if os.name == "nt" and time.monotonic() - _last_wake > 120:
            _last_wake = time.monotonic()
            subprocess.Popen(["wsl.exe", "-e", "true"], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            print("openwa watchdog: gateway unreachable, woke WSL")
        return
    if status == "disconnected":
        wa._ow("POST", f"/sessions/{wa.ow_session_id()}/start")


async def worker():
    tick = 0
    while True:
        tick += 1
        try:
            if wa.mode() == "openwa" and tick % 10 == 0:  # about every 30 seconds
                try:
                    await asyncio.to_thread(keep_openwa_alive)
                except Exception as e:  # OpenWA being down must not stop scheduled broadcasts
                    print("openwa watchdog:", e)
            due = db.q("SELECT * FROM broadcasts WHERE status='scheduled' AND scheduled_at<=?", (ts(now()),))
            for b in due:
                await asyncio.to_thread(run_broadcast, b)
            if wa.mode() == "mock":
                await asyncio.to_thread(advance_mock_statuses)
        except Exception as e:
            print("worker error:", e)
        await asyncio.sleep(3)


@asynccontextmanager
async def lifespan(_):
    db.init()
    auth.bootstrap()
    task = asyncio.create_task(worker())
    yield
    task.cancel()


app = FastAPI(title="BTree WhatsApp CRM", lifespan=lifespan)
app.include_router(contacts.router)
app.include_router(livechat.router)
app.include_router(campaigns.router)


# ---------- login, users and permissions ----------
# Each person has their own account (see auth.py). Public on purpose: the enquiry form + its API,
# the website widget, and the WhatsApp webhooks.

SESSION_COOKIE = "btree_session"
_fails: dict = {}  # ip -> recent failed-login times

PUBLIC_PAGES = {"/login", "/login.html", "/enquiry", "/enquiry.html", "/widget.js", "/demo-site.html", "/favicon.ico", "/logo.svg"}


def _is_public(method: str, path: str) -> bool:
    return (path in PUBLIC_PAGES or path.startswith("/webhook")
            or path in ("/api/auth/login", "/api/auth/logout", "/api/auth/me", "/api/auth/setup")
            or path == "/api/widget-config"
            or (path == "/api/courses" and method == "GET")
            or (path == "/api/enquiry" and method == "POST"))


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if request.method == "OPTIONS" or _is_public(request.method, path):
        return await call_next(request)
    user = await asyncio.to_thread(auth.user_from_token, request.cookies.get(SESSION_COOKIE))
    if not user:
        if path.startswith("/api/"):
            return Response(json.dumps({"detail": "Please sign in"}), 401, media_type="application/json")
        return Response(status_code=302, headers={"Location": "/login"})
    if path.startswith("/api/"):
        need = auth.required_perm(request.method, path)
        if need and need not in auth.perms_of(user):
            return Response(json.dumps({"detail": "Your role does not have access to this. Ask an admin."}), 403,
                            media_type="application/json")
    request.state.user = user
    return await call_next(request)


def me(request: Request) -> dict:
    return request.state.user


def _set_session(response: Response, request: Request, user: dict):
    response.set_cookie(SESSION_COOKIE, auth.make_token(user), max_age=auth.SESSION_DAYS * 86400, httponly=True,
                        samesite="lax", secure=request.url.scheme == "https")


class LoginIn(BaseModel):
    username: str
    password: str


class SetupIn(BaseModel):
    name: str
    username: str
    password: str


class PasswordIn(BaseModel):
    current: str
    new: str


class UserIn(BaseModel):
    name: str
    username: str = ""
    password: str = ""      # required on create; on edit, set only to reset it
    role: str = "agent"
    perms: list[str] | None = None
    active: bool = True


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    recent = [t for t in _fails.get(ip, []) if time.time() - t < 300]
    if len(recent) >= 5:
        raise HTTPException(429, "Too many attempts. Wait a few minutes and try again.")
    u = db.one("SELECT * FROM users WHERE username=? COLLATE NOCASE", (body.username.strip(),))
    if not u or not auth.check_password(body.password, u["password_hash"]):
        _fails[ip] = recent + [time.time()]
        raise HTTPException(401, "Wrong username or password")
    if not u["active"]:
        raise HTTPException(403, "This account is disabled. Ask an admin.")
    _fails.pop(ip, None)
    db.run("UPDATE users SET last_login=? WHERE id=?", (ts(now()), u["id"]))
    _set_session(response, request, u)
    return {"ok": True}


@app.post("/api/auth/setup")
def first_admin(body: SetupIn, request: Request, response: Response):
    """Creates the first admin. Only works while there are no users at all."""
    if not auth.needs_setup():
        raise HTTPException(409, "Setup is already done. Sign in instead.")
    if not auth.USERNAME_RE.fullmatch(body.username) or not body.name.strip():
        raise HTTPException(422, "Enter a name and a username of 3-32 letters, digits, dots, dashes or underscores")
    if err := auth.validate_password(body.password):
        raise HTTPException(422, err)
    uid = db.run("INSERT INTO users(name,username,password_hash,role,perms) VALUES(?,?,?,?,?)",
                 (body.name.strip(), body.username, auth.hash_password(body.password), "admin", json.dumps(list(auth.PERMS))))
    _set_session(response, request, db.one("SELECT * FROM users WHERE id=?", (uid,)))
    return {"ok": True}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
def auth_me(request: Request):
    u = auth.user_from_token(request.cookies.get(SESSION_COOKIE))
    return {"signed_in": bool(u), "needs_setup": auth.needs_setup(), "user": auth.public_user(u) if u else None}


@app.post("/api/auth/password")
def change_password(body: PasswordIn, request: Request, response: Response):
    u = me(request)
    if not auth.check_password(body.current, u["password_hash"]):
        raise HTTPException(401, "Current password is wrong")
    if err := auth.validate_password(body.new):
        raise HTTPException(422, err)
    db.run("UPDATE users SET password_hash=? WHERE id=?", (auth.hash_password(body.new), u["id"]))
    _set_session(response, request, db.one("SELECT * FROM users WHERE id=?", (u["id"],)))  # stay signed in here
    return {"ok": True}


@app.get("/api/users/roles")
def roles():
    return {"perms": auth.PERMS, "roles": auth.ROLES}


@app.get("/api/users")
def list_users():
    return [auth.public_user(u) for u in db.q("SELECT * FROM users ORDER BY role='admin' DESC, name")]


def _clean_perms(role: str, perms: list[str] | None) -> str:
    if role not in auth.ROLES:
        raise HTTPException(422, "Unknown role")
    chosen = auth.ROLES[role]["perms"] if perms is None or role == "admin" else perms
    return json.dumps([p for p in auth.PERMS if p in chosen])


def _other_admins(uid: int) -> int:
    return db.one("SELECT COUNT(*) n FROM users WHERE role='admin' AND active=1 AND id!=?", (uid,))["n"]


@app.post("/api/users")
def add_user(body: UserIn):
    if not body.name.strip() or not auth.USERNAME_RE.fullmatch(body.username):
        raise HTTPException(422, "Enter a name and a username of 3-32 letters, digits, dots, dashes or underscores")
    if err := auth.validate_password(body.password):
        raise HTTPException(422, err)
    try:
        uid = db.run("INSERT INTO users(name,username,password_hash,role,perms,active) VALUES(?,?,?,?,?,?)",
                     (body.name.strip(), body.username, auth.hash_password(body.password), body.role,
                      _clean_perms(body.role, body.perms), int(body.active)))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "That username is taken")
    return {"id": uid}


@app.put("/api/users/{uid}")
def edit_user(uid: int, body: UserIn, request: Request):
    u = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        raise HTTPException(404)
    losing_admin = u["role"] == "admin" and u["active"] and (body.role != "admin" or not body.active)
    if losing_admin and not _other_admins(uid):
        raise HTTPException(409, "There must be at least one active admin")
    if uid == me(request)["id"] and not body.active:
        raise HTTPException(409, "You cannot disable your own account")
    if body.password and (err := auth.validate_password(body.password)):
        raise HTTPException(422, err)
    db.run("UPDATE users SET name=?,role=?,perms=?,active=? WHERE id=?",
           (body.name.strip() or u["name"], body.role, _clean_perms(body.role, body.perms), int(body.active), uid))
    if body.password:
        db.run("UPDATE users SET password_hash=? WHERE id=?", (auth.hash_password(body.password), uid))
    return {"ok": True}


@app.delete("/api/users/{uid}")
def del_user(uid: int, request: Request):
    u = db.one("SELECT * FROM users WHERE id=?", (uid,))
    if not u:
        raise HTTPException(404)
    if uid == me(request)["id"]:
        raise HTTPException(409, "You cannot delete your own account")
    if u["role"] == "admin" and u["active"] and not _other_admins(uid):
        raise HTTPException(409, "There must be at least one active admin")
    db.run("DELETE FROM users WHERE id=?", (uid,))
    return {"ok": True}


@app.get("/login")
def login_page():
    return FileResponse(FRONTEND / "login.html")


# ---------- schemas ----------

class LeadIn(BaseModel):
    name: str
    phone: str
    email: str = ""
    course: str = ""
    source: str = "manual"
    status: str = "new"
    consent: bool = False


class EnquiryIn(BaseModel):
    name: str
    phone: str
    email: str = ""
    course: str = ""
    consent: bool = False
    source: str = "website"


class CourseIn(BaseModel):
    name: str
    fee: str = ""
    duration: str = ""
    batch_timing: str = ""
    syllabus_url: str = ""
    description: str = ""


class ButtonIn(BaseModel):
    text: str                 # the label the lead taps (or the option they reply with)
    reply: str = ""           # sent back automatically; supports {{name}} {{course}} ...
    status: str = ""          # optionally move the lead to this status
    handover: bool = False    # optionally pause the bot so a person takes over


class TemplateIn(BaseModel):
    name: str
    category: str = "MARKETING"
    language: str = "en"
    body: str
    buttons: list[ButtonIn | str] = []


class BroadcastIn(BaseModel):
    name: str
    template_id: int
    course: str = ""
    status: str = ""
    scheduled_at: str | None = None  # "YYYY-MM-DD HH:MM:SS" UTC; None = send now


class SendIn(BaseModel):
    text: str


class IncomingIn(BaseModel):
    phone: str
    text: str
    name: str = ""


# ---------- API: general ----------

@app.get("/api/config")
def config():
    return {"mode": wa.mode(), "institute": os.getenv("INSTITUTE_NAME", "BTree Systems"),
            "option_link_base": option_link_base()}


@app.get("/api/widget-config")
def widget_config(response: Response):
    """Read by the floating widget on the public website, which is served from a different origin."""
    response.headers["Access-Control-Allow-Origin"] = "*"
    return {"number": wa.normalize_phone(os.getenv("WHATSAPP_NUMBER", "")),
            "institute": os.getenv("INSTITUTE_NAME", "BTree Systems"),
            "courses": [c["name"] for c in db.q("SELECT name FROM courses ORDER BY name")]}


@app.get("/api/whatsapp/status")
def whatsapp_status(request: Request):
    """Connection checklist for going live."""
    token, pid, waba = wa._cfg()
    base = os.getenv("PUBLIC_URL", "").rstrip("/") or f"{request.url.scheme}://{request.headers.get('host')}"
    out = {"mode": wa.mode(), "number": os.getenv("WHATSAPP_NUMBER", ""),
           "webhook_url": base + "/webhook", "verify_token": os.getenv("WA_VERIFY_TOKEN", "btree-verify"),
           "has_token": bool(token), "has_phone_id": bool(pid), "has_waba_id": bool(waba),
           "has_app_secret": bool(os.getenv("WA_APP_SECRET")), "graph_ok": None, "graph_detail": "",
           "openwa_webhook_url": base + "/webhook/openwa", "openwa_url": os.getenv("OPENWA_URL", "http://localhost:2785"),
           "has_openwa_key": bool(os.getenv("OPENWA_API_KEY")), "openwa_status": None, "openwa_detail": ""}
    if wa.mode() == "openwa":
        try:
            s = wa.ow_status()
            out["openwa_status"] = s.get("status")
            out["openwa_detail"] = " ".join(x for x in (s.get("pushName"), s.get("phone"), s.get("lastError")) if x)
        except Exception as e:
            out["openwa_status"], out["openwa_detail"] = "unreachable", str(e)
    if wa.mode() == "cloud" and token and pid:
        try:
            r = wa.httpx.get(f"{wa.GRAPH}/{pid}", params={"fields": "display_phone_number,verified_name,quality_rating"},
                             headers={"Authorization": f"Bearer {token}"}, timeout=15)
            d = r.json()
            out["graph_ok"] = r.status_code == 200
            out["graph_detail"] = (f"{d.get('verified_name', '')} {d.get('display_phone_number', '')} quality={d.get('quality_rating', '?')}"
                                   if r.status_code == 200 else d.get("error", {}).get("message", r.text))
        except Exception as e:
            out["graph_ok"], out["graph_detail"] = False, str(e)
    return out


class TestIn(BaseModel):
    phone: str


@app.post("/api/whatsapp/test")
def whatsapp_test(t: TestIn):
    """Sends Meta's built-in 'hello_world' template to verify the live pipeline end to end."""
    phone = wa.normalize_phone(t.phone)
    try:
        if wa.mode() == "openwa":
            wamid = wa.send_text(phone, f"Test message from {os.getenv('INSTITUTE_NAME', 'BTree Systems')} CRM. WhatsApp is connected.")
        else:
            wamid = wa.send_template(phone, "hello_world", "en_US", "", {})
    except Exception as e:
        raise HTTPException(502, str(e))
    return {"ok": True, "wamid": wamid, "mode": wa.mode()}


@app.get("/api/stats")
def stats():
    out = {r["status"]: r["n"] for r in db.q(
        "SELECT status, COUNT(*) n FROM messages WHERE direction='out' GROUP BY status")}
    sent_total = sum(out.values())
    return {
        "leads": db.one("SELECT COUNT(*) n FROM leads")["n"],
        "leads_by_status": db.q("SELECT status, COUNT(*) n FROM leads GROUP BY status"),
        "leads_by_source": db.q("SELECT source, COUNT(*) n FROM leads GROUP BY source"),
        "leads_by_course": db.q("SELECT course, COUNT(*) n FROM leads WHERE course!='' GROUP BY course"),
        "outbound": sent_total, "by_status": out,
        "inbound": db.one("SELECT COUNT(*) n FROM messages WHERE direction='in'")["n"],
        "today": db.one("SELECT COUNT(*) n FROM messages WHERE direction!='sys' AND date(created_at)=date('now')")["n"],
        "total_messages": db.one("SELECT COUNT(*) n FROM messages WHERE direction!='sys'")["n"],
    }


# ---------- API: courses ----------

@app.get("/api/courses")
def courses():
    return db.q("SELECT * FROM courses ORDER BY name")


@app.post("/api/courses")
def add_course(c: CourseIn):
    try:
        cid = db.run("INSERT INTO courses(name,fee,duration,batch_timing,syllabus_url,description) VALUES(?,?,?,?,?,?)",
                     (c.name, c.fee, c.duration, c.batch_timing, c.syllabus_url, c.description))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Course already exists")
    return {"id": cid}


@app.put("/api/courses/{cid}")
def edit_course(cid: int, c: CourseIn):
    db.run("UPDATE courses SET name=?,fee=?,duration=?,batch_timing=?,syllabus_url=?,description=? WHERE id=?",
           (c.name, c.fee, c.duration, c.batch_timing, c.syllabus_url, c.description, cid))
    return {"ok": True}


@app.delete("/api/courses/{cid}")
def del_course(cid: int):
    db.run("DELETE FROM courses WHERE id=?", (cid,))
    return {"ok": True}


# ---------- API: leads ----------

@app.get("/api/leads")
def leads(course: str = "", status: str = "", q: str = ""):
    sql, args = "SELECT * FROM leads WHERE 1=1", []
    if course:
        sql += " AND course=?"; args.append(course)
    if status:
        sql += " AND status=?"; args.append(status)
    if q:
        sql += " AND (name LIKE ? OR phone LIKE ?)"; args += [f"%{q}%"] * 2
    return db.q(sql + " ORDER BY id DESC", args)


@app.post("/api/leads")
def add_lead(l: LeadIn):
    try:
        lid = db.run("INSERT INTO leads(name,phone,email,course,source,status,consent) VALUES(?,?,?,?,?,?,?)",
                     (l.name, wa.normalize_phone(l.phone), l.email, l.course, l.source, l.status, int(l.consent)))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "A lead with this phone already exists")
    return {"id": lid}


@app.put("/api/leads/{lid}")
def edit_lead(lid: int, l: LeadIn):
    db.run("UPDATE leads SET name=?,phone=?,email=?,course=?,source=?,status=?,consent=? WHERE id=?",
           (l.name, wa.normalize_phone(l.phone), l.email, l.course, l.source, l.status, int(l.consent), lid))
    return {"ok": True}


@app.delete("/api/leads/{lid}")
def del_lead(lid: int):
    for tbl in ("messages", "lead_tags", "lead_attrs"):
        db.run(f"DELETE FROM {tbl} WHERE lead_id=?", (lid,))
    db.run("DELETE FROM list_members WHERE lead_id=?", (lid,))
    db.run("DELETE FROM leads WHERE id=?", (lid,))
    return {"ok": True}


@app.post("/api/leads/{lid}/bot/{state}")
def bot_toggle(lid: int, state: str):
    # Resuming starts the menu chatbot from the welcome again on the lead's next message.
    db.run("UPDATE leads SET bot_paused=?, bot_node=CASE WHEN ?=0 THEN '' ELSE bot_node END WHERE id=?",
           (1 if state == "pause" else 0, 1 if state == "pause" else 0, lid))
    return {"ok": True}


_welcome_queued: dict = {}  # lead id -> time the welcome was queued


@app.post("/api/enquiry")
def enquiry(e: EnquiryIn, background: BackgroundTasks):
    """Public endpoint for the website enquiry form. Queues the welcome template if the person opted in.
    The send runs after the response so the form never waits on WhatsApp."""
    phone = wa.normalize_phone(e.phone)
    if len(phone) < 10:
        raise HTTPException(422, "Invalid phone number")
    lead = db.one("SELECT * FROM leads WHERE phone=?", (phone,))
    if lead:
        db.run("UPDATE leads SET name=?,course=?,consent=MAX(consent,?) WHERE id=?",
               (e.name, e.course or lead["course"], int(e.consent), lead["id"]))
    else:
        lid = db.run("INSERT INTO leads(name,phone,email,course,source,consent) VALUES(?,?,?,?,?,?)",
                     (e.name, phone, e.email, e.course, e.source, int(e.consent)))
        lead = {"id": lid}
    lead = db.one("SELECT * FROM leads WHERE id=?", (lead["id"],))
    welcome = db.one("SELECT * FROM templates WHERE name='welcome_enquiry'")
    queued = False
    if welcome and lead["consent"]:
        # A repeat submit (double click, impatient refresh) must not message the person again.
        last = _welcome_queued.get(lead["id"], 0)
        if time.monotonic() - last > 600:
            _welcome_queued[lead["id"]] = time.monotonic()
            background.add_task(send_template_to_lead, lead, welcome, None, "auto")
            queued = True
    return {"lead_id": lead["id"], "welcome_sent": queued}


# ---------- API: templates ----------

@app.get("/api/templates")
def templates():
    rows = db.q("SELECT * FROM templates ORDER BY id DESC")
    for r in rows:
        r["buttons"] = norm_buttons(r["buttons"])
        r["variables"] = wa.variables(r["body"])
    return rows


def _buttons_json(t: TemplateIn) -> str:
    buttons = norm_buttons([b.model_dump() if isinstance(b, ButtonIn) else b for b in t.buttons])
    if any(len(b["text"]) > 20 for b in buttons):
        raise HTTPException(422, "Button text can be at most 20 characters (WhatsApp limit)")
    return json.dumps(buttons)


@app.post("/api/templates")
def add_template(t: TemplateIn):
    if not t.name.replace("_", "").isalnum() or t.name != t.name.lower():
        raise HTTPException(422, "Name must be lowercase letters, digits and underscores")
    buttons = _buttons_json(t)
    try:
        tid = db.run("INSERT INTO templates(name,category,language,body,buttons,status) VALUES(?,?,?,?,?,'DRAFT')",
                     (t.name, t.category.upper(), t.language, t.body, buttons))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "Template name already exists")
    return {"id": tid}


@app.put("/api/templates/{tid}")
def edit_template(tid: int, t: TemplateIn):
    db.run("UPDATE templates SET category=?,language=?,body=?,buttons=?,status='DRAFT' WHERE id=?",
           (t.category.upper(), t.language, t.body, _buttons_json(t), tid))
    return {"ok": True}


@app.post("/api/templates/{tid}/submit")
def submit_template(tid: int):
    t = db.one("SELECT * FROM templates WHERE id=?", (tid,))
    if not t:
        raise HTTPException(404)
    try:
        status = wa.submit_template(t["name"], t["category"], t["language"], t["body"],
                                    [b["text"] for b in norm_buttons(t["buttons"])])
    except Exception as e:
        raise HTTPException(502, str(e))
    db.run("UPDATE templates SET status=? WHERE id=?", (status, tid))
    return {"status": status}


@app.delete("/api/templates/{tid}")
def del_template(tid: int):
    if db.one("SELECT 1 FROM broadcasts WHERE template_id=?", (tid,)):
        raise HTTPException(409, "Template is used by a broadcast")
    db.run("DELETE FROM templates WHERE id=?", (tid,))
    return {"ok": True}


@app.post("/api/templates/{tid}/preview/{lid}")
def preview(tid: int, lid: int):
    t = db.one("SELECT * FROM templates WHERE id=?", (tid,))
    l = db.one("SELECT * FROM leads WHERE id=?", (lid,))
    if not t or not l:
        raise HTTPException(404)
    return {"text": wa.render(body_with_options(t), lead_values(l))}


@app.post("/api/templates/{tid}/send/{lid}")
def send_one(tid: int, lid: int, request: Request):
    t = db.one("SELECT * FROM templates WHERE id=?", (tid,))
    l = db.one("SELECT * FROM leads WHERE id=?", (lid,))
    if not t or not l:
        raise HTTPException(404)
    l = livechat.ensure_can_send(lid, me(request))
    ok, err = send_template_to_lead(l, t, sender=me(request)["name"])
    if not ok:
        raise HTTPException(422, err)
    return {"ok": True}


# ---------- API: inbox ----------

@app.post("/api/leads/{lid}/send")
def send_message(lid: int, s: SendIn, request: Request):
    lead = livechat.ensure_can_send(lid, me(request))   # claims the chat for this person, or refuses if someone else has it
    send_text_to_lead(lead, s.text, me(request)["name"])
    return {"ok": True}


# ---------- Meta webhook ----------

@app.get("/webhook")
def webhook_verify(request: Request):
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == os.getenv("WA_VERIFY_TOKEN", "btree-verify"):
        return Response(p.get("hub.challenge", ""), media_type="text/plain")
    raise HTTPException(403)


@app.post("/webhook")
async def webhook(request: Request):
    raw = await request.body()
    secret = os.getenv("WA_APP_SECRET", "")
    if secret:
        sig = request.headers.get("X-Hub-Signature-256", "")
        expected = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(403, "Bad signature")
    payload = json.loads(raw or "{}")
    for entry in payload.get("entry", []):
        for ch in entry.get("changes", []):
            val = ch.get("value", {})
            names = {c["wa_id"]: c.get("profile", {}).get("name", "") for c in val.get("contacts", [])}
            for m in val.get("messages", []):
                if m.get("type") == "text":
                    text = m["text"]["body"]
                elif m.get("type") == "button":
                    text = m["button"]["text"]
                elif m.get("type") == "interactive":
                    text = (m["interactive"].get("button_reply") or m["interactive"].get("list_reply") or {}).get("title", "")
                else:
                    text = f"[{m.get('type')} message]"
                await asyncio.to_thread(handle_inbound, m["from"], text, m.get("id"), names.get(m["from"], ""))
            for s in val.get("statuses", []):
                err = (s.get("errors") or [{}])[0].get("title", "")
                db.run("UPDATE messages SET status=?, error=? WHERE wamid=?", (s["status"], err, s["id"]))
    return {"ok": True}


# ---------- OpenWA (unofficial gateway) ----------

@app.post("/api/openwa/setup")
def openwa_setup(request: Request):
    """Creates + starts the OpenWA session and registers this server's webhook."""
    if wa.mode() != "openwa":
        raise HTTPException(400, "Set WA_MODE=gateway in .env first")
    # OpenWA must reach this URL. Same machine: localhost works. Docker: use OPENWA_CALLBACK_URL=http://host.docker.internal:8000
    base = (os.getenv("OPENWA_CALLBACK_URL") or os.getenv("PUBLIC_URL", "")).rstrip("/") or f"{request.url.scheme}://{request.headers.get('host')}"
    try:
        return {"ok": True, **wa.ow_setup(base + "/webhook/openwa")}
    except Exception as e:
        raise HTTPException(502, str(e))


@app.get("/api/openwa/qr")
def openwa_qr():
    try:
        return wa.ow_qr()
    except Exception as e:
        raise HTTPException(502, str(e))


def _ow_phone(data: dict) -> str:
    """Sender's number. Prefers senderPhone (resolved from @lid ids), else the digits of the chat id."""
    raw = data.get("senderPhone") or data.get("from") or data.get("chatId") or ""
    return re.sub(r"\D", "", str(raw).split("@")[0])


@app.post("/webhook/openwa")
async def openwa_webhook(request: Request):
    raw = await request.body()
    secret = os.getenv("OPENWA_WEBHOOK_SECRET", "")
    if secret:
        sig = request.headers.get("X-OpenWA-Signature", "").removeprefix("sha256=")
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            raise HTTPException(403, "Bad signature")
    payload = json.loads(raw or "{}")
    event, data = payload.get("event", ""), payload.get("data") or {}
    if os.getenv("OPENWA_DEBUG"):  # raw payloads, for diagnosing parsing problems
        with open(Path(__file__).parent / "openwa-webhook.log", "a", encoding="utf-8") as f:
            f.write(raw.decode("utf-8", "replace") + "\n")
    if event == "message.received":
        chat = str(data.get("chatId") or data.get("from") or "")
        # Ignore our own messages, groups, channels and status updates: only 1:1 chats are leads.
        if data.get("fromMe") or data.get("isGroup") or not chat.endswith(("@c.us", "@lid", "@s.whatsapp.net")):
            return {"ok": True, "ignored": True}
        text = data.get("body") or ""
        if not text:
            text = f"[{data.get('type') or data.get('kind') or 'media'} message]"
        phone = _ow_phone(data)
        if phone:
            # Answer OpenWA at once; the bot reply (a slow WhatsApp send) runs in the background.
            # Without this OpenWA waits, times out and re-delivers the same message.
            task = asyncio.create_task(asyncio.to_thread(handle_inbound, phone, text, data.get("id"),
                                                         data.get("pushName") or data.get("chatName") or "", chat))
            _tasks.add(task)  # keep a reference so it is not garbage-collected mid-run
            task.add_done_callback(_tasks.discard)
    elif event in ("message.ack", "message.failed"):
        mid = data.get("messageId") or data.get("id")
        ack = data.get("status", data.get("ack"))
        status = {1: "sent", 2: "delivered", 3: "read", 4: "read", "sent": "sent", "delivered": "delivered", "read": "read",
                  "failed": "failed"}.get(ack) or ("failed" if event == "message.failed" else None)
        if mid and status:
            # never move a message backwards (e.g. late 'sent' after 'read')
            db.run("UPDATE messages SET status=? WHERE wamid=? AND status NOT IN ('read') AND NOT (status='delivered' AND ?='sent')",
                   (status, str(mid), status))
    return {"ok": True}


# ---------- OpenWA management (Sessions, Number Checker, Message Tester, Webhooks) ----------

def _need_openwa():
    if wa.mode() != "openwa":
        raise HTTPException(400, "These tools need WA_MODE=gateway in .env")


def _ow_call(method: str, path: str, **kw):
    try:
        return wa._ow(method, path, **kw)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(502, str(e))


class SessionIn(BaseModel):
    name: str


class CheckIn(BaseModel):
    numbers: list[str]
    session_id: str = ""


class SendTestIn(BaseModel):
    phone: str
    text: str
    session_id: str = ""


@app.get("/api/openwa/sessions")
def openwa_sessions():
    if wa.mode() != "openwa":
        return []
    default = os.getenv("OPENWA_SESSION", "btree")
    rows = _ow_call("GET", "/sessions")
    keep = ("id", "name", "status", "phone", "pushName", "connectedAt", "lastActive", "createdAt", "lastError")
    return [{**{k: s.get(k) for k in keep}, "is_default": default in (s.get("id"), s.get("name"))} for s in rows]


@app.post("/api/openwa/sessions")
def openwa_create_session(s: SessionIn):
    _need_openwa()
    name = s.name.strip()
    if not re.fullmatch(r"[A-Za-z0-9-]{1,40}", name):
        raise HTTPException(422, "Use letters, numbers and hyphens only")
    return _ow_call("POST", "/sessions", json={"name": name})


@app.post("/api/openwa/sessions/{sid}/{action}")
def openwa_session_action(sid: str, action: str):
    _need_openwa()
    if action not in ("start", "stop", "logout", "force-kill"):
        raise HTTPException(404)
    return _ow_call("POST", f"/sessions/{sid}/{action}")


@app.delete("/api/openwa/sessions/{sid}")
def openwa_delete_session(sid: str):
    _need_openwa()
    wa._session_cache.clear()
    return _ow_call("DELETE", f"/sessions/{sid}") or {"ok": True}


@app.get("/api/openwa/sessions/{sid}/qr")
def openwa_session_qr(sid: str):
    _need_openwa()
    return _ow_call("GET", f"/sessions/{sid}/qr")


@app.post("/api/openwa/check")
def openwa_check_numbers(c: CheckIn):
    """Asks WhatsApp whether each number has an account. Limited and paced: bulk lookups can get a number restricted."""
    _need_openwa()
    nums = list(dict.fromkeys(wa.normalize_phone(n) for n in c.numbers if re.sub(r"\D", "", n)))
    if not nums:
        raise HTTPException(422, "Enter at least one number")
    if len(nums) > 20:
        raise HTTPException(422, "Check at most 20 numbers at a time")
    sid = c.session_id or wa.ow_session_id()
    out = []
    for i, n in enumerate(nums):
        try:
            r = wa._ow("GET", f"/sessions/{sid}/contacts/check/{n}")
            out.append({"number": n, "exists": bool(r.get("exists")), "whatsapp_id": r.get("whatsappId"), "error": ""})
        except Exception as e:
            out.append({"number": n, "exists": None, "whatsapp_id": None, "error": str(e)})
        if i < len(nums) - 1:
            time.sleep(0.5)
    return out


@app.post("/api/openwa/send-test")
def openwa_send_test(t: SendTestIn):
    _need_openwa()
    phone = wa.normalize_phone(t.phone)
    if len(phone) < 10:
        raise HTTPException(422, "Invalid phone number")
    if not t.text.strip() or len(t.text) > 4096:
        raise HTTPException(422, "Message must be 1 to 4096 characters")
    sid = t.session_id or wa.ow_session_id()
    started = time.monotonic()
    r = _ow_call("POST", f"/sessions/{sid}/messages/send-text", json={"chatId": f"{phone}@c.us", "text": t.text})
    return {"ok": True, "to": phone, "message_id": r.get("messageId"), "timestamp": r.get("timestamp"),
            "ms": int((time.monotonic() - started) * 1000)}


@app.get("/api/openwa/webhooks")
def openwa_webhooks():
    _need_openwa()
    hooks = _ow_call("GET", f"/sessions/{wa.ow_session_id()}/webhooks")
    return [{"id": h.get("id"), "url": h.get("url"), "events": h.get("events") or [], "active": h.get("active", True)}
            for h in (hooks if isinstance(hooks, list) else [])]


@app.post("/api/openwa/webhooks/{hid}/test")
def openwa_webhook_test(hid: str):
    _need_openwa()
    return _ow_call("POST", f"/sessions/{wa.ow_session_id()}/webhooks/{hid}/test")


@app.get("/api/analytics")
def analytics(span: str = Query("24h", alias="range")):
    """Message counts per bucket (hourly for 24h, daily for 7d/30d), from the CRM's own message log."""
    hours = {"24h": 24, "7d": 7 * 24, "30d": 30 * 24}.get(span, 24)
    step = timedelta(hours=1) if hours == 24 else timedelta(days=1)
    end = now().replace(minute=0, second=0, microsecond=0) if hours == 24 else now().replace(hour=0, minute=0, second=0, microsecond=0)
    n = 24 if hours == 24 else hours // 24
    start = end - step * (n - 1)
    buckets = {start + step * i: {"in": 0, "out": 0} for i in range(n)}
    for m in db.q("SELECT direction, created_at FROM messages WHERE direction!='sys' AND created_at>=?", (ts(start),)):
        t = parse_ts(m["created_at"])
        key = t.replace(minute=0, second=0, microsecond=0) if hours == 24 else t.replace(hour=0, minute=0, second=0, microsecond=0)
        if key in buckets:
            buckets[key]["in" if m["direction"] == "in" else "out"] += 1
    return [{"t": k.strftime("%Y-%m-%dT%H:%M:%SZ"), **v} for k, v in sorted(buckets.items())]


# ---------- menu chatbot ----------

class BotTestIn(BaseModel):
    flow: dict          # the flow as currently edited (may be unsaved)
    state: str = ""
    invalid: int = 0
    text: str = ""


@app.get("/api/bot/flow")
def get_flow():
    return {"flow": bot_flow.load(), "courses": [c["name"] for c in db.q("SELECT name FROM courses ORDER BY name")],
            "ai_on": ai_agent.enabled() and ai_agent.has_key()}


@app.put("/api/bot/flow")
def put_flow(flow: dict):
    try:
        return {"ok": True, "flow": bot_flow.save(flow)}
    except (ValueError, TypeError) as e:
        raise HTTPException(422, str(e))


@app.post("/api/bot/test")
def test_flow(body: BotTestIn):
    """Runs one chatbot turn on the edited flow. Nothing is sent or saved."""
    try:
        flow = bot_flow.validate(body.flow)
    except (ValueError, TypeError) as e:
        raise HTTPException(422, str(e))
    values = {"name": "there", "institute": os.getenv("INSTITUTE_NAME", "BTree Systems")}
    return bot_flow.step(flow, body.state, body.invalid, body.text, values, db.q("SELECT * FROM courses ORDER BY name"),
                         option_link_base(flow))


# ---------- AI agent ----------

class AISettingsIn(BaseModel):
    ai_enabled: bool = False
    ai_team_phone: str = ""
    ai_ack_message: str
    ai_handover_message: str
    ai_max_replies: int = 6
    ai_knowledge: str = ""


class AITestIn(BaseModel):
    messages: list[dict]   # [{"role": "user"|"assistant", "content": "..."}], the sample chat so far


@app.get("/api/ai/settings")
def ai_settings():
    s = ai_agent.settings()
    return {**s, "ai_enabled": s["ai_enabled"] == "1", "ai_max_replies": int(s["ai_max_replies"] or 6),
            "has_key": ai_agent.has_key(), "model": ai_agent.MODEL}


@app.put("/api/ai/settings")
def save_ai_settings(body: AISettingsIn):
    if not body.ai_ack_message.strip() or not body.ai_handover_message.strip():
        raise HTTPException(422, "The acknowledgement and hand-over messages cannot be empty")
    if body.ai_enabled and not ai_agent.has_key():
        raise HTTPException(422, "Add ANTHROPIC_API_KEY to .env and restart before switching the agent on")
    ai_agent.save_settings({**body.model_dump(), "ai_enabled": "1" if body.ai_enabled else "0",
                            "ai_max_replies": max(1, min(20, body.ai_max_replies))})
    return {"ok": True}


@app.post("/api/ai/test")
def ai_test(body: AITestIn):
    """Ask the agent what it would reply to a sample chat. Nothing is sent on WhatsApp."""
    if not ai_agent.has_key():
        raise HTTPException(422, "Add ANTHROPIC_API_KEY to .env and restart to try the agent")
    msgs = [{"role": m.get("role"), "content": str(m.get("content", ""))} for m in body.messages
            if m.get("role") in ("user", "assistant") and str(m.get("content", "")).strip()]
    if not msgs or msgs[-1]["role"] != "user":
        raise HTTPException(422, "Type a message from the lead first")
    try:
        out = ai_agent.ask(msgs, ai_agent.settings())
    except Exception as e:
        raise HTTPException(502, f"AI agent error: {e}")
    cfg = ai_agent.settings()
    out["handover_text"] = ai_agent.fill(cfg["ai_handover_message"], {"name": "there", "team_phone": cfg["ai_team_phone"] or "our office number",
                                                                       "institute": os.getenv("INSTITUTE_NAME", "BTree Systems")}) if out["handover"] else ""
    return out


@app.post("/api/leads/{lid}/callback-done")
def callback_done(lid: int):
    db.run("UPDATE leads SET needs_callback=0 WHERE id=?", (lid,))
    return {"ok": True}


# ---------- mock helpers (local testing) ----------

@app.post("/api/mock/incoming")
def mock_incoming(i: IncomingIn):
    if wa.mode() != "mock":
        raise HTTPException(403, "Only available in mock mode")
    handle_inbound(i.phone, i.text, name=i.name)
    return {"ok": True}


# ---------- frontend ----------

@app.get("/enquiry")
def enquiry_page():
    return FileResponse(FRONTEND / "enquiry.html")


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="ui")
