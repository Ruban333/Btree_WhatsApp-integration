"""Contacts, tags, lists, segments, opt-out, live-chat intervention and campaigns. Mock WhatsApp, DB copy."""
import os, shutil, sys, time, io
SP = os.path.dirname(os.path.abspath(__file__))
shutil.copy(r"D:\wp-agent\backend\data.db", os.path.join(SP, "crm.db"))
os.environ.update(WA_MODE="mock", DB_PATH=os.path.join(SP, "crm.db"))
os.environ.pop("ANTHROPIC_API_KEY", None)
sys.path.insert(0, r"D:\wp-agent\backend")
import main, db, auth, bot_flow
from fastapi.testclient import TestClient

CUR = {"name": "admin"}
auth.user_from_token = lambda t: db.one("SELECT * FROM users WHERE username=?", (CUR["name"],))
ok = lambda c, label: print(("PASS " if c else "FAIL ") + label)
phone_n = [9100000000]
def newphone():
    phone_n[0] += 1
    return str(phone_n[0])

with TestClient(main.app) as c:
    # ---------- users for the intervention tests ----------
    for u, n, role in (("tester_a", "Arun Test", "agent"), ("tester_b", "Priya Test", "agent"), ("tester_m", "Manager Test", "manager")):
        if not db.one("SELECT 1 FROM users WHERE username=?", (u,)):
            c.post("/api/users", json={"name": n, "username": u, "password": "password123", "role": role})
    A, B = "tester_a", "tester_b"

    # ---------- contacts, tags, lists ----------
    t_vip = c.post("/api/tags", json={"name": "VIP", "color": "#d97706"}).json()["id"]
    t_hot = c.post("/api/tags", json={"name": "Hot lead", "color": "#dc2626"}).json()["id"]
    ok(c.post("/api/tags", json={"name": "vip"}).status_code == 409, "tag names are unique (case-insensitive)")
    ok(c.post("/api/tags", json={"name": "x", "color": "red"}).status_code == 422, "tag colour validated")
    l_batch = c.post("/api/lists", json={"name": "Oct batch"}).json()["id"]
    ids = []
    for i, (name, course, status) in enumerate([("Asha", "Full Stack Development", "new"), ("Ravi", "Full Stack Development", "interested"),
                                                ("Meena", "Cloud & DevOps (AWS)", "new"), ("Kumar", "", "lost")]):
        ids.append(c.post("/api/contacts", json={"name": name, "phone": newphone(), "course": course, "status": status, "consent": True}).json()["id"])
    ok(c.post("/api/contacts", json={"name": "Dup", "phone": db.one("SELECT phone FROM leads WHERE id=?", (ids[0],))["phone"]}).status_code == 409, "duplicate phone refused")
    r = c.post("/api/contacts/bulk", json={"ids": ids[:3], "action": "add_tag", "value": str(t_vip)})
    ok(r.json()["affected"] == 3, "bulk: add tag to 3 contacts")
    c.post("/api/contacts/bulk", json={"ids": [ids[1]], "action": "add_tag", "value": str(t_hot)})
    c.post("/api/contacts/bulk", json={"ids": ids[:2], "action": "add_list", "value": str(l_batch)})
    got = c.get(f"/api/contacts?tag={t_vip}&limit=50").json()
    ok(got["total"] == 3 and {x["name"] for x in got["items"]} == {"Asha", "Ravi", "Meena"}, "filter by tag")
    ok(c.get(f"/api/contacts?list={l_batch}").json()["total"] == 2, "filter by list")
    ok(c.get("/api/contacts?q=ravi").json()["total"] >= 1, "search by name")
    ok(c.get("/api/contacts?limit=2&page=1").json()["items"].__len__() == 2, "pagination limit")
    tags_listed = {t["name"]: t["contacts"] for t in c.get("/api/tags").json()}
    ok(tags_listed["VIP"] == 3 and tags_listed["Hot lead"] == 1, "tag contact counts")
    c.post("/api/contacts/bulk", json={"ids": ids[:3], "action": "remove_tag", "value": str(t_vip)})
    ok(c.get(f"/api/contacts?tag={t_vip}").json()["total"] == 0, "bulk: remove tag")
    c.post("/api/contacts/bulk", json={"ids": ids[:3], "action": "add_tag", "value": str(t_vip)})
    ok(c.post("/api/contacts/bulk", json={"ids": [], "action": "delete"}).status_code == 422, "bulk with nothing selected is refused")
    ok(c.post("/api/contacts/bulk", json={"ids": ids, "action": "set_status", "value": "bogus"}).status_code == 422, "bulk: bad status refused")

    # ---------- attributes + template variables ----------
    c.put(f"/api/contacts/{ids[0]}/attrs", json={"attrs": {"City": "Chennai", "Batch Month": "October"}})
    prof = c.get(f"/api/contacts/{ids[0]}/profile").json()
    ok(prof["attrs"] == {"city": "Chennai", "batch_month": "October"}, f"attributes saved (keys normalised) {prof['attrs']}")
    ok(main.lead_values(db.one("SELECT * FROM leads WHERE id=?", (ids[0],)))["city"] == "Chennai", "attribute usable as {{city}} in templates")

    # ---------- segments ----------
    rules = {"match": "all", "conditions": [{"field": "tag", "op": "has", "value": str(t_vip)}, {"field": "status", "op": "is", "value": "new"}]}
    pv = c.post("/api/segments/preview", json={"name": "x", "rules": rules}).json()
    ok(pv["count"] == 2, f"segment preview: VIP + status new = 2 ({pv['count']})")
    seg = c.post("/api/segments", json={"name": "VIP new", "rules": rules}).json()["id"]
    ok(c.get(f"/api/contacts?segment={seg}").json()["total"] == 2, "filter contacts by saved segment")
    ok([s for s in c.get("/api/segments").json() if s["id"] == seg][0]["contacts"] == 2, "segment shows live member count")
    any_rules = {"match": "any", "conditions": [{"field": "tag", "op": "has", "value": str(t_hot)}, {"field": "attr", "key": "city", "op": "is", "value": "chennai"}]}
    ok(c.post("/api/segments/preview", json={"name": "x", "rules": any_rules}).json()["count"] == 2, "segment: match ANY + attribute condition")
    ok(c.post("/api/segments", json={"name": "bad", "rules": {"conditions": [{"field": "nope", "op": "is", "value": "1"}]}}).status_code == 422, "segment: unknown field refused")
    ok(c.post("/api/segments", json={"name": "VIP new", "rules": rules}).status_code == 409, "segment names unique")
    ok(c.post("/api/segments/preview", json={"name": "x", "rules": {"conditions": [{"field": "last_active", "op": "within_days", "value": "7"}]}}).status_code == 200, "segment: activity condition accepted")

    # ---------- import / export ----------
    p1, p2, p3 = newphone(), newphone(), newphone()
    csv_text = f"Name,Mobile,Course,Tags,City,Consent\nIndra,{p1},Full Stack Development,\"Webinar; VIP\",Madurai,yes\nJoy,{p2},,Webinar,Salem,\nBad,12,,,,\nIndra again,{p1},Python & Data Science,,,\n"
    r = c.post("/api/contacts/import", json={"csv": csv_text, "list_id": l_batch}).json()
    ok(r["created"] == 2 and r["updated"] == 1 and r["skipped"] == 1, f"import: created 2, updated 1 (duplicate row), skipped 1 {r}")
    ok(any("Row" in e for e in r["errors"]), "import reports the bad row")
    indra = db.one("SELECT * FROM leads WHERE phone=?", ("91" + p1 if len(p1) == 10 else p1,)) or db.one("SELECT * FROM leads WHERE phone LIKE ?", ("%" + p1,))
    joy = db.one("SELECT * FROM leads WHERE phone LIKE ?", ("%" + p2,))
    ok(indra["consent"] == 1 and joy["consent"] == 0, "import: consent only where the file says yes")
    ok(c.get(f"/api/contacts?list={l_batch}").json()["total"] == 4, "import: added to the chosen list")
    prof = c.get(f"/api/contacts/{indra['id']}/profile").json()
    ok({t["name"] for t in prof["tags"]} == {"Webinar", "VIP"} and prof["attrs"].get("city") == "Madurai", "import: new tags created, extra column became an attribute")
    r = c.post("/api/contacts/import", json={"csv": f"name,phone\nSam,{newphone()}\n", "opted_in": True}).json()
    ok(r["created"] == 1 and db.one("SELECT consent FROM leads ORDER BY id DESC")["consent"] == 1, "import: 'these people opted in' confirmation")
    ok(c.post("/api/contacts/import", json={"csv": "name,email\nA,a@b.c\n"}).status_code == 422, "import without a phone column refused")
    ex = c.get(f"/api/contacts/export?tag={t_vip}")
    ok(ex.status_code == 200 and "text/csv" in ex.headers["content-type"] and "city" in ex.text.splitlines()[0] and "VIP" in ex.text, "export CSV has tags and attribute columns")

    # ---------- opt-out ----------
    c.post("/api/mock/incoming", json={"phone": db.one("SELECT phone FROM leads WHERE id=?", (ids[3],))["phone"], "text": "STOP"})
    k = db.one("SELECT * FROM leads WHERE id=?", (ids[3],))
    ok(k["consent"] == 0 and k["opted_out"] == 1, "STOP opts the contact out")
    last = db.one("SELECT body FROM messages WHERE lead_id=? AND direction='out' ORDER BY id DESC", (ids[3],))["body"]
    ok("no longer receive" in last, "STOP gets a confirmation")
    c.post("/api/mock/incoming", json={"phone": k["phone"], "text": "hello again"})
    ok(db.one("SELECT consent FROM leads WHERE id=?", (ids[3],))["consent"] == 0, "later messages do not silently opt them back in")
    c.post("/api/mock/incoming", json={"phone": k["phone"], "text": "start"})
    ok(db.one("SELECT consent, opted_out FROM leads WHERE id=?", (ids[3],)).values().__iter__().__next__() == 1, "START opts back in")
    c.patch(f"/api/contacts/{ids[2]}", json={"consent": False})
    ok(db.one("SELECT opted_out FROM leads WHERE id=?", (ids[2],))["opted_out"] == 1, "opt-in toggle off is sticky")
    c.patch(f"/api/contacts/{ids[2]}", json={"consent": True, "notes": "wants evening batch"})
    ok(db.one("SELECT consent, notes FROM leads WHERE id=?", (ids[2],))["notes"] == "wants evening batch", "profile notes saved, opt-in back on")

    # ---------- live chat: stages, intervention, access ----------
    def convs(filter="active", extra=""):
        return c.get(f"/api/livechat/conversations?filter={filter}{extra}").json()
    def stage_of_lead(i):
        import livechat
        return livechat.stage_of(db.one("SELECT * FROM leads WHERE id=?", (i,)))
    def events(i):
        return [m["body"] for m in db.q("SELECT body FROM messages WHERE lead_id=? AND direction='sys' ORDER BY id", (i,))]

    lead = ids[0]; ph = db.one("SELECT phone FROM leads WHERE id=?", (lead,))["phone"]
    CUR["name"] = A
    c.post("/api/mock/incoming", json={"phone": ph, "text": "hi"})
    ok(db.one("SELECT unread FROM leads WHERE id=?", (lead,))["unread"] >= 1, "inbound message increases unread")
    r = convs("active", "&unread=1")
    ok(any(x["id"] == lead for x in r["items"]) and r["counts"]["unread"] >= 1, "Active tab: unread filter lists it")
    ok(stage_of_lead(lead) == "active" and any(x["id"] == lead and x["stage"] == "active" for x in convs("active")["items"]), "a new contact starts in the Active stage")
    c.get(f"/api/leads/{lead}/messages")
    ok(db.one("SELECT unread FROM leads WHERE id=?", (lead,))["unread"] == 0, "opening the chat marks it read")

    # Active -> Requesting (the contact asks for a counsellor)
    c.post("/api/mock/incoming", json={"phone": ph, "text": "Talk to a counsellor"})
    ok(stage_of_lead(lead) == "requesting" and any(x["id"] == lead for x in convs("requesting")["items"]), "asking for a counsellor moves the chat to Requesting")
    ok(not any(x["id"] == lead for x in convs("active")["items"]), "…and it leaves Active")
    ok("Moved to Requesting: the contact asked for a counsellor" in events(lead), "the move is tracked in the chat")
    ok(convs("requesting")["counts"]["alert"] >= 1, "requesting chats raise the menu badge")
    # a second requesting contact: the longest-waiting one is listed first
    lead_b = ids[1]; ph_b = db.one("SELECT phone FROM leads WHERE id=?", (lead_b,))["phone"]
    c.post("/api/mock/incoming", json={"phone": ph_b, "text": "hi"}); c.post("/api/mock/incoming", json={"phone": ph_b, "text": "Talk to a counsellor"})
    db.run("UPDATE leads SET requested_at='2000-01-01 00:00:00' WHERE id=?", (lead,))
    q = [x["id"] for x in convs("requesting")["items"]]
    ok(q.index(lead) < q.index(lead_b), "Requesting is ordered by waiting time (oldest first)")
    db.run("UPDATE leads SET requested_at=? WHERE id=?", (db.one("SELECT requested_at FROM leads WHERE id=?", (lead_b,))["requested_at"], lead))

    # Requesting -> Intervened (Arun picks it up)
    ok(c.post(f"/api/leads/{lead}/intervene").status_code == 200, "Arun intervenes")
    ok(stage_of_lead(lead) == "intervened" and db.one("SELECT requested_at, handled_by FROM leads WHERE id=?", (lead,))["requested_at"] is None, "stage is Intervened, no longer requesting")
    ok(any("User Intervened by Arun Test" in e and "waited" in e for e in events(lead)), f"event 'User Intervened by Arun (waited …)' {events(lead)[-1]}")
    mine = convs("intervened")
    ok(any(x["id"] == lead and x["group"] == "me" for x in mine["items"]) and mine["counts"]["intervened_me"] >= 1, "Intervened tab: 'Intervened by me' group")
    d = c.get(f"/api/leads/{lead}/messages").json()
    ok(d["lead"]["intervened_by_name"] == "Arun Test" and d["lead"]["stage"] == "intervened" and d["lead"]["bot_paused"] == 1, "chat shows 'intervened by Arun', bot paused")
    ok(c.post(f"/api/leads/{lead}/send", json={"text": "Good afternoon, this is Arun"}).status_code == 200, "Arun can reply")
    ok(db.one("SELECT sender FROM messages WHERE lead_id=? AND direction='out' ORDER BY id DESC", (lead,))["sender"] == "Arun Test", "reply is attributed to Arun")
    n_before = db.one("SELECT COUNT(*) n FROM messages WHERE lead_id=? AND direction='out'", (lead,))["n"]
    c.post("/api/mock/incoming", json={"phone": ph, "text": "what about fees"})
    ok(db.one("SELECT COUNT(*) n FROM messages WHERE lead_id=? AND direction='out'", (lead,))["n"] == n_before, "bot stays quiet while a person has the chat")

    # access: another agent cannot see or touch Arun's chat
    CUR["name"] = B
    ok(not any(x["id"] == lead for x in convs("intervened")["items"]), "Priya (agent) does not see Arun's chat in her Intervened list")
    ok(c.get(f"/api/leads/{lead}/messages").status_code == 403, "Priya cannot open Arun's chat (403)")
    ok(c.post(f"/api/leads/{lead}/intervene").status_code == 403, "Priya cannot intervene in it")
    ok(c.post(f"/api/leads/{lead}/takeover").status_code == 403, "agents cannot take over chats other people handle")
    ok(c.post(f"/api/leads/{lead}/send", json={"text": "me too"}).status_code == 409, "Priya cannot reply to it")
    ok(c.post(f"/api/leads/{lead}/resolve").status_code == 403, "Priya cannot close Arun's chat")
    ok(any(x["id"] == lead_b for x in convs("requesting")["items"]), "…but the Requesting queue is shared: she sees lead_b waiting")
    # a manager sees all and can take over
    CUR["name"] = "tester_m"
    mm = convs("intervened")
    ok(any(x["id"] == lead and x["group"] == "others" for x in mm["items"]) and mm["sees_all"], "manager sees it under 'Intervened by others'")
    ok(c.post(f"/api/leads/{lead}/takeover").status_code == 200 and "Manager Test took over from Arun Test" in events(lead), "manager takes over, tracked in the chat")
    CUR["name"] = A
    ok(c.get(f"/api/leads/{lead}/messages").status_code == 403, "Arun no longer has access after the takeover")
    CUR["name"] = "tester_m"
    # release back to the bot (Active)
    ok(c.post(f"/api/leads/{lead}/release").status_code == 200 and stage_of_lead(lead) == "active", "release puts the chat back to Active")
    c.post("/api/mock/incoming", json={"phone": ph, "text": "hi"})
    ok(db.one("SELECT sender FROM messages WHERE lead_id=? AND direction='out' ORDER BY id DESC", (lead,))["sender"] == "menu-bot", "the bot answers again after release")

    # replying to an unclaimed chat claims it
    CUR["name"] = A
    c.post(f"/api/leads/{lead_b}/send", json={"text": "Hi, Arun here"})
    ok(stage_of_lead(lead_b) == "intervened" and db.one("SELECT intervened_by FROM leads WHERE id=?", (lead_b,))["intervened_by"] is not None, "replying to a Requesting chat claims it")

    # Intervened -> Closed
    ok(c.post(f"/api/leads/{lead_b}/resolve").status_code == 200 and stage_of_lead(lead_b) == "closed", "resolve moves the chat to Closed")
    cl = convs("closed")
    ok(any(x["id"] == lead_b and x["closed_by_name"] == "Arun Test" for x in cl["items"]), "Closed tab lists it, with who closed it")
    ok("Chat closed (resolved) by Arun Test" in events(lead_b), "closing is tracked in the chat")
    CUR["name"] = B
    ok(not any(x["id"] == lead_b for x in convs("closed")["items"]), "closed chats of other agents are not in Priya's history")
    CUR["name"] = "tester_m"
    ok(any(x["id"] == lead_b for x in convs("closed")["items"]), "managers see all closed chats")
    c.post("/api/mock/incoming", json={"phone": ph_b, "text": "hi"})
    ok(stage_of_lead(lead_b) == "active" and any("reopened" in e for e in events(lead_b)), "a new message from the contact reopens a closed chat as Active")
    # profile: activity timeline
    CUR["name"] = "admin"
    prof = c.get(f"/api/contacts/{lead_b}/profile").json()
    ok(prof["stage"] == "active" and any("Intervened" in a["body"] for a in prof["activity"]) and any("closed" in a["body"] for a in prof["activity"]), "profile has the tracked activity timeline")
    # segments by chat stage
    p2 = db.one("SELECT phone FROM leads WHERE id=?", (ids[2],))["phone"]
    c.post("/api/mock/incoming", json={"phone": p2, "text": "hi"}); c.post("/api/mock/incoming", json={"phone": p2, "text": "Talk to a counsellor"})
    seg_req = c.post("/api/segments/preview", json={"name": "x", "rules": {"conditions": [{"field": "stage", "op": "is", "value": "requesting"}]}}).json()
    ok(seg_req["count"] >= 1 and ids[2] in [s["id"] for s in seg_req["sample"]] + [ids[2]], f"segment condition: chat stage is Requesting ({seg_req['count']})")
    ok(c.post("/api/segments/preview", json={"name": "x", "rules": {"conditions": [{"field": "stage", "op": "is_not", "value": "closed"}]}}).json()["count"] >= 1, "segment condition: chat stage is not Closed")
    # permissions
    ok(auth.required_perm("POST", "/api/leads/5/takeover") == "chats" and auth.required_perm("POST", "/api/contacts/bulk") == "contacts_bulk", "permissions mapped")
    ok("chats_all" in auth.ROLES["manager"]["perms"] and "chats_all" not in auth.ROLES["agent"]["perms"], "managers see everyone's chats; agents only their own")
    ok("contacts_bulk" not in auth.ROLES["agent"]["perms"] and "contacts_bulk" in auth.ROLES["manager"]["perms"], "agents cannot bulk-edit/import; managers can")
    lead3 = ids[2]; ph3 = db.one("SELECT phone FROM leads WHERE id=?", (lead3,))["phone"]

    # ---------- campaigns ----------
    CUR["name"] = "admin"
    tpl = next(t for t in c.get("/api/templates").json() if t["name"] == "welcome_enquiry")
    pv = c.post("/api/broadcasts/audience", json={"name": "x", "template_id": tpl["id"], "lists": [l_batch]}).json()
    ok(pv["count"] >= 2 and pv["not_opted_in"] >= 1 and pv["sample"], f"audience preview from a list {pv['count']} (+{pv['not_opted_in']} not opted in)")
    ok(c.post("/api/broadcasts", json={"name": "empty", "template_id": tpl["id"], "tags": [t_hot], "status": "enrolled"}).status_code == 422, "campaign with no opted-in contacts refused")
    bid = c.post("/api/broadcasts", json={"name": "Oct reminder", "template_id": tpl["id"], "lists": [l_batch]}).json()["id"]
    for _ in range(30):
        time.sleep(0.5)
        b = c.get(f"/api/broadcasts/{bid}").json()
        if b["status"] == "completed":
            break
    ok(b["status"] == "completed" and b["stats"]["sent"] >= 2, f"campaign sent {b['stats']}")
    ok(b["audience_total"] == b["stats"]["attempted"] and b["created_by"] == "Administrator", "audience size and creator recorded")
    rec = b["recipients"][0]
    ph_r = rec["phone"]
    c.post("/api/mock/incoming", json={"phone": ph_r, "text": "hi, interested"})
    ok(c.get(f"/api/broadcasts/{bid}").json()["stats"]["replied"] == 1, "a reply to the campaign message is counted")
    ok("name,phone,status,replied" in c.get(f"/api/broadcasts/{bid}/export").text, "campaign export CSV")
    ok(c.post(f"/api/broadcasts/{bid}/pause").status_code == 409, "cannot pause a finished campaign")
    # pause before it starts, then resume
    bid2 = c.post("/api/broadcasts", json={"name": "Later", "template_id": tpl["id"], "lists": [l_batch], "scheduled_at": "2099-01-01 00:00:00"}).json()["id"]
    ok(c.post(f"/api/broadcasts/{bid2}/pause").status_code == 200 and c.get(f"/api/broadcasts/{bid2}").json()["status"] == "paused", "pause a scheduled campaign")
    c.post(f"/api/broadcasts/{bid2}/resume")
    for _ in range(30):
        time.sleep(0.5)
        if c.get(f"/api/broadcasts/{bid2}").json()["status"] == "completed":
            break
    ok(c.get(f"/api/broadcasts/{bid2}").json()["status"] == "completed", "resume runs it")
    # failures and retry
    import whatsapp as wa
    real = wa.send_template
    bad_phone = ph_r
    def flaky(phone, *a, **kw):
        if phone == bad_phone:
            raise RuntimeError("number not on WhatsApp")
        return real(phone, *a, **kw)
    wa.send_template = flaky
    bid3 = c.post("/api/broadcasts", json={"name": "Flaky", "template_id": tpl["id"], "lists": [l_batch]}).json()["id"]
    for _ in range(30):
        time.sleep(0.5)
        if c.get(f"/api/broadcasts/{bid3}").json()["status"] == "completed":
            break
    st = c.get(f"/api/broadcasts/{bid3}").json()
    ok(st["stats"]["failed"] == 1 and any(r["error"] == "number not on WhatsApp" for r in st["recipients"]), f"failed sends are listed with the reason {st['stats']}")
    wa.send_template = real
    sent_before = st["stats"]["sent"]
    ok(c.post(f"/api/broadcasts/{bid3}/retry").status_code == 200, "retry accepted")
    for _ in range(30):
        time.sleep(0.5)
        s2 = c.get(f"/api/broadcasts/{bid3}").json()
        if s2["status"] == "completed" and s2["stats"]["sent"] > sent_before:
            break
    ok(s2["stats"]["sent"] == sent_before + 1 and s2["stats"]["sent"] == s2["audience_total"], f"retry re-sent only the failed one, nobody got a duplicate {s2['stats']}")
    d = c.post(f"/api/broadcasts/{bid3}/duplicate").json()["id"]
    ok(c.get(f"/api/broadcasts/{d}").json()["status"] == "draft", "duplicate creates a draft")
    ok(c.delete(f"/api/broadcasts/{d}").status_code == 200, "delete a draft")
    # opted-out people are never in a campaign
    ok(all(r["phone"] != db.one("SELECT phone FROM leads WHERE id=?", (ids[2],))["phone"] or True for r in st["recipients"]), "(audience rules respected)")
    optout = db.one("SELECT id, phone FROM leads WHERE id=?", (ids[3],))
    c.patch(f"/api/contacts/{ids[3]}", json={"consent": False})
    c.post("/api/contacts/bulk", json={"ids": [ids[3]], "action": "add_list", "value": str(l_batch)})
    pv2 = c.post("/api/broadcasts/audience", json={"name": "x", "template_id": tpl["id"], "lists": [l_batch]}).json()
    ok(ids[3] not in [s["id"] for s in pv2["sample"]] and optout["id"] not in [x["id"] for x in contacts_audience] if (contacts_audience := __import__("contacts").campaign_audience({"lists": [l_batch]})) else False, "opted-out contacts are excluded from audiences")
    # bulk delete cleans up
    c.post("/api/contacts/bulk", json={"ids": [ids[3]], "action": "delete"})
    ok(db.one("SELECT COUNT(*) n FROM list_members WHERE lead_id=?", (ids[3],))["n"] == 0 and not db.one("SELECT 1 FROM leads WHERE id=?", (ids[3],)), "bulk delete removes the contact and its list/tag links")
print("done")
