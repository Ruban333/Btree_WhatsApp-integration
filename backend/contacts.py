"""Contacts: filtering, tags, lists, segments, custom attributes, bulk actions and CSV import/export.

A "contact" is a row in `leads`. `enriched()` joins tags, lists, attributes and activity once, so the same
filter code serves the Contacts page, saved Segments and Campaign audiences.
"""
import csv
import io
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

import db
import livechat
import whatsapp as wa

router = APIRouter()

RESERVED_COLS = {"name", "phone", "email", "course", "status", "source", "tags", "consent", "notes"}
STATUSES = ("new", "contacted", "interested", "enrolled", "lost")
CONDITION_FIELDS = {
    "course": ("is", "is_not"), "status": ("is", "is_not"), "source": ("is", "is_not"),
    "tag": ("has", "not_has"), "list": ("in", "not_in"),
    "consent": ("is",), "has_replied": ("is",), "callback": ("is",), "intervened": ("is",),
    "name": ("contains",), "phone": ("contains",), "email": ("contains",),
    "last_active": ("within_days", "older_than_days"), "created": ("within_days", "older_than_days"),
    "attr": ("is", "is_not", "contains", "exists"), "stage": ("is", "is_not"),
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def parse(s: str | None):
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc) if s else None


# ---------- enriched contacts ----------

def enriched(ids: list[int] | None = None) -> list[dict]:
    """Contacts with tag ids, list ids, attributes, reply and activity info attached."""
    where, args = "", ()
    if ids is not None:
        if not ids:
            return []
        where, args = f" WHERE id IN ({','.join('?' * len(ids))})", tuple(ids)
    leads = db.q("SELECT * FROM leads" + where, args)
    tags, lists, attrs = {}, {}, {}
    for r in db.q("SELECT lead_id, tag_id FROM lead_tags"):
        tags.setdefault(r["lead_id"], []).append(r["tag_id"])
    for r in db.q("SELECT lead_id, list_id FROM list_members"):
        lists.setdefault(r["lead_id"], []).append(r["list_id"])
    for r in db.q("SELECT lead_id, key, value FROM lead_attrs"):
        attrs.setdefault(r["lead_id"], {})[r["key"]] = r["value"]
    replied = {r["lead_id"] for r in db.q("SELECT DISTINCT lead_id FROM messages WHERE direction='in'")}
    users = {u["id"]: u["name"] for u in db.q("SELECT id, name FROM users")}
    for l in leads:
        l["tag_ids"] = tags.get(l["id"], [])
        l["list_ids"] = lists.get(l["id"], [])
        l["attrs"] = attrs.get(l["id"], {})
        l["has_replied"] = l["id"] in replied
        l["intervened_by_name"] = users.get(l.get("intervened_by")) if l.get("intervened_by") else None
    return leads


def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def condition_matches(c: dict, l: dict) -> bool:
    f, op, v = c.get("field"), c.get("op"), c.get("value")
    t = now()
    if f in ("course", "status", "source"):
        eq = str(l.get(f) or "").lower() == str(v or "").lower()
        return eq if op == "is" else not eq
    if f == "tag":
        has = int(v or 0) in l["tag_ids"]
        return has if op == "has" else not has
    if f == "list":
        inn = int(v or 0) in l["list_ids"]
        return inn if op == "in" else not inn
    if f == "consent":
        return bool(l.get("consent")) == _truthy(v)
    if f == "has_replied":
        return l["has_replied"] == _truthy(v)
    if f == "callback":
        return bool(l.get("needs_callback")) == _truthy(v)
    if f == "intervened":
        return bool(l.get("intervened_by")) == _truthy(v)
    if f in ("name", "phone", "email"):
        return str(v or "").lower() in str(l.get(f) or "").lower()
    if f in ("last_active", "created"):
        when = parse(l.get("last_inbound_at" if f == "last_active" else "created_at"))
        days = float(v or 0)
        recent = bool(when) and (t - when) <= timedelta(days=days)
        return recent if op == "within_days" else not recent
    if f == "stage":
        same = livechat.stage_of(l) == str(v)
        return same if op == "is" else not same
    if f == "attr":
        key = str(c.get("key") or "").strip().lower()
        val = l["attrs"].get(key)
        if op == "exists":
            return val not in (None, "")
        if val is None:
            return op == "is_not"
        if op == "contains":
            return str(v or "").lower() in val.lower()
        eq = val.lower() == str(v or "").lower()
        return eq if op == "is" else not eq
    return False


def rules_match(rules: dict, l: dict) -> bool:
    conds = [c for c in (rules or {}).get("conditions", []) if c.get("field")]
    if not conds:
        return True
    results = (condition_matches(c, l) for c in conds)
    return all(results) if rules.get("match", "all") == "all" else any(results)


def clean_rules(rules: dict) -> dict:
    """Validates a segment's rules; raises ValueError with a message the editor can show."""
    out = []
    for c in (rules or {}).get("conditions", []):
        f, op = c.get("field"), c.get("op")
        if f not in CONDITION_FIELDS:
            raise ValueError(f"Unknown field '{f}'")
        if op not in CONDITION_FIELDS[f]:
            raise ValueError(f"'{op}' does not work with {f}")
        if f == "attr" and not str(c.get("key") or "").strip():
            raise ValueError("Attribute conditions need an attribute name")
        if f in ("tag", "list") and not str(c.get("value") or "").isdigit():
            raise ValueError(f"Pick a {f}")
        if f in ("last_active", "created"):
            try:
                float(c.get("value"))
            except (TypeError, ValueError):
                raise ValueError("Enter the number of days")
        out.append({"field": f, "op": op, "value": c.get("value", ""),
                    **({"key": str(c["key"]).strip().lower()} if f == "attr" else {})})
    return {"match": "any" if rules.get("match") == "any" else "all", "conditions": out}


def segment_rules(seg_id: int) -> dict:
    row = db.one("SELECT rules FROM segments WHERE id=?", (seg_id,))
    return json.loads(row["rules"]) if row else {"match": "all", "conditions": []}


def filter_contacts(p: dict) -> list[dict]:
    """Shared filter. p keys (all optional): q, status, course, tag, list, segment, consent, callback,
    chat_status, source, ids."""
    rows = enriched(p.get("ids"))
    q = (p.get("q") or "").strip().lower()
    seg = segment_rules(int(p["segment"])) if p.get("segment") else None
    out = []
    for l in rows:
        if q and not any(q in str(l.get(k) or "").lower() for k in ("name", "phone", "email", "course")):
            continue
        if p.get("status") and l["status"] != p["status"]:
            continue
        if p.get("course") and l["course"] != p["course"]:
            continue
        if p.get("source") and l["source"] != p["source"]:
            continue
        if p.get("tag") and int(p["tag"]) not in l["tag_ids"]:
            continue
        if p.get("list") and int(p["list"]) not in l["list_ids"]:
            continue
        if p.get("consent") in ("yes", "no") and bool(l["consent"]) != (p["consent"] == "yes"):
            continue
        if p.get("callback") == "yes" and not l.get("needs_callback"):
            continue
        if p.get("chat_status") and (l.get("chat_status") or "open") != p["chat_status"]:
            continue
        if seg is not None and not rules_match(seg, l):
            continue
        out.append(l)
    return out


def campaign_audience(aud: dict) -> list[dict]:
    """Opted-in contacts that match a campaign's audience: any chosen list, any chosen tag, a segment, course/status."""
    lists = {int(x) for x in aud.get("lists") or []}
    tags = {int(x) for x in aud.get("tags") or []}
    seg = segment_rules(int(aud["segment"])) if aud.get("segment") else None
    out = []
    for l in enriched():
        if not l["consent"] or l.get("opted_out"):
            continue
        if aud.get("course") and l["course"] != aud["course"]:
            continue
        if aud.get("status") and l["status"] != aud["status"]:
            continue
        if lists and not lists & set(l["list_ids"]):
            continue
        if tags and not tags & set(l["tag_ids"]):
            continue
        if seg is not None and not rules_match(seg, l):
            continue
        out.append(l)
    return out


def public(l: dict, tag_map: dict, list_map: dict) -> dict:
    return {"id": l["id"], "name": l["name"], "phone": l["phone"], "email": l["email"], "course": l["course"],
            "source": l["source"], "status": l["status"], "consent": bool(l["consent"]), "created_at": l["created_at"],
            "last_active": l.get("last_inbound_at"), "needs_callback": bool(l.get("needs_callback")),
            "unread": l.get("unread") or 0, "intervened_by_name": l.get("intervened_by_name"),
            "bot_paused": bool(l.get("bot_paused")), "chat_status": l.get("chat_status") or "open",
            "tags": [tag_map[t] for t in l["tag_ids"] if t in tag_map],
            "lists": [list_map[x] for x in l["list_ids"] if x in list_map], "attrs": l["attrs"]}


def _maps():
    return ({t["id"]: t for t in db.q("SELECT * FROM tags")}, {x["id"]: x for x in db.q("SELECT * FROM lists")})


# ---------- contacts ----------

@router.get("/api/contacts")
def list_contacts(request: Request, q: str = "", status: str = "", course: str = "", tag: str = "", list: str = "",
                  segment: str = "", consent: str = "", source: str = "", page: int = 1, limit: int = 50, sort: str = "recent"):
    rows = filter_contacts({"q": q, "status": status, "course": course, "tag": tag, "list": list, "segment": segment,
                            "consent": consent, "source": source})
    key = {"name": lambda l: (l["name"] or "").lower(), "recent": lambda l: -l["id"],
           "active": lambda l: l.get("last_inbound_at") or ""}.get(sort, lambda l: -l["id"])
    rows.sort(key=key, reverse=(sort == "active"))
    limit = max(1, min(200, limit))
    page = max(1, page)
    tag_map, list_map = _maps()
    return {"total": len(rows), "page": page, "limit": limit,
            "items": [public(l, tag_map, list_map) for l in rows[(page - 1) * limit: page * limit]]}


class ContactIn(BaseModel):
    name: str
    phone: str
    email: str = ""
    course: str = ""
    source: str = "manual"
    status: str = "new"
    consent: bool = False
    notes: str = ""
    tag_ids: list[int] = []
    list_ids: list[int] = []
    attrs: dict[str, str] = {}


def _set_attrs(lid: int, attrs: dict):
    db.run("DELETE FROM lead_attrs WHERE lead_id=?", (lid,))
    for k, v in attrs.items():
        k = re.sub(r"[^a-z0-9_]", "_", str(k).strip().lower())[:40]
        if k and str(v).strip():
            db.run("INSERT OR REPLACE INTO lead_attrs(lead_id,key,value) VALUES(?,?,?)", (lid, k, str(v).strip()[:500]))


@router.post("/api/contacts")
def create_contact(c: ContactIn):
    phone = wa.normalize_phone(c.phone)
    if len(phone) < 10:
        raise HTTPException(422, "Invalid phone number")
    if c.status not in STATUSES:
        raise HTTPException(422, "Unknown status")
    try:
        lid = db.run("INSERT INTO leads(name,phone,email,course,source,status,consent,notes) VALUES(?,?,?,?,?,?,?,?)",
                     (c.name.strip() or phone, phone, c.email, c.course, c.source, c.status, int(c.consent), c.notes))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "A contact with this phone already exists")
    for t in c.tag_ids:
        db.run("INSERT OR IGNORE INTO lead_tags(lead_id,tag_id) VALUES(?,?)", (lid, t))
    for x in c.list_ids:
        db.run("INSERT OR IGNORE INTO list_members(list_id,lead_id) VALUES(?,?)", (x, lid))
    _set_attrs(lid, c.attrs)
    return {"id": lid}


class ContactPatch(BaseModel):
    name: str | None = None
    email: str | None = None
    course: str | None = None
    status: str | None = None
    consent: bool | None = None
    notes: str | None = None


@router.patch("/api/contacts/{lid}")
def patch_contact(lid: int, p: ContactPatch, request: Request):
    lead = db.one("SELECT * FROM leads WHERE id=?", (lid,))
    if not lead:
        raise HTTPException(404)
    if p.status is not None and p.status not in STATUSES:
        raise HTTPException(422, "Unknown status")
    sets, args = [], []
    for col in ("name", "email", "course", "status", "notes"):
        v = getattr(p, col)
        if v is not None:
            sets.append(f"{col}=?"); args.append(v.strip() if col == "name" else v)
    if p.consent is not None:
        sets += ["consent=?", "opted_out=?"]
        args += [int(p.consent), 0 if p.consent else 1]  # switching opt-in off blocks sends until it is switched on again
        import livechat
        livechat.log_event(lid, f"Opt-in {'turned on' if p.consent else 'turned off'} by {request.state.user['name']}")
    if sets:
        db.run(f"UPDATE leads SET {', '.join(sets)} WHERE id=?", (*args, lid))
    return {"ok": True}


class AttrsIn(BaseModel):
    attrs: dict[str, str]


@router.put("/api/contacts/{lid}/attrs")
def put_attrs(lid: int, body: AttrsIn):
    if not db.one("SELECT 1 FROM leads WHERE id=?", (lid,)):
        raise HTTPException(404)
    _set_attrs(lid, body.attrs)
    return {"ok": True}


class IdsIn(BaseModel):
    ids: list[int]


@router.post("/api/contacts/{lid}/tags")
def set_contact_tags(lid: int, body: IdsIn):
    if not db.one("SELECT 1 FROM leads WHERE id=?", (lid,)):
        raise HTTPException(404)
    db.run("DELETE FROM lead_tags WHERE lead_id=?", (lid,))
    for t in body.ids:
        db.run("INSERT OR IGNORE INTO lead_tags(lead_id,tag_id) SELECT ?, id FROM tags WHERE id=?", (lid, t))
    return {"ok": True}


@router.post("/api/contacts/{lid}/lists")
def set_contact_lists(lid: int, body: IdsIn):
    if not db.one("SELECT 1 FROM leads WHERE id=?", (lid,)):
        raise HTTPException(404)
    db.run("DELETE FROM list_members WHERE lead_id=?", (lid,))
    for x in body.ids:
        db.run("INSERT OR IGNORE INTO list_members(list_id,lead_id) SELECT id, ? FROM lists WHERE id=?", (lid, x))
    return {"ok": True}


@router.get("/api/contacts/{lid}/profile")
def contact_profile(lid: int):
    """Everything the chat's profile panel shows."""
    rows = enriched([lid])
    if not rows:
        raise HTTPException(404)
    l = rows[0]
    tag_map, list_map = _maps()
    count = lambda sql, *a: db.one(sql, a)["n"]
    first = db.one("SELECT body FROM messages WHERE lead_id=? AND direction='in' ORDER BY id LIMIT 1", (lid,))
    last_in = parse(l.get("last_inbound_at"))
    users = {u["id"]: u["name"] for u in db.q("SELECT id, name FROM users")}
    campaigns = db.q("""SELECT b.id, b.name, m.status, m.created_at FROM messages m JOIN broadcasts b ON b.id=m.broadcast_id
                        WHERE m.lead_id=? ORDER BY m.id DESC LIMIT 20""", (lid,))
    if l["opted_out"]:
        opt = "opted_out"
    else:
        opt = "opted_in" if l["consent"] else "no_consent"
    return {**public(l, tag_map, list_map),
            "notes": l.get("notes") or "", "opted_out": bool(l["opted_out"]), "opt_state": opt,
            "intervened_by": l.get("intervened_by"), "intervened_at": l.get("intervened_at"),
            "template_messages": count("SELECT COUNT(*) n FROM messages WHERE lead_id=? AND direction='out' AND kind='template' AND status!='failed'", lid),
            "session_messages": count("SELECT COUNT(*) n FROM messages WHERE lead_id=? AND direction IN ('in','out') AND kind IN ('text','button')", lid),
            "unresolved": l.get("unresolved") or 0,
            "first_message": (first["body"] if first else ""),
            "conversation_active": bool(last_in and now() - last_in < timedelta(hours=24)),
            "campaigns": campaigns,
            "stage": livechat.stage_of(l), "requested_at": l.get("requested_at"), "closed_at": l.get("closed_at"),
            "closed_by_name": users.get(l.get("closed_by")) if l.get("closed_by") else None,
            "activity": db.q("SELECT body, sender, created_at FROM messages WHERE lead_id=? AND direction='sys' ORDER BY id DESC LIMIT 40", (lid,)),
            "tag_ids": l["tag_ids"], "list_ids": l["list_ids"]}


# ---------- tags ----------

class TagIn(BaseModel):
    name: str
    color: str = "#1a5bec"


@router.get("/api/tags")
def list_tags():
    return db.q("""SELECT t.*, (SELECT COUNT(*) FROM lead_tags x WHERE x.tag_id=t.id) AS contacts FROM tags t ORDER BY t.name""")


@router.post("/api/tags")
def create_tag(t: TagIn):
    name = t.name.strip()
    if not name or len(name) > 40:
        raise HTTPException(422, "Tag name must be 1-40 characters")
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", t.color):
        raise HTTPException(422, "Colour must look like #1a5bec")
    try:
        return {"id": db.run("INSERT INTO tags(name,color) VALUES(?,?)", (name, t.color))}
    except sqlite3.IntegrityError:
        raise HTTPException(409, "That tag already exists")


@router.put("/api/tags/{tid}")
def edit_tag(tid: int, t: TagIn):
    try:
        db.run("UPDATE tags SET name=?, color=? WHERE id=?", (t.name.strip(), t.color, tid))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "That tag already exists")
    return {"ok": True}


@router.delete("/api/tags/{tid}")
def delete_tag(tid: int):
    db.run("DELETE FROM lead_tags WHERE tag_id=?", (tid,))
    db.run("DELETE FROM tags WHERE id=?", (tid,))
    return {"ok": True}


# ---------- lists ----------

class ListIn(BaseModel):
    name: str


@router.get("/api/lists")
def get_lists():
    return db.q("""SELECT l.*, (SELECT COUNT(*) FROM list_members m WHERE m.list_id=l.id) AS contacts FROM lists l ORDER BY l.name""")


@router.post("/api/lists")
def create_list(body: ListIn):
    name = body.name.strip()
    if not name or len(name) > 60:
        raise HTTPException(422, "List name must be 1-60 characters")
    try:
        return {"id": db.run("INSERT INTO lists(name) VALUES(?)", (name,))}
    except sqlite3.IntegrityError:
        raise HTTPException(409, "That list already exists")


@router.put("/api/lists/{xid}")
def rename_list(xid: int, body: ListIn):
    try:
        db.run("UPDATE lists SET name=? WHERE id=?", (body.name.strip(), xid))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "That list already exists")
    return {"ok": True}


@router.delete("/api/lists/{xid}")
def delete_list(xid: int):
    db.run("DELETE FROM list_members WHERE list_id=?", (xid,))
    db.run("DELETE FROM lists WHERE id=?", (xid,))
    return {"ok": True}


# ---------- segments ----------

class SegmentIn(BaseModel):
    name: str
    rules: dict


def _segment_out(r: dict) -> dict:
    rules = json.loads(r["rules"])
    return {"id": r["id"], "name": r["name"], "rules": rules, "contacts": len([l for l in enriched() if rules_match(rules, l)]),
            "created_at": r["created_at"]}


@router.get("/api/segments")
def get_segments():
    people = enriched()
    out = []
    for r in db.q("SELECT * FROM segments ORDER BY name"):
        rules = json.loads(r["rules"])
        out.append({"id": r["id"], "name": r["name"], "rules": rules, "created_at": r["created_at"],
                    "contacts": sum(1 for l in people if rules_match(rules, l))})
    return out


@router.post("/api/segments/preview")
def preview_segment(body: SegmentIn):
    try:
        rules = clean_rules(body.rules)
    except ValueError as e:
        raise HTTPException(422, str(e))
    hits = [l for l in enriched() if rules_match(rules, l)]
    return {"count": len(hits), "sample": [{"id": l["id"], "name": l["name"], "phone": l["phone"]} for l in hits[:6]]}


@router.post("/api/segments")
def create_segment(body: SegmentIn):
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Give the segment a name")
    try:
        rules = clean_rules(body.rules)
    except ValueError as e:
        raise HTTPException(422, str(e))
    try:
        return {"id": db.run("INSERT INTO segments(name,rules) VALUES(?,?)", (name, json.dumps(rules)))}
    except sqlite3.IntegrityError:
        raise HTTPException(409, "A segment with that name already exists")


@router.put("/api/segments/{sid}")
def edit_segment(sid: int, body: SegmentIn):
    try:
        rules = clean_rules(body.rules)
    except ValueError as e:
        raise HTTPException(422, str(e))
    try:
        db.run("UPDATE segments SET name=?, rules=? WHERE id=?", (body.name.strip(), json.dumps(rules), sid))
    except sqlite3.IntegrityError:
        raise HTTPException(409, "A segment with that name already exists")
    return {"ok": True}


@router.delete("/api/segments/{sid}")
def delete_segment(sid: int):
    db.run("DELETE FROM segments WHERE id=?", (sid,))
    return {"ok": True}


# ---------- bulk actions ----------

class BulkIn(BaseModel):
    ids: list[int] = []
    filter: dict | None = None   # alternative to ids: every contact matching this filter
    action: str
    value: str = ""


@router.post("/api/contacts/bulk")
def bulk(body: BulkIn):
    ids = body.ids if body.filter is None else [l["id"] for l in filter_contacts(body.filter)]
    if not ids:
        raise HTTPException(422, "No contacts selected")
    a, v = body.action, body.value
    if a in ("add_tag", "remove_tag", "add_list", "remove_list") and not str(v).isdigit():
        raise HTTPException(422, "Pick a tag or list")
    marks = ",".join("?" * len(ids))
    if a == "add_tag":
        for i in ids:
            db.run("INSERT OR IGNORE INTO lead_tags(lead_id,tag_id) SELECT ?, id FROM tags WHERE id=?", (i, int(v)))
    elif a == "remove_tag":
        db.run(f"DELETE FROM lead_tags WHERE tag_id=? AND lead_id IN ({marks})", (int(v), *ids))
    elif a == "add_list":
        for i in ids:
            db.run("INSERT OR IGNORE INTO list_members(list_id,lead_id) SELECT id, ? FROM lists WHERE id=?", (i, int(v)))
    elif a == "remove_list":
        db.run(f"DELETE FROM list_members WHERE list_id=? AND lead_id IN ({marks})", (int(v), *ids))
    elif a == "set_status":
        if v not in STATUSES:
            raise HTTPException(422, "Unknown status")
        db.run(f"UPDATE leads SET status=? WHERE id IN ({marks})", (v, *ids))
    elif a == "set_consent":
        on = _truthy(v)
        db.run(f"UPDATE leads SET consent=?, opted_out=? WHERE id IN ({marks})", (int(on), 0 if on else 1, *ids))
    elif a == "delete":
        db.run(f"DELETE FROM messages WHERE lead_id IN ({marks})", tuple(ids))
        for tbl in ("lead_tags", "list_members", "lead_attrs"):
            db.run(f"DELETE FROM {tbl} WHERE lead_id IN ({marks})", tuple(ids))
        db.run(f"DELETE FROM leads WHERE id IN ({marks})", tuple(ids))
    else:
        raise HTTPException(422, "Unknown action")
    return {"ok": True, "affected": len(ids)}


# ---------- import / export ----------

class ImportIn(BaseModel):
    csv: str
    list_id: int | None = None
    tag_id: int | None = None
    opted_in: bool = False   # the uploader confirms these people agreed to WhatsApp messages


def _split(v: str) -> list[str]:
    return [x.strip() for x in re.split(r"[;,|]", v or "") if x.strip()]


@router.post("/api/contacts/import")
def import_contacts(body: ImportIn):
    if len(body.csv) > 2_000_000:
        raise HTTPException(413, "File too large (2 MB max)")
    reader = csv.DictReader(io.StringIO(body.csv.lstrip("﻿")))
    if not reader.fieldnames:
        raise HTTPException(422, "The file is empty")
    header = {re.sub(r"[^a-z0-9_]", "_", (h or "").strip().lower()): h for h in reader.fieldnames}
    header.setdefault("phone", header.get("mobile") or header.get("mobile_number") or header.get("whatsapp") or header.get("phone_number"))
    if not header.get("phone"):
        raise HTTPException(422, "The file needs a phone column (phone, mobile or whatsapp)")
    get = lambda row, k: (row.get(header[k]) or "").strip() if header.get(k) else ""
    tags_by_name = {t["name"].lower(): t["id"] for t in db.q("SELECT id, name FROM tags")}
    created = updated = skipped = 0
    errors = []
    for n, row in enumerate(reader, start=2):
        if n > 5002:
            errors.append("Stopped after 5000 rows")
            break
        phone = wa.normalize_phone(get(row, "phone"))
        if len(phone) < 10:
            skipped += 1
            errors.append(f"Row {n}: invalid phone '{get(row, 'phone')}'")
            continue
        status = get(row, "status").lower() or "new"
        if status not in STATUSES:
            status = "new"
        consent = body.opted_in or _truthy(get(row, "consent"))
        existing = db.one("SELECT id FROM leads WHERE phone=?", (phone,))
        if existing:
            lid = existing["id"]
            for col in ("name", "email", "course"):
                if get(row, col):
                    db.run(f"UPDATE leads SET {col}=? WHERE id=?", (get(row, col), lid))
            if consent:
                db.run("UPDATE leads SET consent=1 WHERE id=? AND opted_out=0", (lid,))
            updated += 1
        else:
            lid = db.run("INSERT INTO leads(name,phone,email,course,source,status,consent) VALUES(?,?,?,?,?,?,?)",
                         (get(row, "name") or phone, phone, get(row, "email"), get(row, "course"), get(row, "source") or "import", status, int(consent)))
            created += 1
        for tname in _split(get(row, "tags")):
            tid = tags_by_name.get(tname.lower())
            if tid is None:
                tid = db.run("INSERT INTO tags(name,color) VALUES(?,?)", (tname[:40], "#1a5bec"))
                tags_by_name[tname.lower()] = tid
            db.run("INSERT OR IGNORE INTO lead_tags(lead_id,tag_id) VALUES(?,?)", (lid, tid))
        if body.tag_id:
            db.run("INSERT OR IGNORE INTO lead_tags(lead_id,tag_id) SELECT ?, id FROM tags WHERE id=?", (lid, body.tag_id))
        if body.list_id:
            db.run("INSERT OR IGNORE INTO list_members(list_id,lead_id) SELECT id, ? FROM lists WHERE id=?", (lid, body.list_id))
        for key, orig in header.items():   # any other column becomes a custom attribute
            if key not in RESERVED_COLS and key not in ("phone", "mobile", "mobile_number", "whatsapp", "phone_number") and (row.get(orig) or "").strip():
                db.run("INSERT OR REPLACE INTO lead_attrs(lead_id,key,value) VALUES(?,?,?)", (lid, key[:40], row[orig].strip()[:500]))
    return {"created": created, "updated": updated, "skipped": skipped, "errors": errors[:20]}


@router.get("/api/contacts/export")
def export_contacts(q: str = "", status: str = "", course: str = "", tag: str = "", list: str = "", segment: str = "",
                    consent: str = "", source: str = ""):
    rows = filter_contacts({"q": q, "status": status, "course": course, "tag": tag, "list": list, "segment": segment,
                            "consent": consent, "source": source})
    tag_map, list_map = _maps()
    keys = sorted({k for l in rows for k in l["attrs"]})
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "phone", "email", "course", "status", "source", "consent", "tags", "lists", "created", *keys])
    for l in rows:
        w.writerow([l["name"], l["phone"], l["email"], l["course"], l["status"], l["source"], "yes" if l["consent"] else "no",
                    "; ".join(tag_map[t]["name"] for t in l["tag_ids"] if t in tag_map),
                    "; ".join(list_map[x]["name"] for x in l["list_ids"] if x in list_map), l["created_at"],
                    *[l["attrs"].get(k, "") for k in keys]])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="contacts.csv"'})
