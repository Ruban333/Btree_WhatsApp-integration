"""Campaign audience from an uploaded file: CSV / TXT / DOCX / PDF reading, country-code validation, campaign creation."""
import base64
import io
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import helpers

main, db, auth, cur = helpers.setup()
import phones  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

ok, results = helpers.checker()
b64 = lambda s: base64.b64encode(s if isinstance(s, bytes) else s.encode()).decode()

# ---- the number rules ----
good = {"+91 98765 43210": "919876543210", "919876543210": "919876543210", "0091 98765 43210": "919876543210",
        "+1 (415) 555-2671": "14155552671", "+44 7911 123456": "447911123456", "+971 50 123 4567": "971501234567"}
for raw, want in good.items():
    ok(phones.check_phone(raw)[0] == want, f"valid: {raw}")
bad = {"9876543210": "country code", "09876543210": "country code", "+91 12345 67890": "Indian", "+91 98765": "Wrong length",
       "+999 1234567": "Unknown", "98765abc4321": "letters", "": "No phone"}
for raw, why in bad.items():
    p, err = phones.check_phone(raw)
    ok(not p and why.lower() in err.lower(), f"error for {raw!r}: {err}")

# ---- files ----
csv_text = "Name,Phone\nAsha,+91 98765 43210\nRavi,9876543211\nMeena,919876543212\nDup,+919876543210\nX,abc\n"
r = phones.read_contacts("list.csv", b64(csv_text))
ok(r["valid"] == 2 and r["invalid"] == 3, "csv: 2 valid, 3 errors (missing code, duplicate, letters)")
ok(any("Duplicate of line 2" in x["error"] for x in r["rows"]), "csv: duplicates are named")
ok(next(x for x in r["rows"] if x["name"] == "Ravi")["error"].startswith("Missing country code"), "csv: a bare 10-digit number is an error")

r = phones.read_contacts("notes.txt", b64("Asha Kumar +91 98765 43210\nRavi - 919876543211\nno number here\nBad 9876543212\n"))
ok(r["valid"] == 2 and r["invalid"] == 1 and r["rows"][0]["name"] == "Asha Kumar", "txt: numbers found in free text, names kept")

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
doc = (f'<w:document xmlns:w="{W}"><w:body><w:p><w:r><w:t>Leads</w:t></w:r></w:p><w:p><w:r><w:t>Asha +91 98765 43210</w:t></w:r></w:p>'
       f'<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Ravi</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>+91 98765 43211</w:t></w:r></w:p></w:tc></w:tr></w:tbl></w:body></w:document>')
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w") as z:
    z.writestr("word/document.xml", doc)
r = phones.read_contacts("leads.docx", b64(buf.getvalue()))
ok(r["valid"] == 2 and any(x["name"] == "Ravi" for x in r["rows"]), "docx: paragraphs and table rows are read")

try:
    from pypdf import PdfWriter  # noqa: F401
    have_pdf = True
except ImportError:
    have_pdf = False
if have_pdf:
    stream = b"BT /F1 12 Tf 20 150 Td (Asha +91 98765 43210) Tj 0 -20 Td (Ravi 9876543211) Tj ET"
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
            b"<</Length %d>>\nstream\n" % len(stream) + stream + b"\nendstream", b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    pdf, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, start=1):
        offs.append(len(pdf))
        pdf += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offs)
    pdf += b"trailer<</Root 1 0 R/Size %d>>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)
    r = phones.read_contacts("leads.pdf", b64(pdf))
    ok(r["valid"] == 1 and r["invalid"] == 1, "pdf: text is read, the number without a country code is an error")
for fn, data, why in (("x.doc", b"abc", ".docx"), ("x.png", b"abc", "CSV"), ("e.csv", b"", "empty"), ("n.txt", b"no numbers", "No phone numbers")):
    try:
        phones.read_contacts(fn, b64(data) if data else "")
        ok(False, f"{fn} is refused")
    except ValueError as e:
        ok(why.lower() in str(e).lower(), f"{fn} is refused: {e}")

# ---- through the API ----
with TestClient(main.app) as c:
    tpl = next(t for t in c.get("/api/templates").json() if t["status"] == "APPROVED")
    p = c.post("/api/broadcasts/parse-contacts", json={"filename": "list.csv", "data": b64(csv_text)})
    ok(p.status_code == 200 and p.json()["valid"] == 2, "parse endpoint reports valid and invalid rows")
    ok(c.post("/api/broadcasts/parse-contacts", json={"filename": "x.png", "data": b64("a")}).status_code == 422, "parse endpoint: bad type is a 422")
    rows = [{"name": x["name"], "phone": x["phone"]} for x in p.json()["rows"] if x["phone"]]
    base = {"name": "Upload test", "template_id": tpl["id"], "upload": rows}
    r = c.post("/api/broadcasts", json=base)
    ok(r.status_code == 422 and "agreed" in r.text, "creating without the opt-in confirmation is refused")
    r = c.post("/api/broadcasts", json={**base, "opted_in": True, "upload": [{"name": "A", "phone": "9876543210"}]})
    ok(r.status_code == 422 and "country code" in r.text, "numbers without a country code are re-checked and refused on the server")
    r = c.post("/api/broadcasts", json={**base, "opted_in": True})
    ok(r.status_code == 200 and r.json()["audience"] == 2, "campaign created for the 2 valid numbers")
    bid = r.json()["id"]
    leads = db.q("SELECT * FROM leads WHERE phone IN ('919876543210','919876543212')")
    ok(len(leads) == 2 and all(l["consent"] for l in leads), "contacts are saved and marked opted in")
    ok(all(l["source"] == "campaign upload" for l in leads if l["name"] in ("Asha", "Meena") and l["source"]), "new contacts carry the source 'campaign upload'")
    ok(c.get(f"/api/broadcasts/{bid}").status_code == 200, "campaign detail opens")
    # an opted-out person in the file is never messaged
    db.run("UPDATE leads SET opted_out=1 WHERE phone='919876543212'")
    r = c.post("/api/broadcasts", json={**base, "name": "Upload test 2", "opted_in": True})
    ok(r.status_code == 200 and r.json()["audience"] == 1, "an opted-out contact in the file is left out")

print(f"\n{results['pass']} passed, {results['fail']} failed")
sys.exit(1 if results["fail"] else 0)
