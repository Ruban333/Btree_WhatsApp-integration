"""Read-only check of the live database after an upgrade: new tables/columns exist and no data was lost."""
import os
import sqlite3

db = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend", "data.db")
c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
tables = [r[0] for r in c.execute("select name from sqlite_master where type='table'")]
cols = [r[1] for r in c.execute("pragma table_info(leads)")]
print("new tables present:", all(t in tables for t in ("tags", "lead_tags", "lists", "list_members", "segments", "lead_attrs")))
print("lead columns added:", all(x in cols for x in ("intervened_by", "unread", "chat_status", "opted_out", "notes")))
print("contacts:", c.execute("select count(*) from leads").fetchone()[0], "| messages:", c.execute("select count(*) from messages").fetchone()[0],
      "| users:", c.execute("select count(*) from users").fetchone()[0], "| templates:", c.execute("select count(*) from templates").fetchone()[0])
b = os.path.join(os.path.dirname(db), "data.db.bak-before-crm")
cb = sqlite3.connect(f"file:{b}?mode=ro", uri=True)
print("backup contacts:", cb.execute("select count(*) from leads").fetchone()[0], "| messages:", cb.execute("select count(*) from messages").fetchone()[0])
