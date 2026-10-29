"""Campaigns (bulk template sends): audience from lists, tags, segments or filters; stats; pause, resume, retry."""
import csv
import io
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

import contacts
import db
import phones

router = APIRouter()
REPLY_WINDOW_DAYS = 3   # a reply within this long after the campaign message counts as a response to it


def ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class UploadRow(BaseModel):
    name: str = ""
    phone: str


class CampaignIn(BaseModel):
    name: str
    template_id: int
    lists: list[int] = []
    tags: list[int] = []
    segment: int | None = None
    course: str = ""
    status: str = ""
    scheduled_at: str | None = None   # "YYYY-MM-DD HH:MM:SS" UTC; None = send now
    upload: list[UploadRow] = []      # contacts read from an uploaded file: they become the audience
    opted_in: bool = False            # the sender confirms the uploaded people agreed to WhatsApp messages


class ParseIn(BaseModel):
    filename: str = ""
    data: str   # the file, base64


def _aud(c: CampaignIn) -> dict:
    return {"lists": c.lists, "tags": c.tags, "segment": c.segment, "course": c.course, "status": c.status}


def stats(bid: int) -> dict:
    by = {r["status"]: r["n"] for r in db.q("SELECT status, COUNT(*) n FROM messages WHERE broadcast_id=? GROUP BY status", (bid,))}
    replied = db.one(f"""SELECT COUNT(DISTINCT m.lead_id) n FROM messages m WHERE m.broadcast_id=? AND m.status!='failed' AND EXISTS (
        SELECT 1 FROM messages r WHERE r.lead_id=m.lead_id AND r.direction='in' AND r.id>m.id
        AND r.created_at <= datetime(m.created_at, '+{REPLY_WINDOW_DAYS} day'))""", (bid,))["n"]
    sent = sum(v for k, v in by.items() if k != "failed")
    return {"attempted": sum(by.values()), "sent": sent, "delivered": by.get("delivered", 0) + by.get("read", 0),
            "read": by.get("read", 0), "failed": by.get("failed", 0), "replied": replied}


def _row(b: dict) -> dict:
    aud = json.loads(b["audience"] or "{}")
    return {**b, "audience": aud, "stats": stats(b["id"])}


@router.get("/api/broadcasts")
def list_campaigns():
    rows = db.q("""SELECT b.*, t.name AS template_name FROM broadcasts b JOIN templates t ON t.id=b.template_id ORDER BY b.id DESC""")
    return [_row(b) for b in rows]


@router.post("/api/broadcasts/audience")
def audience_preview(c: CampaignIn):
    people = contacts.campaign_audience(_aud(c))
    # how many matching contacts are left out because they have not opted in
    everyone = contacts.filter_contacts({"course": c.course, "status": c.status})
    return {"count": len(people), "sample": [{"id": l["id"], "name": l["name"]} for l in people[:5]],
            "not_opted_in": sum(1 for l in everyone if not l["consent"] or l.get("opted_out"))}


@router.post("/api/broadcasts/parse-contacts")
def parse_contacts(body: ParseIn):
    """Reads an uploaded CSV / TXT / DOCX / PDF and reports which numbers are valid; nothing is saved."""
    try:
        return phones.read_contacts(body.filename, body.data)
    except ValueError as e:
        raise HTTPException(422, str(e))


def _save_upload(c: CampaignIn) -> int:
    """Saves the uploaded contacts (new ones are created, known ones reused) into a fresh list and returns its id."""
    if not c.opted_in:
        raise HTTPException(422, "Confirm that these contacts agreed to receive WhatsApp messages")
    good, bad = {}, 0
    for r in c.upload[:phones.MAX_ROWS]:
        phone, err = phones.check_phone(r.phone)
        if err:
            bad += 1
        else:
            good.setdefault(phone, r.name.strip())
    if not good:
        raise HTTPException(422, f"None of the {bad} uploaded numbers is valid (every number needs a country code)")
    lid = db.run("INSERT INTO lists(name) VALUES(?)", (f"Upload: {c.name.strip()[:40]} · {ts()}",))
    for phone, name in good.items():
        row = db.one("SELECT id FROM leads WHERE phone=?", (phone,))
        if row:
            lead = row["id"]
            db.run("UPDATE leads SET consent=1 WHERE id=? AND opted_out=0", (lead,))
        else:
            lead = db.run("INSERT INTO leads(name,phone,source,status,consent) VALUES(?,?,?,?,1)", (name or phone, phone, "campaign upload", "new"))
        db.run("INSERT OR IGNORE INTO list_members(list_id,lead_id) VALUES(?,?)", (lid, lead))
    return lid


@router.post("/api/broadcasts")
def create_campaign(c: CampaignIn, request: Request):
    tpl = db.one("SELECT * FROM templates WHERE id=?", (c.template_id,))
    if not tpl:
        raise HTTPException(404, "Template not found")
    if tpl["status"] != "APPROVED":
        raise HTTPException(422, f"Template is {tpl['status']}; only APPROVED templates can be sent")
    if not c.name.strip():
        raise HTTPException(422, "Give the campaign a name")
    if c.upload:
        aud = {"lists": [_save_upload(c)], "tags": [], "segment": None, "course": "", "status": ""}
    else:
        aud = _aud(c)
    if not contacts.campaign_audience(aud):
        raise HTTPException(422, "This audience has no opted-in contacts")
    when = c.scheduled_at or ts()
    bid = db.run("INSERT INTO broadcasts(name,template_id,audience,scheduled_at,created_by) VALUES(?,?,?,?,?)",
                 (c.name.strip(), c.template_id, json.dumps(aud), when, request.state.user["name"]))
    return {"id": bid, "audience": len(contacts.campaign_audience(aud))}


def _get(bid: int) -> dict:
    b = db.one("SELECT b.*, t.name AS template_name FROM broadcasts b JOIN templates t ON t.id=b.template_id WHERE b.id=?", (bid,))
    if not b:
        raise HTTPException(404, "Campaign not found")
    return b


@router.get("/api/broadcasts/{bid}")
def campaign_detail(bid: int):
    b = _get(bid)
    rows = db.q("""
        SELECT l.id AS lead_id, l.name, l.phone, m.status, m.error, m.created_at,
               EXISTS (SELECT 1 FROM messages r WHERE r.lead_id=l.id AND r.direction='in' AND r.id>m.id
                       AND r.created_at <= datetime(m.created_at, '+3 day')) AS replied
        FROM messages m JOIN leads l ON l.id=m.lead_id
        WHERE m.broadcast_id=? AND m.id=(SELECT MAX(id) FROM messages x WHERE x.broadcast_id=m.broadcast_id AND x.lead_id=m.lead_id)
        ORDER BY m.id DESC LIMIT 500""", (bid,))
    return {**_row(b), "recipients": rows}


@router.get("/api/broadcasts/{bid}/export")
def export_campaign(bid: int):
    b = _get(bid)
    d = campaign_detail(bid)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["name", "phone", "status", "replied", "error", "time (UTC)"])
    for r in d["recipients"]:
        w.writerow([r["name"], r["phone"], r["status"], "yes" if r["replied"] else "no", r["error"], r["created_at"]])
    return Response(buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="campaign-{bid}.csv"'})


def _set(bid: int, allowed: tuple, to: str, extra: str = "", args: tuple = ()):
    b = _get(bid)
    if b["status"] not in allowed:
        raise HTTPException(409, f"A {b['status']} campaign cannot do that")
    db.run(f"UPDATE broadcasts SET status=?{extra} WHERE id=?", (to, *args, bid))
    return {"ok": True}


@router.post("/api/broadcasts/{bid}/pause")
def pause(bid: int):
    return _set(bid, ("running", "scheduled"), "paused")


@router.post("/api/broadcasts/{bid}/resume")
def resume(bid: int):
    """Continues a paused campaign or starts a draft: queued for the next worker pass."""
    return _set(bid, ("paused", "draft"), "scheduled", ", scheduled_at=?", (ts(),))


@router.post("/api/broadcasts/{bid}/retry")
def retry(bid: int):
    """Re-sends to contacts whose message failed (and anyone new in the audience); everyone else is skipped."""
    return _set(bid, ("completed", "cancelled"), "scheduled", ", scheduled_at=?, finished_at=NULL", (ts(),))


@router.post("/api/broadcasts/{bid}/cancel")
def cancel(bid: int):
    return _set(bid, ("scheduled", "paused", "running", "draft"), "cancelled")


@router.post("/api/broadcasts/{bid}/duplicate")
def duplicate(bid: int, request: Request):
    b = _get(bid)
    new = db.run("INSERT INTO broadcasts(name,template_id,audience,scheduled_at,status,created_by) VALUES(?,?,?,?,?,?)",
                 (f"{b['name']} (copy)", b["template_id"], b["audience"], ts(), "draft", request.state.user["name"]))
    return {"id": new}


@router.delete("/api/broadcasts/{bid}")
def delete_campaign(bid: int):
    b = _get(bid)
    if b["status"] == "running":
        raise HTTPException(409, "Pause or cancel the campaign before deleting it")
    db.run("DELETE FROM broadcasts WHERE id=?", (bid,))
    return {"ok": True}
