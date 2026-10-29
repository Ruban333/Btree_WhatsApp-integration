"""Menu chatbot: every message the lead gets is a set reply, every choice is a numbered option, and every
path ends with the counsellor hand-over. No free-form understanding (and no AI).

The flow is a small graph stored as JSON in settings['bot_flow'] and edited on the Chatbot page:
  nodes[id] = {"title", "text", "options": [{"label", "goto"}]}
Two special nodes:
  "courses"  - lists the courses from the Courses page; picking one sends its `detail` text, then its options.
  "handover" - the final message (team contact). The bot pauses so a counsellor takes over.
The lead's position is a state string: a node id, or "courses#detail" after a course was shown.
"""
import copy
import json
import re
from urllib.parse import quote

import db

MAX_OPTIONS = 9

DEFAULT_FLOW = {
    "enabled": True,
    "option_style": "links",   # "links": tap an option, then Send. "numbers": reply with 1, 2, 3
    "team_phone": "",
    "max_invalid": 2,
    "invalid_text": "Sorry, I can only understand the options below.",
    "nodes": {
        "start": {
            "title": "Welcome",
            "text": "Hi {{name}}! 👋 Welcome to *{{institute}}*.\nOur counsellor will contact you promptly. "
                    "Meanwhile, choose what you would like to know:",
            "menu_text": "Main menu. What would you like to know?",
            "options": [
                {"label": "Courses, fees & syllabus", "goto": "courses"},
                {"label": "Free demo class", "goto": "demo"},
                {"label": "Placement support", "goto": "placement"},
                {"label": "Talk to a counsellor", "goto": "handover"},
            ],
        },
        "courses": {
            "title": "Course list",
            "text": "Which course would you like to know about?",
            "detail": "*{{course}}*\n⏱ Duration: {{duration}}\n💰 Fee: {{fee}}\n🕒 Batches: {{batch_timing}}\n"
                      "📄 Syllabus: {{syllabus_url}}",
            "options": [
                {"label": "See another course", "goto": "courses"},
                {"label": "Main menu", "goto": "start"},
                {"label": "Talk to a counsellor", "goto": "handover"},
            ],
        },
        "demo": {
            "title": "Free demo class",
            "text": "We run a *free demo class* every Saturday. Our counsellor will share the timing and joining details.",
            "options": [
                {"label": "Book my demo seat", "goto": "handover"},
                {"label": "Main menu", "goto": "start"},
            ],
        },
        "placement": {
            "title": "Placement support",
            "text": "Every course includes resume building, mock interviews and placement assistance with our hiring partners.",
            "options": [
                {"label": "Main menu", "goto": "start"},
                {"label": "Talk to a counsellor", "goto": "handover"},
            ],
        },
        "handover": {
            "title": "Talk to a counsellor",
            "text": "Thank you, {{name}}! 🙏 Our counsellor will contact you promptly.\n"
                    "You can also reach our team directly on {{team_phone}}.",
            "options": [],
        },
    },
}


# ---------- storage ----------

OLD_INVALID_TEXT = "Sorry, I can only understand the options below. Please reply with a number 👇"


def load() -> dict:
    row = db.one("SELECT value FROM settings WHERE key='bot_flow'")
    flow = json.loads(row["value"]) if row else copy.deepcopy(DEFAULT_FLOW)
    if flow.get("invalid_text") == OLD_INVALID_TEXT:  # the old default told leads to type a number; options carry their own hint now
        flow["invalid_text"] = DEFAULT_FLOW["invalid_text"]
    flow.setdefault("option_style", "links")
    return flow


def validate(flow: dict) -> dict:
    """Returns a cleaned flow or raises ValueError with a message for the editor."""
    nodes = flow.get("nodes") or {}
    for required in ("start", "courses", "handover"):
        if required not in nodes:
            raise ValueError(f"The '{required}' step is required")
    clean = {}
    for nid, n in nodes.items():
        if not re.fullmatch(r"[a-z0-9_]{1,30}", nid):
            raise ValueError(f"Step id '{nid}' must be lowercase letters, digits or _")
        title = str(n.get("title", "")).strip() or nid
        text = str(n.get("text", "")).strip()
        if not text:
            raise ValueError(f"'{title}' needs a message")
        opts = []
        for o in (n.get("options") or [])[:MAX_OPTIONS]:
            label, goto = str(o.get("label", "")).strip(), str(o.get("goto", "")).strip()
            if not label:
                continue
            if len(label) > 60:
                raise ValueError(f"Option '{label[:20]}…' in '{title}' is longer than 60 characters")
            if goto not in nodes:
                raise ValueError(f"Option '{label}' in '{title}' points to a step that does not exist")
            opts.append({"label": label, "goto": goto})
        if nid not in ("handover", "courses") and not opts:
            raise ValueError(f"'{title}' needs at least one option, so the lead can always move on")
        clean[nid] = {"title": title, "text": text, "options": [] if nid == "handover" else opts}
        if nid == "start":
            clean[nid]["menu_text"] = str(n.get("menu_text", "")).strip() or DEFAULT_FLOW["nodes"]["start"]["menu_text"]
        if nid == "courses":
            clean[nid]["detail"] = str(n.get("detail", "")).strip() or DEFAULT_FLOW["nodes"]["courses"]["detail"]
    return {"enabled": bool(flow.get("enabled", True)),
            "option_style": "numbers" if flow.get("option_style") == "numbers" else "links", "team_phone": str(flow.get("team_phone", "")).strip(),
            "max_invalid": max(0, min(5, int(flow.get("max_invalid", 2) or 0))),
            "invalid_text": str(flow.get("invalid_text", "")).strip() or DEFAULT_FLOW["invalid_text"],
            "nodes": clean}


def save(flow: dict) -> dict:
    clean = validate(flow)
    db.run("INSERT INTO settings(key,value) VALUES('bot_flow',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
           (json.dumps(clean),))
    return clean


# ---------- rendering ----------

def fill(text: str, values: dict) -> str:
    return re.sub(r"\{\{\s*(\w+)\s*\}\}", lambda m: str(values.get(m.group(1)) or "—"), text)


def _num(i: int) -> str:
    return f"{i + 1}."   # plain text numbering, no emoji


def _with_options(text: str, labels: list[str], link_base: str | None = None) -> str:
    """Appends the choices. With `link_base` ("https://wa.me/<number>?text=") each option is a tappable link that
    opens the chat with the option's text already typed: tap, then Send. Without it, numbered options."""
    if not labels:
        return text
    if link_base:
        blocks = [f"{_num(i)} {l}\n{link_base}{quote(l, safe='')}" for i, l in enumerate(labels)]
        return text + "\n\n👇 *Pick one* (tap an option, then press Send)\n\n" + "\n\n".join(blocks)
    return text + "\n\n" + "\n".join(f"{_num(i)} {l}" for i, l in enumerate(labels)) + "\n\nReply with the number of your choice."


def _course_values(c: dict) -> dict:
    return {"course": c["name"], "fee": c["fee"], "duration": c["duration"], "batch_timing": c["batch_timing"],
            "syllabus_url": c["syllabus_url"], "description": c.get("description", "")}


def options_for(flow: dict, state: str, courses: list[dict]) -> list[dict]:
    """The numbered choices the lead has at `state`: [{"label", "goto", "course"?}]."""
    nid, _, sub = state.partition("#")
    node = flow["nodes"].get(nid)
    if not node:
        return []
    if nid == "courses" and sub != "detail":
        return [{"label": c["name"], "goto": "courses#detail", "course": c} for c in courses] + [{"label": "Main menu", "goto": "start"}]
    return node["options"]


def render(flow: dict, state: str, values: dict, courses: list[dict], course: dict | None = None, first: bool = False,
           link_base: str | None = None) -> str:
    nid, _, sub = state.partition("#")
    node = flow["nodes"][nid]
    if nid == "start" and not first:  # the welcome (and its promise) goes out once; coming back shows the short menu
        text = fill(node.get("menu_text") or "What would you like to know?", values)
    elif nid == "courses" and sub == "detail":
        text = fill(node["detail"], {**values, **_course_values(course)}) if course else node["text"]
    else:
        text = fill(node["text"], values)
        if nid == "courses" and not courses:
            text = "Our course list is being updated. Our counsellor will share the details."
    return _with_options(text, [o["label"] for o in options_for(flow, state, courses)], link_base)


def _plain(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", (s or "").lower()).split())


def step(flow: dict, state: str, invalid: int, text: str, values: dict, courses: list[dict], link_base: str | None = None) -> dict:
    """One turn. Returns {"messages": [...], "state", "invalid", "started", "handover"}.
    Pure: nothing is sent or stored here, so the editor's Try-it uses the very same logic."""
    values = {**values, "team_phone": flow.get("team_phone") or "our office number"}
    nodes = flow["nodes"]
    if not state or state.partition("#")[0] not in nodes:
        # New conversation: always the start menu, whatever the lead wrote.
        return {"messages": [render(flow, "start", values, courses, first=True, link_base=link_base)], "state": "start", "invalid": 0,
                "started": True, "handover": False}
    opts = options_for(flow, state, courses)
    t = _plain(text)
    choice = next((o for i, o in enumerate(opts) if t in (str(i + 1), _plain(o["label"]))), None)
    if not choice:
        invalid += 1
        if invalid > flow.get("max_invalid", 2):
            choice = {"goto": "handover", "invalid": True}  # still not choosing after a few tries: pass to a person
        else:
            labels = [o["label"] for o in opts]
            return {"messages": [_with_options(flow["invalid_text"], labels, link_base)], "state": state, "invalid": invalid,
                    "started": False, "handover": False}
    goto = choice["goto"]
    if goto == "handover":
        return {"messages": [render(flow, "handover", values, courses, link_base=link_base)], "state": "", "invalid": 0,
                "started": False, "handover": True, "reason": "invalid" if choice.get("invalid") else "asked"}
    if goto == "courses#detail":
        return {"messages": [render(flow, "courses#detail", values, courses, choice["course"], link_base=link_base)],
                "state": "courses#detail", "invalid": 0, "started": False, "handover": False}
    if goto not in nodes:
        goto = "start"
    return {"messages": [render(flow, goto, values, courses, link_base=link_base)], "state": goto, "invalid": 0,
            "started": False, "handover": False}
