"""Reads a contact file (CSV, TXT, DOCX, PDF) and checks every number carries a country code.

A number is only accepted in international form: "+91 98765 43210", "0091 98765 43210" or "919876543210".
A bare national number (the 10 digits of "9876543210") has no country code, so it is reported as an error
instead of being guessed.
"""
import base64
import csv
import io
import re
import zipfile
from xml.etree import ElementTree

MAX_BYTES = 5_000_000
MAX_ROWS = 5000

# ITU calling codes: 1 and 7 are one digit, the rest are listed by length.
_ONE = {"1", "7"}
_TWO = set("20 27 30 31 32 33 34 36 39 40 41 43 44 45 46 47 48 49 51 52 53 54 55 56 57 58 60 61 62 63 64 65 66 81 82 84 86 90 91 92 93 94 95 98".split())
_THREE = ({str(n) for n in (212, 213, 216, 218, 291, 297, 298, 299, 350, 351, 352, 353, 354, 355, 356, 357, 358, 359, 370, 371, 372, 373, 374, 375, 376, 377,
                            378, 380, 381, 382, 383, 385, 386, 387, 389, 420, 421, 423, 670, 672, 673, 674, 675, 676, 677, 678, 679, 680, 681, 682, 683, 685,
                            686, 687, 688, 689, 690, 691, 692, 850, 852, 853, 855, 856, 880, 886, 992, 993, 994, 995, 996, 998)}
          | {str(n) for n in range(220, 259)} | {str(n) for n in range(260, 270)} | {"290"}
          | {str(n) for n in range(500, 510)} | {str(n) for n in range(590, 600)} | {str(n) for n in range(960, 978)})


def country_code(digits: str) -> str:
    """The calling code the number starts with, or '' when it starts with none."""
    for n, pool in ((1, _ONE), (2, _TWO), (3, _THREE)):
        if digits[:n] in pool:
            return digits[:n]
    return ""


def check_phone(raw: str) -> tuple[str, str]:
    """(digits with country code, "") for a good number, ("", reason) for a bad one."""
    s = (raw or "").strip()
    if not s:
        return "", "No phone number"
    if re.search(r"[A-Za-z]", s):
        return "", "Contains letters"
    digits = re.sub(r"\D", "", s)
    if s.startswith("+"):
        pass
    elif digits.startswith("00"):
        digits = digits[2:]
    elif len(digits) < 11 or not country_code(digits):
        return "", "Missing country code (use +91 98765 43210 or 919876543210)"
    cc = country_code(digits)
    if not cc:
        return "", "Unknown country code"
    if not 8 <= len(digits) <= 15:
        return "", f"Wrong length ({len(digits)} digits)"
    if cc == "91" and not re.fullmatch(r"91[6-9]\d{9}", digits):
        return "", "Indian numbers need 10 digits after +91, starting with 6-9"
    return digits, ""


# ---------- reading files ----------

def _docx_text(data: bytes) -> str:
    try:
        xml = zipfile.ZipFile(io.BytesIO(data)).read("word/document.xml")
    except (zipfile.BadZipFile, KeyError):
        raise ValueError("This is not a valid .docx file (older .doc files must be saved as .docx or PDF first)")
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    lines = []

    def para(p):
        out = []
        for el in p.iter():
            if el.tag == ns + "t":
                out.append(el.text or "")
            elif el.tag == ns + "tab":
                out.append("\t")
        return "".join(out)

    body = ElementTree.fromstring(xml).find(ns + "body")
    for el in body:
        if el.tag == ns + "p":
            lines.append(para(el))
        elif el.tag == ns + "tbl":       # a table row: its cells share one line
            for tr in el.iter(ns + "tr"):
                lines.append("\t".join(para(c) for c in tr.iter(ns + "tc")))
    return "\n".join(lines)


def _pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:
        raise ValueError("PDF reading needs the pypdf package: run  .venv\\Scripts\\pip install pypdf")
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages[:60])
    except Exception:
        raise ValueError("This PDF could not be read (it may be a scanned image or password-protected)")


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


_PHONE = re.compile(r"\+?\(?\d[\d\s().\-]{6,}\d")


def _from_text(text: str) -> list[dict]:
    """Rows from free text: on every line the phone-looking part is the number, what is left is the name."""
    rows = []
    for n, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        m = _PHONE.search(line)
        if not m:
            if re.search(r"\d{6,}", line):
                rows.append({"line": n, "name": "", "raw": line.strip()[:40]})
            continue
        name = re.sub(r"[\t,;|]+", " ", line[:m.start()] + " " + line[m.end():]).strip(" -:")
        rows.append({"line": n, "name": re.sub(r"\s+", " ", name)[:80], "raw": m.group(0).strip()})
    return rows


def _from_csv(text: str) -> list[dict] | None:
    """Rows from a CSV with a phone column, or None when the file has no such header."""
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    table = list(csv.reader(io.StringIO(text), dialect))
    if not table:
        return []
    head = [re.sub(r"[^a-z0-9]", "", (h or "").lower()) for h in table[0]]
    pcol = next((i for i, h in enumerate(head) if h in ("phone", "mobile", "mobilenumber", "whatsapp", "phonenumber", "contact", "number", "whatsappnumber")), None)
    if pcol is None:
        return None
    ncol = next((i for i, h in enumerate(head) if h in ("name", "fullname", "contactname", "student", "studentname")), None)
    rows = []
    for n, r in enumerate(table[1:], start=2):
        if not any((c or "").strip() for c in r):
            continue
        raw = (r[pcol] if pcol < len(r) else "").strip()
        rows.append({"line": n, "name": (r[ncol].strip() if ncol is not None and ncol < len(r) else "")[:80], "raw": raw})
    return rows


def read_contacts(filename: str, b64: str) -> dict:
    """{"rows": [...], "valid": n, "invalid": n}: every row has line, name, raw, phone, error."""
    try:
        data = base64.b64decode(b64.split(",", 1)[-1], validate=False)
    except Exception:
        raise ValueError("The file could not be read")
    if not data:
        raise ValueError("The file is empty")
    if len(data) > MAX_BYTES:
        raise ValueError("File too large (5 MB max)")
    ext = (filename or "").rsplit(".", 1)[-1].lower() if "." in (filename or "") else ""
    if ext == "docx":
        rows = _from_text(_docx_text(data))
    elif ext == "pdf" or data[:4] == b"%PDF":
        rows = _from_text(_pdf_text(data))
    elif ext == "doc":
        raise ValueError("Older .doc files are not supported: save it as .docx, PDF or CSV and upload that")
    elif ext in ("csv", "txt", "tsv", ""):
        text = _decode(data)
        rows = _from_csv(text)
        if rows is None:
            rows = _from_text(text)
    else:
        raise ValueError("Upload a CSV, TXT, DOCX or PDF file")
    if not rows:
        raise ValueError("No phone numbers were found in this file")
    seen: dict[str, int] = {}
    out = []
    for r in rows[:MAX_ROWS]:
        phone, err = check_phone(r["raw"])
        if phone and phone in seen:
            phone, err = "", f"Duplicate of line {seen[check_phone(r['raw'])[0]]}"
        out.append({**r, "phone": phone, "error": err})
        if phone:
            seen[phone] = r["line"]
    valid = sum(1 for r in out if r["phone"])
    return {"rows": out, "valid": valid, "invalid": len(out) - valid, "truncated": len(rows) > MAX_ROWS}

