"""Live chat: conversation stages, intervention and a tracked history of who did what.

A conversation is always in one of four stages:
  active      the bot is handling the contact
  requesting  the contact asked for a person (chatbot / AI hand-over) and nobody has picked the chat up yet
  intervened  a team member has taken it: only that person replies
  closed      resolved; a new message from the contact reopens it as active

Who sees what: everyone with Live Chat sees Active and Requesting chats and the ones they intervened in.
Chats other people are handling, and the history of closed chats they did not handle, need the
"see & take over chats handled by others" permission (managers and admins).

Every change is written into the chat as an event ("User Intervened by Arun (waited 4 min)"),
stored as a message with direction 'sys', kind 'event'.
"""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request

import auth
import db

router = APIRouter()
STAGES = ("active", "requesting", "intervened", "closed")


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _parse(s):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) if s else None


def stage_of(l: dict) -> str:
    if (l.get("chat_status") or "open") == "resolved":
        return "closed"
    if l.get("intervened_by"):
        return "intervened"
    return "requesting" if l.get("requested_at") else "active"


def sees_all(user: dict) -> bool:
    return "chats_all" in auth.perms_of(user)


def can_view(lead: dict, user: dict) -> bool:
    """Whether this team member may open the conversation."""
    if sees_all(user):
        return True
    st = stage_of(lead)
    if st == "intervened":
        return lead["intervened_by"] == user["id"]
    if st == "closed":
        return user["id"] in (lead.get("handled_by"), lead.get("closed_by"))
    return True


def _wait(since: str | None) -> str:
    t = _parse(since)
    if not t:
        return ""
    m = int((datetime.now(timezone.utc) - t).total_seconds() // 60)
    return "under a minute" if m < 1 else f"{m} min" if m < 60 else f"{m // 60} h {m % 60} min"


def log_event(lead_id: int, text: str, sender: str = "") -> int:
    return db.run("INSERT INTO messages(lead_id,direction,kind,body,status,sender,created_at) VALUES(?,?,?,?,?,?,?)",
                  (lead_id, "sys", "event", text, "sent", sender, ts()))


def _lead(lid: int) -> dict:
    l = db.one("SELECT * FROM leads WHERE id=?", (lid,))
    if not l:
        raise HTTPException(404, "Contact not found")
    return l


def _name(uid) -> str:
    u = db.one("SELECT name FROM users WHERE id=?", (uid,)) if uid else None
    return u["name"] if u else "someone"


def _guard(lead: dict, user: dict):
    if not can_view(lead, user):
        who = _name(lead.get("intervened_by")) if lead.get("intervened_by") else "another team member"
        raise HTTPException(403, f"This chat is being handled by {who}.")


# ---------- stage changes (used by the chatbot, the AI agent and the endpoints below) ----------

def request_human(lead_id: int, reason: str):
    """The contact needs a person: Active -> Requesting. The bot stops answering until someone steps in."""
    lead = _lead(lead_id)
    db.run("UPDATE leads SET bot_paused=1, needs_callback=1 WHERE id=?", (lead_id,))
    if stage_of(lead) == "active":
        db.run("UPDATE leads SET requested_at=? WHERE id=?", (ts(), lead_id))
        log_event(lead_id, f"Moved to Requesting: {reason}")


def intervene(lead: dict, user: dict, taking_over: bool = False):
    prev = lead.get("intervened_by")
    waited = _wait(lead.get("requested_at")) if stage_of(lead) == "requesting" else ""
    db.run("""UPDATE leads SET intervened_by=?, intervened_at=?, handled_by=?, requested_at=NULL, bot_paused=1,
              chat_status='open', closed_by=NULL, closed_at=NULL WHERE id=?""", (user["id"], ts(), user["id"], lead["id"]))
    if taking_over and prev and prev != user["id"]:
        log_event(lead["id"], f"{user['name']} took over from {_name(prev)}", user["name"])
    else:
        log_event(lead["id"], f"User Intervened by {user['name']}" + (f" (waited {waited})" if waited else ""), user["name"])


def ensure_can_send(lid: int, user: dict) -> dict:
    """Called before a team member sends. Claims an unclaimed chat; refuses if someone else has it."""
    lead = _lead(lid)
    holder = lead.get("intervened_by")
    if holder and holder != user["id"]:
        raise HTTPException(409, f"This chat is intervened by {_name(holder)}. Take over to reply.")
    if not holder:
        intervene(lead, user)
        lead = _lead(lid)
    return lead


# ---------- conversations ----------

@router.get("/api/livechat/conversations")
def conversations(request: Request, filter: str = "active", q: str = "", unread: int = 0, counts_only: int = 0):
    me = request.state.user
    everything = sees_all(me)
    rows = db.q("""
      SELECT l.id, l.name, l.phone, l.unread, l.bot_paused, l.needs_callback, l.chat_status, l.intervened_by, l.requested_at,
             l.handled_by, l.closed_by, l.closed_at, l.status, l.course,
             (SELECT body FROM messages m WHERE m.lead_id=l.id AND m.direction!='sys' ORDER BY m.id DESC LIMIT 1) AS last_body,
             (SELECT direction FROM messages m WHERE m.lead_id=l.id AND m.direction!='sys' ORDER BY m.id DESC LIMIT 1) AS last_dir,
             (SELECT created_at FROM messages m WHERE m.lead_id=l.id AND m.direction!='sys' ORDER BY m.id DESC LIMIT 1) AS last_at
      FROM leads l WHERE EXISTS (SELECT 1 FROM messages m WHERE m.lead_id=l.id AND m.direction!='sys')""")
    names = {u["id"]: u["name"] for u in db.q("SELECT id, name FROM users")}
    counts = {"active": 0, "requesting": 0, "intervened": 0, "intervened_me": 0, "intervened_others": 0, "closed": 0, "unread": 0, "alert": 0}
    out, needle = [], q.strip().lower()
    for r in rows:
        if not can_view(r, me):
            continue
        st = stage_of(r)
        r["stage"] = st
        r["intervened_by_name"] = names.get(r["intervened_by"])
        r["closed_by_name"] = names.get(r["closed_by"])
        r["group"] = ("me" if r["intervened_by"] == me["id"] else "others") if st == "intervened" else None
        counts[st] += 1
        if r["group"]:
            counts["intervened_me" if r["group"] == "me" else "intervened_others"] += 1
        if st != "closed" and r["unread"] and (r["group"] != "others"):
            counts["unread"] += 1
        # the menu badge: contacts waiting for a person, plus unread messages in my own chats
        if st == "requesting" or (r["group"] == "me" and r["unread"]):
            counts["alert"] += 1
        if st != filter:
            continue
        if unread and not r["unread"]:
            continue
        if needle and needle not in (r["name"] or "").lower() and needle not in (r["phone"] or ""):
            continue
        out.append(r)
    if filter == "requesting":
        out.sort(key=lambda r: r["requested_at"] or "")            # the longest-waiting contact first
    elif filter == "closed":
        out.sort(key=lambda r: r["closed_at"] or r["last_at"] or "", reverse=True)
    else:
        out.sort(key=lambda r: r["last_at"] or "", reverse=True)
    return {"items": [] if counts_only else out, "counts": counts, "sees_all": everything}


@router.get("/api/leads/{lid}/messages")
def lead_messages(lid: int, request: Request):
    import main  # window_open lives with the sending code
    lead = _lead(lid)
    _guard(lead, request.state.user)
    if lead.get("unread"):
        db.run("UPDATE leads SET unread=0 WHERE id=?", (lid,))   # opening the chat reads it
    lead["intervened_by_name"] = _name(lead["intervened_by"]) if lead.get("intervened_by") else None
    lead["closed_by_name"] = _name(lead["closed_by"]) if lead.get("closed_by") else None
    lead["stage"] = stage_of(lead)
    lead["waiting"] = _wait(lead.get("requested_at")) if lead["stage"] == "requesting" else ""
    return {"lead": lead, "window_open": main.window_open(lead),
            "messages": db.q("SELECT * FROM messages WHERE lead_id=? ORDER BY id", (lid,))}


# ---------- actions ----------

@router.post("/api/leads/{lid}/intervene")
def api_intervene(lid: int, request: Request):
    lead, user = _lead(lid), request.state.user
    _guard(lead, user)
    if lead.get("intervened_by") and lead["intervened_by"] != user["id"]:
        raise HTTPException(409, f"This chat is intervened by {_name(lead['intervened_by'])}. Use Take Over.")
    if lead.get("intervened_by") != user["id"]:
        intervene(lead, user)
    return {"ok": True}


@router.post("/api/leads/{lid}/takeover")
def api_takeover(lid: int, request: Request):
    lead, user = _lead(lid), request.state.user
    if lead.get("intervened_by") and lead["intervened_by"] != user["id"] and not sees_all(user):
        raise HTTPException(403, "Only managers can take over a chat another team member is handling.")
    intervene(lead, user, taking_over=True)
    return {"ok": True}


def _own_or_manager(lead: dict, user: dict, what: str):
    holder = lead.get("intervened_by")
    if holder and holder != user["id"] and not sees_all(user):
        raise HTTPException(403, f"Only {_name(holder)} or a manager can {what} this chat.")


@router.post("/api/leads/{lid}/release")
def api_release(lid: int, request: Request):
    """Hands the chat back to the bot (Active); it starts again from the welcome on the contact's next message."""
    lead, user = _lead(lid), request.state.user
    _own_or_manager(lead, user, "release")
    db.run("UPDATE leads SET intervened_by=NULL, intervened_at=NULL, requested_at=NULL, bot_paused=0, bot_node='', bot_invalid=0 WHERE id=?", (lid,))
    log_event(lid, f"Chat released to the bot by {user['name']}", user["name"])
    return {"ok": True}


@router.post("/api/leads/{lid}/resolve")
def api_resolve(lid: int, request: Request):
    """Closes the conversation (stage Closed)."""
    lead, user = _lead(lid), request.state.user
    _own_or_manager(lead, user, "close")
    db.run("""UPDATE leads SET chat_status='resolved', closed_by=?, closed_at=?, intervened_by=NULL, intervened_at=NULL,
              requested_at=NULL, needs_callback=0, unread=0 WHERE id=?""", (user["id"], ts(), lid))
    log_event(lid, f"Chat closed (resolved) by {user['name']}", user["name"])
    return {"ok": True}


@router.post("/api/leads/{lid}/reopen")
def api_reopen(lid: int, request: Request):
    lead, user = _lead(lid), request.state.user
    _guard(lead, user)
    db.run("UPDATE leads SET chat_status='open', closed_by=NULL, closed_at=NULL, bot_paused=0, bot_node='', bot_invalid=0 WHERE id=?", (lid,))
    log_event(lid, f"Chat reopened by {user['name']}", user["name"])
    return {"ok": True}
