"""Regression: menu chatbot, tap-to-reply options, template buttons, keyword bot fallback. Mock mode, nothing sent."""
import os
import re
import sys
from urllib.parse import unquote

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers

main, db, auth, cur = helpers.setup({"WHATSAPP_NUMBER": "+91 89259 67585"})
from fastapi.testclient import TestClient  # noqa: E402

ok, results = helpers.checker()
LINK = re.compile(r"https://wa\.me/(\d+)\?text=(\S+)")


def say(c, phone, text):
    c.post("/api/mock/incoming", json={"phone": phone, "name": "Divya", "text": text})
    lead = db.one("SELECT * FROM leads WHERE phone=?", (phone,))
    out = db.q("SELECT body, sender FROM messages WHERE lead_id=? AND direction='out' ORDER BY id DESC LIMIT 1", (lead["id"],))
    return lead, (out[0]["body"] if out else ""), (out[0]["sender"] if out else "")


def tap(msg, n):  # what the phone types when option n of a message is tapped
    return unquote(LINK.findall(msg)[n - 1][1])


with TestClient(main.app) as c:
    flow = c.get("/api/bot/flow").json()["flow"]
    c.put("/api/bot/flow", json={**flow, "team_phone": "+91 98400 12345", "option_style": "links"})
    p = helpers.newphone("9300")
    lead, msg, sender = say(c, p, "what are the fees??")
    ok(sender == "menu-bot" and "counsellor will contact you promptly" in msg, "first message: welcome with the counsellor promise")
    ok(len(LINK.findall(msg)) == 4 and "Pick one" in msg and chr(0x20E3) not in msg and "1. Courses, fees & syllabus\nhttps://wa.me/918925967585" in msg, "options are plain-text tap links to our number")
    ok(lead["needs_callback"] == 1, "lead flagged callback pending")
    lead, msg, _ = say(c, p, tap(msg, 1))
    ok("Which course" in msg, "tap 1: course list")
    names = [x["name"] for x in db.q("SELECT name FROM courses ORDER BY name")]
    lead, msg, _ = say(c, p, tap(msg, names.index("Full Stack Development") + 1))
    ok("*Full Stack Development*" in msg and "Rs. 35,000" in msg, "tap a course: its details")
    lead, msg, _ = say(c, p, "what about placements")
    ok(msg.startswith("Sorry, I can only understand") and "placements" not in msg.lower().split("sorry")[0] and lead["unresolved"] == 1, "free text is not answered; counted as unresolved")
    lead, msg, _ = say(c, p, tap(msg, 3))
    ok("+91 98400 12345" in msg and lead["bot_paused"] == 1, "'Talk to a counsellor': hand-over with the team number, bot pauses")
    n = db.one("SELECT COUNT(*) n FROM messages WHERE lead_id=? AND direction='out'", (lead["id"],))["n"]
    say(c, p, "hello?")
    ok(db.one("SELECT COUNT(*) n FROM messages WHERE lead_id=? AND direction='out'", (lead["id"],))["n"] == n, "bot silent after hand-over")

    # numbers style + typing numbers
    c.put("/api/bot/flow", json={**flow, "team_phone": "+91 98400 12345", "option_style": "numbers"})
    p2 = helpers.newphone("9300")
    _, msg, _ = say(c, p2, "hi")
    ok(not LINK.search(msg) and "Reply with the number" in msg, "numbers style: numbered list")
    _, msg, _ = say(c, p2, "1")
    ok("Which course" in msg, "typing a number works")
    c.put("/api/bot/flow", json={**flow, "team_phone": "+91 98400 12345", "option_style": "links"})

    # too many wrong replies -> hand over
    p3 = helpers.newphone("9300")
    say(c, p3, "hi"); say(c, p3, "x"); say(c, p3, "y")
    lead, msg, _ = say(c, p3, "z")
    ok(lead["bot_paused"] == 1 and "+91 98400 12345" in msg, "third wrong reply hands over")

    # editor validation
    bad = {**flow, "nodes": {**flow["nodes"], "demo": {**flow["nodes"]["demo"], "options": [{"label": "x", "goto": "nowhere"}]}}}
    ok(c.put("/api/bot/flow", json=bad).status_code == 422, "option to a missing step refused")
    ok(c.put("/api/bot/flow", json={**flow, "nodes": {k: v for k, v in flow["nodes"].items() if k != "handover"}}).status_code == 422, "hand-over step cannot be deleted")

    # template buttons
    t = next(x for x in c.get("/api/templates").json() if x["name"] == "demo_class_invite")
    lid = c.post("/api/leads", json={"name": "Asha", "phone": helpers.newphone("9300"), "course": "Full Stack Development", "consent": True}).json()["id"]
    ph = db.one("SELECT phone FROM leads WHERE id=?", (lid,))["phone"]
    c.post(f"/api/templates/{t['id']}/send/{lid}")
    msg = db.one("SELECT body, sender FROM messages WHERE lead_id=? ORDER BY id DESC", (lid,))
    ok(len(LINK.findall(msg["body"])) == 2 and msg["sender"] == "Administrator", "template message: tap options, sent by the signed-in user")
    c.post("/api/mock/incoming", json={"phone": ph, "text": tap(msg["body"], 1)})
    d = c.get(f"/api/leads/{lid}/messages").json()
    ok(d["messages"][-2]["kind"] == "button" and d["lead"]["status"] == "interested" and "reserved" in d["messages"][-1]["body"], "tapping a template option: auto-reply + status")

    # old keyword bot when the menu is switched off
    c.put("/api/bot/flow", json={**flow, "enabled": False})
    lead, msg, sender = say(c, helpers.newphone("9300"), "fees")
    ok(sender == "bot", "menu off: the old keyword bot answers")

    # AI agent stays out of the way when off
    ok(c.get("/api/ai/settings").json()["ai_enabled"] is False, "AI agent off by default")

print(f"\n{results['pass']} passed, {results['fail']} failed")
sys.exit(1 if results["fail"] else 0)
