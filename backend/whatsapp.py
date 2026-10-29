"""WhatsApp sender. MOCK mode (default) needs no credentials; CLOUD mode calls Meta's WhatsApp Cloud API;
OPENWA mode uses a self-hosted OpenWA gateway (unofficial, ban risk)."""
import os
import random
import re
import time
import uuid

import httpx

GRAPH = "https://graph.facebook.com/v20.0"
VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")


def mode() -> str:
    m = os.getenv("WA_MODE", "mock").lower()
    return "openwa" if m == "gateway" else m  # "gateway" is the neutral name for the self-hosted mode


def _cfg():
    return os.getenv("WA_TOKEN", ""), os.getenv("WA_PHONE_ID", ""), os.getenv("WA_WABA_ID", "")


def variables(body: str) -> list[str]:
    """Named variables in order of first appearance."""
    seen = []
    for v in VAR_RE.findall(body):
        if v not in seen:
            seen.append(v)
    return seen


def render(body: str, values: dict) -> str:
    return VAR_RE.sub(lambda m: str(values.get(m.group(1), "")), body)


def to_meta_body(body: str) -> str:
    names = variables(body)
    return VAR_RE.sub(lambda m: "{{%d}}" % (names.index(m.group(1)) + 1), body)


def normalize_phone(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) == 10:
        digits = os.getenv("DEFAULT_COUNTRY_CODE", "91") + digits
    return digits


def _post(path: str, payload: dict) -> dict:
    token, _, _ = _cfg()
    r = httpx.post(f"{GRAPH}/{path}", json=payload, timeout=20,
                   headers={"Authorization": f"Bearer {token}"})
    data = r.json()
    if r.status_code >= 400:
        raise RuntimeError(data.get("error", {}).get("message", r.text))
    return data


# ---------- OpenWA (unofficial, self-hosted gateway driving a normal WhatsApp account) ----------

_session_cache: dict = {}
_get_cache: dict = {}   # path -> (time, data) for session status reads
STATUS_TTL = 20         # seconds. The gateway allows ~1000 calls/hour per key; dashboards poll, so share reads.
_STATUS_PATH = re.compile(r"/sessions(/[^/]+)?")


class NoSession(RuntimeError):
    pass


class GatewayDown(RuntimeError):
    pass


def _ow_base() -> str:
    return os.getenv("OPENWA_URL", "http://localhost:2785").rstrip("/") + "/api"


def _ow_headers() -> dict:
    return {"X-API-Key": os.getenv("OPENWA_API_KEY", "")}


def _ow(method: str, path: str, **kw):
    cacheable = method == "GET" and _STATUS_PATH.fullmatch(path)
    if cacheable and path in _get_cache and time.monotonic() - _get_cache[path][0] < STATUS_TTL:
        return _get_cache[path][1]
    if method != "GET":
        _get_cache.clear()  # an action (start, stop, send...) may change status: read fresh next time
    try:
        r = httpx.request(method, _ow_base() + path, headers=_ow_headers(), timeout=20, **kw)
    except httpx.ConnectError:
        raise GatewayDown("The WhatsApp gateway is not running. It usually restarts by itself within a minute.")
    try:
        data = r.json()
    except ValueError:
        data = {}
    if r.status_code >= 400:
        msg = data.get("message") if isinstance(data, dict) else None
        if r.status_code == 429:
            msg = "WhatsApp gateway is rate-limiting requests (too many in the last hour). Try again shortly."
        raise RuntimeError(msg if isinstance(msg, str) else f"WhatsApp gateway error (HTTP {r.status_code}): {r.text[:200]}")
    if cacheable:
        _get_cache[path] = (time.monotonic(), data)
    return data


def ow_session_id() -> str:
    """OPENWA_SESSION may be a session id or a name; names are resolved to ids once."""
    ref = os.getenv("OPENWA_SESSION", "btree")
    if ref in _session_cache:
        return _session_cache[ref]
    for s in _ow("GET", "/sessions"):
        if ref in (s.get("id"), s.get("name")):
            _session_cache[ref] = s["id"]
            return s["id"]
    raise NoSession("No WhatsApp session yet. Open Sessions, create one and link a phone.")


def ow_setup(webhook_url: str) -> dict:
    """Creates the session if missing, starts it and registers the webhook. Safe to call repeatedly."""
    ref = os.getenv("OPENWA_SESSION", "btree")
    sess = next((s for s in _ow("GET", "/sessions") if ref in (s.get("id"), s.get("name"))), None)
    if not sess:
        sess = _ow("POST", "/sessions", json={"name": ref})
    sid = sess["id"]
    _session_cache[ref] = sid
    if sess.get("status") in (None, "created", "disconnected", "failed"):
        _ow("POST", f"/sessions/{sid}/start")
    body = {"url": webhook_url, "events": ["message.received", "message.ack", "session.status"]}
    if os.getenv("OPENWA_WEBHOOK_SECRET"):
        body["secret"] = os.getenv("OPENWA_WEBHOOK_SECRET")
    try:
        hooks = _ow("GET", f"/sessions/{sid}/webhooks")
    except RuntimeError:
        hooks = []
    # Re-create our webhook each time so a changed secret/URL always takes effect.
    for h in (hooks if isinstance(hooks, list) else []):
        if h.get("url") == webhook_url and h.get("id"):
            _ow("DELETE", f"/sessions/{sid}/webhooks/{h['id']}")
    _ow("POST", f"/sessions/{sid}/webhooks", json=body)
    return {"session_id": sid}


def ow_status() -> dict:
    try:
        sid = ow_session_id()
    except NoSession:  # other errors (gateway down, rate limit) are reported as such, not as "not created"
        return {"status": "not_created"}
    return _ow("GET", f"/sessions/{sid}")


def ow_qr() -> dict:
    return _ow("GET", f"/sessions/{ow_session_id()}/qr")


def own_number() -> str:
    """The business WhatsApp number (digits), used for tap-to-reply links. '' when unknown."""
    n = re.sub(r"\D", "", os.getenv("WHATSAPP_NUMBER", ""))
    if not n and mode() == "openwa":
        try:
            n = re.sub(r"\D", "", str(ow_status().get("phone") or ""))
        except Exception:
            n = ""
    return n


def _ow_send_text(phone: str, text: str, chat_id: str = "") -> str:
    body = {"chatId": chat_id or f"{phone}@c.us", "text": text}
    if "wa.me/" in text:
        body["linkPreview"] = False  # tap-to-reply options are links; a preview card would just add clutter
    # WhatsApp may identify a contact by a privacy id (@lid), not a phone number: reply to the id we received.
    data = _ow("POST", f"/sessions/{ow_session_id()}/messages/send-text", json=body)
    return str(data.get("messageId") or data.get("id") or "openwa." + uuid.uuid4().hex[:16])


def pace():
    """Pause between broadcast messages (OpenWA mode only) to lower the chance of a ban."""
    if mode() == "openwa":
        delay = float(os.getenv("OPENWA_SEND_DELAY", "8"))
        time.sleep(delay + random.uniform(0, delay / 2))


def send_text(phone: str, text: str, chat_id: str = "") -> str:
    if mode() == "mock":
        return "mock." + uuid.uuid4().hex[:16]
    if mode() == "openwa":
        return _ow_send_text(phone, text, chat_id)
    _, pid, _ = _cfg()
    data = _post(f"{pid}/messages", {"messaging_product": "whatsapp", "to": phone,
                                     "type": "text", "text": {"body": text}})
    return data["messages"][0]["id"]


def send_template(phone: str, name: str, language: str, body: str, values: dict, chat_id: str = "") -> str:
    if mode() == "mock":
        return "mock." + uuid.uuid4().hex[:16]
    if mode() == "openwa":
        # No Meta templates here: send the rendered text.
        return _ow_send_text(phone, render(body, values), chat_id)
    _, pid, _ = _cfg()
    params = [{"type": "text", "text": str(values.get(v, "")) or "-"} for v in variables(body)]
    components = [{"type": "body", "parameters": params}] if params else []
    data = _post(f"{pid}/messages", {
        "messaging_product": "whatsapp", "to": phone, "type": "template",
        "template": {"name": name, "language": {"code": language}, "components": components}})
    return data["messages"][0]["id"]


def submit_template(name: str, category: str, language: str, body: str, buttons: list[str]) -> str:
    """Returns the resulting status. Mock mode auto-approves."""
    if mode() in ("mock", "openwa"):
        return "APPROVED"
    _, _, waba = _cfg()
    names = variables(body)
    body_comp = {"type": "BODY", "text": to_meta_body(body)}
    if names:
        body_comp["example"] = {"body_text": [["sample"] * len(names)]}
    components = [body_comp]
    if buttons:
        components.append({"type": "BUTTONS", "buttons": [{"type": "QUICK_REPLY", "text": b} for b in buttons]})
    data = _post(f"{waba}/message_templates", {"name": name, "category": category,
                                               "language": language, "components": components})
    return data.get("status", "PENDING")
