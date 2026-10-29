"""Starts a throwaway copy of the CRM with demo data, for looking at the UI or driving it from a browser test.

  .venv\\Scripts\\python -I tests\\preview.py <username> <port>      e.g.  admin 8011

Mock WhatsApp (nothing is sent), a copy of the database, and you are signed in automatically as <username>.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers

who = sys.argv[1] if len(sys.argv) > 1 else "admin"
port = int(sys.argv[2]) if len(sys.argv) > 2 else 8011
main, db, auth, cur = helpers.setup({"WHATSAPP_NUMBER": "918925967585"}, db_name=f"preview-{port}.db")
from fastapi.testclient import TestClient  # noqa: E402


def seed():
    T0 = time.time()
    step = lambda n: print(f'  seed {n}: {time.time() - T0:.1f}s', flush=True)
    with TestClient(main.app) as c:
        step('app started')
        for u, n, role in (("arun", "Arun", "agent"), ("priya", "Priya", "agent"), ("maya", "Maya", "manager")):
            if not db.one("SELECT 1 FROM users WHERE username=?", (u,)):
                c.post("/api/users", json={"name": n, "username": u, "password": "password123", "role": role})
        step('users')
        tag = {n: c.post("/api/tags", json={"name": n, "color": col}).json()["id"] for n, col in
               (("VIP", "#d97706"), ("Hot lead", "#dc2626"), ("Webinar", "#1a5bec"), ("Weekend batch", "#0d9488"))}
        lst = c.post("/api/lists", json={"name": "October batch"}).json()["id"]
        lst2 = c.post("/api/lists", json={"name": "Webinar attendees"}).json()["id"]
        step('tags and lists')
        people = [("Sudharsan", "Full Stack Development", "interested"), ("Sridhar", "Cloud & DevOps (AWS)", "new"), ("Tami Magi", "Python & Data Science", "contacted"),
                  ("Reshma", "Full Stack Development", "new"), ("Kanna", "Cloud & DevOps (AWS)", "enrolled"), ("Jai Saravanan", "Python & Data Science", "interested"),
                  ("Iksha", "Full Stack Development", "new"), ("Divya", "Cloud & DevOps (AWS)", "lost"), ("Karthik", "Full Stack Development", "interested"),
                  ("Meena", "Python & Data Science", "new")]
        ids = {}
        for i, (name, course, status) in enumerate(people):
            ids[name] = c.post("/api/contacts", json={"name": name, "phone": f"9198400{i:05d}", "course": course, "status": status, "consent": i != 7,
                                                      "email": f"{name.split()[0].lower()}@example.com"}).json()["id"]
        step('contacts')
        db.run("UPDATE leads SET source='ORGANIC' WHERE id IN (?,?)", (ids["Sudharsan"], ids["Reshma"]))
        for n in ("Sudharsan", "Reshma", "Jai Saravanan"):
            c.post("/api/contacts/bulk", json={"ids": [ids[n]], "action": "add_tag", "value": str(tag["VIP"])})
        for n in ("Sudharsan", "Karthik", "Kanna"):
            c.post("/api/contacts/bulk", json={"ids": [ids[n]], "action": "add_tag", "value": str(tag["Hot lead"])})
        c.post("/api/contacts/bulk", json={"ids": list(ids.values())[:6], "action": "add_list", "value": str(lst)})
        c.post("/api/contacts/bulk", json={"ids": list(ids.values())[3:9], "action": "add_list", "value": str(lst2)})
        c.post("/api/contacts/bulk", json={"ids": list(ids.values())[2:7], "action": "add_tag", "value": str(tag["Webinar"])})
        c.put(f"/api/contacts/{ids['Sudharsan']}/attrs", json={"attrs": {"city": "Chennai", "batch_month": "November"}})
        c.post("/api/segments", json={"name": "Hot and new", "rules": {"match": "any", "conditions": [{"field": "tag", "op": "has", "value": str(tag["Hot lead"])}, {"field": "status", "op": "is", "value": "new"}]}})
        c.post("/api/segments", json={"name": "Replied this week, not enrolled", "rules": {"match": "all", "conditions": [
            {"field": "has_replied", "op": "is", "value": "yes"}, {"field": "last_active", "op": "within_days", "value": "7"}, {"field": "status", "op": "is_not", "value": "enrolled"}]}})
        step('segments')
        phone = lambda n: db.one("SELECT phone FROM leads WHERE id=?", (ids[n],))["phone"]
        # conversations
        cur["name"] = "arun"
        for text in ("Yes madam", ):
            c.post("/api/mock/incoming", json={"phone": phone("Sudharsan"), "text": "hi"})
        c.post(f"/api/leads/{ids['Sudharsan']}/intervene")
        c.post(f"/api/leads/{ids['Sudharsan']}/send", json={"text": "Good afternoon Sudharsan, this is Arun, Consultant from BTree Systems."})
        c.post(f"/api/leads/{ids['Sudharsan']}/send", json={"text": "Which course are you looking for?"})
        c.post("/api/mock/incoming", json={"phone": phone("Sudharsan"), "text": "Yes madam"})
        c.post("/api/mock/incoming", json={"phone": phone("Sridhar"), "text": "hi"})
        c.post("/api/mock/incoming", json={"phone": phone("Sridhar"), "text": "Courses, fees & syllabus"})
        c.post("/api/mock/incoming", json={"phone": phone("Tami Magi"), "text": "hello"})
        c.post("/api/mock/incoming", json={"phone": phone("Tami Magi"), "text": "Talk to a counsellor"})
        c.post("/api/mock/incoming", json={"phone": phone("Reshma"), "text": "hi"})
        c.post(f"/api/leads/{ids['Reshma']}/resolve")
        c.post("/api/mock/incoming", json={"phone": phone("Karthik"), "text": "hi"})
        c.post("/api/mock/incoming", json={"phone": phone("Karthik"), "text": "Talk to a counsellor"})
        c.post("/api/mock/incoming", json={"phone": phone("Kanna"), "text": "fees?"})
        c.post("/api/mock/incoming", json={"phone": phone("Iksha"), "text": "STOP"})
        cur["name"] = "priya"
        c.post("/api/mock/incoming", json={"phone": phone("Meena"), "text": "hi"})
        c.post(f"/api/leads/{ids['Meena']}/intervene")
        c.post(f"/api/leads/{ids['Meena']}/send", json={"text": "Hi Meena, Priya from BTree here. How can I help?"})
        step('conversations')
        cur["name"] = "admin"
        # campaigns
        tpl = next(t for t in c.get("/api/templates").json() if t["name"] == "welcome_enquiry")
        b1 = c.post("/api/broadcasts", json={"name": "October batch reminder", "template_id": tpl["id"], "lists": [lst]}).json()["id"]
        for _ in range(40):
            time.sleep(0.4)
            if c.get(f"/api/broadcasts/{b1}").json()["status"] == "completed":
                break
        step('campaign 1 done')
        c.post("/api/mock/incoming", json={"phone": phone("Kanna"), "text": "interested"})
        c.post("/api/broadcasts", json={"name": "Hot leads: demo invite", "template_id": tpl["id"], "tags": [tag["Hot lead"]], "scheduled_at": "2099-01-01 10:00:00"})
        b3 = c.post("/api/broadcasts", json={"name": "Webinar follow-up", "template_id": tpl["id"], "lists": [lst2]}).json()["id"]
        c.post(f"/api/broadcasts/{b3}/duplicate")
        time.sleep(4)
    print("seeded", flush=True)


seed()
cur["name"] = who
# Preview only: the "session cookie" may simply be a username, so a browser test can switch between users.
auth.user_from_token = lambda t: db.one("SELECT * FROM users WHERE username=?", (t or cur["name"],))
import uvicorn  # noqa: E402
uvicorn.run(main.app, port=port, log_level="warning")
