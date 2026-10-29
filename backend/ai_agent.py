"""AI auto-reply agent (Claude) for incoming WhatsApp messages.

Flow for one conversation:
  1. First message of a conversation -> a fixed acknowledgement: "a counsellor will contact you promptly".
  2. Questions about courses, fees, syllabus, timings -> answered by Claude from the course catalog + knowledge text.
  3. When the lead is done, wants a person (call, admission, payment, demo booking...), asks something the
     catalog can't answer, or after N AI replies -> a final hand-over message with the team contact,
     and the bot pauses so a counsellor takes over. The lead is flagged "callback pending".

Settings live in the `settings` table (edited on the AI Agent page). Needs ANTHROPIC_API_KEY in .env.
"""
import json
import os
from datetime import datetime, timedelta, timezone

import anthropic

import db
import livechat

MODEL = os.getenv("AI_MODEL", "claude-opus-5-5")
SESSION_GAP = timedelta(hours=12)   # a new conversation starts after this much silence from the lead

DEFAULTS = {
    "ai_enabled": "0",
    "ai_team_phone": "",
    "ai_ack_message": ("Thanks for reaching out to {{institute}}! 🙏 Our counsellor will contact you promptly. "
                       "Meanwhile, ask me anything about our courses, fees, syllabus or batch timings."),
    "ai_handover_message": ("Thank you, {{name}}! Our counsellor will contact you promptly. "
                            "You can also reach our team directly on {{team_phone}}."),
    "ai_max_replies": "6",
    "ai_knowledge": "",
}

REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "reply": {"type": "string", "description": "The WhatsApp message to send to the lead. Empty only if nothing useful to add."},
        "handover": {"type": "boolean", "description": "True when a human counsellor should take over now."},
        "course": {"type": "string", "description": "Exact course name the lead is interested in, from the catalog, or empty."},
    },
    "required": ["reply", "handover", "course"],
    "additionalProperties": False,
}

_client = None


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(timeout=60.0, max_retries=2)
    return _client


def has_key() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN"))


# ---------- settings ----------

def settings() -> dict:
    rows = {r["key"]: r["value"] for r in db.q("SELECT key, value FROM settings WHERE key LIKE 'ai_%'")}
    return {k: rows.get(k, v) for k, v in DEFAULTS.items()}


def save_settings(values: dict):
    for k in DEFAULTS:
        if k in values:
            db.run("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (k, str(values[k])))


def enabled() -> bool:
    return settings()["ai_enabled"] == "1"


def fill(text: str, values: dict) -> str:
    for k, v in values.items():
        text = text.replace("{{" + k + "}}", str(v or ""))
    return text


# ---------- prompt ----------

def _catalog() -> str:
    lines = []
    for c in db.q("SELECT * FROM courses ORDER BY name"):
        lines.append(f"- {c['name']}: duration {c['duration'] or 'ask counsellor'}; fee {c['fee'] or 'ask counsellor'}; "
                     f"batches {c['batch_timing'] or 'ask counsellor'}; syllabus {c['syllabus_url'] or 'ask counsellor'}"
                     + (f"; about: {c['description']}" if c.get("description") else ""))
    return "\n".join(lines) or "(no courses listed yet)"


def system_prompt(cfg: dict) -> str:
    inst = os.getenv("INSTITUTE_NAME", "BTree Systems")
    return f"""You are the WhatsApp assistant of {inst}, an IT training institute in Chennai. You chat with people who enquired about courses.

Your job: answer questions about our courses (fees, duration, batch timings, syllabus, what the course covers) using only the information below, and hand the conversation to a human counsellor at the right moment.

Course catalog:
{_catalog()}

Other information from the institute:
{cfg['ai_knowledge'].strip() or '(none)'}

How to reply:
- This is WhatsApp: short, warm, plain messages (usually 1-4 sentences). WhatsApp formatting only: *bold*, line breaks, simple lists. No headings or tables.
- Reply in the language the lead uses (English, Tamil or Tanglish).
- The lead has already been told a counsellor will contact them. Do not repeat that in every message.
- Use only the facts above. If something is not listed (discounts, exact start dates, placement guarantees, refunds, certificates, anything else), do not guess: say the counsellor will confirm, and set handover to true.
- Share the syllabus link when they ask about syllabus or course content.
- If you can tell which course they mean, put its exact catalog name in "course".

Set handover to true when any of these is true:
- they want to talk to a person, ask for a call back, or want to visit, enrol, pay, book a demo or reserve a seat;
- they seem done (thanks, ok, bye, got it) or have no further questions;
- you cannot answer from the information above;
- they are upset or complaining.
When handover is true, "reply" may answer their last question briefly or be empty; the team-contact message is sent after it automatically."""


def _history(lead_id: int, limit: int = 16) -> list[dict]:
    """Recent conversation as alternating user/assistant turns (the lead is the user)."""
    rows = db.q("SELECT direction, body FROM messages WHERE lead_id=? AND status!='failed' ORDER BY id DESC LIMIT ?",
                (lead_id, limit))[::-1]
    msgs = []
    for r in rows:
        role = "user" if r["direction"] == "in" else "assistant"
        text = (r["body"] or "").strip() or "[non-text message]"
        if msgs and msgs[-1]["role"] == role:
            msgs[-1]["content"] += "\n" + text
        else:
            msgs.append({"role": role, "content": text})
    while msgs and msgs[0]["role"] != "user":   # the API needs the first turn to be the user's
        msgs.pop(0)
    while msgs and msgs[-1]["role"] != "user":  # ...and the last one (we are answering the lead's latest message)
        msgs.pop()
    return msgs


def ask(messages: list[dict], cfg: dict) -> dict:
    """One Claude call. Returns {"reply", "handover", "course"}; raises on API errors."""
    resp = client().beta.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=[{"type": "text", "text": system_prompt(cfg), "cache_control": {"type": "ephemeral"}}],
        messages=messages,
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": REPLY_SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if resp.stop_reason == "refusal":
        return {"reply": "", "handover": True, "course": ""}
    text = next((b.text for b in resp.content if b.type == "text"), "")
    data = json.loads(text)
    return {"reply": str(data.get("reply", "")).strip(), "handover": bool(data.get("handover")),
            "course": str(data.get("course", "")).strip()}


# ---------- the conversation flow ----------

def _session_start(lead_id: int) -> str:
    """Timestamp where the current conversation began (after the last long silence)."""
    ins = db.q("SELECT created_at FROM messages WHERE lead_id=? AND direction='in' ORDER BY id DESC LIMIT 50", (lead_id,))
    start = ins[0]["created_at"] if ins else None
    for newer, older in zip(ins, ins[1:]):
        t_new = datetime.strptime(newer["created_at"], "%Y-%m-%d %H:%M:%S")
        t_old = datetime.strptime(older["created_at"], "%Y-%m-%d %H:%M:%S")
        if t_new - t_old > SESSION_GAP:
            break
        start = older["created_at"]
    return start or "1970-01-01 00:00:00"


def handle(lead: dict, send, lead_values: dict) -> bool:
    """Reply to the lead's latest message. `send(text, sender)` delivers a WhatsApp message.
    Returns False when the agent did not handle it (disabled, no key, API error) so the keyword bot can."""
    if not (enabled() and has_key()):
        return False
    cfg = settings()
    values = {**lead_values, "team_phone": cfg["ai_team_phone"] or "our office number"}
    start = _session_start(lead["id"])
    in_session = db.q("SELECT sender, body FROM messages WHERE lead_id=? AND direction='out' AND created_at>=?",
                      (lead["id"], start))
    history = _history(lead["id"])  # read before the acknowledgement goes out, so it ends on the lead's message
    if not history:
        return True
    if not any(m["sender"] == "ai-ack" for m in in_session):
        send(fill(cfg["ai_ack_message"], values), "ai-ack")
        db.run("UPDATE leads SET needs_callback=1, callback_at=? WHERE id=?",
               (datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), lead["id"]))
    ai_replies = sum(1 for m in in_session if m["sender"] == "ai")
    try:
        out = ask(history, cfg)
    except (anthropic.APIError, ValueError) as e:   # API/network problems or unparseable output
        print("ai agent:", type(e).__name__, e)
        return True  # the acknowledgement already went out; a counsellor will follow up
    if out["course"]:
        db.run("UPDATE leads SET course=? WHERE id=? AND (course IS NULL OR course='')", (out["course"], lead["id"]))
    if out["reply"]:
        send(out["reply"], "ai")
        ai_replies += 1
    if out["handover"] or ai_replies >= int(cfg["ai_max_replies"] or 6):
        send(fill(cfg["ai_handover_message"], values), "ai-handover")
        livechat.request_human(lead["id"], "the AI agent handed the chat to a counsellor")
    return True
